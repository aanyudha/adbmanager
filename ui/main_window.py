import os
import time
import html
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from adb.device import AndroidDevice, list_devices
from adb.manager import (
    adb_connect,
    append_inspection_checkpoint,
    append_inspection_event,
    adb_poweroff,
    adb_reboot,
    adb_screenshot,
    get_boot_diagnostics,
    adb_send_notification,
    adb_shell,
    adb_vendor_settings_combo,
    adb_wake,
    auto_reconnect,
    collect_device_inspection,
    get_device_health_snapshot,
    get_all_device_status,
    get_device_status,
    launch_scrcpy,
)
from utils.device_store import add_device, load_devices
from utils.paths import ensure_runtime_dir


RAW_STATUS_ICONS = {
    "device": "[OK]",
    "booting": "[BOOT]",
    "unauthorized": "[AUTH]",
    "offline": "[OFF]",
}

RAW_TO_UI_STATUS = {
    "device": "CONNECTED",
    "booting": "BOOTING",
    "unauthorized": "UNAUTHORIZED",
    "offline": "OFFLINE",
}

STATUS_THEME = {
    "CONNECTED": {
        "text": "[OK]",
        "fg": "#166534",
        "bg": "#dcfce7",
    },
    "UNAUTHORIZED": {
        "text": "[AUTH]",
        "fg": "#92400e",
        "bg": "#fef3c7",
    },
    "OFFLINE": {
        "text": "[OFF]",
        "fg": "#b45309",
        "bg": "#ffedd5",
    },
    "BOOTING": {
        "text": "[BOOT]",
        "fg": "#1d4ed8",
        "bg": "#dbeafe",
    },
    "OS DOWN": {
        "text": "[DOWN]",
        "fg": "#991b1b",
        "bg": "#fee2e2",
    },
}

ACTIVE_WATCHDOG_INTERVAL_MS = 1500
BACKGROUND_STATUS_INTERVAL_SECONDS = 2
SAVED_DEVICE_HEALTH_REFRESH_SECONDS = 15
SCANNED_DEVICE_REFRESH_SECONDS = 10


class StatusWorker(QThread):
    status_updated = Signal(dict)

    def __init__(
        self,
        get_devices_callback,
        interval=BACKGROUND_STATUS_INTERVAL_SECONDS,
        health_refresh_seconds=SAVED_DEVICE_HEALTH_REFRESH_SECONDS,
    ):
        super().__init__()
        self.get_devices = get_devices_callback
        self.interval = interval
        self.health_refresh_seconds = health_refresh_seconds
        self.health_cache = {}
        self.running = True

    def run(self):
        while self.running:
            try:
                saved_devices = self.get_devices() or []
                saved_serials = []

                for device in saved_devices:
                    ip = device.get("ip", "").strip()
                    port = str(device.get("port") or "5555").strip()
                    if ip:
                        saved_serials.append(f"{ip}:{port}")

                status_map = get_all_device_status(refresh_serials=saved_serials)
                now = time.time()
                active_serials = set(saved_serials)

                for serial in list(self.health_cache):
                    if serial not in active_serials:
                        self.health_cache.pop(serial, None)

                for serial in saved_serials:
                    if status_map.get(serial) not in ("device", "booting"):
                        continue

                    cached = self.health_cache.get(serial)
                    if cached and (now - cached.get("fetched_at", 0)) < self.health_refresh_seconds:
                        continue

                    snapshot = get_device_health_snapshot(serial)
                    snapshot["fetched_at"] = now
                    self.health_cache[serial] = snapshot

                self.status_updated.emit(
                    {
                        "status_map": status_map,
                        "health_map": {
                            serial: {
                                key: value
                                for key, value in data.items()
                                if key != "fetched_at"
                            }
                            for serial, data in self.health_cache.items()
                        },
                    }
                )

            except Exception as e:
                print("StatusWorker error:", e)

            for _ in range(int(self.interval * 10)):
                if not self.running:
                    return
                self.msleep(100)

    def stop(self):
        self.running = False


class DeviceActionWorker(QThread):
    result_ready = Signal(object)

    def __init__(self, action):
        super().__init__()
        self.action = action

    def run(self):
        try:
            result = self.action() or ""
        except Exception as exc:
            result = str(exc)
        self.result_ready.emit(result)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Heisenberg ADB Control Tool")
        self.resize(1100, 600)

        self.active_serial = None
        self.active_source = None
        self.last_active_status = None
        self.devices = []
        self.saved_devices = []
        self.saved_device_health = {}
        self.inspection_sessions = {}
        self.inspection_worker = None
        self.reboot_worker = None
        self.last_background_scan_refresh_at = 0.0

        self.tabs = QTabWidget()
        self.tab_import = QWidget()
        self.tab_control = QWidget()

        self.init_import_tab()
        self.init_control_tab()

        self.tabs.addTab(self.tab_import, "Device Import")
        self.tabs.addTab(self.tab_control, "ADB Control")
        self.setCentralWidget(self.tabs)

        self.set_controls_enabled(False)
        self.load_saved_devices()
        self.scan_devices()

        self.timer = QTimer()
        self.timer.timeout.connect(self.watchdog)
        self.timer.start(ACTIVE_WATCHDOG_INTERVAL_MS)

        self.status_worker = StatusWorker(
            get_devices_callback=lambda: self.saved_devices,
            interval=BACKGROUND_STATUS_INTERVAL_SECONDS,
            health_refresh_seconds=SAVED_DEVICE_HEALTH_REFRESH_SECONDS,
        )
        self.status_worker.status_updated.connect(self.update_saved_device_status)
        self.status_worker.start()

    # =========================================================
    # ================= TAB 1 : IMPORT ========================
    # =========================================================

    def init_import_tab(self):
        layout = QVBoxLayout()

        self.import_btn = QPushButton("Import Device TXT File")
        self.import_btn.clicked.connect(self.import_device_file)
        self.template_btn = QPushButton("Download Template TXT")
        self.template_btn.clicked.connect(self.download_template_file)

        self.import_log = QTextEdit()
        self.import_log.setReadOnly(True)

        layout.addWidget(QLabel("Import Devices (name, ip, port, private_key, public_key)"))
        layout.addWidget(self.import_btn)
        layout.addWidget(self.template_btn)
        layout.addWidget(self.import_log)

        self.tab_import.setLayout(layout)

    def import_device_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Device File", "", "Text Files (*.txt)"
        )
        if not file_path:
            return

        devices = self.parse_device_file(file_path)

        for device in devices:
            if not device.get("name") or not device.get("ip"):
                self.import_log.append(f"Skipped invalid entry: {device}")
                continue

            add_device(
                device.get("name"),
                device.get("ip"),
                device.get("port") or "5555",
                device.get("private_key"),
                device.get("public_key"),
            )

        self.import_log.setText(f"Imported {len(devices)} device(s) successfully.")
        self.load_saved_devices()

    def parse_device_file(self, file_path):
        devices = []
        current = {}

        with open(file_path, "r", encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()

                if not line:
                    if current:
                        devices.append(current)
                        current = {}
                    continue

                if line.startswith("#"):
                    continue

                if "=" in line:
                    key, value = line.split("=", 1)
                    current[key.strip().lower()] = value.strip()

            if current:
                devices.append(current)

        return devices

    def download_template_file(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Template File",
            "device_template.txt",
            "Text Files (*.txt)",
        )

        if not file_path:
            return

        template_content = """# ============================================
# Heisenberg ADB Control Tool - RAW KEY TEMPLATE
# ============================================
# IMPORTANT:
# Paste RAW ADB key strings directly (no file path)
# Leave one empty line between devices
# ============================================

name=STB_Ruangan_101
ip=192.168.1.101
port=5555
private_key=MIIEvQIBADANBgkqhkiG9w0BAQEFAASAMPLEPRIVATEKEY
public_key=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8ASAMPLEPUBLICKEY

name=STB_Ruangan_102
ip=192.168.1.102
port=5555
private_key=MIIEvQIBADANBgkqhkiG9w0BAQEFAASAMPLEPRIVATEKEY2
public_key=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8ASAMPLEPUBLICKEY2
"""

        try:
            with open(file_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(template_content)

            QMessageBox.information(
                self,
                "Template Saved",
                f"Template file saved successfully:\n{file_path}",
            )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to save template:\n{e}",
            )

    # =========================================================
    # ================= TAB 2 : CONTROL =======================
    # =========================================================

    def init_control_tab(self):
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP Address")

        self.port_input = QLineEdit("5555")
        self.connect_btn = QPushButton("ADB Connect")

        self.reboot_btn = QPushButton("Restart OS")
        self.turn_on_btn = QPushButton("Turn On")
        self.power_btn = QPushButton("Power Off")
        self.scrcpy_btn = QPushButton("Scrcpy")
        self.shot_btn = QPushButton("Screenshot")
        self.inspect_btn = QPushButton("Inspection")
        self.settings_btn = QPushButton("System Settings")
        self.vendor_btn = QPushButton("Vendor Settings")
        self.vendor_btn.clicked.connect(self.open_vendor_settings)

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("adb shell command")
        self.cmd_exec_btn = QPushButton("Run Command")

        self.scan_btn = QPushButton("Scan Devices")
        self.device_list = QListWidget()
        self.saved_list = QListWidget()
        self.save_btn = QPushButton("Save Device")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search IP or room number")
        self.reset_search_btn = QPushButton("Reset Search")

        self.info_box = QTextEdit()
        self.info_box.setReadOnly(True)
        self.inspection_meta = QLabel("Inspection log belum ada. Pilih device lalu klik Inspection.")
        self.inspection_meta.setWordWrap(True)
        self.inspection_box = QTextEdit()
        self.inspection_box.setReadOnly(True)
        self.inspection_box.setPlaceholderText(
            "Hasil inspection akan tampil di sini, termasuk warning listrik dan jaringan."
        )

        self.status_label = QLabel("[OFF] No device selected")
        self.status_label.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(QLabel("IP"))
        top.addWidget(self.ip_input)
        top.addWidget(QLabel("Port"))
        top.addWidget(self.port_input)
        top.addWidget(self.connect_btn)

        action = QHBoxLayout()
        action.addWidget(self.reboot_btn)
        action.addWidget(self.turn_on_btn)
        action.addWidget(self.power_btn)
        action.addWidget(self.scrcpy_btn)
        action.addWidget(self.shot_btn)
        action.addWidget(self.inspect_btn)
        action.addWidget(self.settings_btn)
        action.addWidget(self.vendor_btn)

        cmd_bar = QHBoxLayout()
        cmd_bar.addWidget(self.cmd_input)
        cmd_bar.addWidget(self.cmd_exec_btn)

        search_bar = QHBoxLayout()
        search_bar.addWidget(self.search_input)
        search_bar.addWidget(self.reset_search_btn)

        left = QVBoxLayout()
        left.addLayout(top)
        left.addWidget(self.scan_btn)
        left.addWidget(QLabel("Scanned Devices"))
        left.addWidget(self.device_list)
        left.addWidget(self.save_btn)
        left.addLayout(action)
        left.addWidget(QLabel("Saved Devices"))
        left.addLayout(search_bar)
        left.addWidget(self.saved_list)
        left.addWidget(self.status_label)

        right = QVBoxLayout()
        right.addWidget(QLabel("Manual ADB Command"))
        right.addLayout(cmd_bar)
        right.addWidget(QLabel("Device Info / Output"))
        right.addWidget(self.info_box, 1)
        right.addWidget(QLabel("Inspection Log"))
        right.addWidget(self.inspection_meta)
        right.addWidget(self.inspection_box, 1)

        main = QHBoxLayout()
        main.addLayout(left, 1)
        main.addLayout(right, 2)

        self.tab_control.setLayout(main)

        self.connect_btn.clicked.connect(self.connect_device)
        self.scan_btn.clicked.connect(self.scan_devices)
        self.device_list.itemClicked.connect(self.on_scanned_clicked)
        self.saved_list.itemClicked.connect(self.on_saved_clicked)
        self.save_btn.clicked.connect(self.save_current_device)

        self.reboot_btn.clicked.connect(self.reboot_device)
        self.turn_on_btn.clicked.connect(self.turn_on_device)
        self.power_btn.clicked.connect(self.poweroff_device)
        self.scrcpy_btn.clicked.connect(self.scrcpy_device)
        self.shot_btn.clicked.connect(self.screenshot_device)
        self.inspect_btn.clicked.connect(self.run_inspection)
        self.settings_btn.clicked.connect(self.open_settings)
        self.cmd_exec_btn.clicked.connect(self.run_manual_command)
        self.search_input.textChanged.connect(self.apply_device_filters)
        self.reset_search_btn.clicked.connect(self.reset_search)

        self.device_list.setStyleSheet(
            "QListWidget::item { padding: 6px 8px; border-radius: 6px; }"
            "QListWidget::item:selected { background: #dbeafe; color: #0f172a; }"
        )
        self.device_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.device_list.setUniformItemSizes(True)
        self.saved_list.setStyleSheet(
            "QListWidget::item { padding: 6px 8px; border-radius: 6px; }"
            "QListWidget::item:selected { background: #dbeafe; color: #0f172a; }"
        )
        self.saved_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.saved_list.setUniformItemSizes(True)
        self.status_label.setStyleSheet(
            "padding: 12px; border-radius: 10px; font-weight: 600;"
        )
        self.info_box.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")
        self.inspection_box.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace;"
        )

    # ================= HELPERS =================

    def normalize_port(self, port):
        return str(port or "5555").strip() or "5555"

    def make_serial(self, ip, port):
        return f"{ip}:{self.normalize_port(port)}"

    def raw_status_to_icon(self, raw_status):
        return RAW_STATUS_ICONS.get(raw_status, "[DOWN]")

    def raw_status_to_ui_status(self, raw_status):
        return RAW_TO_UI_STATUS.get(raw_status, "OS DOWN")

    def get_status_theme(self, status):
        return STATUS_THEME.get(status, STATUS_THEME["OS DOWN"])

    def apply_status_style_to_item(self, item, status):
        theme = self.get_status_theme(status)
        item.setForeground(QColor(theme["fg"]))
        item.setBackground(QColor(theme["bg"]))

    def get_saved_serials(self):
        serials = []
        for device in self.saved_devices:
            ip = device.get("ip", "").strip()
            if not ip:
                continue
            serials.append(self.make_serial(ip, device.get("port")))
        return serials

    def get_item_serial(self, item):
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def update_turn_on_button(self):
        self.turn_on_btn.setEnabled(bool(self.active_serial))
        self.inspect_btn.setEnabled(bool(self.active_serial))

    def get_inspection_session(self, serial=None):
        target_serial = serial or self.active_serial
        if not target_serial:
            return None
        return self.inspection_sessions.get(target_serial)

    def render_inspection_session(self, serial=None):
        session = self.get_inspection_session(serial)
        if not session:
            self.inspection_meta.setText(
                "Inspection log belum ada. Klik Inspection untuk membaca kondisi device ini."
            )
            self.inspection_box.clear()
            return

        report = session.get("latest_report") or {}
        log_path = Path(session["log_path"])
        log_text = ""
        if log_path.exists():
            log_text = log_path.read_text(encoding="utf-8")

        display_path = (
            log_path.relative_to(Path.cwd())
            if log_path.is_relative_to(Path.cwd())
            else log_path
        )
        self.inspection_meta.setText(
            f"Inspection monitor aktif: {display_path} | Last status: {session.get('last_logged_status', '-')}"
        )
        self.inspection_box.setHtml(
            self.format_inspection_html(
                report,
                full_log_text=log_text,
                monitor_active=True,
                session=session,
            )
        )

    def reset_inspection_view(self):
        if self.active_serial and self.get_inspection_session(self.active_serial):
            self.render_inspection_session(self.active_serial)
            return

        self.inspection_meta.setText(
            "Inspection log belum ada. Klik Inspection untuk membaca kondisi device ini."
        )
        self.inspection_box.clear()

    def remember_inspection_session(self, report):
        electrical = report.get("hardware", {}).get("electrical", {})
        storage_entries = report.get("hardware", {}).get("storage", [])
        storage_text = report.get("hardware", {}).get("storage_summary", "-")
        storage_percent = None
        storage_mount = "-"
        if storage_entries:
            primary_storage = storage_entries[0]
            for entry in storage_entries:
                mount = entry.get("mount") or ""
                if mount == "/data" or mount.startswith("/data/"):
                    primary_storage = entry
                    break
            storage_mount = primary_storage.get("mount") or "-"
            storage_percent = primary_storage.get("use_percent")
            if storage_percent is not None:
                storage_text = f"{storage_mount} {storage_percent}% used"

        self.inspection_sessions[report["serial"]] = {
            "log_path": report["log_path"],
            "last_logged_status": report["status"],
            "latest_report": report,
            "stats": {
                "voltage_min": electrical.get("voltage_volts"),
                "voltage_max": electrical.get("voltage_volts"),
                "temperature_min": electrical.get("temperature_c"),
                "temperature_max": electrical.get("temperature_c"),
            },
        }
        self.saved_device_health[report["serial"]] = {
            "voltage_raw": electrical.get("voltage_raw"),
            "voltage_volts": electrical.get("voltage_volts"),
            "voltage_text": electrical.get("voltage_text", "-"),
            "storage_mount": storage_mount,
            "storage_percent": storage_percent,
            "storage_text": storage_text,
        }

    def update_inspection_session_stats(self, session, report):
        stats = session.setdefault(
            "stats",
            {
                "voltage_min": None,
                "voltage_max": None,
                "temperature_min": None,
                "temperature_max": None,
            },
        )
        electrical = report.get("hardware", {}).get("electrical", {})

        voltage = electrical.get("voltage_volts")
        if voltage is not None:
            if stats["voltage_min"] is None or voltage < stats["voltage_min"]:
                stats["voltage_min"] = voltage
            if stats["voltage_max"] is None or voltage > stats["voltage_max"]:
                stats["voltage_max"] = voltage

        temperature = electrical.get("temperature_c")
        if temperature is not None:
            if stats["temperature_min"] is None or temperature < stats["temperature_min"]:
                stats["temperature_min"] = temperature
            if stats["temperature_max"] is None or temperature > stats["temperature_max"]:
                stats["temperature_max"] = temperature

    def format_session_range(self, stats, prefix):
        min_value = stats.get(f"{prefix}_min")
        max_value = stats.get(f"{prefix}_max")
        if min_value is None and max_value is None:
            return "-"
        if min_value is None:
            return f"max {max_value:.2f}"
        if max_value is None:
            return f"min {min_value:.2f}"
        return f"min {min_value:.2f} / max {max_value:.2f}"

    def append_monitored_inspection_event(self, title, detail="", status="", serial=None):
        target_serial = serial or self.active_serial
        session = self.get_inspection_session(target_serial)
        if not session or not target_serial:
            return

        append_inspection_event(
            session["log_path"],
            target_serial,
            title,
            detail=detail,
            status=status or session.get("last_logged_status", ""),
        )
        if target_serial == self.active_serial:
            self.render_inspection_session(target_serial)

    def capture_monitored_inspection_checkpoint(
        self,
        reason,
        status=None,
        force=False,
        serial=None,
    ):
        target_serial = serial or self.active_serial
        session = self.get_inspection_session(target_serial)
        if not session or not target_serial:
            return

        previous_status = session.get("last_logged_status")
        if not force and status is not None and status == previous_status:
            return

        report = append_inspection_checkpoint(
            session["log_path"],
            target_serial,
            reason,
            previous_status=previous_status,
        )
        session["last_logged_status"] = report["status"]
        session["latest_report"] = report
        self.update_inspection_session_stats(session, report)
        if target_serial == self.active_serial:
            self.render_inspection_session(target_serial)

    def reset_search(self):
        if self.search_input.text():
            self.search_input.clear()
        else:
            self.apply_device_filters()
        self.search_input.setFocus()

    def matches_device_filter(self, query, name="", serial=""):
        normalized_query = (query or "").strip().lower()
        if not normalized_query:
            return True

        haystacks = [name.lower(), serial.lower()]
        return any(normalized_query in haystack for haystack in haystacks)

    def get_saved_device_name(self, serial):
        for device in self.saved_devices:
            device_serial = self.make_serial(device["ip"], device.get("port"))
            if device_serial == serial:
                return device.get("name", "").strip()
        return ""

    def get_device_display_name(self, serial):
        saved_name = self.get_saved_device_name(serial)
        if saved_name:
            return f"{saved_name} ({serial})"
        return serial

    def make_scanned_item(self, device):
        item = QListWidgetItem(
            f"{self.raw_status_to_icon(device.status)} {device.serial} ({device.status})"
        )
        item.setData(Qt.UserRole, device.serial)
        item.setData(Qt.UserRole + 1, device.status)
        self.apply_status_style_to_item(
            item,
            self.raw_status_to_ui_status(device.status),
        )
        return item

    def get_saved_item_text(self, device_data, raw_status):
        serial = self.make_serial(device_data["ip"], device_data.get("port"))
        health = self.saved_device_health.get(serial, {})
        voltage_text = health.get("voltage_text") or "-"
        storage_text = health.get("storage_text") or "-"
        return (
            f"{self.raw_status_to_icon(raw_status)} {device_data['name']} ({serial})\n"
            f"Volt: {voltage_text} | Storage: {storage_text}"
        )

    def get_saved_item_tooltip(self, device_data, raw_status):
        serial = self.make_serial(device_data["ip"], device_data.get("port"))
        health = self.saved_device_health.get(serial, {})
        voltage_text = health.get("voltage_text") or "-"
        storage_text = health.get("storage_text") or "-"
        return (
            f"{device_data['name']} ({serial})\n"
            f"Status: {self.raw_status_to_ui_status(raw_status)}\n"
            f"Voltage: {voltage_text}\n"
            f"Storage: {storage_text}"
        )

    def update_saved_item_widget(self, item, device_data, raw_status):
        serial = self.make_serial(device_data["ip"], device_data.get("port"))
        item.setText(self.get_saved_item_text(device_data, raw_status))
        item.setData(Qt.UserRole, serial)
        item.setData(Qt.UserRole + 1, raw_status)
        item.setData(Qt.UserRole + 2, device_data["name"])
        item.setToolTip(self.get_saved_item_tooltip(device_data, raw_status))
        self.apply_status_style_to_item(
            item,
            self.raw_status_to_ui_status(raw_status),
        )

    def make_saved_item(self, device_data, raw_status):
        item = QListWidgetItem()
        self.update_saved_item_widget(item, device_data, raw_status)
        return item

    def sync_active_device_state(self, status, refresh_info=False):
        if not self.active_serial:
            return False

        status_changed = status != self.last_active_status
        self.last_active_status = status
        self.update_ui_by_status(status)

        if refresh_info or status_changed:
            self.show_device_info_by_serial(self.active_serial, status=status)

        return status_changed

    def refresh_saved_list(self, status_map, reload_devices=False):
        query = self.search_input.text().strip()
        selected_serial = self.get_item_serial(self.saved_list.currentItem())
        if not selected_serial and self.active_source == "saved":
            selected_serial = self.active_serial

        if reload_devices:
            self.saved_devices = load_devices()

        filtered_devices = []
        desired_serials = []

        for device in self.saved_devices:
            serial = self.make_serial(device["ip"], device.get("port"))
            if not self.matches_device_filter(query, device["name"], serial):
                continue
            filtered_devices.append(device)
            desired_serials.append(serial)

        current_serials = [
            self.get_item_serial(self.saved_list.item(index))
            for index in range(self.saved_list.count())
        ]

        self.saved_list.blockSignals(True)
        self.saved_list.setUpdatesEnabled(False)
        try:
            if current_serials != desired_serials:
                self.saved_list.clear()

                selected_item = None
                for device in filtered_devices:
                    serial = self.make_serial(device["ip"], device.get("port"))
                    raw_status = status_map.get(serial, "offline")
                    item = self.make_saved_item(device, raw_status)
                    self.saved_list.addItem(item)

                    if serial == selected_serial:
                        selected_item = item

                if selected_item is not None:
                    self.saved_list.setCurrentItem(selected_item)
            else:
                for index, device in enumerate(filtered_devices):
                    serial = desired_serials[index]
                    raw_status = status_map.get(serial, "offline")
                    item = self.saved_list.item(index)
                    self.update_saved_item_widget(item, device, raw_status)

                if selected_serial:
                    for index, serial in enumerate(desired_serials):
                        if serial == selected_serial:
                            selected_item = self.saved_list.item(index)
                            if selected_item is not self.saved_list.currentItem():
                                self.saved_list.setCurrentItem(selected_item)
                            break
        finally:
            self.saved_list.setUpdatesEnabled(True)
            self.saved_list.blockSignals(False)

    # ================= CORE =================

    def connect_device(self):
        ip = self.ip_input.text().strip()
        port = self.normalize_port(self.port_input.text())

        if not ip:
            QMessageBox.warning(self, "ADB", "IP address is required.")
            return

        self.port_input.setText(port)
        self.active_serial = self.make_serial(ip, port)
        self.active_source = "manual"
        self.update_turn_on_button()
        self.reset_inspection_view()

        message = adb_connect(ip, port, timeout=5) or "No response from adb."
        QMessageBox.information(self, "ADB", message)

        self.scan_devices()
        self.load_saved_devices()

        status = get_device_status(self.active_serial)
        self.sync_active_device_state(status, refresh_info=True)

    def scan_devices(self):
        query = self.search_input.text().strip()
        selected_serial = self.get_item_serial(self.device_list.currentItem())
        if not selected_serial and self.active_source == "scanned":
            selected_serial = self.active_serial

        self.device_list.clear()
        self.devices = list_devices()

        selected_item = None
        for device in self.devices:
            if not self.matches_device_filter(query, serial=device.serial):
                continue

            item = self.make_scanned_item(device)
            self.device_list.addItem(item)

            if device.serial == selected_serial:
                selected_item = item

        if selected_item is not None:
            self.device_list.setCurrentItem(selected_item)

    def apply_device_filters(self):
        status_map = get_all_device_status()
        self.refresh_saved_list(status_map)
        self.scan_devices()

    def set_controls_enabled(self, enabled):
        for button in [
            self.reboot_btn,
            self.power_btn,
            self.scrcpy_btn,
            self.shot_btn,
            self.settings_btn,
            self.cmd_exec_btn,
            self.vendor_btn,
        ]:
            button.setEnabled(enabled)

        self.update_turn_on_button()

    def open_vendor_settings(self):
        if not self.active_serial:
            QMessageBox.warning(self, "Error", "No active device selected")
            return
        adb_vendor_settings_combo(self.active_serial)

    def on_scanned_clicked(self, item):
        serial = self.get_item_serial(item)
        raw_status = item.data(Qt.UserRole + 1)
        if not serial:
            return

        serial_changed = serial != self.active_serial
        self.active_serial = serial
        self.active_source = "scanned"
        self.update_turn_on_button()
        if serial_changed:
            self.reset_inspection_view()

        if ":" in serial:
            ip, port = serial.split(":", 1)
            self.ip_input.setText(ip)
            self.port_input.setText(port)

        status = self.raw_status_to_ui_status(raw_status)
        if raw_status == "device":
            status = get_device_status(serial)

        self.sync_active_device_state(
            status,
            refresh_info=True,
        )

    def on_saved_clicked(self, item):
        serial = self.get_item_serial(item)
        if not serial or ":" not in serial:
            return

        serial_changed = serial != self.active_serial
        self.active_serial = serial
        self.active_source = "saved"
        self.update_turn_on_button()
        if serial_changed:
            self.reset_inspection_view()

        ip, port = serial.split(":", 1)
        self.ip_input.setText(ip)
        self.port_input.setText(port)

        adb_connect(ip, port, timeout=2)
        self.sync_active_device_state(get_device_status(serial), refresh_info=True)

    def show_device_info_by_serial(self, serial, status=None):
        try:
            status = status or get_device_status(serial)

            if status == "BOOTING":
                self.info_box.setText(
                    self.format_boot_diagnostics(
                        serial,
                        get_boot_diagnostics(serial),
                    )
                )
                return

            if status != "CONNECTED":
                self.info_box.setText(f"Device not ready\n\nStatus: {status}")
                return

            device = AndroidDevice(serial, "device")
            self.info_box.setText(self.format_device_info(device.info()))

        except Exception as e:
            self.info_box.setText(f"Failed to read device info\n\n{e}")

    def format_boot_diagnostics(self, serial, diagnostics):
        foreground = diagnostics.get("foreground_activity") or "-"
        boot_completed = diagnostics.get("sys.boot_completed") or "(empty)"
        dev_bootcomplete = diagnostics.get("dev.bootcomplete") or "(empty)"
        bootanim = diagnostics.get("init.svc.bootanim") or "(empty)"

        return f"""
BOOT DIAGNOSTICS
----------------
Serial         : {serial}
Status         : BOOTING
sys.boot_completed : {boot_completed}
dev.bootcomplete   : {dev_bootcomplete}
init.svc.bootanim  : {bootanim}

Foreground Activity
-------------------
{foreground}

Notes
-----
ADB is reachable, but Android boot is not marked complete yet.
If this state stays for a long time, the device may be stuck on the Google logo or app splash screen.
""".strip()

    def format_device_info(self, info):
        return f"""
DEVICE INFORMATION
------------------
Brand          : {info['brand']}
Model          : {info['model']}
Manufacturer   : {info['manufacturer']}
Device         : {info['device']}
Board          : {info['board']}
Hardware       : {info['hardware']}

ANDROID
-------
Android Version: {info['android_version']}
SDK Level      : {info['sdk']}
Security Patch : {info['security_patch']}

FIRMWARE
--------
Firmware       : {info['firmware']}
Build ID       : {info['build_id']}
Build Type     : {info['build_type']}
Build Tags     : {info['build_tags']}
Fingerprint    :
{info['fingerprint']}

CPU / ABI
---------
Primary ABI    : {info['abi']}
ABI List       : {info['abi_list']}
""".strip()

    # ================= SAVED DEVICE =================

    def load_saved_devices(self):
        status_map = get_all_device_status()
        self.refresh_saved_list(status_map, reload_devices=True)

    def save_current_device(self):
        if not self.active_serial:
            QMessageBox.warning(self, "Error", "No device selected")
            return

        if ":" not in self.active_serial:
            QMessageBox.warning(self, "Error", "Invalid device serial")
            return

        ip, port = self.active_serial.split(":", 1)
        name, ok = QInputDialog.getText(self, "Save Device", "Device name:")

        if ok and name:
            add_device(name.strip(), ip, port)
            self.load_saved_devices()

    def update_saved_device_status(self, payload):
        status_map = payload.get("status_map", {}) if isinstance(payload, dict) else payload
        health_map = payload.get("health_map", {}) if isinstance(payload, dict) else {}
        if health_map:
            self.saved_device_health.update(health_map)
        self.refresh_saved_list(status_map)
        now = time.time()
        if now - self.last_background_scan_refresh_at >= SCANNED_DEVICE_REFRESH_SECONDS:
            self.scan_devices()
            self.last_background_scan_refresh_at = now

        if self.active_source == "saved" and self.active_serial:
            raw_status = status_map.get(self.active_serial, "offline")
            self.sync_active_device_state(
                self.raw_status_to_ui_status(raw_status),
                refresh_info=False,
            )

    def closeEvent(self, event):
        if hasattr(self, "status_worker"):
            self.status_worker.stop()
            self.status_worker.wait(2000)
        for worker in [self.inspection_worker, self.reboot_worker]:
            if worker and worker.isRunning():
                worker.wait(2000)
        event.accept()

    # ================= ACTIONS =================

    def reboot_device(self):
        if not self.active_serial:
            QMessageBox.warning(self, "Restart OS", "No device selected")
            return

        if self.reboot_worker and self.reboot_worker.isRunning():
            QMessageBox.information(
                self,
                "Restart OS",
                "Restart OS untuk device yang dipilih masih sedang diproses.",
            )
            return

        device_label = self.get_device_display_name(self.active_serial)
        reply = QMessageBox.question(
            self,
            "Confirm Restart OS",
            (
                "Device yang akan di-restart:\n\n"
                f"{device_label}\n\n"
                "Lanjutkan restart OS?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        target_serial = self.active_serial
        target_label = device_label

        self.timer.stop()
        self.reboot_btn.setEnabled(False)
        self.status_label.setText(
            f"[BOOT] {target_serial}\n\n"
            "Restart command is being sent...\n"
            "Mohon tunggu, status device akan diperbarui otomatis."
        )
        self.info_box.setText(
            "Restart OS sedang dikirim.\n\n"
            f"Target device : {target_label}\n"
            "UI tetap aktif. Monitor akan lanjut membaca perubahan status setelah reboot mulai berjalan."
        )

        self.reboot_worker = DeviceActionWorker(
            lambda: adb_reboot(target_serial) or "Reboot command sent."
        )
        self.reboot_worker.result_ready.connect(
            lambda message, serial=target_serial, label=target_label: self.on_reboot_finished(
                serial,
                label,
                message,
            )
        )
        self.reboot_worker.start()

    def on_reboot_finished(self, serial, device_label, message):
        self.append_monitored_inspection_event(
            "Restart OS command sent",
            f"ADB reboot command dikirim dari aplikasi untuk {device_label}. Monitor akan terus mencatat perubahan status sesudah ini.",
            status=self.last_active_status or "CONNECTED",
            serial=serial,
        )

        if serial == self.active_serial:
            self.sync_active_device_state("BOOTING", refresh_info=False)
            self.info_box.setText(
                "Restart OS command sent.\n\n"
                f"Target device : {device_label}\n"
                "ADB biasanya akan terputus sebentar saat reboot dimulai.\n"
                "Status akan diperbarui otomatis oleh watchdog."
            )

        self.reboot_btn.setEnabled(True)
        self.reboot_worker = None
        QTimer.singleShot(ACTIVE_WATCHDOG_INTERVAL_MS, self.watchdog)
        self.timer.start(ACTIVE_WATCHDOG_INTERVAL_MS)
        QMessageBox.information(
            self,
            "Restart OS",
            f"Target device:\n{device_label}\n\n{message or 'Reboot command sent.'}",
        )

    def turn_on_device(self):
        if not self.active_serial:
            QMessageBox.warning(self, "Turn On", "No device selected")
            return

        ip = self.ip_input.text().strip()
        port = self.normalize_port(self.port_input.text())

        if not ip and ":" in self.active_serial:
            ip, port = self.active_serial.split(":", 1)
            self.ip_input.setText(ip)
            self.port_input.setText(port)

        if ip:
            adb_connect(ip, port, timeout=2)

        message = adb_wake(self.active_serial)
        status = get_device_status(self.active_serial)

        self.scan_devices()
        self.load_saved_devices()
        self.sync_active_device_state(status, refresh_info=True)

        if status == "OS DOWN":
            message = (
                f"{message}\n\n"
                "Device still not reachable.\n"
                "Turn On hanya bisa bekerja jika STB masih standby/sleep dan ADB masih bisa dijangkau."
            )

        QMessageBox.information(self, "Turn On", message)

    def poweroff_device(self):
        if not self.active_serial:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Power Off",
            f"Are you sure to turn off this device?\n\n{self.active_serial}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            adb_poweroff(self.active_serial)
            self.append_monitored_inspection_event(
                "Power Off command sent",
                "ADB power off command dikirim dari aplikasi.",
                status=self.last_active_status or "CONNECTED",
            )

    def scrcpy_device(self):
        if not self.active_serial:
            return

        self.timer.stop()
        launch_scrcpy(self.active_serial)
        self.timer.start(ACTIVE_WATCHDOG_INTERVAL_MS)

    def screenshot_device(self):
        if not self.active_serial:
            return

        screenshots_dir = ensure_runtime_dir("screenshots")
        filename = screenshots_dir / f"{self.active_serial.replace(':', '_')}_{int(time.time())}.png"
        adb_screenshot(self.active_serial, str(filename))
        QMessageBox.information(
            self,
            "Screenshot",
            f"Saved:\n{filename.relative_to(Path.cwd()) if filename.is_relative_to(Path.cwd()) else filename}",
        )

    def open_settings(self):
        if not self.active_serial:
            return

        adb_shell(
            self.active_serial,
            "am start -n com.android.settings/.Settings",
        )

    def run_manual_command(self):
        if not self.active_serial:
            QMessageBox.warning(self, "ADB", "No active device selected.")
            return

        command = self.cmd_input.text().strip()
        if not command:
            return

        output = adb_shell(self.active_serial, command)
        self.info_box.setText(output)

    def format_inspection_html(self, report, full_log_text="", monitor_active=False, session=None):
        level_theme = {
            "HIGH": {"bg": "#fee2e2", "fg": "#991b1b"},
            "MEDIUM": {"bg": "#ffedd5", "fg": "#9a3412"},
            "LOW": {"bg": "#ecfccb", "fg": "#3f6212"},
        }
        hardware = report.get("hardware", {})
        electrical = hardware.get("electrical", {})
        memory = hardware.get("memory", {})
        storage_entries = hardware.get("storage", [])
        session_stats = (session or {}).get("stats", {})

        voltage_range = self.format_session_range(session_stats, "voltage")
        temperature_range = self.format_session_range(session_stats, "temperature")
        voltage_range_text = (
            f"{voltage_range} V"
            if voltage_range != "-"
            else "-"
        )
        temperature_range_text = (
            f"{temperature_range} C"
            if temperature_range != "-"
            else "-"
        )

        parts = [
            "<div style='font-family: Segoe UI, Arial, sans-serif;'>",
            "<h3 style='margin-bottom: 6px;'>Inspection Summary</h3>",
            (
                "<div style='margin: 0 0 10px 0; padding: 8px 10px; border-radius: 8px; "
                f"background: {'#dbeafe' if monitor_active else '#f3f4f6'}; color: #1e3a8a;'>"
                f"<b>Monitor:</b> {'ACTIVE' if monitor_active else 'Snapshot only'}"
                "</div>"
            ),
            (
                "<p style='margin-top: 0;'>"
                f"<b>Serial:</b> {html.escape(report['serial'])}<br>"
                f"<b>Status:</b> {html.escape(report['status'])}<br>"
                f"<b>ADB Transport:</b> {html.escape(report['adb_state'] or '-')}<br>"
                f"<b>Host Ping:</b> {html.escape(report['summary'].get('host_ping', '-'))}<br>"
                f"<b>Device IP(s):</b> {html.escape(report['summary'].get('device_ips', '-'))}<br>"
                f"<b>Battery:</b> {html.escape(report['summary'].get('battery', '-'))}<br>"
                f"<b>Power State:</b> {html.escape(report['summary'].get('power', '-'))}<br>"
                f"<b>Electrical:</b> {html.escape(report['summary'].get('electrical', '-'))}<br>"
                f"<b>Memory:</b> {html.escape(report['summary'].get('memory', '-'))}<br>"
                f"<b>Storage:</b> {html.escape(report['summary'].get('storage', '-'))}<br>"
                f"<b>Uptime:</b> {html.escape(report['summary'].get('uptime', '-'))}<br>"
                f"<b>Boot Reason:</b> {html.escape(report['summary'].get('boot_reason', '-'))}"
                "</p>"
            ),
        ]

        parts.append(
            "<div style='margin: 8px 0; padding: 10px 12px; border-radius: 8px; background: #f8fafc; color: #0f172a;'>"
            "<b>Electrical Detail</b><br>"
            f"Voltage sekarang: {html.escape(electrical.get('voltage_text', '-'))}<br>"
            f"Voltage monitor: {html.escape(voltage_range_text)}<br>"
            f"Temperature sekarang: {html.escape(electrical.get('temperature_text', '-'))}<br>"
            f"Temperature monitor: {html.escape(temperature_range_text)}<br>"
            f"Sumber daya: {html.escape(electrical.get('source_text', '-'))}"
            "</div>"
        )

        parts.append(
            "<div style='margin: 8px 0; padding: 10px 12px; border-radius: 8px; background: #f8fafc; color: #0f172a;'>"
            "<b>Hardware Health</b><br>"
            f"Memory: {html.escape(memory.get('summary', '-'))}<br>"
            f"Storage: {html.escape(hardware.get('storage_summary', '-'))}<br>"
            f"Uptime: {html.escape(hardware.get('uptime_text', '-'))}<br>"
            f"Boot reason: {html.escape(hardware.get('boot_reason', '-'))}"
            "</div>"
        )

        if storage_entries:
            storage_lines = []
            for entry in storage_entries:
                use_percent = entry.get("use_percent")
                use_text = f"{use_percent}%" if use_percent is not None else "-"
                storage_lines.append(
                    f"{entry['mount']}: {use_text} used, free {entry['available_kb'] / 1024:.1f} MB"
                )
            parts.append(
                "<div style='margin: 8px 0; padding: 10px 12px; border-radius: 8px; background: #f8fafc; color: #0f172a;'>"
                "<b>Storage Detail</b><br>"
                + "<br>".join(html.escape(line) for line in storage_lines)
                + "</div>"
            )

        if report["alerts"]:
            for alert in report["alerts"]:
                theme = level_theme.get(alert["level"], level_theme["LOW"])
                parts.append(
                    "<div style='margin: 8px 0; padding: 10px 12px; border-radius: 8px; "
                    f"background: {theme['bg']}; color: {theme['fg']};'>"
                    f"<b>[{html.escape(alert['level'])}] {html.escape(alert['category'])}</b><br>"
                    f"<b>{html.escape(alert['title'])}</b><br>"
                    f"{html.escape(alert['detail'])}"
                    "</div>"
                )
        else:
            parts.append(
                "<div style='margin: 8px 0; padding: 10px 12px; border-radius: 8px; "
                "background: #dcfce7; color: #166534;'>"
                "<b>[OK]</b> Tidak ada indikasi kritis dari inspection ini."
                "</div>"
            )

        parts.append("<h4 style='margin-bottom: 6px;'>Full Log</h4>")
        parts.append(
            "<pre style='white-space: pre-wrap; font-family: Consolas, "
            "\"Courier New\", monospace; font-size: 12px; line-height: 1.4;'>"
            f"{html.escape(full_log_text or report.get('log_text', ''))}</pre>"
        )
        parts.append("</div>")
        return "".join(parts)

    def run_inspection(self):
        if not self.active_serial:
            QMessageBox.warning(self, "Inspection", "No active device selected.")
            return

        if self.inspection_worker and self.inspection_worker.isRunning():
            QMessageBox.information(
                self,
                "Inspection",
                "Inspection untuk device yang dipilih masih sedang berjalan.",
            )
            return

        target_serial = self.active_serial
        target_label = self.get_device_display_name(target_serial)

        self.inspection_meta.setText("Inspection sedang berjalan...")
        self.inspection_box.setHtml(
            (
                "<p style='font-family: Segoe UI, Arial, sans-serif;'>"
                f"Inspection berjalan di background untuk <b>{html.escape(target_label)}</b>.<br>"
                "Mengambil log device, power, network, dan hardware health..."
                "</p>"
            )
        )
        self.inspect_btn.setEnabled(False)

        self.inspection_worker = DeviceActionWorker(
            lambda: collect_device_inspection(target_serial)
        )
        self.inspection_worker.result_ready.connect(
            lambda result, serial=target_serial: self.on_inspection_finished(
                serial,
                result,
            )
        )
        self.inspection_worker.start()

    def on_inspection_finished(self, serial, result):
        self.inspection_worker = None
        self.inspect_btn.setEnabled(bool(self.active_serial))

        if not isinstance(result, dict):
            if serial == self.active_serial:
                self.inspection_meta.setText("Inspection gagal dibuat.")
                self.inspection_box.setPlainText(
                    f"Failed to inspect device\n\n{result}"
                )
            return

        self.remember_inspection_session(result)
        self.refresh_saved_list(get_all_device_status())

        if serial == self.active_serial:
            self.render_inspection_session(serial)

    def update_ui_by_status(self, status):
        serial_text = self.active_serial or "-"
        theme = self.get_status_theme(status)
        prefix = theme["text"]
        self.status_label.setStyleSheet(
            "padding: 12px; border-radius: 10px; font-weight: 600;"
            f"color: {theme['fg']}; background-color: {theme['bg']};"
            "border: 1px solid rgba(15, 23, 42, 0.08);"
        )

        if status == "UNAUTHORIZED":
            self.status_label.setText(
                f"{prefix} {serial_text}\n\n"
                "1. Check TV / STB screen\n"
                "2. Allow USB debugging\n"
                "3. Press OK on remote\n\n"
                "Waiting for confirmation..."
            )
            self.set_controls_enabled(False)

        elif status == "CONNECTED":
            self.status_label.setText(
                f"{prefix} {serial_text}\n\nDevice is ready."
            )
            self.set_controls_enabled(True)

        elif status == "OFFLINE":
            self.status_label.setText(
                f"{prefix} {serial_text}\n\nTrying to reconnect..."
            )
            self.set_controls_enabled(False)

        elif status == "BOOTING":
            self.status_label.setText(
                f"{prefix} {serial_text}\n\n"
                "ADB is reachable but boot is not complete yet.\n"
                "If this stays too long, the device may be stuck on the Google logo."
            )
            self.set_controls_enabled(False)
            self.reboot_btn.setEnabled(True)
            self.scrcpy_btn.setEnabled(True)
            self.shot_btn.setEnabled(True)
            self.cmd_exec_btn.setEnabled(True)

        else:
            self.status_label.setText(
                f"{prefix} {serial_text}\n\nOS down / not reachable."
            )
            self.set_controls_enabled(False)

    # ================= WATCHDOG =================

    def watchdog(self):
        active_status = None

        if self.active_serial:
            active_status = get_device_status(self.active_serial)
            status_changed = self.sync_active_device_state(
                active_status,
                refresh_info=False,
            )

            if status_changed:
                self.capture_monitored_inspection_checkpoint(
                    "Status change detected by watchdog",
                    status=active_status,
                    serial=self.active_serial,
                )

            if active_status == "UNAUTHORIZED":
                adb_send_notification(
                    self.active_serial,
                    "ADB Authorization",
                    "Allow USB debugging using your remote",
                )

            elif active_status in ("OFFLINE", "OS DOWN"):
                ip = self.ip_input.text().strip()
                port = self.normalize_port(self.port_input.text())
                if ip:
                    auto_reconnect(self.active_serial, ip, port)

        for serial, session in list(self.inspection_sessions.items()):
            if serial == self.active_serial:
                continue

            status = get_device_status(serial)
            if status != session.get("last_logged_status"):
                self.capture_monitored_inspection_checkpoint(
                    "Status change detected by background monitor",
                    status=status,
                    serial=serial,
                )

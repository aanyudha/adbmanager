import os
import time
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
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
    adb_poweroff,
    adb_reboot,
    adb_screenshot,
    adb_send_notification,
    adb_shell,
    adb_vendor_settings_combo,
    adb_wake,
    auto_reconnect,
    get_all_device_status,
    get_device_status,
    launch_scrcpy,
)
from utils.device_store import add_device, load_devices
from utils.paths import ensure_runtime_dir


RAW_STATUS_ICONS = {
    "device": "[OK]",
    "unauthorized": "[AUTH]",
    "offline": "[OFF]",
}

RAW_TO_UI_STATUS = {
    "device": "CONNECTED",
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
    "OS DOWN": {
        "text": "[DOWN]",
        "fg": "#991b1b",
        "bg": "#fee2e2",
    },
}


class StatusWorker(QThread):
    status_updated = Signal(dict)

    def __init__(self, get_devices_callback, interval=3):
        super().__init__()
        self.get_devices = get_devices_callback
        self.interval = interval
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
                self.status_updated.emit(status_map)

            except Exception as e:
                print("StatusWorker error:", e)

            for _ in range(int(self.interval * 10)):
                if not self.running:
                    return
                self.msleep(100)

    def stop(self):
        self.running = False


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
        self.timer.start(3000)

        self.status_worker = StatusWorker(
            get_devices_callback=lambda: self.saved_devices,
            interval=3,
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
        right.addWidget(self.info_box)

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
        self.settings_btn.clicked.connect(self.open_settings)
        self.cmd_exec_btn.clicked.connect(self.run_manual_command)
        self.search_input.textChanged.connect(self.apply_device_filters)
        self.reset_search_btn.clicked.connect(self.reset_search)

        self.device_list.setStyleSheet(
            "QListWidget::item { padding: 6px 8px; border-radius: 6px; }"
            "QListWidget::item:selected { background: #dbeafe; color: #0f172a; }"
        )
        self.saved_list.setStyleSheet(
            "QListWidget::item { padding: 6px 8px; border-radius: 6px; }"
            "QListWidget::item:selected { background: #dbeafe; color: #0f172a; }"
        )
        self.status_label.setStyleSheet(
            "padding: 12px; border-radius: 10px; font-weight: 600;"
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

    def make_saved_item(self, device_data, raw_status):
        serial = self.make_serial(device_data["ip"], device_data.get("port"))
        item = QListWidgetItem(
            f"{self.raw_status_to_icon(raw_status)} {device_data['name']} ({serial})"
        )
        item.setData(Qt.UserRole, serial)
        item.setData(Qt.UserRole + 1, raw_status)
        item.setData(Qt.UserRole + 2, device_data["name"])
        self.apply_status_style_to_item(
            item,
            self.raw_status_to_ui_status(raw_status),
        )
        return item

    def sync_active_device_state(self, status, refresh_info=False):
        if not self.active_serial:
            return

        status_changed = status != self.last_active_status
        self.last_active_status = status
        self.update_ui_by_status(status)

        if refresh_info or status_changed:
            self.show_device_info_by_serial(self.active_serial, status=status)

    def refresh_saved_list(self, status_map):
        query = self.search_input.text().strip()
        selected_serial = self.get_item_serial(self.saved_list.currentItem())
        if not selected_serial and self.active_source == "saved":
            selected_serial = self.active_serial

        self.saved_devices = load_devices()

        self.saved_list.blockSignals(True)
        self.saved_list.clear()

        selected_item = None
        for device in self.saved_devices:
            serial = self.make_serial(device["ip"], device.get("port"))
            if not self.matches_device_filter(query, device["name"], serial):
                continue

            raw_status = status_map.get(serial, "offline")
            item = self.make_saved_item(device, raw_status)
            self.saved_list.addItem(item)

            if serial == selected_serial:
                selected_item = item

        if selected_item is not None:
            self.saved_list.setCurrentItem(selected_item)

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

        self.active_serial = serial
        self.active_source = "scanned"
        self.update_turn_on_button()

        if ":" in serial:
            ip, port = serial.split(":", 1)
            self.ip_input.setText(ip)
            self.port_input.setText(port)

        self.sync_active_device_state(
            self.raw_status_to_ui_status(raw_status),
            refresh_info=True,
        )

    def on_saved_clicked(self, item):
        serial = self.get_item_serial(item)
        if not serial or ":" not in serial:
            return

        self.active_serial = serial
        self.active_source = "saved"
        self.update_turn_on_button()

        ip, port = serial.split(":", 1)
        self.ip_input.setText(ip)
        self.port_input.setText(port)

        adb_connect(ip, port, timeout=2)
        self.sync_active_device_state(get_device_status(serial), refresh_info=True)

    def show_device_info_by_serial(self, serial, status=None):
        try:
            status = status or get_device_status(serial)

            if status != "CONNECTED":
                self.info_box.setText(f"Device not ready\n\nStatus: {status}")
                return

            device = AndroidDevice(serial, "device")
            self.info_box.setText(self.format_device_info(device.info()))

        except Exception as e:
            self.info_box.setText(f"Failed to read device info\n\n{e}")

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
        self.saved_devices = load_devices()
        status_map = get_all_device_status()
        self.refresh_saved_list(status_map)

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

    def update_saved_device_status(self, status_map):
        self.refresh_saved_list(status_map)
        self.scan_devices()

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
        event.accept()

    # ================= ACTIONS =================

    def reboot_device(self):
        if self.active_serial:
            adb_reboot(self.active_serial)

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

    def scrcpy_device(self):
        if not self.active_serial:
            return

        self.timer.stop()
        launch_scrcpy(self.active_serial)
        self.timer.start(5000)

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

        else:
            self.status_label.setText(
                f"{prefix} {serial_text}\n\nOS down / not reachable."
            )
            self.set_controls_enabled(False)

    # ================= WATCHDOG =================

    def watchdog(self):
        if not self.active_serial:
            return

        status = get_device_status(self.active_serial)
        self.sync_active_device_state(status, refresh_info=False)

        if status == "UNAUTHORIZED":
            adb_send_notification(
                self.active_serial,
                "ADB Authorization",
                "Allow USB debugging using your remote",
            )

        elif status in ("OFFLINE", "OS DOWN"):
            ip = self.ip_input.text().strip()
            port = self.normalize_port(self.port_input.text())
            if ip:
                auto_reconnect(self.active_serial, ip, port)

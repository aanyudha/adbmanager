import os
import time

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QTextEdit, QLabel,
    QLineEdit, QMessageBox, QInputDialog
)
from PySide6.QtCore import QTimer

from adb.device import list_devices
from adb.manager import (
    adb_connect, adb_reboot, adb_poweroff,
    adb_screenshot, launch_scrcpy,
    auto_reconnect, get_device_status,
    adb_shell, adb_vendor_settings_combo, adb_send_notification
)
from utils.device_store import load_devices, add_device


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Heisenberg ADB Control Tool")
        self.resize(1100, 600)
        self.saved_timer = QTimer()
        self.saved_timer.timeout.connect(self.refresh_saved_devices_status)
        self.saved_timer.start(5000)  # refresh tiap 5 detik
        self.active_serial = None
        self.devices = []

        # ===== INPUT CONNECT =====
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP Address")

        self.port_input = QLineEdit("5555")

        self.connect_btn = QPushButton("ADB Connect")

        # ===== ACTION BUTTONS =====
        self.reboot_btn = QPushButton("Restart OS")
        self.power_btn = QPushButton("Power Off")
        self.scrcpy_btn = QPushButton("Scrcpy")
        self.shot_btn = QPushButton("Screenshot")
        self.settings_btn = QPushButton("System Settings")
        self.vendor_btn = QPushButton("Vendor Settings")
        self.vendor_btn.clicked.connect(self.open_vendor_settings)

        # ===== MANUAL COMMAND =====
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("adb shell command")
        self.cmd_exec_btn = QPushButton("Run Command")

        # ===== DEVICE LIST =====
        self.scan_btn = QPushButton("Scan Devices")
        self.device_list = QListWidget()
        self.saved_list = QListWidget()
        self.save_btn = QPushButton("Save Device")

        self.info_box = QTextEdit()
        self.info_box.setReadOnly(True)

        self.status_label = QLabel("🔴 No device selected")
        self.status_label.setWordWrap(True)

        # ===== LAYOUT =====
        top = QHBoxLayout()
        top.addWidget(QLabel("IP"))
        top.addWidget(self.ip_input)
        top.addWidget(QLabel("Port"))
        top.addWidget(self.port_input)
        top.addWidget(self.connect_btn)

        action = QHBoxLayout()
        action.addWidget(self.reboot_btn)
        action.addWidget(self.power_btn)
        action.addWidget(self.scrcpy_btn)
        action.addWidget(self.shot_btn)
        action.addWidget(self.settings_btn)
        action.addWidget(self.vendor_btn)

        cmd_bar = QHBoxLayout()
        cmd_bar.addWidget(self.cmd_input)
        cmd_bar.addWidget(self.cmd_exec_btn)

        left = QVBoxLayout()
        left.addLayout(top)
        left.addWidget(self.scan_btn)
        left.addWidget(QLabel("Scanned Devices"))
        left.addWidget(self.device_list)
        left.addLayout(action)
        left.addWidget(QLabel("Saved Devices"))
        left.addWidget(self.saved_list)
        left.addWidget(self.save_btn)
        left.addWidget(self.status_label)

        right = QVBoxLayout()
        right.addWidget(QLabel("Manual ADB Command"))
        right.addLayout(cmd_bar)
        right.addWidget(QLabel("Device Info / Output"))
        right.addWidget(self.info_box)

        main = QHBoxLayout()
        main.addLayout(left, 1)
        main.addLayout(right, 2)

        w = QWidget()
        w.setLayout(main)
        self.setCentralWidget(w)

        # ===== SIGNALS =====
        self.connect_btn.clicked.connect(self.connect_device)
        self.scan_btn.clicked.connect(self.scan_devices)

        #self.device_list.itemClicked.connect(self.select_scanned_device)
        #self.saved_list.itemClicked.connect(self.select_saved_device)
        self.device_list.itemClicked.connect(self.on_scanned_clicked)
        self.saved_list.itemClicked.connect(self.on_saved_clicked)

        self.save_btn.clicked.connect(self.save_current_device)

        self.reboot_btn.clicked.connect(self.reboot_device)
        self.power_btn.clicked.connect(self.poweroff_device)
        self.scrcpy_btn.clicked.connect(self.scrcpy_device)
        self.shot_btn.clicked.connect(self.screenshot_device)
        self.settings_btn.clicked.connect(self.open_settings)

        self.cmd_exec_btn.clicked.connect(self.run_manual_command)

        # ===== WATCHDOG =====
        self.timer = QTimer()
        self.timer.timeout.connect(self.watchdog)
        self.timer.start(5000)

        self.load_saved_devices()
        self.set_controls_enabled(False)

    # ================= CORE =================

    def connect_device(self):
        ip = self.ip_input.text().strip()
        port = self.port_input.text().strip()
        if not ip:
            return
        msg = adb_connect(ip, port)
        QMessageBox.information(self, "ADB", msg)

    def scan_devices(self):
        self.device_list.clear()
        self.devices = list_devices()

        for d in self.devices:
            state = d.status
            icon = "🟢" if state == "device" else "🟡" if state == "unauthorized" else "🔴"
            self.device_list.addItem(f"{icon} {d.serial} ({state})")

    def select_scanned_device(self, item):
        self.active_serial = item.text().split()[1]
        self.status_label.setText(f"🟢 Active (Scanned): {self.active_serial}")
        self.info_box.setText(f"Selected scanned device:\n{self.active_serial}")
        self.set_controls_enabled(True)

    def select_saved_device(self, item):
        addr = item.text().split("(")[1].split(")")[0]
        self.active_serial = addr
        ip, port = addr.split(":")
        adb_connect(ip, port)
        self.status_label.setText(f"🟢 Active (Saved): {addr}")
        self.set_controls_enabled(True)

    def set_controls_enabled(self, enabled):
        for b in [
            self.reboot_btn, self.power_btn,
            self.scrcpy_btn, self.shot_btn,
            self.settings_btn, self.cmd_exec_btn,
            self.vendor_btn, self.vendor_btn,
        ]:
            b.setEnabled(enabled)

    def open_vendor_settings(self):
        if not self.current_serial:
            QMessageBox.warning(self, "Error", "No active device selected")
            return
        adb_vendor_settings_combo(self.active_serial)
    
    def show_device_info(self, item):
        serial = item.text()
        device = next(d for d in self.devices if d.serial == serial)

        info = device.info()

        self.info_box.setText(
            "\n".join(f"{k.upper():15}: {v}" for k, v in info.items())
        )
    
    def on_scanned_clicked(self, item):
        text = item.text()
        serial = text.split()[1]  # 🟢 SERIAL (status)

        self.active_serial = serial
        self.status_label.setText(f"🟢 Active (Scanned): {serial}")
        self.set_controls_enabled(True)

        self.show_device_info_by_serial(serial)

    def on_saved_clicked(self, item):
        text = item.text()
        addr = text.split("(")[1].split(")")[0]  # ip:port

        self.active_serial = addr
        ip, port = addr.split(":")

        adb_connect(ip, port)
        self.status_label.setText(f"🟢 Active (Saved): {addr}")
        self.set_controls_enabled(True)

        self.show_device_info_by_serial(addr)
    
    def show_device_info_by_serial(self, serial):
        try:
            from adb.device import AndroidDevice
            from adb.manager import get_device_status

            status = get_device_status(serial)

            if status != "CONNECTED" and status != "device":
                self.info_box.setText(
                    f"⚠ Device not ready\n\nStatus: {status}"
                )
                return

            device = AndroidDevice(serial, status)
            info = device.info()

            self.info_box.setText(self.format_device_info(info))

        except Exception as e:
            self.info_box.setText(f"❌ Failed to read device info\n\n{e}")

    def format_device_info(self, info: dict) -> str:
        return f"""
    📱 DEVICE INFORMATION
    ────────────────────────────
    Brand          : {info['brand']}
    Model          : {info['model']}
    Manufacturer   : {info['manufacturer']}
    Device         : {info['device']}
    Board          : {info['board']}
    Hardware       : {info['hardware']}

    🧠 ANDROID
    ────────────────────────────
    Android Version: {info['android_version']}
    SDK Level      : {info['sdk']}
    Security Patch : {info['security_patch']}

    🧬 FIRMWARE
    ────────────────────────────
    Firmware       : {info['firmware']}
    Build ID       : {info['build_id']}
    Build Type     : {info['build_type']}
    Build Tags     : {info['build_tags']}
    Fingerprint    :
    {info['fingerprint']}

    ⚙ CPU / ABI
    ────────────────────────────
    Primary ABI    : {info['abi']}
    ABI List       : {info['abi_list']}
    """.strip()
    # ================= SAVED DEVICE =================

    def load_saved_devices(self):
        self.saved_list.clear()
        for d in load_devices():
            state = get_device_status(f'{d["ip"]}:{d["port"]}')
            icon = "🟢" if state == "CONNECTED" else "🔴"
            self.saved_list.addItem(
                f'{icon} {d["name"]} ({d["ip"]}:{d["port"]})'
            )

    def save_current_device(self):
        ip = self.ip_input.text().strip()
        port = self.port_input.text().strip()
        if not ip:
            return

        name, ok = QInputDialog.getText(
            self, "Save Device", "Device name:"
        )
        if ok and name:
            add_device(name, ip, port)
            self.load_saved_devices()

    # ================= ACTIONS =================

    def reboot_device(self):
        adb_reboot(self.active_serial)

    def poweroff_device(self):
        adb_poweroff(self.active_serial)

    def scrcpy_device(self):
        launch_scrcpy(self.active_serial)

    def screenshot_device(self):
        os.makedirs("screenshots", exist_ok=True)
        fn = f"screenshots/{self.active_serial.replace(':','_')}_{int(time.time())}.png"
        adb_screenshot(self.active_serial, fn)
        QMessageBox.information(self, "Screenshot", f"Saved:\n{fn}")

    def open_settings(self):
        # Kombinasi remote: back right left right left back
        seq = [
            "input keyevent 4",
            "input keyevent 22",
            "input keyevent 21",
            "input keyevent 22",
            "input keyevent 21",
            "input keyevent 4",
        ]
        for cmd in seq:
            adb_shell(self.active_serial, cmd)
            time.sleep(0.3)

    def run_manual_command(self):
        cmd = self.cmd_input.text().strip()
        if not cmd:
            return
        out = adb_shell(self.active_serial, cmd)
        self.info_box.setText(out)
        
    def refresh_saved_devices_status(self):
        """
        Refresh status icon saved devices TANPA mengubah selection
        """
        if not hasattr(self, "saved_devices"):
            return

        current_row = self.saved_list.currentRow()
        self.saved_list.blockSignals(True)
        self.saved_list.clear()

        for d in self.saved_devices:
            serial = f'{d["ip"]}:{d["port"]}'
            status = get_device_status(serial)
            status_text = self.format_status_icon(status)

            item_text = f'{d["name"]} ({d["ip"]}:{d["port"]})    {status_text}'
            self.saved_list.addItem(item_text)

        # restore selection
        if current_row >= 0 and current_row < self.saved_list.count():
            self.saved_list.setCurrentRow(current_row)

        self.saved_list.blockSignals(False)

    def update_ui_by_status(self, status: str):
        if status == "UNAUTHORIZED":
            self.status_label.setText(
                "🟡 Authorization Required\n\n"
                "1. Check TV / STB screen\n"
                "2. Allow USB debugging\n"
                "3. Press OK on remote\n\n"
                "Waiting for confirmation..."
            )
            self.set_controls_enabled(False)

        elif status == "CONNECTED":
            self.status_label.setText(
                "🟢 Device Connected\n\nDevice is ready."
            )
            self.set_controls_enabled(True)

        elif status == "OFFLINE":
            self.status_label.setText(
                "🔴 Device Offline\n\nTrying to reconnect..."
            )
            self.set_controls_enabled(False)

        else:
            self.status_label.setText(
                "⚫ OS Down / Not Reachable"
            )
            self.set_controls_enabled(False)

    # ================= WATCHDOG =================

    def watchdog(self):
        if not self.active_serial:
            return

        status = get_device_status(self.active_serial)
        self.update_ui_by_status(status)

        if status == "UNAUTHORIZED":
            adb_send_notification(
                self.active_serial,
                "ADB Authorization",
                "Allow USB debugging using your remote"
            )

        elif status in ("OFFLINE", "OS DOWN"):
            ip = self.ip_input.text().strip()
            port = self.port_input.text().strip()
            auto_reconnect(self.active_serial, ip, port)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QTextEdit, QLabel,
    QLineEdit, QMessageBox
)
from PySide6.QtCore import QTimer
from adb.device import list_devices
from adb.manager import (
    adb_connect, adb_reboot, adb_poweroff,
    adb_screenshot, launch_scrcpy, auto_reconnect
)
import time
import os

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Heisenberg ADB Control Tool")
        self.resize(1000, 560)

        self.current_serial = None

        # ==== INPUT IP & PORT ====
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP Address")

        self.port_input = QLineEdit("5555")

        self.connect_btn = QPushButton("ADB Connect")

        # ==== CONTROL BUTTONS ====
        self.reboot_btn = QPushButton("Reboot")
        self.power_btn = QPushButton("Power Off")
        self.scrcpy_btn = QPushButton("Open Scrcpy")
        self.shot_btn = QPushButton("Screenshot")

        # ==== DEVICE LIST & INFO ====
        self.scan_btn = QPushButton("Scan Devices")
        self.device_list = QListWidget()
        self.info_box = QTextEdit()
        self.info_box.setReadOnly(True)

        # ==== TOP BAR ====
        top = QHBoxLayout()
        top.addWidget(QLabel("IP"))
        top.addWidget(self.ip_input)
        top.addWidget(QLabel("Port"))
        top.addWidget(self.port_input)
        top.addWidget(self.connect_btn)

        self.status_label = QLabel("🔴 Not connected")
        self.status_label.setWordWrap(True)

        # ==== CONTROL BAR ====
        ctrl = QHBoxLayout()
        ctrl.addWidget(self.reboot_btn)
        ctrl.addWidget(self.power_btn)
        ctrl.addWidget(self.scrcpy_btn)
        ctrl.addWidget(self.shot_btn)

        # ==== LEFT ====
        left = QVBoxLayout()
        left.addLayout(top)
        left.addWidget(self.scan_btn)
        left.addWidget(QLabel("Devices"))
        left.addWidget(self.device_list)
        left.addLayout(ctrl)

        left.addWidget(self.status_label)

        # ==== RIGHT ====
        right = QVBoxLayout()
        right.addWidget(QLabel("Device Info"))
        right.addWidget(self.info_box)

        main = QHBoxLayout()
        main.addLayout(left, 1)
        main.addLayout(right, 2)

        w = QWidget()
        w.setLayout(main)
        self.setCentralWidget(w)

        # ==== EVENTS ====
        self.connect_btn.clicked.connect(self.connect_device)
        self.scan_btn.clicked.connect(self.scan_devices)
        self.device_list.itemClicked.connect(self.select_device)
        self.reboot_btn.clicked.connect(self.reboot_device)
        self.power_btn.clicked.connect(self.poweroff_device)
        self.scrcpy_btn.clicked.connect(self.open_scrcpy)
        self.shot_btn.clicked.connect(self.take_screenshot)

        # ==== WATCHDOG TIMER ====
        self.timer = QTimer()
        self.timer.timeout.connect(self.watchdog)
        self.timer.start(5000)  # cek tiap 5 detik

        self.devices = []

    # ================= CORE =================

    def connect_device(self):
        ip = self.ip_input.text().strip()
        port = self.port_input.text().strip()
        result = adb_connect(ip, port)
        QMessageBox.information(self, "ADB", result)

    def scan_devices(self):
        self.device_list.clear()
        self.devices = list_devices()

        for d in self.devices:
            label = f"{d.serial} ({d.status})"
            self.device_list.addItem(label)

    def select_device(self, item):
        text = item.text()
        serial = text.split(" ")[0]
        self.current_serial = serial

        device = next(d for d in self.devices if d.serial == serial)
        info = device.info()

        self.info_box.setText(
            "\n".join(f"{k.upper():15}: {v}" for k, v in info.items())
        )

        if device.status == "unauthorized":
            self.status_label.setText(
                "🟡 Device terdeteksi\n\n"
                "📺 Lihat layar STB\n"
                "👉 Pilih 'Allow USB debugging'\n"
                "☑ Centang 'Always allow'\n"
                "⏳ Menunggu konfirmasi..."
            )
            self.set_controls_enabled(False)
        elif device.status == "device":
            self.status_label.setText("🟢 Device connected")
            self.set_controls_enabled(True)
    def set_controls_enabled(self, enabled: bool):
        self.reboot_btn.setEnabled(enabled)
        self.power_btn.setEnabled(enabled)
        self.scrcpy_btn.setEnabled(enabled)
        self.shot_btn.setEnabled(enabled)
    # ================= ACTIONS =================

    def reboot_device(self):
        if self.current_serial:
            adb_reboot(self.current_serial)

    def poweroff_device(self):
        if self.current_serial:
            adb_poweroff(self.current_serial)

    def open_scrcpy(self):
        if self.current_serial:
            launch_scrcpy(self.current_serial)

    def take_screenshot(self):
        if not self.current_serial:
            return

        os.makedirs("screenshots", exist_ok=True)
        filename = f"screenshots/{self.current_serial.replace(':','_')}_{int(time.time())}.png"
        adb_screenshot(self.current_serial, filename)
        QMessageBox.information(self, "Screenshot", f"Saved: {filename}")

    # ================= WATCHDOG =================

    def watchdog(self):
        if not self.current_serial:
            return

        ip = self.ip_input.text().strip()
        port = self.port_input.text().strip()

        auto_reconnect(self.current_serial, ip, port)

        from adb.manager import get_device_state
        state = get_device_state(self.current_serial)

        if state == "unauthorized":
            self.status_label.setText(
                "🟡 Waiting authorization\n"
                "Please allow USB debugging on device"
            )
            self.set_controls_enabled(False)

        elif state == "connected":
            self.status_label.setText("🟢 Device authorized & ready")
            self.set_controls_enabled(True)

        else:
            self.status_label.setText("🔴 Device offline")
            self.set_controls_enabled(False)
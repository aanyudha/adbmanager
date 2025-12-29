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
            self.device_list.addItem(d.serial)

    def select_device(self, item):
        self.current_serial = item.text()
        device = next(d for d in self.devices if d.serial == self.current_serial)
        info = device.info()
        self.info_box.setText(
            "\n".join(f"{k.upper():15}: {v}" for k, v in info.items())
        )

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
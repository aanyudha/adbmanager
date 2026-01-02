from adb.manager import run_adb, get_device_status

class AndroidDevice:
    def __init__(self, serial, status):
        self.serial = serial
        self.status = status  # device / unauthorized / offline

    def get_prop(self, prop):
        return run_adb(
            f"adb -s {self.serial} shell getprop {prop}"
        ).strip()

    def info(self):
        """
        Device information (safe to call only when CONNECTED)
        """
        return {
            "serial": self.serial,
            "status": self.status,
            "brand": self.get_prop("ro.product.brand"),
            "model": self.get_prop("ro.product.model"),
            "manufacturer": self.get_prop("ro.product.manufacturer"),
            "device": self.get_prop("ro.product.device"),
            "board": self.get_prop("ro.product.board"),
            "hardware": self.get_prop("ro.hardware"),

            "android_version": self.get_prop("ro.build.version.release"),
            "sdk": self.get_prop("ro.build.version.sdk"),
            "security_patch": self.get_prop("ro.build.version.security_patch"),

            "firmware": self.get_prop("ro.build.display.id"),
            "build_id": self.get_prop("ro.build.id"),
            "build_type": self.get_prop("ro.build.type"),
            "build_tags": self.get_prop("ro.build.tags"),
            "fingerprint": self.get_prop("ro.build.fingerprint"),

            "abi": self.get_prop("ro.product.cpu.abi"),
            "abi_list": self.get_prop("ro.product.cpu.abilist"),
        }


def list_devices():
    """
    Ambil SEMUA device termasuk unauthorized
    """
    output = run_adb("adb devices")
    devices = []

    for line in output.splitlines():
        if "\t" in line and not line.startswith("List"):
            serial, status = line.split("\t")
            devices.append(AndroidDevice(serial, status))

    return devices

class Device:
    def __init__(self):
        self.state = "no_device"

    def refresh(self):
        self.state = get_device_status()
        return self.state
from adb.manager import run_adb

class AndroidDevice:
    def __init__(self, serial, status):
        self.serial = serial
        self.status = status  # device / unauthorized / offline

    def get_prop(self, prop):
        return run_adb(f"adb -s {self.serial} shell getprop {prop}").strip()

    def info(self):
        return {
            "serial": self.serial,
            "status": self.status,
            "model": self.get_prop("ro.product.model"),
            "manufacturer": self.get_prop("ro.product.manufacturer"),
            "android": self.get_prop("ro.build.version.release"),
            "sdk": self.get_prop("ro.build.version.sdk"),
            "abi": self.get_prop("ro.product.cpu.abi"),
            "hardware": self.get_prop("ro.hardware"),
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
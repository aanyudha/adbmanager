from adb.manager import run_adb

class AndroidDevice:
    def __init__(self, serial):
        self.serial = serial

    def get_prop(self, prop):
        cmd = f"adb -s {self.serial} shell getprop {prop}"
        return run_adb(cmd).strip()

    def info(self):
        return {
            "serial": self.serial,
            "model": self.get_prop("ro.product.model"),
            "manufacturer": self.get_prop("ro.product.manufacturer"),
            "android": self.get_prop("ro.build.version.release"),
            "sdk": self.get_prop("ro.build.version.sdk"),
            "abi": self.get_prop("ro.product.cpu.abi"),
            "hardware": self.get_prop("ro.hardware"),
        }


def list_devices():
    """
    Ambil daftar device dari adb devices
    """
    output = run_adb("adb devices")
    devices = []

    for line in output.splitlines():
        if "\tdevice" in line:
            serial = line.split("\t")[0]
            devices.append(AndroidDevice(serial))

    return devices
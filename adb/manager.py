import subprocess
import time
from utils.logger import log_error, log_info

def run_adb(command: str) -> str:
    try:
        result = subprocess.check_output(
            command,
            stderr=subprocess.STDOUT,
            shell=True,
            text=True
        )
        return result
    except subprocess.CalledProcessError as e:
        log_error(e.output)
        return e.output


def adb_connect(ip: str, port: str) -> str:
    cmd = f"adb connect {ip}:{port}"
    return run_adb(cmd)


def adb_reboot(serial: str) -> str:
    return run_adb(f"adb -s {serial} reboot")


def adb_poweroff(serial: str) -> str:
    return run_adb(f"adb -s {serial} shell reboot -p")


def adb_screenshot(serial: str, filename: str) -> str:
    cmd = f'adb -s {serial} exec-out screencap -p > "{filename}"'
    return run_adb(cmd)


def launch_scrcpy(serial: str):
    # non-blocking
    subprocess.Popen(
        f"scrcpy -s {serial}",
        shell=True
    )


def is_device_online(serial: str) -> bool:
    out = run_adb("adb devices")
    return f"{serial}\tdevice" in out


def auto_reconnect(serial: str, ip: str, port: str):
    if not is_device_online(serial):
        log_info("Device offline, reconnecting...")
        adb_connect(ip, port)
        time.sleep(2)

def get_adb_devices():
    result = subprocess.run(
        ["adb", "devices"],
        capture_output=True,
        text=True
    )
    lines = result.stdout.strip().splitlines()[1:]

    devices = []
    for line in lines:
        if not line.strip():
            continue
        serial, status = line.split()
        devices.append((serial, status))

    return devices
def get_device_state(serial: str) -> str:
    out = run_adb("adb devices")
    for line in out.splitlines():
        if serial in line:
            if "unauthorized" in line:
                return "unauthorized"
            elif "\tdevice" in line:
                return "connected"
            else:
                return "offline"
    return "offline"
def get_device_status(serial: str) -> str:
    out = run_adb("adb devices")

    for line in out.splitlines():
        if serial in line:
            if "unauthorized" in line:
                return "UNAUTHORIZED"
            elif "\tdevice" in line:
                return "CONNECTED"
            elif "offline" in line:
                return "OFFLINE"

    return "OS DOWN"
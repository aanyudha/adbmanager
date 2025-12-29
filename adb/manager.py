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
import subprocess
import time
from utils.logger import log_error


# =========================================================
# CORE EXEC
# =========================================================

def run_adb(args) -> str:
    try:
        if isinstance(args, str):
            args = args.split()

        result = subprocess.run(
            ["adb"] + args,
            capture_output=True,
            text=True
        )
        return result.stdout.strip()

    except Exception as e:
        log_error(str(e))
        return str(e)


# =========================================================
# BASIC COMMANDS
# =========================================================

def adb_connect(ip: str, port: str) -> str:
    return run_adb(["connect", f"{ip}:{port}"])


def adb_reboot(serial: str) -> str:
    return run_adb(["-s", serial, "reboot"])


def adb_poweroff(serial: str) -> str:
    return run_adb(["-s", serial, "shell", "reboot", "-p"])


def adb_screenshot(serial: str, filename: str) -> str:
    try:
        with open(filename, "wb") as f:
            subprocess.run(
                ["adb", "-s", serial, "exec-out", "screencap", "-p"],
                stdout=f
            )
        return "OK"
    except Exception as e:
        log_error(str(e))
        return str(e)


# =========================================================
# SCRCPY
# =========================================================

def launch_scrcpy(serial: str):
    subprocess.Popen([
        "scrcpy",
        "-s", serial,
        "--render-driver=direct3d",
        "--max-size", "640",
        "--video-bit-rate", "600K",
        "--max-fps", "15",
        "--no-audio"
    ])


# =========================================================
# DEVICE STATUS
# =========================================================

import subprocess

def connect_device(serial):
    try:
        result = subprocess.run(
            ["adb", "connect", serial],
            capture_output=True,
            text=True
        )
        return result.stdout.strip()
    except Exception as e:
        return str(e)
    
def get_all_device_status():
    # 🔥 Warm-up call (force adb refresh internal state)
    run_adb(["devices"])
    time.sleep(0.3)

    out = run_adb(["devices"])
    status_map = {}

    lines = out.splitlines()[1:]

    for line in lines:
        if not line.strip():
            continue

        parts = line.split()
        if len(parts) >= 2:
            serial = parts[0]
            status = parts[1]
            status_map[serial] = status

    return status_map
    


def get_device_status(serial: str) -> str:
    status_map = get_all_device_status()
    status = status_map.get(serial)

    if status == "device":
        return "CONNECTED"
    elif status == "unauthorized":
        return "UNAUTHORIZED"
    elif status == "offline":
        return "OFFLINE"
    else:
        return "OS DOWN"


def auto_reconnect(serial, ip, port):
    if get_device_status(serial) != "CONNECTED":
        adb_connect(ip, port)
        time.sleep(1)


def adb_shell(serial, command):
    return run_adb(["-s", serial, "shell"] + command.split())


def adb_send_key(serial: str, keycode: int, delay: float = 0.3):
    run_adb(["-s", serial, "shell", "input", "keyevent", str(keycode)])
    time.sleep(delay)


def adb_vendor_settings_combo(serial: str):
    # Back → Right → Left → Right → Left → Up → Down → Back
    sequence = [4, 22, 21, 22, 21, 19, 20, 4]

    for key in sequence:
        adb_send_key(serial, key)


def adb_send_notification(serial: str, title: str, text: str):
    run_adb([
        "-s", serial,
        "shell",
        "cmd", "notification", "post",
        "adbtool", title, text
    ])
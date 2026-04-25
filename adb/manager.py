import shlex
import subprocess
import time

from utils.logger import log_error
from utils.paths import get_app_root


# =========================================================
# CORE EXEC
# =========================================================

BASE_DIR = get_app_root()
ADB_EXE = BASE_DIR / "adb" / "adb.exe"
SCRCPY_EXE = BASE_DIR / "scrcpy" / "scrcpy.exe"


def _normalize_args(args):
    if isinstance(args, str):
        return shlex.split(args, posix=False)
    return list(args)


def run_adb(args, timeout=15) -> str:
    try:
        result = subprocess.run(
            [str(ADB_EXE)] + _normalize_args(args),
            capture_output=True,
            text=True,
            cwd=str(ADB_EXE.parent),
            timeout=timeout
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        output = "\n".join(part for part in (stdout, stderr) if part).strip()

        if not output and result.returncode != 0:
            output = f"adb exited with code {result.returncode}"

        return output

    except subprocess.TimeoutExpired:
        message = f"adb command timed out after {timeout}s"
        log_error(message)
        return message

    except Exception as e:
        log_error(str(e))
        return str(e)


# =========================================================
# BASIC COMMANDS
# =========================================================

def adb_connect(ip: str, port: str, timeout=3) -> str:
    return run_adb(["connect", f"{ip}:{port}"], timeout=timeout)


def adb_reboot(serial: str) -> str:
    return run_adb(["-s", serial, "reboot"])


def adb_poweroff(serial: str) -> str:
    return run_adb(["-s", serial, "shell", "reboot", "-p"])


def adb_wake(serial: str) -> str:
    outputs = []

    for args in [
        ["-s", serial, "shell", "input", "keyevent", "224"],
        ["-s", serial, "shell", "wm", "dismiss-keyguard"],
        ["-s", serial, "shell", "input", "keyevent", "82"],
    ]:
        result = run_adb(args, timeout=3)
        if result:
            outputs.append(result)

    return "\n".join(outputs).strip() or "Wake command sent."


def adb_screenshot(serial: str, filename: str) -> str:
    try:
        with open(filename, "wb") as f:
            subprocess.run(
                [str(ADB_EXE), "-s", serial, "exec-out", "screencap", "-p"],
                stdout=f,
                cwd=str(ADB_EXE.parent),
                timeout=30,
                check=False
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
        str(SCRCPY_EXE),
        "-s", serial,
        "--render-driver=direct3d",
        "--max-size", "640",
        "--video-bit-rate", "600K",
        "--max-fps", "15",
        "--no-audio"
    ], cwd=str(SCRCPY_EXE.parent))


# =========================================================
# DEVICE STATUS
# =========================================================

def connect_device(serial):
    return run_adb(["connect", serial], timeout=3)


def get_all_device_status(refresh_serials=None):
    refresh_serials = refresh_serials or []

    for serial in refresh_serials:
        run_adb(["connect", serial], timeout=2)

    # Warm-up call to let adb refresh its internal device list.
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
    if status == "unauthorized":
        return "UNAUTHORIZED"
    if status == "offline":
        return "OFFLINE"
    return "OS DOWN"


def auto_reconnect(serial, ip, port):
    if get_device_status(serial) != "CONNECTED":
        adb_connect(ip, port, timeout=2)
        time.sleep(1)


def adb_shell(serial, command):
    return run_adb(["-s", serial, "shell", command])


def adb_send_key(serial: str, keycode: int, delay: float = 0.3):
    run_adb(["-s", serial, "shell", "input", "keyevent", str(keycode)])
    time.sleep(delay)


def adb_vendor_settings_combo(serial: str):
    # Back -> Right -> Left -> Right -> Left -> Up -> Down -> Back
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

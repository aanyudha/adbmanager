import ctypes
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from utils.logger import log_error
from utils.paths import ensure_runtime_dir, get_bundle_root, get_runtime_path


# =========================================================
# CORE EXEC
# =========================================================

BASE_DIR = get_bundle_root()
ADB_EXE = BASE_DIR / "adb" / "adb.exe"
ADB_DEVICE_REFRESH_DELAY_SECONDS = 0.25
SCRCPY_DEBUG_MODE = True
SCRCPY_STARTUP_WAIT_SECONDS = 1.5
SCRCPY_PROCESS_OUTPUT_MARKER = "SCRCPY PROCESS OUTPUT"
SCRCPY_REQUIRED_FILES = [
    "scrcpy.exe",
    "scrcpy-server",
    "SDL2.dll",
    "avcodec-61.dll",
    "avformat-61.dll",
    "avutil-59.dll",
    "swresample-5.dll",
]


def _normalize_args(args):
    if isinstance(args, str):
        return shlex.split(args, posix=False)
    return list(args)


def _get_windows_subprocess_kwargs():
    kwargs = {}
    if os.name != "nt":
        return kwargs

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def _get_windows_gui_subprocess_kwargs():
    if os.name != "nt":
        return {}
    return {}


def _decode_process_output(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _run_process(
    command,
    *,
    cwd=None,
    timeout=15,
    text=True,
    stdout_target=None,
):
    popen_kwargs = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "shell": False,
        **_get_windows_subprocess_kwargs(),
    }

    if text:
        popen_kwargs["text"] = True
        popen_kwargs["encoding"] = "utf-8"
        popen_kwargs["errors"] = "replace"

    if stdout_target is None:
        popen_kwargs["stdout"] = subprocess.PIPE
    else:
        popen_kwargs["stdout"] = stdout_target

    with subprocess.Popen(command, **popen_kwargs) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise

        return (
            process.returncode,
            _decode_process_output(stdout),
            _decode_process_output(stderr),
        )


def _launch_background_process(command, *, cwd=None):
    process = subprocess.Popen(  # pylint: disable=consider-using-with
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        **_get_windows_subprocess_kwargs(),
    )
    return process


def _set_windows_dll_directory(path):
    if os.name != "nt":
        return

    ctypes.windll.kernel32.SetDllDirectoryW(path)


def _build_tool_signature(source_dir: Path) -> str:
    digest = hashlib.sha256()

    for source_path in sorted(source_dir.rglob("*")):
        relative_path = source_path.relative_to(source_dir).as_posix()
        digest.update(relative_path.encode("utf-8"))

        if source_path.is_file():
            stats = source_path.stat()
            digest.update(str(stats.st_size).encode("utf-8"))
            digest.update(str(stats.st_mtime_ns).encode("utf-8"))

    return digest.hexdigest()[:12]


def _ensure_runtime_tool_dir(relative_dir: str) -> Path:
    source_dir = BASE_DIR / relative_dir
    if not getattr(sys, "frozen", False):
        return source_dir

    signature = _build_tool_signature(source_dir)
    runtime_dir = get_runtime_path("runtime_tools", relative_dir, signature)

    if runtime_dir.exists():
        return runtime_dir

    # One-file PyInstaller extracts files under _MEIPASS. Long-lived child
    # processes such as scrcpy must run from a persistent runtime copy so the
    # bootloader can clean up the temporary _MEI directory after startup.
    # A versioned cache directory also avoids overwriting scrcpy.exe while an
    # older mirror session is still using it.
    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source_dir, runtime_dir)
    except FileExistsError:
        return runtime_dir

    return runtime_dir


def _sanitize_external_process_env(runtime_dir: Path):
    env = os.environ.copy()
    runtime_dir_str = str(runtime_dir)
    bundle_root_str = str(BASE_DIR)
    sanitized_path_entries = []

    for entry in env.get("PATH", "").split(os.pathsep):
        normalized_entry = entry.strip()
        if not normalized_entry:
            continue

        try:
            resolved_entry = str(Path(normalized_entry).resolve())
        except OSError:
            resolved_entry = normalized_entry

        if resolved_entry.startswith(bundle_root_str):
            continue

        sanitized_path_entries.append(normalized_entry)

    env["PATH"] = os.pathsep.join([runtime_dir_str] + sanitized_path_entries)
    return env


def _read_process_log(log_path: Path) -> str:
    try:
        return log_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _extract_scrcpy_process_output(log_text: str) -> str:
    marker = f"{SCRCPY_PROCESS_OUTPUT_MARKER}\n{'-' * len(SCRCPY_PROCESS_OUTPUT_MARKER)}"
    if marker not in log_text:
        return log_text.strip()
    _, output = log_text.split(marker, 1)
    return output.strip()


def _read_scrcpy_process_output(log_path: Path) -> str:
    return _extract_scrcpy_process_output(_read_process_log(log_path))


def _format_block(title: str, lines) -> str:
    clean_lines = [str(line) for line in lines if line is not None]
    return "\n".join([title, "-" * len(title), *clean_lines]).strip()


def _format_scrcpy_debug_text(details: dict) -> str:
    sections = [
        _format_block(
            "SCRCPY DEBUG",
            [
                f"Timestamp      : {details.get('timestamp', '-')}",
                f"Serial         : {details.get('serial', '-')}",
                f"ADB Path       : {details.get('adb_path', '-')}",
                f"ADB Exists     : {details.get('adb_exists', False)}",
                f"scrcpy Path    : {details.get('scrcpy_path', '-')}",
                f"scrcpy Exists  : {details.get('scrcpy_exists', False)}",
                f"Working Dir    : {details.get('cwd', '-')}",
                f"Log Path       : {details.get('log_path', '-')}",
                f"Debug Mode     : {details.get('debug_mode', False)}",
                f"Frozen         : {details.get('frozen', False)}",
            ],
        ),
        _format_block(
            "COMMAND",
            [
                details.get("command_display", "-"),
            ],
        ),
        _format_block(
            "PREFLIGHT",
            [
                f"Serial Format  : {details.get('serial_format', '-')}",
                f"ADB Start      : {details.get('adb_start_server_output', '-') or '-'}",
                f"ADB Status     : {details.get('adb_status', '-')}",
                f"ADB Get-State  : {details.get('adb_get_state', '-') or '-'}",
                f"Auth Check     : {details.get('authorization_hint', '-')}",
                f"USB Debugging  : {details.get('usb_debugging_hint', '-')}",
                f"DLL Check      : {details.get('dll_hint', '-')}",
                f"Server Binary  : {details.get('server_hint', '-')}",
            ],
        ),
        _format_block(
            "ADB DEVICES",
            [
                details.get("adb_devices_output", "-"),
            ],
        ),
    ]

    if details.get("missing_files"):
        sections.append(
            _format_block(
                "MISSING FILES",
                details["missing_files"],
            )
        )

    if "returncode" in details:
        sections.append(
            _format_block(
                "PROCESS",
                [
                    f"Return Code    : {details.get('returncode')}",
                ],
            )
        )

    process_output = details.get("process_output", "").strip()
    if process_output:
        sections.append(
            _format_block(
                "STDOUT / STDERR",
                [process_output],
            )
        )

    return "\n\n".join(section for section in sections if section).strip()


def _write_scrcpy_debug_preamble(log_file, details: dict):
    preamble = _format_scrcpy_debug_text(details)
    log_file.write(
        preamble
        + "\n\n"
        + f"{SCRCPY_PROCESS_OUTPUT_MARKER}\n"
        + f"{'-' * len(SCRCPY_PROCESS_OUTPUT_MARKER)}\n"
    )
    log_file.flush()


def _get_scrcpy_missing_files(scrcpy_dir: Path):
    missing_files = []
    for filename in SCRCPY_REQUIRED_FILES:
        if not (scrcpy_dir / filename).exists():
            missing_files.append(str(scrcpy_dir / filename))
    return missing_files


def _get_scrcpy_adb_status(serial: str):
    devices_output = run_adb(["devices", "-l"], timeout=8, log_timeout=False)
    status_map = _parse_adb_device_lines(devices_output)
    adb_status = status_map.get(serial, "")

    adb_get_state = ""
    if serial:
        adb_get_state = run_adb(
            ["-s", serial, "get-state"],
            timeout=4,
            log_timeout=False,
        )

    return devices_output, adb_status, adb_get_state


def _build_scrcpy_debug_details(
    serial: str,
    *,
    log_path: Path,
    scrcpy_dir: Path,
    scrcpy_exe: Path,
    command,
    adb_start_server_output: str,
):
    missing_files = _get_scrcpy_missing_files(scrcpy_dir)
    devices_output, adb_status, adb_get_state = _get_scrcpy_adb_status(serial)
    serial_format = "OK" if serial and " " not in serial else "INVALID"

    if adb_status == "device":
        authorization_hint = "ADB device is authorized."
        usb_debugging_hint = "ADB transport is active and USB/network debugging is enabled."
    elif adb_status == "unauthorized":
        authorization_hint = "Device is visible to adb but authorization has not been accepted."
        usb_debugging_hint = (
            "Check the device screen and accept the USB debugging prompt."
        )
    elif adb_status == "offline":
        authorization_hint = "ADB transport exists but is offline."
        usb_debugging_hint = (
            "ADB is unstable. Check network/USB connectivity and whether Android is still booting."
        )
    else:
        authorization_hint = "Device serial was not found in `adb devices -l`."
        usb_debugging_hint = (
            "Check the serial format, USB debugging state, and whether adb connect/start-server has succeeded."
        )

    dll_hint = (
        "All required scrcpy-side files are present."
        if not missing_files
        else "Missing required files next to scrcpy.exe."
    )
    server_hint = (
        "scrcpy-server is present."
        if (scrcpy_dir / "scrcpy-server").exists()
        else "scrcpy-server is missing."
    )

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "serial": serial,
        "adb_path": str(ADB_EXE),
        "adb_exists": ADB_EXE.exists(),
        "scrcpy_path": str(scrcpy_exe),
        "scrcpy_exists": scrcpy_exe.exists(),
        "cwd": str(scrcpy_dir),
        "log_path": str(log_path),
        "debug_mode": SCRCPY_DEBUG_MODE,
        "frozen": getattr(sys, "frozen", False),
        "command_display": subprocess.list2cmdline(command),
        "serial_format": serial_format,
        "adb_start_server_output": adb_start_server_output,
        "adb_devices_output": devices_output,
        "adb_status": adb_status or "NOT FOUND",
        "adb_get_state": adb_get_state,
        "authorization_hint": authorization_hint,
        "usb_debugging_hint": usb_debugging_hint,
        "dll_hint": dll_hint,
        "server_hint": server_hint,
        "missing_files": missing_files,
    }


def _build_scrcpy_failure_message(details: dict) -> str:
    technical_reason = details.get("process_output", "").strip()
    if not technical_reason:
        technical_reason = details.get("authorization_hint", "")
    if not technical_reason:
        technical_reason = "scrcpy failed before it could report a reason."

    return (
        f"{technical_reason}\n\n"
        f"Serial: {details.get('serial', '-')}\n"
        f"ADB: {details.get('adb_path', '-')}\n"
        f"scrcpy: {details.get('scrcpy_path', '-')}\n"
        f"Working directory: {details.get('cwd', '-')}\n"
        f"Return code: {details.get('returncode', '-')}\n"
        f"Log: {details.get('log_path', '-')}"
    )


def run_adb(args, timeout=15, log_timeout=True, stdout_target=None, text=True) -> str:
    # Windowed PyInstaller builds do not own a console. On Windows, CREATE_NO_WINDOW
    # prevents each adb.exe invocation from flashing a transient cmd window.
    try:
        returncode, stdout, stderr = _run_process(
            [str(ADB_EXE)] + _normalize_args(args),
            cwd=str(ADB_EXE.parent),
            timeout=timeout,
            stdout_target=stdout_target,
            text=text,
        )
        output = "\n".join(part for part in (stdout, stderr) if part).strip()

        if not output and returncode != 0:
            output = f"adb exited with code {returncode}"

        return output

    except subprocess.TimeoutExpired:
        message = f"adb command timed out after {timeout}s"
        if log_timeout:
            log_error(message)
        return message

    except Exception as e:
        log_error(str(e))
        return str(e)


def run_host_command(args, timeout=8) -> str:
    try:
        returncode, stdout, stderr = _run_process(
            list(args),
            timeout=timeout,
        )
        output = "\n".join(part for part in (stdout, stderr) if part).strip()

        if not output and returncode != 0:
            output = f"command exited with code {returncode}"

        return output

    except subprocess.TimeoutExpired:
        return f"command timed out after {timeout}s"
    except Exception as e:
        return str(e)


# =========================================================
# BASIC COMMANDS
# =========================================================

def adb_connect(ip: str, port: str, timeout=3, log_timeout=True) -> str:
    return run_adb(
        ["connect", f"{ip}:{port}"],
        timeout=timeout,
        log_timeout=log_timeout,
    )


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
            output = run_adb(
                ["-s", serial, "exec-out", "screencap", "-p"],
                timeout=30,
                stdout_target=f,
                text=False,
            )
        if output:
            return output
        return "OK"
    except Exception as e:
        log_error(str(e))
        return str(e)


# =========================================================
# SCRCPY
# =========================================================

def launch_scrcpy(serial: str) -> dict:
    log_dir = ensure_runtime_dir("logs")
    safe_serial = re.sub(r"[^A-Za-z0-9_.-]+", "_", serial or "unknown")
    log_path = log_dir / (
        f"scrcpy_{safe_serial}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )

    try:
        scrcpy_dir = _ensure_runtime_tool_dir("scrcpy")
        scrcpy_exe = scrcpy_dir / "scrcpy.exe"
        command = [
            str(scrcpy_exe),
            "-s",
            serial,
            "--render-driver=direct3d",
            "--max-size",
            "640",
            "--video-bit-rate",
            "600K",
            "--max-fps",
            "15",
            "--no-audio",
        ]
        adb_start_server_output = run_adb(
            ["start-server"],
            timeout=5,
            log_timeout=False,
        )
        debug_details = _build_scrcpy_debug_details(
            serial,
            log_path=log_path,
            scrcpy_dir=scrcpy_dir,
            scrcpy_exe=scrcpy_exe,
            command=command,
            adb_start_server_output=adb_start_server_output,
        )

        if not scrcpy_exe.exists():
            message = (
                "scrcpy.exe not found. Please place scrcpy.exe in "
                f"{scrcpy_exe}"
            )
            debug_details["process_output"] = message
            with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
                _write_scrcpy_debug_preamble(log_file, debug_details)
            log_error(message)
            return {
                "ok": False,
                "message": message,
                "debug_text": _format_scrcpy_debug_text(debug_details),
                "log_path": str(log_path),
            }

        if not ADB_EXE.exists():
            message = f"adb.exe not found: {ADB_EXE}"
            debug_details["process_output"] = message
            with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
                _write_scrcpy_debug_preamble(log_file, debug_details)
            log_error(message)
            return {
                "ok": False,
                "message": message,
                "debug_text": _format_scrcpy_debug_text(debug_details),
                "log_path": str(log_path),
            }

        if debug_details["missing_files"]:
            message = (
                "scrcpy dependencies are incomplete.\n\n"
                + "\n".join(debug_details["missing_files"])
            )
            debug_details["process_output"] = message
            with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
                _write_scrcpy_debug_preamble(log_file, debug_details)
            log_error(message)
            return {
                "ok": False,
                "message": message,
                "debug_text": _format_scrcpy_debug_text(debug_details),
                "log_path": str(log_path),
            }

        scrcpy_env = _sanitize_external_process_env(scrcpy_dir)
        scrcpy_env["ADB"] = str(ADB_EXE)

        # scrcpy opens its own GUI window, but we still hide the parent console
        # process so packaged --noconsole builds stay visually silent.
        if getattr(sys, "frozen", False) and os.name == "nt":
            _set_windows_dll_directory(None)

        try:
            with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
                _write_scrcpy_debug_preamble(log_file, debug_details)
                process = subprocess.Popen(  # pylint: disable=consider-using-with
                    command,
                    cwd=str(scrcpy_dir),
                    env=scrcpy_env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=log_file,
                    shell=False,
                    **_get_windows_gui_subprocess_kwargs(),
                )
        finally:
            if getattr(sys, "frozen", False) and os.name == "nt":
                _set_windows_dll_directory(str(BASE_DIR))

        # If scrcpy fails immediately, surface the real stderr instead of
        # pretending the launch succeeded and forcing the user to guess.
        time.sleep(SCRCPY_STARTUP_WAIT_SECONDS)
        returncode = process.poll()
        if returncode is not None:
            debug_details["returncode"] = returncode
            debug_details["process_output"] = _read_scrcpy_process_output(log_path)
            if not debug_details["process_output"]:
                debug_details["process_output"] = (
                    f"scrcpy exited with code {returncode}"
                )
            message = _build_scrcpy_failure_message(debug_details)
            log_error(message)
            return {
                "ok": False,
                "message": message,
                "debug_text": _format_scrcpy_debug_text(debug_details),
                "log_path": str(log_path),
            }

        return {
            "ok": True,
            "message": "",
            "debug_text": _format_scrcpy_debug_text(debug_details),
            "log_path": str(log_path),
        }
    except Exception as e:
        fallback_details = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "serial": serial,
            "adb_path": str(ADB_EXE),
            "adb_exists": ADB_EXE.exists(),
            "scrcpy_path": str(BASE_DIR / "scrcpy" / "scrcpy.exe"),
            "scrcpy_exists": (BASE_DIR / "scrcpy" / "scrcpy.exe").exists(),
            "cwd": str(BASE_DIR / "scrcpy"),
            "log_path": str(log_path),
            "debug_mode": SCRCPY_DEBUG_MODE,
            "frozen": getattr(sys, "frozen", False),
            "command_display": "-",
            "serial_format": "UNKNOWN",
            "adb_start_server_output": "",
            "adb_devices_output": "",
            "adb_status": "UNKNOWN",
            "adb_get_state": "",
            "authorization_hint": "",
            "usb_debugging_hint": "",
            "dll_hint": "",
            "server_hint": "",
            "missing_files": [],
            "process_output": str(e),
        }
        with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
            _write_scrcpy_debug_preamble(log_file, fallback_details)
        log_error(str(e))
        return {
            "ok": False,
            "message": str(e),
            "debug_text": _format_scrcpy_debug_text(fallback_details),
            "log_path": str(log_path),
        }


# =========================================================
# DEVICE STATUS
# =========================================================

def connect_device(serial):
    return run_adb(["connect", serial], timeout=3)


def get_boot_markers(serial: str):
    output = run_adb(
        [
            "-s",
            serial,
            "shell",
            "sh",
            "-c",
            (
                "printf 'sys.boot_completed='; getprop sys.boot_completed; "
                "printf '\\ndev.bootcomplete='; getprop dev.bootcomplete; "
                "printf '\\ninit.svc.bootanim='; getprop init.svc.bootanim"
            ),
        ],
        timeout=6,
    )

    markers = {
        "sys.boot_completed": "",
        "dev.bootcomplete": "",
        "init.svc.bootanim": "",
    }

    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in markers:
            markers[key] = value.strip()

    return markers


def get_foreground_activity(serial: str) -> str:
    output = run_adb(
        ["-s", serial, "shell", "dumpsys", "activity", "activities"],
        timeout=4,
    )

    for line in output.splitlines():
        stripped = line.strip()
        if "mResumedActivity:" in stripped or stripped.startswith("ResumedActivity:"):
            return stripped

    return ""


def get_boot_diagnostics(serial: str, include_foreground: bool = True):
    markers = get_boot_markers(serial)
    return {
        **markers,
        "foreground_activity": (
            get_foreground_activity(serial) if include_foreground else ""
        ),
    }


def is_ready_foreground_activity(activity: str) -> bool:
    activity = (activity or "").lower()
    return "com.masi.iptv/.ui.core.activity.mainactivity" in activity


def classify_device_transport_status(serial: str, transport_status: str) -> str:
    if transport_status != "device":
        return transport_status

    markers = get_boot_markers(serial)
    boot_completed = (
        markers["sys.boot_completed"] == "1"
        or markers["dev.bootcomplete"] == "1"
    )
    bootanim_running = markers["init.svc.bootanim"] == "running"

    if bootanim_running:
        return "booting"

    if boot_completed:
        return transport_status

    foreground_ready = is_ready_foreground_activity(
        get_foreground_activity(serial)
    )
    if not foreground_ready:
        return "booting"

    return transport_status


def _parse_adb_device_lines(output: str):
    status_map = {}

    for line in output.splitlines()[1:]:
        if not line.strip():
            continue

        parts = line.split()
        if len(parts) >= 2:
            status_map[parts[0]] = parts[1]

    return status_map


def get_all_device_status(refresh_serials=None):
    refresh_serials = [serial for serial in (refresh_serials or []) if serial]
    status_map = _parse_adb_device_lines(run_adb(["devices"]))

    reconnect_serials = [
        serial
        for serial in refresh_serials
        if status_map.get(serial) in (None, "offline")
    ]

    if reconnect_serials:
        # Reconnecting only missing/offline transports keeps the worker from
        # flooding adb with connect calls when many devices are already healthy.
        for serial in reconnect_serials:
            run_adb(["connect", serial], timeout=2, log_timeout=False)

        time.sleep(ADB_DEVICE_REFRESH_DELAY_SECONDS)
        status_map = _parse_adb_device_lines(run_adb(["devices"]))

    for serial in refresh_serials:
        raw_status = status_map.get(serial)
        if raw_status == "device":
            status_map[serial] = classify_device_transport_status(serial, raw_status)

    return status_map


def get_device_status(serial: str) -> str:
    status_map = get_all_device_status(refresh_serials=[serial])
    status = status_map.get(serial)

    if status == "device":
        return "CONNECTED"
    if status == "booting":
        return "BOOTING"
    if status == "unauthorized":
        return "UNAUTHORIZED"
    if status == "offline":
        return "OFFLINE"
    return "OS DOWN"


def auto_reconnect(serial, ip, port):
    if get_device_status(serial) != "CONNECTED":
        adb_connect(ip, port, timeout=2, log_timeout=False)


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


def _parse_key_value_output(output: str):
    values = {}

    for line in output.splitlines():
        separator = None
        if "=" in line:
            separator = "="
        elif ":" in line:
            separator = ":"

        if separator is None:
            continue

        key, value = line.split(separator, 1)
        values[key.strip().lower()] = value.strip()

    return values


def _parse_ping_summary(output: str):
    summary = {
        "sent": None,
        "received": None,
        "lost": None,
        "loss_percent": None,
        "average_ms": None,
    }

    packet_match = re.search(
        r"Sent = (\d+), Received = (\d+), Lost = (\d+) \((\d+)% loss\)",
        output,
        re.IGNORECASE,
    )
    if packet_match:
        summary["sent"] = int(packet_match.group(1))
        summary["received"] = int(packet_match.group(2))
        summary["lost"] = int(packet_match.group(3))
        summary["loss_percent"] = int(packet_match.group(4))

    average_match = re.search(r"Average = (\d+)ms", output, re.IGNORECASE)
    if average_match:
        summary["average_ms"] = int(average_match.group(1))

    return summary


def _parse_battery_summary(output: str):
    values = _parse_key_value_output(output)

    def parse_int(name):
        raw_value = values.get(name)
        if raw_value is None:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

    def parse_bool(name):
        raw_value = values.get(name)
        if raw_value is None:
            return None
        return raw_value.lower() == "true"

    return {
        "ac_powered": parse_bool("ac powered"),
        "usb_powered": parse_bool("usb powered"),
        "wireless_powered": parse_bool("wireless powered"),
        "dock_powered": parse_bool("dock powered"),
        "present": parse_bool("present"),
        "level": parse_int("level"),
        "scale": parse_int("scale"),
        "status": values.get("status"),
        "health": values.get("health"),
        "voltage": parse_int("voltage"),
        "temperature": parse_int("temperature"),
    }


def _parse_uptime_seconds(output: str):
    first_line = (output or "").strip().splitlines()
    if not first_line:
        return None

    parts = first_line[0].split()
    if not parts:
        return None

    try:
        return float(parts[0])
    except ValueError:
        return None


def _format_duration(seconds):
    if seconds is None:
        return "-"

    total_seconds = int(seconds)
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _format_kb(kb_value):
    if kb_value is None:
        return "-"

    size = float(kb_value)
    units = ["KB", "MB", "GB", "TB"]
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    return f"{size:.1f} {units[unit_index]}"


def _normalize_voltage(raw_value):
    if raw_value is None or raw_value <= 0:
        return None

    if raw_value >= 1000:
        return raw_value / 1000.0

    if raw_value < 10:
        return float(raw_value)

    return raw_value / 1000.0


def _format_voltage(raw_value):
    volts = _normalize_voltage(raw_value)
    if volts is None:
        return "-"

    if raw_value is not None and raw_value < 10:
        return f"{volts:.2f} V (device-reported)"

    return f"{volts:.2f} V ({raw_value} mV)"


def _normalize_temperature(raw_value):
    if raw_value is None:
        return None

    if raw_value >= 200:
        return raw_value / 10.0

    return float(raw_value)


def _format_temperature(raw_value):
    celsius = _normalize_temperature(raw_value)
    if celsius is None:
        return "-"
    return f"{celsius:.1f} C"


def _parse_meminfo(output: str):
    values = {}

    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parts = value.strip().split()
        if not parts:
            continue
        try:
            values[key.strip()] = int(parts[0])
        except ValueError:
            continue

    total_kb = values.get("MemTotal")
    available_kb = values.get("MemAvailable", values.get("MemFree"))
    used_kb = None
    used_percent = None
    available_percent = None

    if total_kb is not None and available_kb is not None:
        used_kb = max(total_kb - available_kb, 0)
        used_percent = (used_kb / total_kb) * 100 if total_kb else None
        available_percent = (available_kb / total_kb) * 100 if total_kb else None

    return {
        "total_kb": total_kb,
        "available_kb": available_kb,
        "used_kb": used_kb,
        "used_percent": used_percent,
        "available_percent": available_percent,
    }


def _parse_df_output(output: str):
    entries = []
    preferred_mounts = ["/data", "/sdcard", "/storage/emulated", "/mnt/media_rw"]

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("filesystem"):
            continue

        parts = stripped.split()
        if len(parts) < 4:
            continue

        mount = parts[-1]
        use_percent = None
        use_token = None
        numeric_tokens = []

        for token in parts[1:]:
            if token.endswith("%") and token[:-1].isdigit():
                use_token = int(token[:-1])
                continue

            cleaned = token.strip("%")
            if cleaned.isdigit():
                numeric_tokens.append(int(cleaned))

        if use_token is not None:
            use_percent = use_token

        if len(numeric_tokens) < 3:
            continue

        total_kb = numeric_tokens[0]
        used_kb = numeric_tokens[1]
        available_kb = numeric_tokens[2]

        if use_percent is None and total_kb:
            use_percent = int((used_kb / total_kb) * 100)

        entries.append(
            {
                "mount": mount,
                "total_kb": total_kb,
                "used_kb": used_kb,
                "available_kb": available_kb,
                "use_percent": use_percent,
            }
        )

    preferred_entries = []
    for preferred_mount in preferred_mounts:
        for entry in entries:
            if entry["mount"] == preferred_mount or entry["mount"].startswith(
                preferred_mount + "/"
            ):
                preferred_entries.append(entry)

    deduped = []
    seen_mounts = set()
    for entry in preferred_entries + entries:
        mount = entry["mount"]
        if mount in seen_mounts:
            continue
        seen_mounts.add(mount)
        deduped.append(entry)

    return deduped[:5]


def _extract_interface_ips(output: str):
    addresses = []

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("inet "):
            continue

        parts = stripped.split()
        if len(parts) >= 2:
            addresses.append(parts[1].split("/", 1)[0])

    return addresses


def _extract_power_markers(output: str):
    markers = {
        "wakefulness": "",
        "display_state": "",
    }

    wakefulness_match = re.search(r"mWakefulness=([A-Za-z]+)", output)
    if wakefulness_match:
        markers["wakefulness"] = wakefulness_match.group(1)

    display_match = re.search(r"Display Power: state=([A-Za-z]+)", output)
    if display_match:
        markers["display_state"] = display_match.group(1)

    return markers


def _make_alert(level: str, category: str, title: str, detail: str):
    return {
        "level": level,
        "category": category,
        "title": title,
        "detail": detail,
    }


def _build_storage_summary(storage_entries):
    if not storage_entries:
        return "-"

    parts = []
    for entry in storage_entries[:3]:
        use_percent = (
            f"{entry['use_percent']}%"
            if entry.get("use_percent") is not None
            else "?"
        )
        parts.append(f"{entry['mount']} {use_percent} used")

    return ", ".join(parts)


def _pick_primary_storage_entry(storage_entries):
    if not storage_entries:
        return None

    for preferred_mount in ["/data", "/sdcard", "/storage/emulated"]:
        for entry in storage_entries:
            mount = entry.get("mount") or ""
            if mount == preferred_mount or mount.startswith(preferred_mount + "/"):
                return entry

    return storage_entries[0]


def _is_ignored_system_storage_mount(mount: str) -> bool:
    mount = (mount or "").strip()
    return mount in {
        "/system",
        "/system_ext",
        "/vendor",
        "/product",
        "/odm",
        "/vendor_dlkm",
        "/system_dlkm",
        "/system/vendor",
    }


def _build_storage_alert(entry, device_status: str):
    mount = (entry.get("mount") or "").strip()
    usage = entry.get("use_percent")

    if usage is None or not mount:
        return None

    if _is_ignored_system_storage_mount(mount):
        return None

    if mount == "/" and device_status not in ("BOOTING", "OS DOWN"):
        return None

    if usage >= 95:
        return _make_alert(
            "HIGH",
            "STORAGE",
            f"Storage {mount} hampir penuh",
            f"Pemakaian storage {mount} sudah {usage}%. Ini berisiko menyebabkan app crash, update gagal, atau boot bermasalah.",
        )

    if usage >= 85:
        return _make_alert(
            "MEDIUM",
            "STORAGE",
            f"Storage {mount} tinggi",
            f"Pemakaian storage {mount} sudah {usage}%. Sebaiknya mulai dibersihkan.",
        )

    return None


def get_device_health_snapshot(serial: str):
    battery_output = run_adb(
        ["-s", serial, "shell", "dumpsys", "battery"],
        timeout=4,
        log_timeout=False,
    )
    storage_output = run_adb(
        ["-s", serial, "shell", "df"],
        timeout=4,
        log_timeout=False,
    )

    battery = _parse_battery_summary(battery_output) if battery_output else {}
    storage_entries = _parse_df_output(storage_output) if storage_output else []
    primary_storage = _pick_primary_storage_entry(storage_entries)

    voltage_raw = battery.get("voltage") if battery else None
    voltage_volts = _normalize_voltage(voltage_raw)
    voltage_text = _format_voltage(voltage_raw)

    storage_mount = primary_storage.get("mount") if primary_storage else "-"
    storage_percent = (
        primary_storage.get("use_percent")
        if primary_storage
        else None
    )
    if primary_storage and storage_percent is not None:
        storage_text = f"{storage_mount} {storage_percent}% used"
    elif primary_storage:
        storage_text = storage_mount
    else:
        storage_text = "-"

    return {
        "voltage_raw": voltage_raw,
        "voltage_volts": voltage_volts,
        "voltage_text": voltage_text,
        "storage_mount": storage_mount,
        "storage_percent": storage_percent,
        "storage_text": storage_text,
    }


def _build_inspection_log(report: dict) -> str:
    lines = [
        "DEVICE INSPECTION REPORT",
        "========================",
        f"Timestamp      : {report['timestamp']}",
        f"Serial         : {report['serial']}",
        f"ADB Status     : {report['status']}",
        f"ADB Transport  : {report['adb_state'] or '-'}",
        f"Log File       : {report['log_path']}",
        "",
        "SUMMARY",
        "-------",
        f"Host Ping      : {report['summary'].get('host_ping', '-')}",
        f"Device IP(s)   : {report['summary'].get('device_ips', '-')}",
        f"Battery        : {report['summary'].get('battery', '-')}",
        f"Power State    : {report['summary'].get('power', '-')}",
        f"Electrical     : {report['summary'].get('electrical', '-')}",
        f"Memory         : {report['summary'].get('memory', '-')}",
        f"Storage        : {report['summary'].get('storage', '-')}",
        f"Uptime         : {report['summary'].get('uptime', '-')}",
        f"Boot Reason    : {report['summary'].get('boot_reason', '-')}",
        "",
        "ALERTS",
        "------",
    ]

    alerts = report.get("alerts") or []
    if alerts:
        for index, alert in enumerate(alerts, start=1):
            lines.append(
                f"{index}. [{alert['level']}] {alert['category']} - {alert['title']}"
            )
            lines.append(f"   {alert['detail']}")
    else:
        lines.append("No critical issue detected from this inspection.")

    lines.extend(
        [
            "",
            "BOOT MARKERS",
            "------------",
            f"sys.boot_completed : {report['boot'].get('sys.boot_completed') or '-'}",
            f"dev.bootcomplete   : {report['boot'].get('dev.bootcomplete') or '-'}",
            f"init.svc.bootanim  : {report['boot'].get('init.svc.bootanim') or '-'}",
            f"foreground_activity: {report['boot'].get('foreground_activity') or '-'}",
            "",
            "HARDWARE HEALTH",
            "---------------",
            f"Voltage        : {report['hardware']['electrical'].get('voltage_text', '-')}",
            f"Temperature    : {report['hardware']['electrical'].get('temperature_text', '-')}",
            f"Power Source   : {report['hardware']['electrical'].get('source_text', '-')}",
            f"Memory         : {report['hardware']['memory'].get('summary', '-')}",
            f"Storage        : {report['hardware'].get('storage_summary', '-')}",
            f"Uptime         : {report['hardware'].get('uptime_text', '-')}",
            f"Boot Reason    : {report['hardware'].get('boot_reason', '-')}",
            "",
            "RAW OUTPUTS",
            "-----------",
        ]
    )

    for key, value in report["raw"].items():
        lines.append(f"[{key}]")
        lines.append((value or "(empty)").strip())
        lines.append("")

    return "\n".join(lines).strip()


def _collect_inspection_report(serial: str, log_path=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = get_device_status(serial)
    adb_state = run_adb(["-s", serial, "get-state"], timeout=4, log_timeout=False)
    boot = {
        "sys.boot_completed": "",
        "dev.bootcomplete": "",
        "init.svc.bootanim": "",
        "foreground_activity": "",
    }

    raw = {
        "adb_get_state": adb_state,
        "host_ping": "",
        "battery": "",
        "power": "",
        "meminfo": "",
        "disk_usage": "",
        "uptime": "",
        "boot_reason": "",
        "network_props": "",
        "wlan0": "",
        "eth0": "",
        "ip_route": "",
    }

    ip = serial.split(":", 1)[0] if ":" in serial else ""
    ping_summary = None
    if ip:
        raw["host_ping"] = run_host_command(["ping", "-n", "2", ip], timeout=8)
        ping_summary = _parse_ping_summary(raw["host_ping"])

    battery = {}
    power = {}
    memory = {}
    storage_entries = []
    device_ips = []
    uptime_seconds = None
    boot_reason = ""
    hardware = {
        "electrical": {
            "voltage_raw": None,
            "voltage_volts": None,
            "voltage_text": "-",
            "temperature_raw": None,
            "temperature_c": None,
            "temperature_text": "-",
            "source_text": "-",
        },
        "memory": {
            "summary": "-",
        },
        "storage": [],
        "storage_summary": "-",
        "uptime_seconds": None,
        "uptime_text": "-",
        "boot_reason": "-",
    }

    if status in ("CONNECTED", "BOOTING"):
        boot = get_boot_diagnostics(serial)

        raw["battery"] = run_adb(
            ["-s", serial, "shell", "dumpsys", "battery"],
            timeout=8,
            log_timeout=False,
        )
        raw["power"] = run_adb(
            ["-s", serial, "shell", "dumpsys", "power"],
            timeout=8,
            log_timeout=False,
        )
        raw["meminfo"] = run_adb(
            ["-s", serial, "shell", "cat", "/proc/meminfo"],
            timeout=6,
            log_timeout=False,
        )
        raw["disk_usage"] = run_adb(
            ["-s", serial, "shell", "df", "-k"],
            timeout=8,
            log_timeout=False,
        )
        raw["uptime"] = run_adb(
            ["-s", serial, "shell", "cat", "/proc/uptime"],
            timeout=4,
            log_timeout=False,
        )
        raw["boot_reason"] = run_adb(
            [
                "-s",
                serial,
                "shell",
                "sh",
                "-c",
                (
                    "printf 'ro.boot.bootreason='; getprop ro.boot.bootreason; "
                    "printf '\\nsys.boot.reason='; getprop sys.boot.reason"
                ),
            ],
            timeout=6,
            log_timeout=False,
        )
        raw["network_props"] = run_adb(
            [
                "-s",
                serial,
                "shell",
                "sh",
                "-c",
                (
                    "printf 'dhcp.wlan0.ipaddress='; getprop dhcp.wlan0.ipaddress; "
                    "printf '\\ndhcp.eth0.ipaddress='; getprop dhcp.eth0.ipaddress; "
                    "printf '\\nnet.hostname='; getprop net.hostname"
                ),
            ],
            timeout=6,
            log_timeout=False,
        )
        raw["wlan0"] = run_adb(
            ["-s", serial, "shell", "ip", "addr", "show", "wlan0"],
            timeout=6,
            log_timeout=False,
        )
        raw["eth0"] = run_adb(
            ["-s", serial, "shell", "ip", "addr", "show", "eth0"],
            timeout=6,
            log_timeout=False,
        )
        raw["ip_route"] = run_adb(
            ["-s", serial, "shell", "ip", "route"],
            timeout=6,
            log_timeout=False,
        )

        battery = _parse_battery_summary(raw["battery"])
        power = _extract_power_markers(raw["power"])
        memory = _parse_meminfo(raw["meminfo"])
        storage_entries = _parse_df_output(raw["disk_usage"])
        uptime_seconds = _parse_uptime_seconds(raw["uptime"])

        device_ips.extend(_extract_interface_ips(raw["wlan0"]))
        device_ips.extend(_extract_interface_ips(raw["eth0"]))

        for line in raw["network_props"].splitlines():
            if "=" not in line:
                continue
            _, value = line.split("=", 1)
            value = value.strip()
            if value:
                device_ips.append(value)

        boot_reason_values = _parse_key_value_output(raw["boot_reason"])
        boot_reason = (
            boot_reason_values.get("sys.boot.reason")
            or boot_reason_values.get("ro.boot.bootreason")
            or ""
        )

    deduped_ips = []
    for device_ip in device_ips:
        if device_ip not in deduped_ips:
            deduped_ips.append(device_ip)

    alerts = []
    if status == "UNAUTHORIZED":
        alerts.append(
            _make_alert(
                "MEDIUM",
                "ADB",
                "ADB authorization belum diberikan",
                "Periksa layar TV/STB lalu izinkan USB debugging agar command inspection bisa membaca detail perangkat.",
            )
        )

    if status == "OFFLINE":
        alerts.append(
            _make_alert(
                "HIGH",
                "ADB",
                "ADB device offline",
                (
                    "Perangkat terlihat oleh ADB tetapi transport tidak stabil. "
                    "Biasanya terkait jaringan putus-nyambung atau service adb di STB hang."
                ),
            )
        )

    if status == "BOOTING":
        alerts.append(
            _make_alert(
                "MEDIUM",
                "BOOT",
                "Android belum selesai boot",
                (
                    "ADB sudah merespons, tetapi marker boot belum lengkap. "
                    "Jika lama berhenti di sini, cek kemungkinan boot loop atau aplikasi launcher macet."
                ),
            )
        )

    if ping_summary and ping_summary["loss_percent"] is not None:
        if ping_summary["loss_percent"] == 100:
            alerts.append(
                _make_alert(
                    "HIGH",
                    "NETWORK",
                    "Host tidak bisa ping perangkat",
                    "IP perangkat tidak merespons sama sekali. Cek kabel LAN/Wi-Fi, IP berubah, atau perangkat mati.",
                )
            )
        elif ping_summary["loss_percent"] > 0:
            alerts.append(
                _make_alert(
                    "MEDIUM",
                    "NETWORK",
                    "Packet loss terdeteksi",
                    f"Koneksi jaringan tidak stabil dengan packet loss {ping_summary['loss_percent']}%.",
                )
            )

    if status == "OS DOWN":
        detail = (
            "Perangkat masih bisa diping dari host, jadi kemungkinan OS hang, reboot loop, atau service adb tidak aktif."
            if ping_summary and ping_summary["loss_percent"] == 0
            else "Perangkat tidak merespons di ADB maupun jaringan. Cek suplai listrik, kabel LAN, Wi-Fi, atau kemungkinan IP berubah."
        )
        alerts.append(
            _make_alert(
                "HIGH",
                "POWER/OS",
                "Perangkat tidak reachable",
                detail,
            )
        )

    if battery:
        power_sources = [
            battery.get("ac_powered"),
            battery.get("usb_powered"),
            battery.get("wireless_powered"),
            battery.get("dock_powered"),
        ]
        level = battery.get("level")

        if level is not None and level <= 15:
            alerts.append(
                _make_alert(
                    "MEDIUM",
                    "POWER",
                    "Level baterai rendah",
                    f"Battery level terbaca {level}%. Jika perangkat memakai baterai/UPS internal, cek suplai dayanya.",
                )
            )

        if battery.get("present") and level is not None and level <= 5 and not any(
            value is True for value in power_sources
        ):
            alerts.append(
                _make_alert(
                    "HIGH",
                    "POWER",
                    "Perangkat tidak terdeteksi sedang mendapat daya",
                    "Semua sumber daya terbaca off dan level baterai sangat rendah. Ini indikasi kuat kendala listrik.",
                )
            )

    if battery:
        source_labels = []
        for key, label in [
            ("ac_powered", "AC"),
            ("usb_powered", "USB"),
            ("wireless_powered", "Wireless"),
            ("dock_powered", "Dock"),
        ]:
            if battery.get(key) is True:
                source_labels.append(label)

        hardware["electrical"] = {
            "voltage_raw": battery.get("voltage"),
            "voltage_volts": _normalize_voltage(battery.get("voltage")),
            "voltage_text": _format_voltage(battery.get("voltage")),
            "temperature_raw": battery.get("temperature"),
            "temperature_c": _normalize_temperature(battery.get("temperature")),
            "temperature_text": _format_temperature(battery.get("temperature")),
            "source_text": ", ".join(source_labels) if source_labels else "-",
        }

        temp_c = hardware["electrical"].get("temperature_c")
        if temp_c is not None and temp_c >= 75:
            alerts.append(
                _make_alert(
                    "HIGH",
                    "THERMAL",
                    "Suhu perangkat sangat tinggi",
                    f"Temperature terbaca {temp_c:.1f} C. Periksa ventilasi, adaptor, dan beban kerja perangkat.",
                )
            )
        elif temp_c is not None and temp_c >= 65:
            alerts.append(
                _make_alert(
                    "MEDIUM",
                    "THERMAL",
                    "Suhu perangkat tinggi",
                    f"Temperature terbaca {temp_c:.1f} C. Perangkat berisiko throttling atau restart jika kondisi ini menetap.",
                )
            )

    if status in ("CONNECTED", "BOOTING") and not deduped_ips:
        alerts.append(
            _make_alert(
                "MEDIUM",
                "NETWORK",
                "IP interface perangkat tidak terbaca",
                "wlan0/eth0 belum menunjukkan alamat IP. Ada kemungkinan DHCP belum didapat atau interface jaringan belum aktif.",
            )
        )

    if memory:
        available_percent = memory.get("available_percent")
        memory_summary = "-"
        if memory.get("total_kb") is not None and memory.get("available_kb") is not None:
            memory_summary = (
                f"free {_format_kb(memory['available_kb'])} / {_format_kb(memory['total_kb'])}"
            )
            if available_percent is not None:
                memory_summary += f" ({available_percent:.0f}% free)"

        hardware["memory"] = {
            **memory,
            "summary": memory_summary,
        }

        if available_percent is not None and available_percent <= 5:
            alerts.append(
                _make_alert(
                    "HIGH",
                    "MEMORY",
                    "Memori bebas sangat rendah",
                    f"Sisa memori hanya {available_percent:.0f}% dari total. Device bisa lambat, force close, atau reboot.",
                )
            )
        elif available_percent is not None and available_percent <= 10:
            alerts.append(
                _make_alert(
                    "MEDIUM",
                    "MEMORY",
                    "Memori bebas rendah",
                    f"Sisa memori hanya {available_percent:.0f}% dari total.",
                )
            )

    if storage_entries:
        hardware["storage"] = storage_entries
        hardware["storage_summary"] = _build_storage_summary(storage_entries)

        for entry in storage_entries:
            alert = _build_storage_alert(entry, status)
            if alert:
                alerts.append(alert)

    hardware["uptime_seconds"] = uptime_seconds
    hardware["uptime_text"] = _format_duration(uptime_seconds)
    hardware["boot_reason"] = boot_reason or "-"

    if boot_reason:
        lowered_reason = boot_reason.lower()
        if any(keyword in lowered_reason for keyword in ["watchdog", "panic", "kernel", "thermal"]):
            alerts.append(
                _make_alert(
                    "MEDIUM",
                    "BOOT",
                    "Boot reason menunjukkan restart tidak normal",
                    f"Boot reason device: {boot_reason}",
                )
            )

    host_ping_summary = "-"
    if ping_summary and ping_summary["loss_percent"] is not None:
        average_text = (
            f", avg {ping_summary['average_ms']} ms"
            if ping_summary["average_ms"] is not None
            else ""
        )
        host_ping_summary = (
            f"{ping_summary['received']}/{ping_summary['sent']} reply, "
            f"loss {ping_summary['loss_percent']}%{average_text}"
        )

    battery_summary = "-"
    if battery:
        level_text = (
            f"{battery['level']}%"
            if battery.get("level") is not None
            else "unknown"
        )
        sources = []
        for key, label in [
            ("ac_powered", "AC"),
            ("usb_powered", "USB"),
            ("wireless_powered", "Wireless"),
            ("dock_powered", "Dock"),
        ]:
            if battery.get(key) is True:
                sources.append(label)
        source_text = ", ".join(sources) if sources else "no external power detected"
        battery_summary = f"level {level_text}, {source_text}"

    power_summary = "-"
    if power:
        wakefulness = power.get("wakefulness") or "-"
        display_state = power.get("display_state") or "-"
        power_summary = f"wakefulness {wakefulness}, display {display_state}"

    electrical_summary = "-"
    if hardware["electrical"].get("voltage_text") != "-" or hardware["electrical"].get("temperature_text") != "-":
        electrical_summary = (
            f"voltage {hardware['electrical'].get('voltage_text', '-')}, "
            f"temp {hardware['electrical'].get('temperature_text', '-')}"
        )

    memory_summary = hardware["memory"].get("summary", "-")
    storage_summary = hardware.get("storage_summary", "-")
    uptime_text = hardware.get("uptime_text", "-")
    boot_reason_text = hardware.get("boot_reason", "-")

    report = {
        "timestamp": timestamp,
        "serial": serial,
        "status": status,
        "adb_state": adb_state,
        "boot": boot,
        "alerts": alerts,
        "summary": {
            "host_ping": host_ping_summary,
            "device_ips": ", ".join(deduped_ips) if deduped_ips else "-",
            "battery": battery_summary,
            "power": power_summary,
            "electrical": electrical_summary,
            "memory": memory_summary,
            "storage": storage_summary,
            "uptime": uptime_text,
            "boot_reason": boot_reason_text,
        },
        "hardware": hardware,
        "raw": raw,
        "log_path": str(log_path) if log_path else "",
    }
    return report


def _append_lines_to_log(log_path, lines):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as file_obj:
        file_obj.write("\n\n" + "\n".join(lines).strip() + "\n")


def _build_inspection_timeline(report: dict, reason: str, previous_status=None):
    lines = [
        "TIMELINE EVENT",
        "--------------",
        f"Timestamp      : {report['timestamp']}",
        f"Reason         : {reason}",
        f"Serial         : {report['serial']}",
    ]

    if previous_status:
        lines.append(f"Status Change  : {previous_status} -> {report['status']}")
    else:
        lines.append(f"Status         : {report['status']}")

    lines.extend(
        [
            f"ADB Transport  : {report['adb_state'] or '-'}",
            f"Host Ping      : {report['summary'].get('host_ping', '-')}",
            f"Device IP(s)   : {report['summary'].get('device_ips', '-')}",
            f"Battery        : {report['summary'].get('battery', '-')}",
            f"Power State    : {report['summary'].get('power', '-')}",
            f"Electrical     : {report['summary'].get('electrical', '-')}",
            f"Memory         : {report['summary'].get('memory', '-')}",
            f"Storage        : {report['summary'].get('storage', '-')}",
            f"Uptime         : {report['summary'].get('uptime', '-')}",
            f"Boot Reason    : {report['summary'].get('boot_reason', '-')}",
            "",
            "Alerts",
            "------",
        ]
    )

    if report.get("alerts"):
        for index, alert in enumerate(report["alerts"], start=1):
            lines.append(
                f"{index}. [{alert['level']}] {alert['category']} - {alert['title']}"
            )
            lines.append(f"   {alert['detail']}")
    else:
        lines.append("No alert on this checkpoint.")

    lines.extend(
        [
            "",
            "Boot Markers",
            "------------",
            f"sys.boot_completed : {report['boot'].get('sys.boot_completed') or '-'}",
            f"dev.bootcomplete   : {report['boot'].get('dev.bootcomplete') or '-'}",
            f"init.svc.bootanim  : {report['boot'].get('init.svc.bootanim') or '-'}",
            f"foreground_activity: {report['boot'].get('foreground_activity') or '-'}",
            "",
            "Hardware Health",
            "---------------",
            f"Voltage        : {report['hardware']['electrical'].get('voltage_text', '-')}",
            f"Temperature    : {report['hardware']['electrical'].get('temperature_text', '-')}",
            f"Memory         : {report['hardware']['memory'].get('summary', '-')}",
            f"Storage        : {report['hardware'].get('storage_summary', '-')}",
            f"Uptime         : {report['hardware'].get('uptime_text', '-')}",
            f"Boot Reason    : {report['hardware'].get('boot_reason', '-')}",
        ]
    )

    return lines


def append_inspection_event(log_path, serial: str, title: str, detail: str = "", status=""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "TIMELINE NOTE",
        "-------------",
        f"Timestamp      : {timestamp}",
        f"Serial         : {serial}",
        f"Event          : {title}",
    ]

    if status:
        lines.append(f"Status         : {status}")

    if detail:
        lines.extend(["", "Detail", "------", detail])

    _append_lines_to_log(log_path, lines)


def append_inspection_checkpoint(log_path, serial: str, reason: str, previous_status=None):
    report = _collect_inspection_report(serial, log_path=log_path)
    lines = _build_inspection_timeline(report, reason, previous_status=previous_status)
    _append_lines_to_log(log_path, lines)
    report["log_text"] = Path(log_path).read_text(encoding="utf-8")
    return report


def collect_device_inspection(serial: str):
    safe_serial = serial.replace(":", "_")
    log_dir = ensure_runtime_dir("logs")
    log_path = log_dir / (
        f"inspection_{safe_serial}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )

    report = _collect_inspection_report(serial, log_path=log_path)
    report["log_text"] = _build_inspection_log(report)

    with open(log_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(report["log_text"])

    append_inspection_event(
        log_path,
        serial,
        "Inspection monitor started",
        detail="Status device setelah ini akan di-append ke file ini saat ada perubahan state penting.",
        status=report["status"],
    )
    report["log_text"] = Path(log_path).read_text(encoding="utf-8")
    return report

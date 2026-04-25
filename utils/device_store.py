import json

from utils.paths import ensure_runtime_dir, get_runtime_path


DATA_DIR = ensure_runtime_dir("data")
FILE = get_runtime_path("data", "saved_devices.json")


def load_devices():
    if not FILE.exists():
        return []
    with open(FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_devices(devices):
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(devices, f, indent=2)


def add_device(name, ip, port, private_key=None, public_key=None):
    if not name or not ip:
        return

    port = str(port or "5555").strip() or "5555"
    devices = load_devices()

    existing = next(
        (d for d in devices if d["ip"] == ip and str(d["port"]) == port),
        None,
    )

    if existing:
        if private_key is not None:
            existing["private_key"] = private_key
        if public_key is not None:
            existing["public_key"] = public_key
    else:
        device_data = {
            "name": name,
            "ip": ip,
            "port": port,
        }

        if private_key is not None:
            device_data["private_key"] = private_key
        if public_key is not None:
            device_data["public_key"] = public_key

        devices.append(device_data)

    save_devices(devices)

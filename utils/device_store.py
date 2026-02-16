import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(BASE_DIR, "saved_devices.json")


def load_devices():
    if not os.path.exists(FILE):
        return []
    with open(FILE, "r") as f:
        return json.load(f)


def save_devices(devices):
    with open(FILE, "w") as f:
        json.dump(devices, f, indent=2)


def add_device(name, ip, port, private_key=None, public_key=None):

    # VALIDASI INPUT WAJIB
    if not name or not ip or not port:
        return
    
    devices = load_devices()

    # cek device sudah ada atau belum
    existing = next(
        (d for d in devices if d["ip"] == ip and d["port"] == port),
        None
    )

    if existing:
        # update key jika diberikan
        if private_key is not None:
            existing["private_key"] = private_key
        if public_key is not None:
            existing["public_key"] = public_key
    else:
        device_data = {
            "name": name,
            "ip": ip,
            "port": port
        }

        # hanya simpan key jika ada
        if private_key is not None:
            device_data["private_key"] = private_key
        if public_key is not None:
            device_data["public_key"] = public_key

        devices.append(device_data)

    save_devices(devices)
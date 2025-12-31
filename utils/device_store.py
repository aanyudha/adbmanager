import json
import os

FILE = "saved_devices.json"


def load_devices():
    if not os.path.exists(FILE):
        return []
    with open(FILE, "r") as f:
        return json.load(f)


def save_devices(devices):
    with open(FILE, "w") as f:
        json.dump(devices, f, indent=2)


def add_device(name, ip, port):
    devices = load_devices()
    if not any(d["ip"] == ip and d["port"] == port for d in devices):
        devices.append({
            "name": name,
            "ip": ip,
            "port": port
        })
    save_devices(devices)
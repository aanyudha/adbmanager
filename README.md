# adbmanager

Portable Windows desktop app for managing Android STB devices over ADB.

## Run from source

```powershell
python main.py
```

## Build single-file `.exe`

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Build the executable:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
```

Build output:

- `dist\HeisenbergADBTool.exe`
- `dist\HeisenbergADBTool-single-exe.zip`

This build bundles:

- application UI
- `adb`
- `scrcpy`
- default `data` template

At runtime:

- bundled resources are extracted automatically by PyInstaller
- `data\saved_devices.json` is created automatically from `data\saved_devices.template.json`
- writable files such as `data\saved_devices.json`, `screenshots\`, and `logs\inspection_*.log` stay next to the `.exe`
- each `Inspection` run creates a monitor log and appends important status changes such as reboot, booting, offline, and connected
- inspection also summarizes hardware signals that are readable over ADB, including voltage/temperature, memory, storage usage, uptime, and boot reason
## Features

- ADB device manager
- Wireless ADB support
- APK installer
- Batch command execution
- Screenshot capture
- Device inspection
- Advanced logging
- scrcpy integration

## Support

If you find this project useful:
Buy me a coffee -> https://paypal.me/FerdinandusYudha

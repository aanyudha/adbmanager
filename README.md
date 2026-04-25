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
- default `data`

At runtime:

- bundled resources are extracted automatically by PyInstaller
- writable files such as `data\saved_devices.json` and `screenshots\` stay next to the `.exe`

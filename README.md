# adbmanager

Portable Windows desktop app for managing Android STB devices over ADB.

## Features

- ADB device manager
- Wireless ADB support
- APK installer
- Batch command execution
- Screenshot capture
- Device inspection
- Advanced logging
- scrcpy integration

## Run from source

1. Open PowerShell in the project root.
2. Create and activate a virtual environment if needed.
3. Install dependencies.
4. Run the app.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python .\main.py
```

## Build EXE

### Fastest way

1. Open PowerShell in the project root.
2. Activate your virtual environment if you use one.
3. Install dependencies, including PyInstaller.
4. Run the build script.

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
```

Output:

- `dist\HeisenbergADBTool.exe`
- `dist\HeisenbergADBTool-single-exe.zip`

### Manual PyInstaller command

If you want to build manually without the helper script:

```powershell
python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onefile `
  --name HeisenbergADBTool `
  --add-data "adb;adb" `
  --add-data "scrcpy;scrcpy" `
  --add-data "data;data" `
  .\main.py
```

### Why this build works for the app

- `--windowed` / `--noconsole` keeps the desktop app from opening a visible console.
- ADB subprocesses are started in hidden mode on Windows, so packaged builds do not flash CMD windows.
- `adb`, `scrcpy`, and `data` are bundled into the executable build.

### Runtime behavior after build

- Bundled resources are extracted automatically by PyInstaller.
- `data\saved_devices.json` is created automatically from `data\saved_devices.template.json`.
- Writable files such as `data\saved_devices.json`, `screenshots\`, and `logs\inspection_*.log` stay next to the `.exe`.
- Each `Inspection` run creates a monitor log and appends important status changes such as reboot, booting, offline, and connected.
- Inspection also summarizes hardware signals that are readable over ADB, including voltage, temperature, memory, storage usage, uptime, and boot reason.

## Lint check

Run pylint locally with:

```powershell
pylint (git ls-files '*.py')
```

## Support

If you find this project useful:
Buy me a coffee -> https://paypal.me/FerdinandusYudha

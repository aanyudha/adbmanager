# adbmanager

Portable Windows desktop app for managing Android STB devices over ADB.

## Run from source

```powershell
python main.py
```

## Build `.exe`

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Build the portable package:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
```

Build output:

- `dist\HeisenbergADBTool\`
- `dist\HeisenbergADBTool-portable.zip`

The build includes:

- `HeisenbergADBTool.exe`
- bundled `adb\`
- bundled `scrcpy\`
- bundled `data\`

This means users can extract the folder or zip and run the app without manually copying `scrcpy`.

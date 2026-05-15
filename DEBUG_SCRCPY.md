# DEBUG_SCRCPY

## Where `scrcpy.exe` should be placed

Put the Windows scrcpy bundle in:

`C:\adbmanager\scrcpy\`

The app expects at least these files in that folder:

- `scrcpy.exe`
- `scrcpy-server`
- `SDL2.dll`
- `avcodec-61.dll`
- `avformat-61.dll`
- `avutil-59.dll`
- `swresample-5.dll`

## How the app detects `scrcpy.exe`

When running from source:

- `adb.exe` is resolved from `C:\adbmanager\adb\adb.exe`
- `scrcpy.exe` is resolved from `C:\adbmanager\scrcpy\scrcpy.exe`
- `scrcpy` is launched with `cwd` set to `C:\adbmanager\scrcpy`

When running from a bundled executable later, the app first copies the bundled
`scrcpy` folder into a persistent runtime directory, then launches it from there.

## How to read the generated log

Every scrcpy launch writes a log to:

`C:\adbmanager\logs\`

Example:

`C:\adbmanager\logs\scrcpy_192.168.32.50_5555_20260515_091500.log`

Each log contains:

- device serial
- adb path
- scrcpy path
- working directory
- full command line
- `adb devices -l` output
- detected ADB status
- missing dependency files, if any
- early exit return code, if scrcpy exits quickly
- captured stdout/stderr from scrcpy

## Common errors and fixes

`scrcpy.exe not found`

- Put the scrcpy Windows files in `C:\adbmanager\scrcpy\`

`adb.exe not found`

- Make sure `C:\adbmanager\adb\adb.exe` exists

`Device is visible to adb but authorization has not been accepted`

- Check the Android device screen
- Accept the USB debugging authorization prompt

`Device serial was not found in adb devices -l`

- Check the selected serial in the UI
- Make sure the device is connected with `adb connect`
- Make sure USB debugging or network ADB is enabled

`ADB transport exists but is offline`

- Reconnect the device
- Check network stability or USB cable quality
- Wait for Android boot to finish

`scrcpy dependencies are incomplete`

- Restore the missing DLL or `scrcpy-server` file into `C:\adbmanager\scrcpy\`

If the popup says scrcpy failed, open the matching file in `C:\adbmanager\logs\`
and read the `SCRCPY DEBUG`, `ADB DEVICES`, and `STDOUT / STDERR` sections first.

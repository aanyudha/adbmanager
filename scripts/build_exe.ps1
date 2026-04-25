$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = "python"
$name = "HeisenbergADBTool"
$distDir = Join-Path $root "dist"
$exePath = Join-Path $distDir "$name.exe"
$zipPath = Join-Path $distDir "$name-single-exe.zip"

Write-Host "Using Python:" $python
& $python -c "import PyInstaller, PySide6; print('PyInstaller and PySide6 are available')"

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name $name `
    --add-data "adb;adb" `
    --add-data "scrcpy;scrcpy" `
    --add-data "data;data" `
    main.py

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

if (Test-Path $exePath) {
    Compress-Archive -Path $exePath -DestinationPath $zipPath
    Write-Host "Single-exe package created:" $zipPath
}

Write-Host "Build complete. Output exe:" $exePath

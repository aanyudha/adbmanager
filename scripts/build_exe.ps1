$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = "python"
$name = "HeisenbergADBTool"
$distDir = Join-Path $root "dist"
$outputDir = Join-Path $distDir $name
$zipPath = Join-Path $distDir "$name-portable.zip"

Write-Host "Using Python:" $python
& $python -c "import PyInstaller, PySide6; print('PyInstaller and PySide6 are available')" 

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name $name `
    --contents-directory "." `
    --add-data "adb;adb" `
    --add-data "scrcpy;scrcpy" `
    --add-data "data;data" `
    main.py

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

if (Test-Path $outputDir) {
    Compress-Archive -Path (Join-Path $outputDir "*") -DestinationPath $zipPath
    Write-Host "Portable package created:" $zipPath
}

Write-Host "Build complete. Output folder:" $outputDir

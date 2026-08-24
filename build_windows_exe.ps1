$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Error "Virtual environment not found. Run install_windows.ps1 first."
    exit 1
}

$python = Join-Path $root ".venv\Scripts\python.exe"
& $python -m PyInstaller --noconsole --onefile --collect-all pvporcupine --name JarvisAgent jarvis.py
Write-Host "Build complete. Check the dist\ folder."
Write-Host "If wake-word detection is required, ensure PORCUPINE_ACCESS_KEY and the keyword model are configured before running the executable."

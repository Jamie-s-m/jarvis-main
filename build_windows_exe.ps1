$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Error "Virtual environment not found. Run install_windows.ps1 first."
    exit 1
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path (Join-Path $root "dist\JarvisAgent.exe")) {
    Remove-Item (Join-Path $root "dist\JarvisAgent.exe") -Force
}
if (Test-Path (Join-Path $root "build\JarvisAgent")) {
    Remove-Item (Join-Path $root "build\JarvisAgent") -Recurse -Force
}
Write-Host "Building the Windows desktop launcher..."
& $python -m PyInstaller --noconsole --onefile --windowed --collect-all pvporcupine --collect-all sounddevice --collect-all psutil --name JarvisAgent jarvis_desktop.py

if ($LASTEXITCODE -ne 0) {
    throw "Jarvis desktop build failed."
}

Write-Host "Build complete. Check the dist\ folder."
Write-Host "Run the generated executable to open the JARVIS desktop interface in the browser."
Write-Host "For real wake-word support, configure PORCUPINE_ACCESS_KEY and a valid Porcupine keyword model."

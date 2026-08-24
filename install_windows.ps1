$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$pythonCommand = $null
foreach ($candidate in @("py -3.12", "py -3.11", "py -3.10", "py -3.13", "py -3")) {
    try {
        $null = Invoke-Expression $candidate + " -V" 2>$null
        $pythonCommand = $candidate
        break
    }
    catch {}
}

if (-not $pythonCommand) {
    throw "Python 3.10+ is required. Install Python 3.12 or 3.11 first."
}

Write-Host "Using Python command: $pythonCommand"
Write-Host "Creating virtual environment..."
if (-not (Test-Path ".venv")) {
    Invoke-Expression "$pythonCommand -m venv .venv"
}

$python = Join-Path $root ".venv\Scripts\python.exe"
& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt

try {
    & $python -m pip install pyaudio
}
catch {
    Write-Warning "Direct PyAudio install failed. Trying pipwin fallback..."
    try {
        & $python -m pip install pipwin
        & $python -m pipwin install pyaudio
    }
    catch {
        Write-Warning "PyAudio installation was not successful. Voice input will remain disabled until it is installed manually."
    }
}

if (-not (Test-Path ".env")) {
    @'
OLLAMA_MODEL=llama3.1
STT_LANGUAGE=en-US
WAKE_WORD=jarvis
WAKE_PHRASES=jarvis,hi jarvis,hey jarvis,hello jarvis
WAKE_CLAP_ENABLED=true
TTS_PROVIDER=pyttsx3
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE=Rachel
ELEVENLABS_MODEL=eleven_turbo_v2
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openai/gpt-4o-mini
PORCUPINE_ACCESS_KEY=
PORCUPINE_KEYWORD_NAME=jarvis
PORT=5000
'@ | Set-Content -Path ".env"
}

Write-Host "Setup complete."
Write-Host "Run the assistant desktop app with:"
Write-Host "  .\.venv\Scripts\python.exe jarvis_desktop.py"
Write-Host "Or use the batch launcher:"
Write-Host "  start_jarvis.bat"
Write-Host "To build a Windows EXE, run:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\build_windows_exe.ps1"

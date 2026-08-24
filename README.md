# JARVIS AI Agent

JARVIS is a desktop AI assistant for Windows designed to help you with voice interaction, command execution, project scaffolding, code review, and everyday task automation in a brief, formal style.

It is designed to feel like a personal assistant that can:
- listen and wake on voice phrases such as "Jarvis", "Hi Jarvis", or "Hey Jarvis"
- respond in a calm, formal tone
- execute direct OS and app commands
- inspect code and propose improvements
- scaffold new software projects and open them in VS Code
- remember useful facts and previous conversations
- work with local Ollama models or remote Claude/OpenAI-style APIs when configured

## Quick start on Windows

Important: for the most stable microphone support, use Python 3.12 or 3.11. PyAudio is much more reliable there than on Python 3.14.

### Install the project

1. Open PowerShell in the project folder.
2. Run the installer:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows.ps1
```

This creates a local `.venv`, installs dependencies, and writes a ready-to-edit `.env` file.

### Run the desktop app in development mode

```powershell
.\start_jarvis.bat
```

This starts the desktop launcher, opens the JARVIS interface in your browser, and keeps the app available at:

```text
http://127.0.0.1:5000
```

### Build the Windows desktop executable

```powershell
powershell -ExecutionPolicy Bypass -File .\build_windows_exe.ps1
```

This creates a packaged Windows executable in the `dist` folder:

```text
dist\JarvisAgent.exe
```

### Build an installer package (optional)

If you have Inno Setup installed, you can build a real Windows installer from the included script:

```powershell
ISCC.exe .\JarvisAgent.iss
```

This generates a Windows setup file for installing JARVIS on another machine.

## Features

### Voice and hearing
- microphone listening loop
- wake phrase detection for "Jarvis", "Hi Jarvis", "Hey Jarvis"
- optional clap detection support for local hardware setups
- speech output through pyttsx3 TTS

### Intelligence and memory
- SQLite-backed persistent chat history
- lightweight semantic memory for recent context retrieval
- intent classification before LLM calls
- tool use for commands, code, and OS tasks

### OS and coding tasks
- open websites or local apps
- close apps or windows
- list processes and system status
- analyze a file or project for maintainability issues
- improve or refactor code suggestions
- scaffold new Python projects and open them in VS Code

### AI model support
- local Ollama models via `OLLAMA_MODEL`
- remote Claude/OpenAI-compatible providers via `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`
- safe fallback responses when no model is configured

## Recommended setup

For the best experience, use one of these options:

1. Local: install Ollama and run:

```bash
ollama serve
ollama pull llama3.1
```

2. Cloud: set one of these in a `.env` file:

```env
ANTHROPIC_API_KEY=your_key_here
# or
OPENAI_API_KEY=your_key_here
```

The app will automatically use the configured provider when available.

## Environment file

A sample environment file is provided in `.env.example`.

You can copy it to `.env` and edit values as needed:

```powershell
Copy-Item .env.example .env
```

Example:

```env
OLLAMA_MODEL=llama3.1
STT_LANGUAGE=en-US
WAKE_WORD=jarvis
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
PORT=5000
```

## Usage examples

- "Jarvis, what is the system status?"
- "Jarvis, open github"
- "Jarvis, close all windows"
- "Jarvis, create project my-app"
- "Jarvis, analyze this file: src/main.py"
- "Jarvis, improve this code for reliability"
- "Jarvis, what can you do?"

## Security notes

- destructive OS actions should be used with care
- the agent is designed for local desktop use; do not expose the web UI to public internet without hardening
- avoid giving it unrestricted permissions to run any user-level system command in shared or production environments

## Troubleshooting

### The assistant cannot reach the model
This is normal when no local or cloud model is configured. JARVIS falls back to built-in command handling and brief formal responses automatically.

### Voice does not wake up
- confirm the microphone is connected and allowed in Windows
- use a clear wake phrase such as "Jarvis"
- if using a quiet room, move closer to the mic or lower the threshold in the wake logic

### VS Code does not open
Install the VS Code shell command (`code`) or start VS Code manually, then rerun the command.

### Port 5000 is already in use
Change the port in `.env`:

```env
PORT=6000
```

## Build to a Windows executable

To make a standalone Windows program:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_windows_exe.ps1
```

This produces an executable in the `dist` folder.

## Current project status

The project is now fully prepared for local desktop usage and developer workflows. Recent production-ready upgrades completed in this branch include:
- Provider-native streaming hooks for Anthropic & OpenAI (best-effort)
- SSE token streaming endpoint at /api/chat/stream for low-latency chat
- ElevenLabs decoding via pydub + ffmpeg fallback and pyttsx3 fallback
- Deepgram ASR adapter (optional, requires DEEPGRAM_API_KEY)
- Dynamic tool generation with AST safety checks and HUD approval flow
- VS Code extension with HUD, Chat participant, auto-detect dist\JarvisAgent.exe launcher, persistent status bar, and stop/restart commands

Ready-to-use checklist (do these once)
1. Copy and edit .env with API keys and preferences:
  - DEEPGRAM_API_KEY, ELEVENLABS_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, LLM_PROVIDER
2. Install OS-level dependency: ffmpeg on PATH (required for pydub audio decoding).
3. Install Python dependencies:
  - pip install -r requirements.txt
4. Install and build the VS Code extension:
  - cd vscode-extension
  - npm install
  - npm run compile
5. (Optional) Build a packaged backend executable:
  - Use PyInstaller or your preferred packager to produce dist\JarvisAgent.exe and place it in workspace/dist/ or extension/dist/ so the extension and Electron wrapper auto-detect it.

Running locally (developer-friendly)
- Start backend (development):
 python jarvis_desktop.py

- Start the VS Code extension HUD (development):
 - Open vscode-extension in VS Code
 - npm run compile
 - Press F5 to run the Extension Development Host
 - Use the StatusBar item or Command Palette (JARVIS: Start Engine) to start the engine

- Test SSE streaming manually:
 curl -N -H "Content-Type: application/json" -X POST -d "{\"message\":\"Hello\"}" http://127.0.0.1:5000/api/chat/stream

Developer notes and further work
- See DEVELOPMENT.md for architecture, extension internals, provider adapters, security model, and packaging guidance.
- The dynamic tool safety flow stores pending proposals in the agent memory and broadcasts proposals to HUD via WebSocket. Approvals trigger /api/confirm_tool to apply the code to custom_tools.py.

Electron wrapper
- Electron assets are included for a native cinematic UI. Recommended production flow: build a self-contained dist\JarvisAgent.exe and have Electron spawn that instead of a Python script.

Support & Troubleshooting
- If audio is silent: confirm ffmpeg is installed and pyttsx3 is available (or ElevenLabs API key is set).
- If streaming is slow: check your LLM_PROVIDER and API key, network conditions, and provider service limits.
- If the extension fails to spawn the backend: ensure Python is on PATH or that dist\JarvisAgent.exe is present in the expected locations.

Want me to finish packaging and produce an installer?
- I can prepare an Electron + installer build that bundles a PyInstaller-built backend exe and ffmpeg. Reply and indicate whether to bundle a Python runtime and ffmpeg or to require users to install them separately.

For deeper developer instructions see [DEVELOPMENT.md](./DEVELOPMENT.md).


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

The project is ready for local desktop use and for further extension. It already includes:
- user-friendly web UI
- persistent memory
- wake detection logic
- tool execution
- code review and project scaffolding
- VS Code integration

## Support and next steps

For deeper improvements, see [DEVELOPMENT.md](./DEVELOPMENT.md).

---

## Electron native wrapper (new)

An Electron-based native wrapper has been added to provide a standalone cinematic desktop app experience. Files added:
- [package.json](C:/Users/morea/Downloads/jarvis-main.worktrees/fully-functional-ai-agent-upgrade/package.json)
- [main.js](C:/Users/morea/Downloads/jarvis-main.worktrees/fully-functional-ai-agent-upgrade/main.js)
- [preload.js](C:/Users/morea/Downloads/jarvis-main.worktrees/fully-functional-ai-agent-upgrade/preload.js)
- [start_electron.bat](C:/Users/morea/Downloads/jarvis-main.worktrees/fully-functional-ai-agent-upgrade/start_electron.bat)

What it does:
- Launches the Python desktop server (jarvis_desktop.py) automatically (prefers .venv\Scripts\python.exe when present)
- Opens a frameless BrowserWindow that loads the HUD at http://127.0.0.1:5000
- Ensures single-instance behavior and tries to terminate the server when the Electron app quits

Development run (quick):
1. Ensure Python venv and dependencies are installed (use install_windows.ps1)
2. Install Node + npm
3. In project root run:
   npm install
   npm start

Packaging notes:
- electron-builder configuration is included in package.json. To produce a Windows installer (NSIS):
  1. npm install --save-dev electron electron-builder
  2. npx electron-builder --win --x64
- Recommended production flow: create a PyInstaller-built dist\JarvisAgent.exe and modify main.js to spawn that executable (safer for end users who don't want to manage Python).

Next recommended steps (can implement on request):
- Switch main.js to detect and spawn dist\JarvisAgent.exe if present (recommended for final installer builds).
- Implement WebSocket push + waveform streaming so the native window shows real-time mic levels and interim ASR transcripts (this is the next priority to make the HUD live).
- Add tray, media keys, and global hotkeys for wake/stop behaviors.

If you'd like me to continue, confirm whether to:
- Use dist\\JarvisAgent.exe as the canonical backend to spawn from Electron (recommended), and
- Implement WebSocket push + waveform streaming next so the native app has live feedback.


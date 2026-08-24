# JARVIS Development Guide

This document explains the design, architecture, and extension points for the JARVIS AI Agent project.

## Goal of the project

JARVIS is a local desktop assistant with a JARVIS-inspired personality. It is intended to behave like a formal, capable assistant that can:
- respond to voice commands and text prompts
- process wake words and user commands
- keep long-term memory and recent history
- execute system commands safely
- inspect and improve code
- scaffold new projects and open them in VS Code
- integrate with local or cloud language models

The project aims to be practical, usable, and extendable without requiring a heavy enterprise stack.

## High-level architecture

The project is organized around a few key layers:

1. User interface layer
   - Flask web UI served from the app
   - browser-based chat UI with text and microphone support
   - state endpoints for enabling/disabling the agent

2. Agent orchestration layer
   - `JarvisAgent` is the main orchestration class
   - command planning, execution, and review are separated
   - `MultiAgentOrchestrator` routes requests through planning and review

3. Intent engine
   - `CommandIntentEngine.classify()` identifies user intent before LLM calls
   - common commands are handled quickly without waiting for a large model
   - direct tasks like weather, open app, status, file analysis, and project scaffolding are resolved deterministically

4. Memory layer
   - `load_memory()` and `save_memory()` manage persistent memory in SQLite
   - `SemanticMemoryStore` stores embeddings-like semantic facts for lightweight retrieval
   - recent chat history is preserved for conversational continuity

5. Tool layer
   - built-in tool functions are defined in `jarvis.py`
   - custom tools can be added in `custom_tools.py`
   - tools cover: weather, file I/O, coding review, app launching, system status, and project scaffolding

6. Voice layer
   - speech recognition and TTS are optional but supported
   - wake word detection is implemented with phrase matching and a real hardware-ready hook for Porcupine
   - this layer is designed to be extended for stronger microphone detection

7. Model integration
   - local Ollama support is primary
   - Claude/OpenAI-compatible API support is included as a fallback when `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is configured

## Core files

### `jarvis.py`
This is the main application file. It contains:
- agent class
- memory helpers
- semantic memory logic
- tool functions
- intent router
- multi-agent orchestrator
- Flask UI and API endpoints
- startup logic

### `custom_tools.py`
This module contains user-defined safe utilities and extension-style tools. It is loaded dynamically by the agent and is a good place to add custom actions without rewriting the main file.

### `requirements.txt`
Dependency list for the project. It includes the key packages for:
- UI: Flask
- voice: pyttsx3, SpeechRecognition, sounddevice
- memory and math: numpy, sqlite
- LLM integration: ollama, requests
- system metrics: psutil
- optional wake support: pvporcupine

### `install_windows.ps1`
Uses a local virtual environment and installs all dependencies for a Windows user. This is the easiest entry point for standard users.

### `start_jarvis.bat`
Simple startup script for launching the assistant from Windows without needing to type Python commands manually.

### `build_windows_exe.ps1`
Packages the app into a single Windows executable using PyInstaller.

### `.env.example` and `.env`
The environment file holds user configuration for:
- model selection
- wake word
- speech language
- API keys
- port settings

## Runtime behavior

### Startup flow
1. environment variables are loaded
2. memory is restored from SQLite or fallback JSON
3. `JarvisAgent` is initialized
4. listener thread is started if enabled
5. Flask server begins serving the web UI

### Request flow
1. user sends text or voice
2. command intent is classified
3. if it is a system or code action, the correct tool is executed
4. result is reviewed and formatted into a brief response
5. memory is updated and persisted

### Execution model
The app intentionally separates tasks into:
- planner: decides what kind of work the message is
- executor: performs the task or calls the tool
- reviewer: condenses the result into a brief formal response

This design keeps output concise and prevents random long responses.

## Memory design

The memory system is intentionally lightweight and practical.

### SQLite memory
Used for:
- user preferences
- recent chat history
- stored facts and self-improvement notes

### Semantic memory
The project keeps a semantic-memory layer for recall using lightweight text tokenization and vector similarity. It is not a full transformer database, but it is enough for short-term, local memory retrieval patterns.

### Extension recommendation
If you want deeper recall in the future, consider replacing the simple vector logic with a real embedding store such as:
- sentence-transformers
- pgvector in PostgreSQL
- Qdrant
- Chroma

This would improve long-term memory recall and multi-session personalization.

## Wake detection design

### Current implementation
The agent supports:
- phrase detection for "Jarvis", "Hi Jarvis", "Hey Jarvis"
- optional clap detection helper which can be tuned for room acoustics
- Porcupine integration for real wake-word hardware support when an access key is available

### Why this is useful
It makes the assistant feel more “always-on” and natural compared with a purely button-based interface.

### Recommendations
- tune sensitivity for real environments
- use a proper noise-isolated mic
- test with different rooms and microphone placement
- use Porcupine for production-grade real wake-word behavior

## Model integration

The current design is built so the assistant can work without a cloud API, but it also supports better models when available.

### Ollama path
Great for local and private work. It is easy to run and does not require sending data to a remote API.

### Claude/OpenAI path
Use `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in `.env` to increase the quality of reasoning and chat responses when you want a stronger model than the local fallback.

This pattern makes the project adaptable to the best available model in your environment.

## Code review and improvement pipeline

The agent has built-in tools for:
- syntax checking
- maintainability review
- recommendation generation
- code improvement guidance
- file and project scaffolding

This is intentionally lightweight and useful for day-to-day coding tasks, but it is not a replacement for full IDE analysis, linting, or a large code intelligence environment.

## Best extension points

### Add a new command type
1. edit `CommandIntentEngine.classify()`
2. add the command handler in `CommandIntentEngine.execute()`
3. optionally add a tool function in `jarvis.py` or `custom_tools.py`
4. test the command with a direct agent call or UI request

### Add a new tool
1. write a pure function in `custom_tools.py`
2. ensure it returns a string summary
3. allow it to be called by the LLM tool layer automatically

### Improve the memory system
1. replace or enhance `SemanticMemoryStore`
2. add metadata like timestamps, user-specific profiles, and document tags
3. consider storing a vector database for more advanced retrieval

### Improve the UX
- add a richer desktop tray icon
- add command history filtering
- add quick-actions bar
- add global hotkey activation and shutdown

## Testing strategy

At a minimum, validate these flows:
- app boots successfully
- `/api/state` returns expected JSON
- `/api/chat` returns replies without 500 errors
- command responses are brief and formal
- file/project creation works
- memory survives a restart

You can verify quickly with Python and Flask or by using the built-in browser UI.

## Roadmap for future improvements

### Short term
- better wake-word tuning
- user profile personalization
- stronger command confirmation for dangerous system actions
- richer project templates (web app, CLI tool, API service)

### Mid term
- stricter sandboxing for code and command execution
- external API provider selection presets
- more advanced semantic memory retrieval
- use of a real vector database

### Long term
- desktop tray and system integration
- background voice monitoring service
- self-improvement loop with user feedback
- multi-agent specialization: planner, reviewer, researcher, coder

## Development principles

The project favors:
- practical functionality over theoretical complexity
- local-first operation
- clear state management
- explicit tool boundaries
- robust fallbacks when dependencies or models are missing

This keeps the assistant usable even in a development environment where a perfect cloud stack is not available.

## Final note

The app is intentionally designed to be extendable and easy to understand. If you want to push it further, the most valuable improvements are:
1. stronger wake-word hardware support
2. a richer memory store
3. real project templates and code-generation workflows
4. safer system controls with user confirmation
5. more polished desktop installation and startup UX

That combination will turn JARVIS into a far more complete, reliable personal desktop agent.

---

## Final readiness checklist (for developers)

Follow these steps to make a local JARVIS installation "production-ready" for end users:

1. Prepare environment and secrets
   - Create and populate `.env` in the repository root with all required API keys: `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`. Set `LLM_PROVIDER` appropriately.
   - Install ffmpeg and ensure it is available on PATH (required for audio decoding with pydub).

2. Install and validate dependencies
   - python -m venv .venv
   - .\.venv\Scripts\activate
   - pip install -r requirements.txt
   - cd vscode-extension && npm install && npm run compile

3. Validate runtime locally
   - Launch the backend for manual testing:
     python jarvis_desktop.py
   - Open the HUD in a browser at http://127.0.0.1:5000 or launch the VS Code extension (F5 in Extension Development Host).
   - Test endpoints:
     - GET /api/state
     - POST /api/chat with a sample message
     - POST /api/chat/stream to confirm SSE streaming

4. Package backend (recommended for end users)
   - Install PyInstaller in the venv: pip install pyinstaller
   - Build an optimized executable (example):
     pyinstaller --onefile --add-data "<path-to-ffmpeg>\ffmpeg.exe;ffmpeg" jarvis_desktop.py
   - Copy the produced exe into `dist\JarvisAgent.exe` and verify the VS Code extension or Electron launcher detects and spawns it.

5. Build Electron installer (optional)
   - In the project root, install electron-builder and build tools and configure package.json as needed.
   - Add the packaged `dist\JarvisAgent.exe` as the preferred backend for the Electron main process.

6. Security hardening
   - Run dynamic tool code (approved via HUD) in an isolated environment (container or separate restricted user account) for extra safety.
   - Add multi-step confirmations for destructive tool actions.

7. CI and tests
   - Add unit tests for AST safety checks, stream parsing, and tool execution wrappers.
   - Add integration tests that start the backend and call the main endpoints in a disposable environment.

If you'd like, I can prepare the packaging scripts (PyInstaller spec, electron-builder config) and add CI test scaffolding next. Otherwise, these docs should make it straightforward for other developers to run and extend JARVIS.

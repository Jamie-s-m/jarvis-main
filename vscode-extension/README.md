JARVIS VS Code Extension

This extension integrates the local JARVIS AI Agent with VS Code.

Features:
- Registers a Chat Participant `@jarvis` (via the Chat API) — forwards chat prompts to `http://127.0.0.1:5000/api/chat`.
- Adds a "JARVIS HUD" sidebar webview that connects to `ws://127.0.0.1:8765` for realtime audio levels and interim transcripts.
- Command: `JARVIS: Start Engine` which tries to start the local Python backend (jarvis_desktop.py) if it's not running.

Setup
1. cd vscode-extension
2. npm install (install dev dependencies)
3. npm run compile
4. Press F5 in VS Code to run the extension in the Extension Development Host.

Notes
- The extension expects the JARVIS backend to be reachable at http://127.0.0.1:5000 and the WS broadcaster at ws://127.0.0.1:8765. Use `jarvis.startEngine` to spawn the Python server if needed.
- The Chat API usage relies on the available Chat extensions in your VS Code build. If the Chat API is not present, the participant registration is skipped gracefully.

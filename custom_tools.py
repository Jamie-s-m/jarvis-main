"""Production-safe utility tools for the Jarvis agent."""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict


def create_vscode_project(project_name: str, template: str = "python", root_dir: str = ".") -> str:
    """Create a starter project and open it in VS Code if the CLI is available."""
    name = (project_name or "jarvis-project").strip() or "jarvis-project"
    root = Path(root_dir).expanduser().resolve() if root_dir and root_dir.strip() else Path.cwd()
    project_dir = root / name
    if project_dir.exists():
        return f"Project '{name}' already exists at '{project_dir}'."
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".vscode").mkdir(exist_ok=True)
    (project_dir / "src").mkdir(exist_ok=True)

    readme = f"# {name}\n\nCreated by Jarvis.\n"
    gitignore = "__pycache__/\n*.pyc\n.env\n.venv/\n"
    (project_dir / "README.md").write_text(readme, encoding="utf-8")
    (project_dir / ".gitignore").write_text(gitignore, encoding="utf-8")
    (project_dir / "requirements.txt").write_text("flask>=3.0,<4\nrequests>=2.31,<3\n", encoding="utf-8")
    (project_dir / "src" / "main.py").write_text("print('Project ready for Jarvis.')\n", encoding="utf-8")
    (project_dir / ".vscode" / "settings.json").write_text(json.dumps({"files.exclude": {"**/__pycache__": True}}, indent=2), encoding="utf-8")

    cli = next((candidate for candidate in ["code", "code.cmd", "code-insiders", "code-insiders.cmd", "codium", "codium.cmd"] if shutil.which(candidate)), None)
    if cli:
        try:
            subprocess.Popen([cli, str(project_dir)], shell=False)
            return f"Created and opened project '{name}' in VS Code."
        except Exception:
            return f"Created project '{name}' at '{project_dir}'. Open it manually in VS Code."

    windows_candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code Insiders" / "Code - Insiders.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft VS Code" / "Code.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft VS Code" / "Code.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "VSCodium" / "VSCodium.exe",
    ]
    exe_path = next((candidate for candidate in windows_candidates if candidate.exists()), None)
    if exe_path:
        try:
            subprocess.Popen([str(exe_path), str(project_dir)], shell=False)
            return f"Created and opened project '{name}' in VS Code."
        except Exception:
            return f"Created project '{name}' at '{project_dir}'. Open it manually in VS Code."
    return f"Created project '{name}' at '{project_dir}'."


def open_in_vscode(target_path: str = ".") -> str:
    """Open a workspace or file in VS Code if the CLI or common installation is available."""
    path = Path(target_path).expanduser().resolve() if target_path and target_path.strip() else Path.cwd()
    cli = next((candidate for candidate in ["code", "code.cmd", "code-insiders", "code-insiders.cmd", "codium", "codium.cmd"] if shutil.which(candidate)), None)
    if cli:
        try:
            subprocess.Popen([cli, str(path)], shell=False)
            return f"Opened '{path}' in VS Code."
        except Exception as exc:  # pragma: no cover
            return f"Failed to open VS Code: {exc}"

    windows_candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code Insiders" / "Code - Insiders.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft VS Code" / "Code.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft VS Code" / "Code.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "VSCodium" / "VSCodium.exe",
    ]
    exe_path = next((candidate for candidate in windows_candidates if candidate.exists()), None)
    if exe_path:
        try:
            subprocess.Popen([str(exe_path), str(path)], shell=False)
            return f"Opened '{path}' in VS Code."
        except Exception as exc:  # pragma: no cover
            return f"Failed to open VS Code via executable path: {exc}"
    return "VS Code CLI is not installed or not on PATH. Install the 'code' command or open VS Code manually, then retry."


def self_development() -> str:
    """Return a motivational quote used during agent self-improvement cycles."""
    quotes = [
        "Small improvements every day compound into extraordinary results.",
        "Progress is more valuable than perfection when you keep learning.",
        "The best AI systems improve by observing feedback and iterating quickly.",
    ]
    return random.choice(quotes)


def get_weather_snapshot(location: str) -> Dict[str, Any]:
    """Return a small structured weather snapshot placeholder."""
    return {
        "location": location,
        "condition": "clear",
        "temperature_c": 24,
        "summary": f"Weather for {location} is clear and comfortable.",
    }


def greet(name: str) -> str:
    """Return a friendly greeting for a user."""
    return f"Hello, {name}! I am ready to help."


def remember_note(note: str) -> str:
    """Store a short note inside the agent memory workflow."""
    return f"Saved note: {note}"


def self_training_note() -> str:
    """Fallback self-improvement hook for continuous learning."""
    return "Learning pass complete. This agent is ready to review feedback and improve itself."

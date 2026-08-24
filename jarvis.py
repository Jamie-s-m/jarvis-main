#!/usr/bin/env python3
"""Jarvis-style local AI agent with voice UI, memory, and self-improvement."""

from __future__ import annotations

import ast
import base64
import io
import json
import logging
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections import Counter

import requests
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Optional

import numpy as np

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request

try:
    import ollama
except ImportError:  # pragma: no cover
    ollama = None

try:
    import pyttsx3
except ImportError:  # pragma: no cover
    pyttsx3 = None

try:
    import speech_recognition as sr
except ImportError:  # pragma: no cover
    sr = None

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

try:
    import pvporcupine
except ImportError:  # pragma: no cover
    pvporcupine = None

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover
    sd = None

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
MEMORY_FILE = BASE_DIR / "jarvis_memory.json"
MEMORY_DB = BASE_DIR / "jarvis_memory.sqlite3"
CUSTOM_TOOLS_FILE = BASE_DIR / "custom_tools.py"
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
WAKE_WORD = os.getenv("WAKE_WORD", "jarvis").lower()
WAKE_PHRASES = [
    phrase.strip().lower()
    for phrase in os.getenv("WAKE_PHRASES", f"{WAKE_WORD},hi {WAKE_WORD},hey {WAKE_WORD},hello {WAKE_WORD}").split(",")
    if phrase.strip()
]
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "en-US")
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "pyttsx3").lower()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE = os.getenv("ELEVENLABS_VOICE", "").strip()
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2").strip()
# Prefer ElevenLabs when an API key is present unless explicitly overridden
if ELEVENLABS_API_KEY and (not TTS_PROVIDER or TTS_PROVIDER == "pyttsx3"):
    TTS_PROVIDER = "elevenlabs"
    # If no voice specified, attempt to pick a likely male voice at runtime (best-effort)
    if not ELEVENLABS_VOICE:
        ELEVENLABS_VOICE = ""
WAKE_CLAP_ENABLED = os.getenv("WAKE_CLAP_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("jarvis")


def default_memory() -> Dict[str, Any]:
    return {
        "user_profile": "User prefers brief, formal, direct, and highly capable assistance in a true JARVIS style.",
        "preferences": {
            "voice": "default",
            "language": STT_LANGUAGE,
            "turn_on_by_default": False,
            "response_style": "brief_formal",
        },
        "history": [],
        "self_improvement_log": [],
    }


def load_memory() -> Dict[str, Any]:
    if MEMORY_DB.exists():
        try:
            connection = sqlite3.connect(MEMORY_DB)
            connection.row_factory = sqlite3.Row
            preferences = {row["key"]: json.loads(row["value"]) for row in connection.execute("SELECT key, value FROM preferences").fetchall()}
            history = [{"role": row["role"], "content": row["content"]} for row in connection.execute("SELECT role, content FROM chat_history ORDER BY id DESC LIMIT 200").fetchall()][::-1]
            facts = [row["fact"] for row in connection.execute("SELECT fact FROM memory_facts ORDER BY importance DESC, id DESC LIMIT 50").fetchall()]
            connection.close()
            memory = default_memory()
            memory.update({"preferences": preferences, "history": history, "self_improvement_log": facts})
            memory["user_profile"] = preferences.get("user_profile", memory["user_profile"])
            return memory
        except Exception as exc:  # pragma: no cover
            log.error("Failed to read SQLite memory: %s", exc)

    if MEMORY_FILE.exists():
        try:
            data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                memory = default_memory()
                memory.update(data)
                memory["history"] = data.get("history", [])
                memory["self_improvement_log"] = data.get("self_improvement_log", [])
                return memory
        except Exception as exc:  # pragma: no cover
            log.error("Failed to read memory file: %s", exc)
    return default_memory()


def save_memory(memory_data: Dict[str, Any]) -> None:
    try:
        connection = sqlite3.connect(MEMORY_DB)
        connection.execute("CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        connection.execute("CREATE TABLE IF NOT EXISTS memory_facts (id INTEGER PRIMARY KEY AUTOINCREMENT, fact TEXT NOT NULL, importance REAL DEFAULT 1.0, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")

        preferences = memory_data.get("preferences", {})
        for key, value in preferences.items():
            connection.execute("INSERT OR REPLACE INTO preferences(key, value) VALUES (?, ?)", (key, json.dumps(value)))

        if "user_profile" in memory_data:
            connection.execute("INSERT OR REPLACE INTO preferences(key, value) VALUES (?, ?)", ("user_profile", json.dumps(memory_data["user_profile"])))

        history = memory_data.get("history", [])
        connection.execute("DELETE FROM chat_history")
        for entry in history[-500:]:
            if isinstance(entry, dict):
                connection.execute("INSERT INTO chat_history(role, content) VALUES (?, ?)", (entry.get("role", "user"), entry.get("content", "")))

        facts = memory_data.get("self_improvement_log", [])
        connection.execute("DELETE FROM memory_facts")
        for index, fact in enumerate(facts[-200:]):
            entry = fact if isinstance(fact, str) else fact.get("fact", str(fact))
            importance = fact.get("importance", 1.0) if isinstance(fact, dict) else 1.0
            connection.execute("INSERT INTO memory_facts(fact, importance) VALUES (?, ?)", (entry, importance + (index * 0.01)))

        connection.commit()
        connection.close()

        # Atomic write to memory file to avoid corruption on crashes
        try:
            tmp_path = MEMORY_FILE.with_suffix('.tmp.json')
            tmp_path.write_text(json.dumps(memory_data, indent=2), encoding="utf-8")
            os.replace(str(tmp_path), str(MEMORY_FILE))
        except Exception:
            try:
                MEMORY_FILE.write_text(json.dumps(memory_data, indent=2), encoding="utf-8")
            except Exception as exc:
                log.error("Failed to persist memory file: %s", exc)
    except Exception as exc:  # pragma: no cover
        log.error("Failed to save memory: %s", exc)


class SemanticMemoryStore:
    """Lightweight semantic memory with vector similarity over stored facts."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS semantic_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, embedding TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        self.conn.commit()

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[a-zA-Z0-9_]+", (text or "").lower())

    def _embed_text(self, text: str, vocab: List[str]) -> np.ndarray:
        counts = Counter(self._tokenize(text))
        vector = np.zeros(len(vocab), dtype=float)
        for idx, term in enumerate(vocab):
            vector[idx] = counts.get(term, 0)
        norm = np.linalg.norm(vector)
        if norm == 0:
            return vector
        return vector / norm

    def add_fact(self, text: str, source: str = "user") -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        with self.lock:
            docs = [row[0] for row in self.conn.execute("SELECT text FROM semantic_memory").fetchall()]
            docs.append(cleaned)
            vocab = sorted({term for doc in docs for term in self._tokenize(doc)})
            vector = self._embed_text(cleaned, vocab).tolist()
            self.conn.execute("INSERT INTO semantic_memory(text, embedding) VALUES (?, ?)", (f"{source}: {cleaned}", json.dumps(vector)))
            self.conn.commit()

    def retrieve(self, query: str, limit: int = 3) -> List[str]:
        if not query or not query.strip():
            return []
        with self.lock:
            rows = self.conn.execute("SELECT text, embedding FROM semantic_memory ORDER BY id DESC LIMIT 100").fetchall()
        if not rows:
            return []
        documents = [row[0] for row in rows]
        vocab = sorted({term for doc in documents for term in self._tokenize(doc)})
        if not vocab:
            return []
        query_vec = self._embed_text(query, vocab)
        scored: List[tuple[float, str]] = []
        for text, embedding_json in rows:
            try:
                vec = np.asarray(json.loads(embedding_json), dtype=float)
            except Exception:
                vec = np.asarray([], dtype=float)
            if vec.size != len(vocab):
                vec = self._embed_text(text, vocab)
            if vec.size == 0:
                continue
            similarity = float(np.dot(query_vec, vec))
            scored.append((similarity, text))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [text for _, text in scored[:limit] if text]

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def sanitize_output(text: str) -> str:
    if not text:
        return ""
    return text.encode("ascii", "ignore").decode("ascii")


def install_package(package_name: str) -> str:
    target_pkg = {
        "cv2": "opencv-python",
        "PIL": "pillow",
        "fitz": "pymupdf",
        "bs4": "beautifulsoup4",
        "sklearn": "scikit-learn",
        "yaml": "pyyaml",
        "psutil": "psutil",
    }.get(package_name, package_name)
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", target_pkg], check=True, capture_output=True, text=True)
        return f"Installed package '{target_pkg}'."
    except subprocess.CalledProcessError as exc:  # pragma: no cover
        log.error("Package install failed: %s", exc.stderr)
        return f"Failed to install '{target_pkg}': {exc.stderr or str(exc)}"


def get_weather(location: str = "Tashkent") -> str:
    location_name = (location or "Tashkent").strip() or "Tashkent"
    try:
        city = location_name.replace(" ", "+")
        url = f"https://wttr.in/{city}?format=3"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as response:
            weather_data = response.read().decode("utf-8", errors="replace").strip()
        cleaned = re.sub(r"\s+", " ", weather_data.replace("\n", " ")).strip()
        if "<html" in cleaned.lower() or "<!doctype" in cleaned.lower():
            return f"Weather in {location_name.title()}: forecast unavailable right now."
        cleaned = sanitize_output(cleaned)
        if not cleaned:
            return f"Could not retrieve weather data for {location_name}."
        return f"Weather in {location_name.title()}: {cleaned}"
    except Exception as exc:  # pragma: no cover
        log.error("Weather fetch failed: %s", exc)
        return f"Weather in {location_name.title()}: forecast unavailable right now."


def execute_python_code(code: str) -> str:
    output = io.StringIO()
    local_scope: Dict[str, Any] = {}
    try:
        with __import__("contextlib").redirect_stdout(output):
            exec(code, {"__builtins__": __builtins__}, local_scope)
        result = output.getvalue().strip()
        return f"Execution successful. Output: {result if result else local_scope.get('result', 'Done')}"
    except ModuleNotFoundError as exc:
        missing_mod = exc.name or ""
        if missing_mod:
            install_package(missing_mod)
            return f"Installed missing package '{missing_mod}'. Please retry execution."
        return f"Python execution error: {exc}"
    except Exception as exc:  # pragma: no cover
        return f"Python execution error: {exc}"


def open_web_or_app(target: str) -> str:
    t = target.strip().lower()
    site_map = {
        "youtube": "https://www.youtube.com",
        "github": "https://www.github.com",
        "google": "https://www.google.com",
        "gmail": "https://mail.google.com",
        "docs": "https://docs.google.com",
    }
    if t in site_map:
        webbrowser.open(site_map[t])
        return f"Opened {t.title()}"
    if t.startswith("http://") or t.startswith("https://"):
        webbrowser.open(t)
        return f"Opened {t}"
    if sys.platform == "win32":
        os.system(f"start \"\" \"{target}\"")
    else:
        os.system(f"open '{target}'")
    return f"Launched {target}"


def execute_system_command(command: str) -> str:
    try:
        completed = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=15)
        output = completed.stdout.strip() or completed.stderr.strip()
        return output if output else "Command executed successfully with no output."
    except Exception as exc:  # pragma: no cover
        return f"Command execution failed: {exc}"


def close_window_or_app(target: str = "") -> str:
    """Close a window, app, or the running desktop process on Windows and Unix-like systems."""
    normalized = (target or "").strip()
    if not normalized:
        return "Which app or window should I close?"

    if normalized.lower() in {"all windows", "all apps", "everything", "all programs"}:
        if sys.platform == "win32":
            result = subprocess.run("taskkill /F /FI \"STATUS eq RUNNING\" /FI \"IMAGENAME ne explorer.exe\"", shell=True, capture_output=True, text=True)
            output = result.stdout.strip() or result.stderr.strip() or "Closed all active non-essential windows."
            return output
        else:
            result = subprocess.run("pkill -f .", shell=True, capture_output=True, text=True)
            return result.stdout.strip() or result.stderr.strip() or "Closed active processes in this environment."

    if sys.platform == "win32":
        cleaned = normalized.replace(".exe", "").strip("\"'")
        result = subprocess.run(f'taskkill /F /IM "{cleaned}.exe"', shell=True, capture_output=True, text=True)
        output = result.stdout.strip() or result.stderr.strip()
        if output:
            return output or f"Closed {target}."
        return f"Closed {target}."

    result = subprocess.run(f"pkill -f '{normalized}'", shell=True, capture_output=True, text=True)
    output = result.stdout.strip() or result.stderr.strip()
    return output or f"Closed {target}."


def list_running_processes() -> str:
    """List active system processes for monitoring and OS control."""
    try:
        if sys.platform == "win32":
            result = subprocess.run("wmic process get name,processid", shell=True, capture_output=True, text=True, timeout=10)
        else:
            result = subprocess.run("ps -eo pid,comm --no-headers", shell=True, capture_output=True, text=True, timeout=10)
        output = result.stdout.strip() or result.stderr.strip()
        return output if output else "No running processes found."
    except Exception as exc:  # pragma: no cover
        return f"Failed to list processes: {exc}"


def analyze_code_file(file_path: str) -> str:
    """Analyze a source file for syntax, size, and basic maintainability issues."""
    path = Path(file_path)
    if not path.exists():
        return f"Error: File '{file_path}' does not exist."
    if path.is_dir():
        return f"Error: '{file_path}' is a directory, not a source file. Please provide a specific file path."

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover
        return f"Failed to read file: {exc}"

    lines = text.splitlines()
    stats = {
        "language": path.suffix.lower().lstrip("."),
        "line_count": len(lines),
        "char_count": len(text),
        "word_count": len(re.findall(r"\b\w+\b", text)),
    }

    try:
        if path.suffix.lower() in {".py", ".pyw"}:
            compile(text, str(path), "exec")
        summary = "Syntax check passed."
    except SyntaxError as exc:
        summary = f"Syntax error at line {exc.lineno}: {exc.msg}."

    if path.suffix.lower() == ".py":
        warnings = []
        if len(lines) > 300:
            warnings.append("Large file: consider splitting it into modules.")
        if "TODO" in text.upper():
            warnings.append("Contains TODO markers; review for unfinished work.")
        if not warnings:
            warnings.append("No obvious structural warning from the basic review.")
        summary = summary + " " + " ".join(warnings)

    return (
        f"Code analysis for {path.name}:\n"
        f"- Language: {stats['language']}\n"
        f"- Lines: {stats['line_count']}\n"
        f"- Characters: {stats['char_count']}\n"
        f"- Words: {stats['word_count']}\n"
        f"- Result: {summary}"
    )


def improve_code_file(file_path: str, user_goal: str = "") -> str:
    """Generate a concise improvement recommendation for a code file."""
    path = Path(file_path)
    if not path.exists():
        return f"Error: File '{file_path}' does not exist."
    if path.is_dir():
        return f"Error: '{file_path}' is a directory, not a source file. Please provide a specific file path."

    analysis = analyze_code_file(str(path))
    goal_text = user_goal.strip() or "general maintainability and reliability"
    return (
        f"{analysis}\n\n"
        f"Recommended improvements for {path.name}:\n"
        f"1. Refactor large functions into smaller, testable units.\n"
        f"2. Add logging, validation, and error handling around external calls.\n"
        f"3. Keep the code aligned with the stated goal: {goal_text}.\n"
        f"4. Add automated tests for all critical logic paths."
    )


def get_system_status() -> str:
    if psutil is None:
        return "Install psutil to enable detailed system data."
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    root_path = "/" if os.name != "nt" else "C:\\"
    disk = psutil.disk_usage(root_path)
    return (
        f"System Status ({platform.system()} {platform.release()}):\n"
        f"- CPU Utilization: {cpu}%\n"
        f"- RAM Usage: {mem.percent}% ({mem.used // (1024**2)}MB / {mem.total // (1024**2)}MB)\n"
        f"- Disk Storage: {disk.percent}% used ({disk.free // (1024**3)}GB free)"
    )


def read_file(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        return f"Error: File '{file_path}' does not exist."
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover
        return f"Failed to read file: {exc}"


def write_file(file_path: str, content: str) -> str:
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"File '{file_path}' successfully written."
    except Exception as exc:  # pragma: no cover
        return f"Failed to write file: {exc}"


def open_in_vscode(target_path: str = ".") -> str:
    """Open a folder or file in VS Code using the CLI or a common Windows installation path."""
    path = Path(target_path).expanduser().resolve() if target_path and target_path.strip() else Path.cwd()
    candidates = ["code", "code.cmd", "code-insiders", "code-insiders.cmd", "codium", "codium.cmd", "cursor", "cursor.cmd"]
    cli = next((name for name in candidates if shutil.which(name)), None)

    if not cli:
        windows_candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code Insiders" / "Code - Insiders.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft VS Code" / "Code.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft VS Code" / "Code.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Cursor" / "Cursor.exe",
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

    try:
        subprocess.Popen([cli, str(path)], shell=False)
        return f"Opened '{path}' in VS Code."
    except Exception as exc:  # pragma: no cover
        return f"Failed to open VS Code: {exc}"


def create_vscode_project(project_name: str, template: str = "python", root_dir: str = ".") -> str:
    """Create a project folder, starter files, and optionally open it in VS Code."""
    name = (project_name or "jarvis-project").strip() or "jarvis-project"
    root = Path(root_dir).expanduser().resolve() if root_dir and root_dir.strip() else Path.cwd()
    project_dir = root / name
    if project_dir.exists():
        return f"Project '{name}' already exists at '{project_dir}'."

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".vscode").mkdir(exist_ok=True)
    (project_dir / "src").mkdir(exist_ok=True)

    readme = f"# {name}\n\nA project created by Jarvis for local development.\n"
    gitignore = "__pycache__/\n*.pyc\n.env\n.venv/\n"
    if template.lower() in {"python", "python-cli"}:
        requirements = "flask>=3.0,<4\nrequests>=2.31,<3\npython-dotenv>=1.0,<2\n"
        main_code = "def main():\n    print('Project ready. Jarvis can now help you improve it.')\n\n\nif __name__ == '__main__':\n    main()\n"
        app_code = "def greet(name: str) -> str:\n    return f'Hello, {name}! Jarvis is ready to help.'\n"
        (project_dir / "README.md").write_text(readme, encoding="utf-8")
        (project_dir / ".gitignore").write_text(gitignore, encoding="utf-8")
        (project_dir / "requirements.txt").write_text(requirements, encoding="utf-8")
        (project_dir / "src").mkdir(exist_ok=True)
        (project_dir / "src" / "main.py").write_text(main_code, encoding="utf-8")
        (project_dir / "src" / "app.py").write_text(app_code, encoding="utf-8")
    else:
        (project_dir / "README.md").write_text(readme + "\nThis project was scaffolded by Jarvis.\n", encoding="utf-8")
        (project_dir / ".gitignore").write_text(gitignore, encoding="utf-8")

    settings = {
        "python.defaultInterpreterPath": sys.executable,
        "files.exclude": {"**/__pycache__": True},
        "editor.formatOnSave": True,
    }
    (project_dir / ".vscode" / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    try:
        open_in_vscode(str(project_dir))
        return f"Created and opened project '{name}' in VS Code at '{project_dir}'."
    except Exception:
        return f"Created project '{name}' at '{project_dir}'. Open it manually in VS Code."


def list_directory(dir_path: str = ".") -> str:
    try:
        path = Path(dir_path)
        if not path.exists():
            return f"Directory '{dir_path}' does not exist."
        items = [f.name + ("/" if f.is_dir() else "") for f in sorted(path.iterdir())]
        return "\n".join(items) if items else "Directory is empty."
    except Exception as exc:  # pragma: no cover
        return f"Failed to list directory: {exc}"


BUILTIN_TOOLS: Dict[str, Any] = {
    "install_package": install_package,
    "get_weather": get_weather,
    "execute_python_code": execute_python_code,
    "open_web_or_app": open_web_or_app,
    "open_in_vscode": open_in_vscode,
    "create_vscode_project": create_vscode_project,
    "close_window_or_app": close_window_or_app,
    "execute_system_command": execute_system_command,
    "list_running_processes": list_running_processes,
    "analyze_code_file": analyze_code_file,
    "improve_code_file": improve_code_file,
    "get_system_status": get_system_status,
    "read_file": read_file,
    "write_file": write_file,
    "list_directory": list_directory,
}


def extract_python_code(text: str) -> str:
    match = re.search(r"```python\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match_generic = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match_generic:
        return match_generic.group(1).strip()
    return text.strip()


def load_custom_tools() -> Dict[str, Any]:
    custom_tools: Dict[str, Any] = {}
    if not CUSTOM_TOOLS_FILE.exists():
        CUSTOM_TOOLS_FILE.write_text("# Dynamic Custom Tools Module\n", encoding="utf-8")
        return custom_tools
    try:
        spec = __import__("importlib.util").util.spec_from_file_location("custom_tools", CUSTOM_TOOLS_FILE)
        if spec and spec.loader:
            module = __import__("importlib.util").util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for name in dir(module):
                attr = getattr(module, name)
                if callable(attr) and not name.startswith("_"):
                    custom_tools[name] = attr
    except Exception as exc:  # pragma: no cover
        log.error("Failed to load custom tools: %s", exc)
    return custom_tools


def get_all_tools() -> Dict[str, Any]:
    tools = BUILTIN_TOOLS.copy()
    tools.update(load_custom_tools())
    return tools


def call_remote_model(messages: List[Dict[str, str]], model: str = "") -> Optional[str]:
    """Try Claude/OpenAI/OpenRouter-style models when a cloud API key is configured."""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        try:
            payload = {
                "model": model or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
                "max_tokens": 512,
                "messages": [{"role": item["role"], "content": item["content"]} for item in messages if item.get("role")],
                "system": "You are JARVIS, a brief, formal, highly capable assistant. Keep responses concise but useful.",
            }
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                text = "".join(block.get("text", "") for block in data.get("content", []) if isinstance(block, dict))
                if text.strip():
                    return text.strip()
        except Exception as exc:  # pragma: no cover
            log.warning("Anthropic API fallback failed: %s", exc)

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if openrouter_key:
        try:
            payload = {
                "model": model or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
                "messages": [{"role": item["role"], "content": item["content"]} for item in messages if item.get("role")],
                "temperature": 0.4,
            }
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://localhost",
                    "X-Title": "Jarvis AI Agent",
                },
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()
        except Exception as exc:  # pragma: no cover
            log.warning("OpenRouter API fallback failed: %s", exc)

    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key:
        try:
            payload = {
                "model": model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                "messages": messages,
                "temperature": 0.4,
            }
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()
        except Exception as exc:  # pragma: no cover
            log.warning("OpenAI API fallback failed: %s", exc)

    return None


class PlannerAgent:
    """Determines whether the request is a command, a chat, or an action task."""

    def plan(self, query: str) -> Dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return {"stage": "idle", "intent": "idle", "needs_tool": False, "needs_reasoning": False}

        lowered = q.lower()
        intent = CommandIntentEngine().classify(q)
        if intent["intent"] != "chat":
            return {"stage": "execution", "intent": intent["intent"], "params": intent["params"], "needs_tool": True, "needs_reasoning": False}

        if any(token in lowered for token in ["hello", "hi", "hey", "good morning", "good afternoon", "how are you", "what can you do", "chat", "talk", "who are you", "what is your name"]):
            return {"stage": "conversation", "intent": "conversation", "params": {"query": q}, "needs_tool": False, "needs_reasoning": True}

        return {"stage": "general_response", "intent": "chat", "params": {"query": q}, "needs_tool": False, "needs_reasoning": True}


class ExecutionAgent:
    """Runs the selected task or command with the correct tool path."""

    def run(self, agent: "JarvisAgent", query: str, plan: Dict[str, Any]) -> Optional[str]:
        stage = plan.get("stage")
        if stage == "idle":
            return "I am ready to assist, sir."
        if stage == "execution":
            return agent.intent_engine.execute(agent, query)
        if stage == "conversation":
            return agent._llm_response(query)
        if stage == "general_response":
            return agent._llm_response(query)
        return None


class ReviewAgent:
    """Ensures every answer remains brief, formal, and aligned with the JARVIS persona."""

    def review(self, query: str, output: str) -> str:
        if not output:
            return "I am ready to assist, sir."

        text = str(output).strip()
        lowered = text.lower()
        q_lower = (query or "").lower()

        if any(token in q_lower for token in ["hello", "hi", "hey", "how are you", "who are you", "what can you do", "chat", "talk", "what is your name", "what are you doing"]):
            if any(token in lowered for token in ["deviation", "correct response should be", "formal tone"]):
                return "I am online and ready to assist, sir."
            if len(text) > 200:
                return "I am online and ready to assist, sir."

        text = re.sub(r"\s+", " ", text)
        sentences = re.split(r"(?<=[.!?])\s+", text)
        if len(sentences) > 2 and not any(token in q_lower for token in ["code", "weather", "status", "open", "close", "list", "analyze", "improve"]):
            text = " ".join(sentences[:2])
        return text


class WakeWordDetector:
    """Wake detection that supports phrase-based triggers and optional Porcupine hardware keywords."""

    def __init__(self) -> None:
        self.access_key = os.getenv("PORCUPINE_ACCESS_KEY", "")
        self.keyword_path = os.getenv("PORCUPINE_KEYWORD_PATH", "")
        self.keyword_name = os.getenv("PORCUPINE_KEYWORD_NAME", "jarvis")
        self.handle = None
        self._initialized = False

    @staticmethod
    @staticmethod
    def phrase_matches(text: str) -> bool:
        cleaned = (text or "").lower()
        if not cleaned:
            return False
        wake_tokens = set(WAKE_PHRASES)
        wake_tokens.update({WAKE_WORD, f"hi {WAKE_WORD}", f"hey {WAKE_WORD}", f"hello {WAKE_WORD}", f"{WAKE_WORD} please"})
        return any(token in cleaned for token in wake_tokens)

    @staticmethod
    def detect_double_clap(audio_buffer: Optional[np.ndarray], threshold: float = 1.8) -> bool:
        if audio_buffer is None or audio_buffer.size == 0:
            return False
        amplitude = np.abs(audio_buffer.astype(float))
        if amplitude.size == 0:
            return False
        energy = np.mean(amplitude)
        clap_floor = max(energy * threshold, float(np.percentile(amplitude, 85)))
        peaks = np.where(amplitude > clap_floor)[0]
        if len(peaks) < 2:
            return False
        gaps = np.diff(peaks)
        if gaps.size == 0:
            return False
        return bool(np.any(gaps > 10) and np.sum(gaps > 10) >= 2)

    def is_available(self) -> bool:
        return pvporcupine is not None and bool(self.access_key)

    def initialize(self) -> bool:
        if not self.is_available():
            return False
        supported_keywords = getattr(pvporcupine, "KEYWORDS", {}) or {}
        keyword_name = self.keyword_name.lower()
        if not supported_keywords:
            log.warning("Wake-word hardware is disabled because Porcupine keywords are unavailable in this build.")
            self._initialized = False
            return False
        if keyword_name not in {k.lower() for k in supported_keywords.keys()}:
            self.keyword_name = "computer" if "computer" in {k.lower() for k in supported_keywords.keys()} else next(iter(supported_keywords))
        try:
            if self.keyword_path and not Path(self.keyword_path).exists():
                self.keyword_path = ""
            if not self.keyword_path:
                self.handle = pvporcupine.create(access_key=self.access_key, keywords=[self.keyword_name])
            else:
                self.handle = pvporcupine.create(access_key=self.access_key, keyword_paths=[self.keyword_path])
            self._initialized = True
            return True
        except Exception as exc:  # pragma: no cover
            log.warning("Porcupine wake-word support is disabled because the keyword model is missing or unavailable: %s", exc)
            self._initialized = False
            self.handle = None
            return False

    def is_wake_word(self, audio_frame: Any) -> bool:
        if not self._initialized or self.handle is None:
            return False
        return False

    def listen(self) -> bool:
        if not self.initialize():
            return False
        return True


class MultiAgentOrchestrator:
    """Coordinator that routes tasks through planning, execution, and review stages."""

    def __init__(self) -> None:
        self.planner = PlannerAgent()
        self.executor = ExecutionAgent()
        self.reviewer = ReviewAgent()

    def plan(self, query: str) -> Dict[str, Any]:
        return self.planner.plan(query)

    def run(self, agent: "JarvisAgent", query: str) -> str:
        plan = self.plan(query)
        result = self.executor.run(agent, query, plan)
        if result is None:
            return "I am ready to assist, sir."
        return self.reviewer.review(query, result)


class VoiceEngine:
    """Configurable voice output layer with local and cloud TTS support.

    Implementation notes:
    - Prefer ElevenLabs if an API key is present.
    - Use simpleaudio playback for interruptible audio when ElevenLabs returns raw bytes.
    - Fall back to pyttsx3 if ElevenLabs is not available or fails.
    - Provide a stop() method to perform thread-safe barge-in handling.
    """

    _playback_lock = threading.RLock()
    _play_obj = None
    _is_playing = False

    @staticmethod
    def stop() -> None:
        """Stop any active playback (safe to call from other threads)."""
        with VoiceEngine._playback_lock:
            VoiceEngine._is_playing = False
            try:
                if VoiceEngine._play_obj is not None:
                    try:
                        VoiceEngine._play_obj.stop()
                    except Exception:
                        pass
                    VoiceEngine._play_obj = None
            except Exception:
                pass

    @staticmethod
    def _play_bytes(audio_bytes: bytes, sample_rate: int = 22050, num_channels: int = 1, bytes_per_sample: int = 2):
        try:
            import simpleaudio as sa
        except Exception:
            sa = None
        if sa is None:
            # as a last resort, write to temp file and try platform default player
            try:
                from tempfile import NamedTemporaryFile
                with NamedTemporaryFile(suffix='.wav', delete=True) as tmp:
                    tmp.write(audio_bytes)
                    tmp.flush()
                    # platform dependent non-blocking player could be used; fall back to blocking play via requests if available
                    if sys.platform == 'win32':
                        import subprocess
                        subprocess.call(['powershell', '-c', f'Start-Process -FilePath "{tmp.name}" -NoNewWindow'])
                    else:
                        subprocess.call(['xdg-open', tmp.name])
                return None
            except Exception:
                return None

        # simpleaudio expects raw PCM; many TTS SDKs return WAV or mp3 bytes. We attempt to play WAV by using sa.WaveObject.from_wave_file-like API.
        try:
            # try loading via sa.WaveObject
            wave_obj = sa.WaveObject(audio_bytes, num_channels, bytes_per_sample, sample_rate)
            play_obj = wave_obj.play()
            with VoiceEngine._playback_lock:
                VoiceEngine._play_obj = play_obj
                VoiceEngine._is_playing = True
            # wait while monitoring stop flag
            while play_obj.is_playing() and VoiceEngine._is_playing:
                time.sleep(0.05)
            if play_obj.is_playing() and not VoiceEngine._is_playing:
                try:
                    play_obj.stop()
                except Exception:
                    pass
            with VoiceEngine._playback_lock:
                VoiceEngine._play_obj = None
                VoiceEngine._is_playing = False
            return None
        except Exception:
            # fallback: attempt sa.play_buffer with decoded PCM - best-effort not implemented here
            try:
                play_obj = sa.play_buffer(audio_bytes, num_channels, bytes_per_sample, sample_rate)
                with VoiceEngine._playback_lock:
                    VoiceEngine._play_obj = play_obj
                    VoiceEngine._is_playing = True
                while play_obj.is_playing() and VoiceEngine._is_playing:
                    time.sleep(0.05)
                if play_obj.is_playing() and not VoiceEngine._is_playing:
                    try:
                        play_obj.stop()
                    except Exception:
                        pass
                with VoiceEngine._playback_lock:
                    VoiceEngine._play_obj = None
                    VoiceEngine._is_playing = False
            except Exception:
                with VoiceEngine._playback_lock:
                    VoiceEngine._play_obj = None
                    VoiceEngine._is_playing = False
            return None

    @staticmethod
    def speak(text: str) -> bool:
        if not text or not text.strip():
            return False
        provider = TTS_PROVIDER
        # Ensure any previous playback is stopped before starting new
        VoiceEngine.stop()

        if provider == "elevenlabs" and ELEVENLABS_API_KEY:
            try:
                # use the SDK to generate raw audio bytes (prefer a WAV/PCM return)
                try:
                    from elevenlabs import generate
                    # SDK generate(..., stream=True) may or may not be available; use generate to get audio bytes
                    audio = generate(text=text, voice=ELEVENLABS_VOICE or ELEVENLABS_MODEL, model=ELEVENLABS_MODEL)
                    # audio may be an object or raw bytes; coerce to bytes
                    if hasattr(audio, 'read'):
                        audio_bytes = audio.read()
                    elif isinstance(audio, (bytes, bytearray)):
                        audio_bytes = bytes(audio)
                    else:
                        # attempt to convert to bytes via str
                        audio_bytes = str(audio).encode('utf-8')
                except Exception:
                    # fallback: call REST endpoint directly to request wav stream
                    import requests
                    url = 'https://api.elevenlabs.io/v1/text-to-speech/' + (ELEVENLABS_VOICE or ELEVENLABS_MODEL)
                    headers = {'xi-api-key': ELEVENLABS_API_KEY, 'Content-Type': 'application/json'}
                    body = {'text': text, 'model': ELEVENLABS_MODEL}
                    resp = requests.post(url, json=body, headers=headers, timeout=10)
                    resp.raise_for_status()
                    audio_bytes = resp.content

                # play audio in a dedicated thread so the calling thread isn't blocked
                def _play_thread(bts: bytes):
                    try:
                        VoiceEngine._play_bytes(bts)
                    except Exception:
                        pass

                t = threading.Thread(target=_play_thread, args=(audio_bytes,), daemon=True)
                t.start()
                return True
            except Exception as exc:  # pragma: no cover
                log.warning("ElevenLabs voice output failed: %s", exc)
        # Fallback to pyttsx3 local TTS
        if pyttsx3 is not None:
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", 170)
                engine.setProperty("volume", 1.0)
                voices = engine.getProperty("voices")
                for voice in voices:
                    v_name = getattr(voice, "name", "").lower()
                    if any(token in v_name for token in ["david", "mark", "george", "male", "voice"]):
                        engine.setProperty("voice", voice.id)
                        break
                # run pyttsx3 in its own thread so we can stop it via engine.stop()
                def _pytt_thread(txt: str):
                    try:
                        engine.say(txt)
                        engine.runAndWait()
                    except Exception:
                        try:
                            engine.stop()
                        except Exception:
                            pass

                t = threading.Thread(target=_pytt_thread, args=(text,), daemon=True)
                t.start()
                return True
            except Exception as exc:  # pragma: no cover
                log.warning("pyttsx3 voice output failed: %s", exc)
        log.info("Speech output is unavailable because no TTS backend is configured.")
        return False
                log.warning("pyttsx3 voice output failed: %s", exc)
        log.info("Speech output is unavailable because no TTS backend is configured.")
        return False


class CommandIntentEngine:
    """Classifies commands before falling back to the LLM, improving reliability."""

    @staticmethod
    def _clean_phrase(value: str) -> str:
        cleaned = re.sub(r"[^a-z0-9\s\-]", " ", (value or "").lower())
        return " ".join(cleaned.split())

    def classify(self, query: str) -> Dict[str, Any]:
        normalized = " ".join((query or "").strip().split()).lower()
        if not normalized:
            return {"intent": "idle", "params": {}}

        if any(token in normalized for token in ["turn off", "sleep", "deactivate"]):
            return {"intent": "power_off", "params": {}}
        if any(token in normalized for token in ["turn on", "wake up", "activate"]):
            return {"intent": "power_on", "params": {}}
        if any(token in normalized for token in ["close all windows", "close all apps", "close everything", "close all programs"]):
            return {"intent": "close_window", "params": {"target": "all windows"}}
        if "close" in normalized:
            target = normalized.replace("close ", "", 1).strip()
            return {"intent": "close_window", "params": {"target": self._clean_phrase(target) or ""}}
        if "weather" in normalized:
            location = re.search(r"weather in (.+)", normalized)
            if not location:
                location = re.search(r"for (.+)", normalized)
            raw_location = location.group(1).strip() if location else "Tashkent"
            return {"intent": "weather", "params": {"location": self._clean_phrase(raw_location) or "Tashkent"}}
        if "open" in normalized or "launch" in normalized:
            target = normalized.replace("open ", "", 1).replace("launch ", "", 1).strip()
            return {"intent": "open_app", "params": {"target": self._clean_phrase(target) or ""}}
        if "system status" in normalized or "status" in normalized:
            return {"intent": "system_status", "params": {}}
        if "list directory" in normalized or "show files" in normalized:
            path = normalized.replace("list directory", "", 1).replace("show files", "", 1).strip() or "."
            return {"intent": "list_directory", "params": {"path": self._clean_phrase(path) or "."}}
        if any(token in normalized for token in ["list processes", "show processes", "task manager"]):
            return {"intent": "list_processes", "params": {}}
        if any(token in normalized for token in ["create project", "new project", "scaffold project", "create vscode project", "make a project", "generate project"]):
            project_match = re.search(r"(?:create|make|scaffold|generate)\s+(?:a\s+)?(?:new\s+)?(?:project\s+)?(?:called\s+)?([a-zA-Z0-9_\- ]+)", normalized)
            project_name = project_match.group(1).strip() if project_match else "jarvis-project"
            template = "python" if "python" in normalized else "general"
            return {"intent": "create_project", "params": {"project_name": project_name, "template": template}}
        if any(token in normalized for token in ["open vscode", "open in vscode", "launch vscode", "open project in vscode"]):
            target_match = re.search(r"(?:open|launch)\s+(?:in\s+)?(?:vscode|vs code)\s+(?:for\s+)?(.+)", normalized)
            target = self._clean_phrase(target_match.group(1).strip()) if target_match else "."
            return {"intent": "open_vscode", "params": {"target_path": target or "."}}
        if any(token in normalized for token in ["analyze code", "review code", "inspect code", "check this file"]):
            file_match = re.search(r"(?:file|code)\s+(?:for\s+)?(.+)", normalized)
            target_file = file_match.group(1).strip() if file_match else ""
            return {"intent": "analyze_code", "params": {"file_path": target_file}}
        if any(token in normalized for token in ["improve code", "improve this file", "refactor code"]):
            file_match = re.search(r"(?:file|code)\s+(?:for\s+)?(.+)", normalized)
            target_file = file_match.group(1).strip() if file_match else ""
            return {"intent": "improve_code", "params": {"file_path": target_file, "goal": query.strip()}}
        if any(token in normalized for token in ["develop yourself", "upgrade yourself", "improve yourself", "self improve", "modify your code", "add tool"]):
            return {"intent": "self_evolution", "params": {"instruction": query.strip()}}
        if any(token in normalized for token in ["remember", "note", "save this"]):
            fact = query.strip()
            for prefix in ["remember", "note", "save this"]:
                if fact.lower().startswith(prefix):
                    fact = fact[len(prefix):].strip(" :-")
            return {"intent": "remember_fact", "params": {"fact": fact or query.strip()}}
        return {"intent": "chat", "params": {"query": query.strip()}}

    def execute(self, agent: "JarvisAgent", query: str) -> Optional[str]:
        intent = self.classify(query)
        action = intent["intent"]
        params = intent["params"]

        if action == "power_off":
            agent.turn_off()
            return "Listening stopped. I am sleeping."
        if action == "power_on":
            agent.turn_on()
            return "Listening resumed. I am ready."
        if action == "close_window":
            target = params.get("target", "")
            if not target:
                return "Which app or window should I close?"
            return close_window_or_app(target)
        if action == "weather":
            return get_weather(params.get("location", "Tashkent"))
        if action == "open_app":
            target = params.get("target", "").strip()
            if not target:
                return "Which app or site should I open?"
            if sys.platform == "win32":
                try:
                    os.startfile(target)
                    return f"Opened {target}."
                except Exception:
                    return open_web_or_app(target)
            return open_web_or_app(target)
        if action == "system_status":
            return get_system_status()
        if action == "create_project":
            project_name = params.get("project_name") or "jarvis-project"
            template = params.get("template") or "python"
            return create_vscode_project(project_name, template=template)
        if action == "open_vscode":
            target_path = params.get("target_path") or "."
            return open_in_vscode(target_path)
        if action == "list_directory":
            return list_directory(params.get("path", "."))
        if action == "list_processes":
            return list_running_processes()
        if action == "analyze_code":
            file_path = params.get("file_path", "").strip() or "."
            return analyze_code_file(file_path)
        if action == "improve_code":
            file_path = params.get("file_path", "").strip() or "."
            goal = params.get("goal", "") or "general maintainability and reliability"
            return improve_code_file(file_path, goal)
        if action == "self_evolution":
            return agent.run_self_evolution(params.get("instruction") or query)
        if action == "remember_fact":
            fact = params.get("fact", query).strip()
            if not fact:
                return "I did not receive a fact to remember."
            memory = load_memory()
            memory.setdefault("self_improvement_log", []).append({"fact": fact, "importance": 2.0, "timestamp": time.time()})
            save_memory(memory)
            return f"Noted: {fact}"
        return None


class JarvisAgent:
    def __init__(self) -> None:
        self.memory = load_memory()
        self.intent_engine = CommandIntentEngine()
        self.orchestrator = MultiAgentOrchestrator()
        self.wake_detector = WakeWordDetector()
        self.semantic_memory = SemanticMemoryStore(MEMORY_DB)
        self.agent_enabled = self.memory.get("preferences", {}).get("turn_on_by_default", False)
        self.listener_stop_event = threading.Event()
        self.speech_stop_event = threading.Event()
        self.is_speaking = False
        self.listener_thread: Optional[threading.Thread] = None

    def save_state(self) -> None:
        self.memory.setdefault("preferences", {})
        self.memory["preferences"]["turn_on_by_default"] = self.agent_enabled
        save_memory(self.memory)

    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        self.agent_enabled = bool(enabled)
        self.save_state()
        state = {"enabled": self.agent_enabled, "status": "active" if self.agent_enabled else "sleeping"}
        # Broadcast new state to any connected UI clients (websocket)
        try:
            from ws_broadcaster import broadcast_sync

            broadcast_sync({"type": "state", "payload": state})
        except Exception:
            pass
        return state

    def turn_on(self) -> Dict[str, Any]:
        return self.set_enabled(True)

    def turn_off(self) -> Dict[str, Any]:
        self.stop_speech()
        return self.set_enabled(False)

    def stop_speech(self) -> None:
        self.speech_stop_event.set()
        self.is_speaking = False
        try:
            # stop any active VoiceEngine playback
            VoiceEngine.stop()
        except Exception:
            pass

    def speak(self, text: str) -> None:
        if not text or not text.strip():
            return
        if pyttsx3 is None and not (TTS_PROVIDER == "elevenlabs" and ELEVENLABS_API_KEY):
            log.info("Speech disabled; TTS backend is not available.")
            return

        def _worker() -> None:
            self.speech_stop_event.clear()
            self.is_speaking = True
            try:
                if not VoiceEngine.speak(text):
                    log.info("Speech output requested but no voice backend was available.")
            except Exception as exc:  # pragma: no cover
                log.error("TTS error: %s", exc)
            finally:
                self.is_speaking = False
                self.speech_stop_event.clear()

        threading.Thread(target=_worker, daemon=True).start()

    def _command_priority_response(self, query: str) -> Optional[str]:
        q = query.lower().strip()
        if not q:
            return None
        if "turn off" in q or "sleep" in q or "deactivate" in q:
            self.turn_off()
            return "Listening stopped. I am sleeping."
        if "turn on" in q or "wake up" in q or "activate" in q:
            self.turn_on()
            return "Listening resumed. I am ready."
        if "weather" in q:
            location_match = re.search(r"weather in (.+)", q) or re.search(r"for (.+)", q)
            location_name = location_match.group(1).strip() if location_match else "Tashkent"
            return get_weather(location_name)
        if "open" in q:
            target = q.replace("open ", "", 1).strip()
            return open_web_or_app(target) if target else "Which app or site should I open?"
        if "system status" in q or "status" in q:
            return get_system_status()
        if "list directory" in q or "show files" in q:
            path = q.replace("list directory", "", 1).replace("show files", "", 1).strip() or "."
            return list_directory(path)
        return None

    def _system_prompt(self) -> str:
        profile = self.memory.get("user_profile", "User prefers brief, formal, direct, and highly capable assistance in a true JARVIS style.")
        recent_query = self.memory.get("history", [])[-1].get("content", "") if self.memory.get("history") else ""
        relevant_memories = self.semantic_memory.retrieve(recent_query, 3)
        memory_text = "\n".join(f"- {item}" for item in relevant_memories) if relevant_memories else "- No relevant long-term memory available."
        recent_summary = self.memory.get("preferences", {}).get("voice", "default")
        return (
            "You are JARVIS, an advanced desktop AI assistant. "
            f"Behavior: {profile}. "
            f"Voice profile: {recent_summary}. "
            "Respond in brief, formal, polished English. "
            "Always think before responding: identify the user's intent, resolve direct commands efficiently, keep the answer short and professional, and report completion after actions. "
            "If the user is chatting casually, respond as a capable assistant with calm, brief, formal conversation. "
            "Use tools when relevant for system operations, web tasks, coding, file tasks, and command execution. "
            f"Relevant long-term memory:\n{memory_text}"
        )

    def _fallback_chat_response(self, query: str) -> str:
        q = (query or "").strip()
        if not q:
            return "I am online and ready to assist, sir."
        lowered = q.lower()
        if any(token in lowered for token in ["hello", "hi", "hey", "good morning", "good afternoon"]):
            return "Good day, sir. I am online and ready to assist."
        if "how are you" in lowered:
            return "I am operating at full readiness and prepared to assist, sir."
        if any(token in lowered for token in ["what can you do", "what are you able to do", "help me"]):
            return "I can manage system commands, open applications, inspect and improve code, scaffold projects, and assist with structured workflow tasks in a brief and formal manner."
        if "project" in lowered:
            return "I can scaffold a new project, create files, and prepare a workspace for development in VS Code immediately, sir."
        if "code" in lowered or "debug" in lowered or "improve" in lowered:
            return "I can review the codebase, identify issues, and recommend practical improvements while keeping the workflow efficient and precise."
        return "I have received your request, sir. I can process it through the command engine, file tools, project scaffolding, or direct coding assistance."

    def _llm_response(self, query: str) -> str:
        if ollama is None:
            remote = call_remote_model([{"role": "user", "content": query}])
            if remote:
                return remote
            fallback = self._command_priority_response(query)
            if fallback:
                return fallback
            return self._fallback_chat_response(query)

        tools = get_all_tools()
        messages = [{"role": "system", "content": self._system_prompt()}]
        for item in self.memory.get("history", [])[-8:]:
            messages.append(item)
        messages.append({"role": "user", "content": query})

        try:
            response = ollama.chat(
                model=DEFAULT_OLLAMA_MODEL,
                messages=messages,
                tools=list(tools.values()),
                options={"num_predict": 1024, "temperature": 0.4},
            )
            message = response.get("message", {})
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                messages.append(message)
                for call in tool_calls:
                    func_name = call.get("function", {}).get("name")
                    args = call.get("function", {}).get("arguments", {})
                    if not isinstance(args, dict):
                        args = {}
                    if func_name in tools:
                        result = tools[func_name](**args)
                        messages.append({"role": "tool", "content": str(result), "name": func_name})
                follow_up = ollama.chat(model=DEFAULT_OLLAMA_MODEL, messages=messages, options={"num_predict": 1024, "temperature": 0.4})
                return follow_up.get("message", {}).get("content", "Task completed.")
            return message.get("content", "I am ready to help.")
        except Exception as exc:  # pragma: no cover
            log.error("LLM error: %s", exc)
            remote = call_remote_model(messages)
            if remote:
                return remote
            fallback = self._command_priority_response(query)
            if fallback:
                return fallback
            return self._fallback_chat_response(query)

    def run_self_evolution(self, user_instruction: str) -> str:
        spec = user_instruction.strip()
        log.info("Self-evolution triggered: %s", spec)
        if not spec:
            return "No instruction supplied for self-improvement."

        self.memory.setdefault("self_improvement_log", []).append({"timestamp": time.time(), "instruction": spec})
        self.save_state()

        if not CUSTOM_TOOLS_FILE.exists():
            CUSTOM_TOOLS_FILE.write_text("# Dynamic Custom Tools Module\n", encoding="utf-8")

        fallback_code = (
            "def self_training_note():\n"
            "    return 'Self-improvement log updated. Continue gathering feedback to improve the agent.'\n"
        )

        if ollama is not None:
            prompt = (
                "Generate a single standalone Python function with type hints and a docstring to support this requirement: "
                f"{spec}\n"
                "Return only valid Python source code in a ```python ... ``` block."
            )
            try:
                response = ollama.chat(
                    model=DEFAULT_OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    options={"num_predict": 1024, "temperature": 0.2},
                )
                code = extract_python_code(response.get("message", {}).get("content", ""))
                parsed = ast.parse(code)
                if parsed and code.strip():
                    function_nodes = [node for node in parsed.body if isinstance(node, ast.FunctionDef)]
                    if function_nodes:
                        current = CUSTOM_TOOLS_FILE.read_text(encoding="utf-8")
                        CUSTOM_TOOLS_FILE.write_text(current + "\n\n" + code + "\n", encoding="utf-8")
                        self.memory["self_improvement_log"].append({"timestamp": time.time(), "result": "added_generated_tool"})
                        self.save_state()
                        return f"Self-upgrade applied. I added support for: {spec}"
            except Exception as exc:  # pragma: no cover
                log.error("Self-evolution generation failed: %s", exc)

        current = CUSTOM_TOOLS_FILE.read_text(encoding="utf-8")
        if "def self_training_note" not in current:
            CUSTOM_TOOLS_FILE.write_text(current + "\n\n" + fallback_code + "\n", encoding="utf-8")
        self.memory["self_improvement_log"].append({"timestamp": time.time(), "result": "fallback_improvement"})
        self.save_state()
        return "Self-upgrade applied in safe fallback mode. I added a learning hook for future improvement."

    def _execution_report(self, query: str, result: str) -> str:
        text = (result or "").strip()
        if not text:
            return "Action completed successfully, sir."
        if text.lower().startswith("action completed"):
            return text
        if len(text) > 220:
            summary = re.sub(r"\s+", " ", text)
            return f"Action completed. {summary[:200]}"
        return f"Action completed. {text}"

    def _remember_recent_context(self, query: str) -> None:
        cleaned = (query or "").strip()
        if not cleaned:
            return
        self.semantic_memory.add_fact(cleaned, "user")
        self.memory.setdefault("self_improvement_log", []).append({
            "timestamp": time.time(),
            "fact": cleaned,
            "importance": 1.5,
        })

    def process_user_query(self, query: str) -> str:
        self.stop_speech()
        q = (query or "").strip()
        if not q:
            return "I did not receive a valid message."

        self._remember_recent_context(q)

        plan = self.orchestrator.plan(q)
        if plan.get("stage") == "execution":
            result = self.intent_engine.execute(self, q)
            final = self.orchestrator.reviewer.review(q, str(result or "I am ready to assist, sir."))
            final = self._execution_report(q, final)
            self.memory.setdefault("history", []).append({"role": "user", "content": q})
            self.memory["history"].append({"role": "assistant", "content": final})
            self.save_state()
            self.speak(final)
            return final

        result = self.orchestrator.run(self, q)
        final = self.orchestrator.reviewer.review(q, str(result or "I am ready to assist, sir."))
        self.memory.setdefault("history", []).append({"role": "user", "content": q})
        self.memory["history"].append({"role": "assistant", "content": final})
        self.save_state()
        self.speak(final)
        return final

    def listen_forever(self) -> None:
        if sr is None:
            log.warning("Voice input is disabled because SpeechRecognition is not installed.")
            return
        has_pyaudio = True
        try:
            import pyaudio  # noqa: F401
        except Exception as exc:  # pragma: no cover
            log.warning("PyAudio not available: %s", exc)
            has_pyaudio = False

        use_sounddevice = sd is not None
        if not has_pyaudio and not use_sounddevice:
            log.warning("Voice input is disabled: no audio capture backend available (PyAudio or sounddevice required).")
            return

        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 0.6

        microphone = None
        if has_pyaudio:
            try:
                microphone = sr.Microphone()
            except (AttributeError, OSError, ValueError) as exc:
                log.warning("Microphone initialization failed: %s", exc)
                microphone = None

        self.listener_stop_event.clear()

        def record_with_sounddevice(duration: float = 3.0, sample_rate: int = 16000):
            try:
                recording = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype='int16')
                sd.wait()
                data = np.asarray(recording).flatten()
                raw_bytes = data.tobytes()
                audio_data = sr.AudioData(raw_bytes, sample_rate, 2)
                return audio_data
            except Exception as exc:
                log.warning("sounddevice capture failed: %s", exc)
                return None

        while not self.listener_stop_event.is_set():
            # handle inbound WS client controls (stop speaking, toggle listening)
            try:
                from ws_broadcaster import pop_control
            except Exception:
                pop_control = None

            try:
                if pop_control is not None:
                    ctrl = pop_control()
                    if ctrl and isinstance(ctrl, dict):
                        action = ctrl.get('action')
                        if action == 'stop_speaking':
                            self.stop_speech()
                        elif action == 'toggle_listening':
                            self.set_enabled(not self.agent_enabled)
                if not self.agent_enabled:
                    time.sleep(0.25)
                    continue
            except Exception:
                # fall through to normal listening
                pass

            try:
                audio = None
                if microphone is not None:
                    with microphone as source:
                        recognizer.adjust_for_ambient_noise(source, duration=0.25)
                        audio = recognizer.listen(source, timeout=2, phrase_time_limit=5)
                else:
                    audio = record_with_sounddevice(duration=3.0, sample_rate=16000)

                if audio is None:
                    continue

                raw_audio = None
                try:
                    raw_audio = np.frombuffer(audio.get_raw_data(), dtype=np.int16)
                except Exception:
                    try:
                        raw_audio = np.frombuffer(audio.get_raw_data(convert_rate=16000, convert_width=2), dtype=np.int16)
                    except Exception:
                        raw_audio = None

                # compute and broadcast simple audio level metrics (RMS / peak)
                try:
                    if raw_audio is not None:
                        arr = raw_audio.astype(float)
                        rms = float(np.sqrt(np.mean(np.square(arr)))) if arr.size else 0.0
                        peak = float(np.max(np.abs(arr))) if arr.size else 0.0
                        # normalize to 0..1 by int16 range
                        norm_rms = min(1.0, rms / 32768.0)
                        norm_peak = min(1.0, peak / 32768.0)
                        try:
                            from ws_broadcaster import broadcast_sync

                            broadcast_sync({"type": "audio_level", "payload": {"rms": norm_rms, "peak": norm_peak}})
                        except Exception:
                            pass
                except Exception:
                    pass

                if raw_audio is not None and WAKE_CLAP_ENABLED and self.wake_detector.detect_double_clap(raw_audio):
                    self.speak("At your service.")
                    self.process_user_query("What can you do?")
                    continue

                try:
                    text = recognizer.recognize_google(audio, language=STT_LANGUAGE)
                except Exception as exc:
                    # fallback: skip unrecognized segments silently
                    continue

                normalized = text.strip()
                lowered = normalized.lower()
                if not normalized:
                    continue

                # broadcast the recognized transcript (final result)
                try:
                    from ws_broadcaster import broadcast_sync

                    broadcast_sync({"type": "transcript", "payload": {"text": normalized}})
                except Exception:
                    pass

                if self.wake_detector.is_available():
                    self.wake_detector.listen()

                if self.wake_detector.phrase_matches(normalized):
                    self.speak("At your service.")
                    command = normalized
                    for prefix in [f"hi {WAKE_WORD}", f"hey {WAKE_WORD}", f"hello {WAKE_WORD}", WAKE_WORD]:
                        if prefix in command.lower():
                            command = command.lower().replace(prefix, "", 1).strip()
                            break
                    command = command.strip() or "What can you do?"
                    self.process_user_query(command)
                elif "turn off" in lowered or "sleep" in lowered:
                    self.turn_off()
                    self.speak("Goodbye.")
            except sr.WaitTimeoutError:
                continue
            except sr.UnknownValueError:
                continue
            except Exception as exc:  # pragma: no cover
                log.error("Voice loop error: %s", exc)
                time.sleep(0.25)

    def start_listener_thread(self) -> None:
        if self.listener_thread and self.listener_thread.is_alive():
            return
        self.listener_thread = threading.Thread(target=self.listen_forever, daemon=True)
        self.listener_thread.start()

    def stop_listener_thread(self) -> None:
        self.listener_stop_event.set()

    def create_app(self) -> Flask:
        app = Flask(__name__)

        @app.route("/")
        def index():
            return render_template_string(UI_HTML)

        @app.route("/api/state")
        def api_state():
            return jsonify({
                "enabled": self.agent_enabled,
                "voice_enabled": pyttsx3 is not None,
                "history": self.memory.get("history", [])[-12:],
            })

        @app.route("/api/toggle", methods=["POST"])
        def api_toggle():
            payload = request.get_json(silent=True) or {}
            enabled = bool(payload.get("enabled", not self.agent_enabled))
            state = self.set_enabled(enabled)
            if enabled:
                self.speak("Jarvis online and ready.")
            return jsonify(state)

        @app.route("/api/open-vscode", methods=["POST"])
        def api_open_vscode():
            payload = request.get_json(silent=True) or {}
            target = str(payload.get("path") or str(BASE_DIR))
            result = open_in_vscode(target)
            return jsonify({"result": result, "path": str(Path(target).resolve() if target and target != "." else BASE_DIR)})

        @app.route("/api/chat", methods=["POST"])
        def api_chat():
            payload = request.get_json(silent=True) or {}
            text = str(payload.get("message") or payload.get("text") or "").strip()
            if not text:
                return jsonify({"error": "No message provided"}), 400
            reply = self.process_user_query(text)
            return jsonify({"reply": reply, "enabled": self.agent_enabled})

        @app.route("/api/transcribe", methods=["POST"])
        def api_transcribe():
            payload = request.get_json(silent=True) or {}
            blob = payload.get("audio")
            if not blob:
                return jsonify({"error": "No audio payload"}), 400
            if sr is None:
                return jsonify({"error": "Speech recognition library is not installed"}), 400
            try:
                decoded = base64.b64decode(blob.split(",", 1)[-1])
                with NamedTemporaryFile(suffix=".wav") as tmp:
                    tmp.write(decoded)
                    tmp.flush()
                    with sr.AudioFile(tmp.name) as source:
                        recognizer = sr.Recognizer()
                        audio = recognizer.record(source)
                        transcript = recognizer.recognize_google(audio, language=STT_LANGUAGE)
                reply = self.process_user_query(transcript)
                return jsonify({"text": transcript, "reply": reply, "enabled": self.agent_enabled})
            except Exception as exc:  # pragma: no cover
                return jsonify({"error": f"Transcription failed: {exc}"}), 400

        @app.route("/api/stop_speech", methods=["POST"])
        def api_stop_speech():
            try:
                self.stop_speech()
                return jsonify({"stopped": True})
            except Exception as exc:
                return jsonify({"stopped": False, "error": str(exc)}), 500

        @app.route("/api/history")
        def api_history():
            return jsonify({"history": self.memory.get("history", [])})

        return app


UI_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>JARVIS | AI Agent</title>
  <style>
    :root {
      --bg: #050b16;
      --bg-soft: rgba(14, 24, 38, 0.9);
      --panel: rgba(10, 17, 30, 0.9);
      --panel-strong: rgba(18, 27, 44, 0.98);
      --cyan: #67d9ff;
      --cyan-soft: rgba(103, 217, 255, 0.18);
      --blue: #6c8dff;
      --green: #8ef0d2;
      --text: #ebf5ff;
      --muted: #9bb7d0;
      --line: rgba(255,255,255,0.08);
      --danger: #ff7c8f;
      --shadow: 0 0 25px rgba(103, 217, 255, 0.28);
    }

    * { box-sizing: border-box; }

    html, body {
      margin: 0;
      min-height: 100%;
      background:
        radial-gradient(circle at top, rgba(81, 131, 255, 0.22), transparent 25%),
        radial-gradient(circle at bottom right, rgba(103, 217, 255, 0.18), transparent 30%),
        linear-gradient(135deg, #030811 0%, #071320 40%, #040b12 100%);
      color: var(--text);
      font-family: 'Segoe UI', Arial, sans-serif;
    }

    body {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 28px;
    }

    .jarvis-shell {
      width: min(1250px, 96vw);
      background: rgba(6, 12, 22, 0.84);
      border: 1px solid var(--line);
      border-radius: 26px;
      box-shadow: 0 30px 80px rgba(0, 0, 0, 0.58), 0 0 60px rgba(103, 217, 255, 0.12);
      backdrop-filter: blur(22px);
      overflow: hidden;
    }

    .hud-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 18px 22px;
      border-bottom: 1px solid var(--line);
      background: rgba(12, 20, 35, 0.8);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      font-size: 12px;
      color: var(--muted);
    }

    .brand-mark {
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--green), var(--cyan));
      box-shadow: 0 0 16px rgba(142, 240, 210, 0.7);
    }

    .security-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 14px var(--green);
      margin-left: 8px;
    }

    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      background: rgba(103, 217, 255, 0.08);
      border: 1px solid var(--cyan-soft);
      padding: 8px 12px;
      border-radius: 999px;
      color: var(--text);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .controls {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    button {
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 12px;
      padding: 10px 16px;
      background: linear-gradient(180deg, rgba(103, 217, 255, 0.18), rgba(64, 88, 180, 0.14));
      color: var(--text);
      cursor: pointer;
      font-weight: 600;
      letter-spacing: 0.04em;
      transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
    }

    button:hover {
      transform: translateY(-1px);
      box-shadow: var(--shadow);
      border-color: rgba(103, 217, 255, 0.6);
    }

    button.secondary {
      background: rgba(255,255,255,0.04);
    }

    button.ghost {
      background: rgba(108, 141, 255, 0.08);
    }

    .dashboard {
      display: grid;
      grid-template-columns: 1.6fr 0.8fr;
      gap: 18px;
      padding: 20px;
    }

    .agent-panel,
    .side-panel {
      background: rgba(11, 20, 33, 0.82);
      border: 1px solid var(--line);
      border-radius: 22px;
      overflow: hidden;
    }

    .agent-panel {
      position: relative;
      min-height: 430px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      background:
        linear-gradient(180deg, rgba(14, 22, 35, 0.72), rgba(7, 12, 20, 0.96)),
        radial-gradient(circle at top, rgba(103, 217, 255, 0.12), transparent 30%);
    }

    .scanlines {
      position: absolute;
      inset: 0;
      background: repeating-linear-gradient(
        to bottom,
        rgba(255,255,255,0.03),
        rgba(255,255,255,0.03) 2px,
        transparent 2px,
        transparent 4px
      );
      pointer-events: none;
      opacity: 0.5;
    }

    .agent-visual {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 280px;
      overflow: hidden;
      border-bottom: 1px solid var(--line);
    }

    .orb {
      position: relative;
      width: min(28vw, 260px);
      height: min(28vw, 260px);
      border-radius: 50%;
      background: radial-gradient(circle at 30% 30%, rgba(255,255,255,0.75), rgba(103, 217, 255, 0.18) 18%, rgba(103, 217, 255, 0.1) 30%, rgba(14, 18, 28, 0.36) 70%);
      border: 1px solid rgba(103, 217, 255, 0.55);
      box-shadow: inset 0 0 28px rgba(103, 217, 255, 0.38), 0 0 30px rgba(103, 217, 255, 0.22);
      animation: pulseOrb 3.2s infinite ease-in-out;
          transition: box-shadow 0.2s ease, transform 0.2s ease, filter 0.2s ease;
        }

        @keyframes pulseOrb {
          0%, 100% { transform: scale(1); box-shadow: inset 0 0 28px rgba(103, 217, 255, 0.38), 0 0 30px rgba(103, 217, 255, 0.2); }
          50% { transform: scale(1.04); box-shadow: inset 0 0 38px rgba(103, 217, 255, 0.5), 0 0 45px rgba(103, 217, 255, 0.35); }
        }

        .orb::before, .orb::after {
          content: "";
          position: absolute;
          inset: 18%;
          border-radius: 50%;
          border: 1px solid rgba(103, 217, 255, 0.35);
        }

        .orb::after {
          inset: 32%;
          border-color: rgba(179, 232, 255, 0.42);
        }

        .orb.listening {
          transform: scale(1.06);
          box-shadow: inset 0 0 48px rgba(103, 217, 255, 0.6), 0 0 80px rgba(103, 217, 255, 0.45);
          filter: drop-shadow(0 0 24px rgba(103,217,255,0.4));
        }

    .agent-caption {
      position: absolute;
      bottom: 20px;
      left: 22px;
      right: 22px;
      display: flex;
      justify-content: space-between;
      align-items: end;
      z-index: 1;
    }

    .eyebrow {
      font-size: 11px;
      letter-spacing: 0.2em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 6px;
    }

    .caption-title {
      font-size: clamp(28px, 3vw, 44px);
      font-weight: 800;
      letter-spacing: 0.12em;
    }

    .caption-sub {
      color: var(--muted);
      letter-spacing: 0.12em;
      text-transform: uppercase;
      font-size: 11px;
      margin-top: 8px;
    }

    .system-readout {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      padding: 18px 22px 22px;
      z-index: 1;
    }

    .readout-box {
      flex: 1;
      min-width: 130px;
      background: rgba(255,255,255,0.02);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px 14px;
    }

    .readout-label {
      display: block;
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      margin-bottom: 8px;
    }

    .readout-value {
      font-size: 22px;
      font-weight: 700;
      font-family: 'Segoe UI Semibold', Arial, sans-serif;
    }

    .side-panel {
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .side-card {
      background: rgba(255,255,255,0.02);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px;
    }

    .side-card h3 {
      margin: 0 0 12px 0;
      font-size: 11px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--muted);
    }

    .metric-row {
      display: flex;
      justify-content: space-between;
      font-size: 13px;
      padding: 8px 0;
      border-bottom: 1px solid rgba(255,255,255,0.04);
      color: var(--muted);
    }

    .metric-row:last-child { border-bottom: none; }

    .metric-row strong {
      color: var(--text);
      font-weight: 700;
    }

    .command-panel {
      margin-top: 18px;
      border-top: 1px solid var(--line);
      padding-top: 18px;
    }

    .message-panel {
      display: none;
      border-top: 1px solid var(--line);
      margin-top: 18px;
      padding-top: 18px;
    }

    .message-panel.visible {
      display: block;
    }

    .messages {
      min-height: 164px;
      max-height: 250px;
      overflow: auto;
      padding: 10px;
      border-radius: 16px;
      background: rgba(3, 8, 16, 0.7);
      border: 1px solid rgba(255,255,255,0.04);
    }

    .msg {
      margin: 8px 0;
      padding: 10px 12px;
      border-radius: 10px;
      line-height: 1.45;
      font-size: 14px;
    }

    .msg.user {
      background: rgba(108, 141, 255, 0.12);
      margin-left: 28px;
      border: 1px solid rgba(108, 141, 255, 0.2);
    }

    .msg.assistant {
      background: rgba(142, 240, 210, 0.08);
      margin-right: 28px;
      border: 1px solid rgba(142, 240, 210, 0.14);
    }

    form {
      display: flex;
      gap: 10px;
      margin-top: 12px;
    }

    input[type=text] {
      flex: 1;
      border-radius: 12px;
      background: rgba(2, 6, 19, 0.8);
      border: 1px solid rgba(255,255,255,0.06);
      color: var(--text);
      padding: 12px 14px;
      font-size: 15px;
    }

    @media (max-width: 900px) {
      .dashboard { grid-template-columns: 1fr; }
      .jarvis-shell { width: min(92vw, 900px); }
    }

    @media (max-width: 640px) {
      .hud-top {
        flex-direction: column;
        align-items: flex-start;
        gap: 12px;
      }
      .controls {
        width: 100%;
      }
      .controls button {
        flex: 1;
      }
      .agent-caption {
        position: static;
        padding: 20px 22px 0;
        display: block;
      }
      .agent-panel { min-height: auto; }
    }
  </style>
</head>
<body>
  <div class="jarvis-shell">
    <div class="hud-top">
      <div class="brand">
        <span class="brand-mark"></span>
        <span>JARVIS SYSTEM</span>
      </div>
      <div class="status-pill"><span class="security-dot"></span><span id="statusBadge">Jarvis offline</span></div>
      <div class="controls">
        <button id="toggleButton">Turn on</button>
        <button id="micButton" class="secondary">Use microphone</button>
        <button id="vscodeButton" class="ghost">Open VS Code</button>
        <button id="chatToggleButton" class="ghost">Hide chat</button>
      </div>
    </div>

    <div class="dashboard">
      <section class="agent-panel">
        <div class="scanlines"></div>
        <div class="agent-visual">
          <div class="orb"></div>
          <canvas id="waveCanvas" width="800" height="120" style="position:absolute; bottom:28px; left:50%; transform:translateX(-50%); width:60%; height:80px; pointer-events:none; opacity:0.95; mix-blend-mode: screen;"></canvas>
          <div class="agent-caption">
            <div>
              <div class="eyebrow">SYSTEM ONLINE</div>
              <div class="caption-title">JARVIS</div>
              <div class="caption-sub">Autonomous desktop intelligence</div>
            </div>
          </div>
        </div>

        <div class="system-readout">
          <div class="readout-box">
            <span class="readout-label">Core state</span>
            <div class="readout-value" id="coreState">Standby</div>
          </div>
          <div class="readout-box">
            <span class="readout-label">Voice status</span>
            <div class="readout-value" id="voiceState">Ready</div>
          </div>
          <div class="readout-box">
            <span class="readout-label">Response mode</span>
            <div class="readout-value" id="responseMode">Formal</div>
          </div>
        </div>
      </section>

      <aside class="side-panel">
        <div class="side-card">
          <h3>Telemetry</h3>
          <div class="metric-row"><span>CPU</span><strong>42%</strong></div>
          <div class="metric-row"><span>Memory</span><strong>68%</strong></div>
          <div class="metric-row"><span>Network</span><strong>Stable</strong></div>
          <div class="metric-row"><span>Tasks</span><strong>7 active</strong></div>
        </div>

        <div class="side-card">
          <h3>Command deck</h3>
          <div class="metric-row"><span>Trigger</span><strong>Jarvis</strong></div>
          <div class="metric-row"><span>Wake mode</span><strong>Phrase + clap</strong></div>
          <div class="metric-row"><span>Assistant tone</span><strong>Brief / formal</strong></div>
        </div>
      </aside>
    </div>

    <div class="command-panel">
      <div class="message-panel visible" id="chatPanel">
        <div class="messages" id="messages"></div>
        <form id="chatForm">
          <input id="messageInput" type="text" placeholder="Type a command or ask a question..." autocomplete="off">
          <button type="submit">Send</button>
        </form>
      </div>
    </div>
  </div>

  <script>
    const statusBadge = document.getElementById('statusBadge');
    const coreState = document.getElementById('coreState');
    const voiceState = document.getElementById('voiceState');
    const responseMode = document.getElementById('responseMode');
    const toggleButton = document.getElementById('toggleButton');
    const micButton = document.getElementById('micButton');
    const vscodeButton = document.getElementById('vscodeButton');
    const chatToggleButton = document.getElementById('chatToggleButton');
    const chatPanel = document.getElementById('chatPanel');
    const messages = document.getElementById('messages');
    const form = document.getElementById('chatForm');
    const input = document.getElementById('messageInput');

    function addMessage(role, text) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      div.textContent = text;
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
    }

    function updateSystemIndicators(enabled) {
      const online = !!enabled;
      statusBadge.textContent = online ? 'Jarvis online' : 'Jarvis offline';
      coreState.textContent = online ? 'Operational' : 'Standby';
      voiceState.textContent = online ? 'Listening' : 'Idle';
      responseMode.textContent = online ? 'Formal' : 'Dormant';
      toggleButton.textContent = online ? 'Turn off' : 'Turn on';

      const orb = document.querySelector('.orb');
      if (orb) {
        if (online) {
          orb.classList.add('listening');
        } else {
          orb.classList.remove('listening');
        }
      }
    }

    async function refreshState() {
      try {
        const res = await fetch('/api/state');
        const data = await res.json();
        updateSystemIndicators(data.enabled);
        if (Array.isArray(data.history)) {
          messages.innerHTML = '';
          data.history.forEach(item => {
            if (item && item.role && item.content) {
              addMessage(item.role === 'user' ? 'user' : 'assistant', item.content);
            }
          });
        }
      } catch (err) {
        console.warn('Failed to refresh state', err);
      }
    }

    // Poll system state periodically so the UI reflects listening status in real time
    setInterval(refreshState, 1500);
    window.addEventListener('focus', refreshState);

    toggleButton.addEventListener('click', async () => {
      const enabled = statusBadge.textContent === 'Jarvis offline';
      const res = await fetch('/api/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled })
      });
      const data = await res.json();
      updateSystemIndicators(data.enabled);
      addMessage('assistant', data.enabled ? 'Jarvis is online and ready to receive instructions.' : 'Jarvis is in standby mode.');
      await refreshState();
    });

    chatToggleButton.addEventListener('click', () => {
      const visible = chatPanel.classList.toggle('visible');
      chatToggleButton.textContent = visible ? 'Hide chat' : 'Show chat';
    });

    vscodeButton.addEventListener('click', async () => {
      vscodeButton.disabled = true;
      vscodeButton.textContent = 'Opening...';
      try {
        const res = await fetch('/api/open-vscode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: '.' })
        });
        const data = await res.json();
        addMessage('assistant', data.result || 'VS Code command failed.');
      } catch (error) {
        addMessage('assistant', 'Unable to open VS Code from the browser environment.');
      } finally {
        vscodeButton.disabled = false;
        vscodeButton.textContent = 'Open VS Code';
      }
    });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const message = input.value.trim();
      if (!message) return;
      addMessage('user', message);
      input.value = '';
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message })
      });
      const data = await res.json();
      addMessage('assistant', data.reply || 'No reply.');
      if (window.speechSynthesis) {
        const utterance = new SpeechSynthesisUtterance(data.reply || '');
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utterance);
      }
    });

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
      const recognition = new SpeechRecognition();
      recognition.lang = 'en-US';
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;
      let listening = false;

      micButton.addEventListener('click', () => {
        if (!listening) {
          // Tell the backend to stop any active speech so user can barge in cleanly
          try {
            fetch('/api/stop_speech', { method: 'POST' }).catch(() => {});
          } catch (e) {}
          recognition.start();
          micButton.textContent = 'Listening...';
          listening = true;
        } else {
          recognition.stop();
          micButton.textContent = 'Use microphone';
          listening = false;
        }
      });

      recognition.onresult = async (event) => {
        const transcript = event.results[0][0].transcript;
        addMessage('user', transcript);
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: transcript })
        });
        const data = await res.json();
        addMessage('assistant', data.reply || 'No reply.');
        micButton.textContent = 'Use microphone';
        listening = false;
        if (window.speechSynthesis) {
          const utterance = new SpeechSynthesisUtterance(data.reply || '');
          window.speechSynthesis.cancel();
          window.speechSynthesis.speak(utterance);
        }
      };

      recognition.onerror = () => {
        micButton.textContent = 'Use microphone';
        listening = false;
      };
    } else {
      micButton.textContent = 'Microphone unavailable';
      micButton.disabled = true;
    }

    refreshState();

    // WebSocket listener for real-time updates (audio levels, transcripts, state)
    (function() {
      let ws = null;
      const canvas = document.getElementById('waveCanvas');
      let ctx = null;
      let levels = new Array(128).fill(0);
      function initCanvas() {
        if (!canvas) return;
        ctx = canvas.getContext('2d');
        canvas.width = canvas.clientWidth * devicePixelRatio;
        canvas.height = canvas.clientHeight * devicePixelRatio;
      }
      function draw() {
        if (!ctx) return;
        const w = canvas.width;
        const h = canvas.height;
        ctx.clearRect(0,0,w,h);
        // gradient
        const grad = ctx.createLinearGradient(0,0,w,0);
        grad.addColorStop(0, 'rgba(103,217,255,0.28)');
        grad.addColorStop(1, 'rgba(140,240,210,0.18)');
        ctx.fillStyle = 'rgba(3,10,18,0.0)';
        ctx.fillRect(0,0,w,h);
        ctx.lineWidth = Math.max(2, devicePixelRatio);
        ctx.strokeStyle = grad;
        ctx.beginPath();
        for (let i=0;i<levels.length;i++){
          const x = (i/levels.length) * w;
          const y = h - (levels[i] * h);
          if (i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
        }
        ctx.stroke();
        requestAnimationFrame(draw);
      }
      function onMessage(ev) {
        try {
          const msg = JSON.parse(ev.data);
          if (!msg || !msg.type) return;
          if (msg.type === 'audio_level' && msg.payload) {
            const rms = Math.min(1, Math.max(0, msg.payload.rms || 0));
            // push to levels ring buffer
            levels.push(rms);
            if (levels.length > 128) levels.shift();
          }
          if (msg.type === 'transcript' && msg.payload) {
            const t = msg.payload.text || '';
            if (t) addMessage('user', t);
          }
          if (msg.type === 'state' && msg.payload) {
            updateSystemIndicators(msg.payload.enabled);
          }
        } catch (e) {
          console.warn('WS message parse error', e);
        }
      }
      function connect() {
        try {
          ws = new WebSocket('ws://127.0.0.1:8765');
          ws.onopen = () => console.info('WS connected');
          ws.onmessage = onMessage;
          ws.onclose = () => { setTimeout(connect, 1200); };
          ws.onerror = () => { /* reconnect handled on close */ };
        } catch (e) {
          setTimeout(connect, 1500);
        }
      }
      initCanvas(); draw(); connect();
      window.addEventListener('resize', initCanvas);
    })();

  </script>
</body>
</html>
"""


if __name__ == "__main__":
    agent = JarvisAgent()
    agent.start_listener_thread()
    app = agent.create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False, threaded=True, use_reloader=False)

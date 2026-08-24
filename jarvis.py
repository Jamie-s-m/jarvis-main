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
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "en-US")

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

        MEMORY_FILE.write_text(json.dumps(memory_data, indent=2), encoding="utf-8")
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
            vec = np.asarray(json.loads(embedding_json), dtype=float)
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
    """Open a folder or file in VS Code if the CLI is installed on the system."""
    path = Path(target_path).expanduser().resolve() if target_path and target_path.strip() else Path.cwd()
    candidates = ["code", "code.cmd", "code-insiders", "code-insiders.cmd"]
    cli = next((name for name in candidates if shutil.which(name)), None)
    if not cli:
        return "VS Code CLI is not installed or not on PATH. Install the 'code' command to open a workspace from here."
    try:
        subprocess.run([cli, str(path)], check=True, capture_output=True, text=True)
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
    def phrase_matches(text: str) -> bool:
        cleaned = (text or "").lower()
        if not cleaned:
            return False
        wake_tokens = [WAKE_WORD, f"hi {WAKE_WORD}", f"hey {WAKE_WORD}", f"hello {WAKE_WORD}", f"{WAKE_WORD} please"]
        return any(token in cleaned for token in wake_tokens)

    @staticmethod
    def detect_double_clap(audio_buffer: Optional[np.ndarray], threshold: float = 1.8) -> bool:
        if audio_buffer is None or audio_buffer.size == 0:
            return False
        amplitude = np.abs(audio_buffer.astype(float))
        energy = np.mean(amplitude)
        peaks = np.where(amplitude > max(energy * threshold, np.percentile(amplitude, 85)))[0]
        if len(peaks) < 2:
            return False
        return bool(np.diff(peaks).min() > 10)

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
        return {"enabled": self.agent_enabled, "status": "active" if self.agent_enabled else "sleeping"}

    def turn_on(self) -> Dict[str, Any]:
        return self.set_enabled(True)

    def turn_off(self) -> Dict[str, Any]:
        self.stop_speech()
        return self.set_enabled(False)

    def stop_speech(self) -> None:
        self.speech_stop_event.set()
        self.is_speaking = False

    def speak(self, text: str) -> None:
        if not text or not text.strip():
            return
        if pyttsx3 is None:
            log.info("Speech disabled; TTS backend is not available.")
            return

        def _worker() -> None:
            self.speech_stop_event.clear()
            self.is_speaking = True
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
                for sentence in re.split(r"(?<=[.!?])\s+", text):
                    if self.speech_stop_event.is_set():
                        break
                    if sentence.strip():
                        engine.say(sentence)
                        engine.runAndWait()
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
        relevant_memories = self.semantic_memory.retrieve(self.memory.get("history", [])[-1].get("content", "") if self.memory.get("history") else "", 3)
        memory_text = "\n".join(f"- {item}" for item in relevant_memories) if relevant_memories else "- No relevant long-term memory available."
        return (
            "You are JARVIS, an advanced desktop AI assistant. "
            f"Behavior: {profile}. "
            "Respond in brief, formal, polished English. "
            "Always think before responding: identify the user's intent, resolve direct commands efficiently, and keep the answer short and professional. "
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
        return f"Action completed. {text}"

    def process_user_query(self, query: str) -> str:
        self.stop_speech()
        q = (query or "").strip()
        if not q:
            return "I did not receive a valid message."

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
        try:
            import pyaudio  # noqa: F401
        except Exception as exc:  # pragma: no cover
            log.warning("Voice input is disabled: PyAudio is missing. Install it with 'pip install pyaudio' or 'pipwin install pyaudio'. Details: %s", exc)
            return

        try:
            recognizer = sr.Recognizer()
            recognizer.dynamic_energy_threshold = True
            recognizer.pause_threshold = 0.6
            microphone = sr.Microphone()
        except (AttributeError, OSError, ValueError) as exc:
            log.warning("Microphone initialization failed: %s", exc)
            return

        self.listener_stop_event.clear()

        while not self.listener_stop_event.is_set():
            if not self.agent_enabled:
                time.sleep(0.25)
                continue
            try:
                with microphone as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.25)
                    audio = recognizer.listen(source, timeout=2, phrase_time_limit=5)
                text = recognizer.recognize_google(audio, language=STT_LANGUAGE)
                normalized = text.strip()
                lowered = normalized.lower()
                if not normalized:
                    continue

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
            target = str(payload.get("path") or ".")
            return jsonify({"result": open_in_vscode(target)})

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
  <title>Jarvis Agent</title>
  <style>
    body { margin: 0; font-family: Arial, sans-serif; background: #0b1020; color: #edf3ff; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
    .card { width: min(900px, 92vw); background: rgba(22,30,49,0.96); border-radius: 18px; padding: 24px; box-shadow: 0 18px 45px rgba(0,0,0,0.45); border: 1px solid rgba(255,255,255,0.08); }
    .topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
    .badge { padding: 8px 14px; border-radius: 999px; font-weight: bold; background: rgba(76, 144, 255, 0.2); }
    .controls { display: flex; gap: 10px; flex-wrap: wrap; }
    button { border: none; border-radius: 10px; padding: 10px 14px; background: #4a78ff; color: white; cursor: pointer; font-weight: 600; }
    button.secondary { background: #2c3b5e; }
    button.ghost { background: rgba(255,255,255,0.08); color: #edf3ff; }
    .messages { min-height: 300px; max-height: 460px; overflow: auto; padding: 14px; border-radius: 12px; background: rgba(10,15,27,0.8); border: 1px solid rgba(255,255,255,0.06); }
    .msg { margin: 10px 0; padding: 10px 12px; border-radius: 10px; }
    .user { background: rgba(90,123,255,0.18); margin-left: 25%; }
    .assistant { background: rgba(43,196,120,0.14); margin-right: 25%; }
    form { display: flex; gap: 12px; margin-top: 16px; }
    input[type=text] { flex: 1; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; background: rgba(2,6,19,0.75); color: #f5f7ff; padding: 12px 14px; font-size: 16px; }
    @media (max-width: 640px) { .card { padding: 16px; } .topbar { flex-direction: column; align-items: flex-start; } }
  </style>
</head>
<body>
  <div class="card">
    <div class="topbar">
      <div class="badge" id="statusBadge">Jarvis offline</div>
      <div class="controls">
        <button id="toggleButton">Turn on</button>
        <button id="micButton" class="secondary">Use microphone</button>
        <button id="vscodeButton" class="ghost">Open VS Code</button>
      </div>
    </div>
    <div class="messages" id="messages"></div>
    <form id="chatForm">
      <input id="messageInput" type="text" placeholder="Type a command or ask a question..." autocomplete="off">
      <button type="submit">Send</button>
    </form>
  </div>

  <script>
    const statusBadge = document.getElementById('statusBadge');
    const toggleButton = document.getElementById('toggleButton');
    const micButton = document.getElementById('micButton');
    const vscodeButton = document.getElementById('vscodeButton');
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

    async function refreshState() {
      const res = await fetch('/api/state');
      const data = await res.json();
      const enabled = !!data.enabled;
      statusBadge.textContent = enabled ? 'Jarvis online' : 'Jarvis offline';
      toggleButton.textContent = enabled ? 'Turn off' : 'Turn on';
      if (Array.isArray(data.history)) {
        messages.innerHTML = '';
        data.history.forEach(item => {
          if (item && item.role && item.content) {
            addMessage(item.role === 'user' ? 'user' : 'assistant', item.content);
          }
        });
      }
    }

    toggleButton.addEventListener('click', async () => {
      const enabled = statusBadge.textContent === 'Jarvis offline';
      const res = await fetch('/api/toggle', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled})});
      const data = await res.json();
      if (data.enabled) {
        addMessage('assistant', 'Jarvis is online and listening for commands.');
      } else {
        addMessage('assistant', 'Jarvis is sleeping.');
      }
      await refreshState();
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
        const res = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ message: transcript })});
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
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    agent = JarvisAgent()
    agent.start_listener_thread()
    app = agent.create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False, threaded=True, use_reloader=False)

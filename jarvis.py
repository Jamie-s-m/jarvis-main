#!/usr/bin/env python3
"""
Desktop Clap Assistant (Jarvis) with Ollama Agent, Tool Calling & Voice Speech

Detects double claps using adaptive noise floor tracking, speaks a verbal greeting,
then triggers a local AI agent (Ollama llama3) that can execute tools and respond aloud.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
import numpy as np
import ollama
import sounddevice as sd

# Try importing offline TTS engine
try:
    import pyttsx3
    HAS_TTS = True
except ImportError:
    HAS_TTS = False

# --- Detection Tuning Knobs ---------------------------------------------------
SAMPLE_RATE = 44100
BLOCK_MS = 40
CHANNELS = 1

SPIKE_RATIO = 7.0
COOLDOWN_S = 0.45
MIN_DOUBLE_GAP_S = 0.05
MAX_DOUBLE_GAP_S = 0.35
RETRIGGER_RATIO = 0.55
NOISE_FLOOR_ALPHA = 0.992
MIN_RMS = 0.012
QUIET_GATE_MULT = 2.2

INPUT_PROBE_S = 0.5
INPUT_SILENT_RMS = 0.001

# --- Ollama Agent Setup -------------------------------------------------------
OLLAMA_MODEL = "llama3"
AGENT_PROMPT = (
    "Double clap detected. System activated. Run necessary quick checks or startup tasks."
)

load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("jarvis")


# --- TTS Function -------------------------------------------------------------

def speak(text: str) -> None:
    """Speaks text using system TTS without blocking the main execution loop."""
    if not HAS_TTS:
        log.warning("pyttsx3 not installed. Run `pip install pyttsx3` for voice output.")
        return

    def _tts_thread():
        try:
            engine = pyttsx3.init()
            engine.setProperty('rate', 175)  # Speaking speed WPM
            engine.setProperty('volume', 1.0)
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            log.error("TTS output error: %s", e)

    threading.Thread(target=_tts_thread, daemon=True).start()


# --- System Tools for Ollama Agent --------------------------------------------

def execute_shell(command: str) -> str:
    """Executes a system shell command safely and returns the output."""
    try:
        res = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        out = res.stdout if res.returncode == 0 else res.stderr
        return out.strip() or "Command executed successfully with no output."
    except Exception as e:
        return f"Shell execution failed: {e}"


def launch_application(app_name: str) -> str:
    """Launches local applications like Cursor, VS Code, Chrome, or Spotify."""
    app = app_name.lower().strip()
    try:
        if app in ["cursor", "code", "vscode"]:
            exe = shutil.which("cursor") or shutil.which("code")
            if not exe and sys.platform == "win32":
                local = os.environ.get("LOCALAPPDATA", "")
                exe = os.path.join(local, "Programs", "cursor", "Cursor.exe")
            if exe and os.path.exists(exe):
                subprocess.Popen([exe])
                return f"Successfully launched {app_name}."
            return f"Could not locate executable for {app_name}."

        elif app in ["chrome", "browser"]:
            webbrowser.open("https://claude.ai")
            return "Opened Chrome."

        else:
            if sys.platform == "win32":
                os.startfile(app_name)
            else:
                subprocess.Popen([app_name])
            return f"Launched {app_name}."
    except Exception as e:
        return f"Failed to launch {app_name}: {e}"


def manage_file(action: str, path: str, content: str = "") -> str:
    """Reads, writes, or checks local files. Action can be 'read', 'write', or 'exists'."""
    p = Path(path).expanduser().resolve()
    try:
        if action == "read":
            return p.read_text(encoding="utf-8") if p.exists() else "File not found."
        elif action == "write":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Wrote content to {p}"
        elif action == "exists":
            return f"Exists: {p.exists()}"
        return "Invalid file action specified."
    except Exception as e:
        return f"File operation error: {e}"


def get_system_status() -> str:
    """Returns basic system status information (OS, current path, and timestamp)."""
    return (
        f"OS: {sys.platform} | "
        f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')} | "
        f"CWD: {os.getcwd()}"
    )


AVAILABLE_TOOLS = {
    "execute_shell": execute_shell,
    "launch_application": launch_application,
    "manage_file": manage_file,
    "get_system_status": get_system_status,
}


# --- Audio Processing Helpers -------------------------------------------------

def block_samples() -> int:
    return max(int(SAMPLE_RATE * BLOCK_MS / 1000), 1)


def rms_mono(block: np.ndarray) -> float:
    if block.ndim > 1:
        block = np.mean(block.astype(np.float64), axis=1)
    else:
        block = block.astype(np.float64)
    return float(np.sqrt(np.mean(block**2))) if block.size > 0 else 0.0


def _choose_input_device(blocksize: int) -> int:
    default = sd.default.device[0]
    return default if (default is not None and default >= 0) else 0


# --- Agent Orchestration -----------------------------------------------------

def run_agent_loop() -> None:
    """Speaks greeting and invokes local Ollama LLM with tool capabilities."""
    log.info("Agent activated via Double-Clap trigger.")
    
    # 1. Verbal Greeting
    speak("Welcome back. Systems online and listening.")

    tools_list = [
        execute_shell,
        launch_application,
        manage_file,
        get_system_status,
    ]

    messages = [{"role": "user", "content": AGENT_PROMPT}]

    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=messages,
            tools=tools_list,
        )

        msg = response.get("message", {})
        tool_calls = msg.get("tool_calls", [])

        if tool_calls:
            for tool_call in tool_calls:
                func_name = tool_call["function"]["name"]
                func_args = tool_call["function"]["arguments"]

                if func_name in AVAILABLE_TOOLS:
                    log.info("Agent executing tool '%s' with args %s", func_name, func_args)
                    result = AVAILABLE_TOOLS[func_name](**func_args)
                    log.info("Tool Result: %s", result)
                    speak(f"Executed {func_name}.")
        else:
            response_text = msg.get("content", "")
            log.info("Agent Response: %s", response_text)
            if response_text:
                speak(response_text)

    except Exception as e:
        log.error("Ollama execution failed: %s", e)


def run_double_clap_actions() -> None:
    threading.Thread(target=run_agent_loop, daemon=True).start()


# --- Main Detection Loop -----------------------------------------------------

def main() -> None:
    bs = block_samples()
    dev_idx = _choose_input_device(bs)

    noise_floor = MIN_RMS
    armed = True
    first_clap_time: float | None = None
    last_trigger_time = 0.0

    log.info("Jarvis system online. Listening for double claps...")

    def callback(indata: np.ndarray, _frames: int, _time: dict, status: sd.CallbackFlags) -> None:
        nonlocal noise_floor, armed, first_clap_time, last_trigger_time

        r = rms_mono(indata)
        now = time.monotonic()

        if r < noise_floor * QUIET_GATE_MULT:
            noise_floor = NOISE_FLOOR_ALPHA * noise_floor + (1.0 - NOISE_FLOOR_ALPHA) * r
            noise_floor = max(noise_floor, 1e-5)

        threshold = max(noise_floor * SPIKE_RATIO, MIN_RMS)

        if not armed and r < (threshold * RETRIGGER_RATIO):
            armed = True

        if armed and r >= threshold:
            armed = False
            if now - last_trigger_time < COOLDOWN_S:
                return

            if first_clap_time is None:
                first_clap_time = now
            else:
                gap = now - first_clap_time
                if MIN_DOUBLE_GAP_S <= gap <= MAX_DOUBLE_GAP_S:
                    last_trigger_time = now
                    first_clap_time = None
                    run_double_clap_actions()
                else:
                    first_clap_time = now

        if first_clap_time and (now - first_clap_time) > MAX_DOUBLE_GAP_S:
            first_clap_time = None

    try:
        with sd.InputStream(
            device=dev_idx,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=bs,
            callback=callback,
        ):
            while True:
                time.sleep(0.1)
    except KeyboardInterrupt:
        log.info("Jarvis shutting down.")


if __name__ == "__main__":
    main()
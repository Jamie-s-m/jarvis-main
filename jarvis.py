#!/usr/bin/env python3
"""
Fully Functional Local AI Agent (Jarvis)

Features:
- Continuous Wake-Word Detection ("Hey Jarvis")
- Speech-to-Text via SpeechRecognition & Text-to-Speech via pyttsx3 (Male Voice)
- Persistent JSON Conversation & Fact Memory
- Dynamic System Tool Calling (Shell Commands, App Launch, File Ops)
- Self-Evolution Module (Analyzes user conversations to learn preferences over time)
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
import ollama

# Speech & Voice Libraries
try:
    import pyttsx3
    HAS_TTS = True
except ImportError:
    HAS_TTS = False

try:
    import speech_recognition as sr
    HAS_STT = True
except ImportError:
    HAS_STT = False

# --- Configuration & Paths ---------------------------------------------------
OLLAMA_MODEL = "llama3"
WAKE_WORD = "jarvis"
MEMORY_FILE = Path("jarvis_memory.json")

load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("jarvis")

# --- Persistent Memory & Evolution Engine -----------------------------------

def load_memory() -> dict:
    """Loads long-term conversation history and learned profile facts."""
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.error("Failed to read memory file: %s", e)
    return {
        "user_profile": "User prefers concise, practical responses.",
        "history": [],
        "learned_facts": []
    }

def save_memory(memory_data: dict) -> None:
    """Saves memory back to local storage."""
    try:
        MEMORY_FILE.write_text(json.dumps(memory_data, indent=2), encoding="utf-8")
    except Exception as e:
        log.error("Failed to save memory: %s", e)

def analyze_and_evolve(user_input: str, assistant_response: str) -> None:
    """Background task: Uses Ollama to extract user preferences/facts and improve profile."""
    memory = load_memory()
    prompt = (
        f"Analyze this interaction:\nUser: '{user_input}'\nJarvis: '{assistant_response}'\n"
        f"Current User Profile: '{memory.get('user_profile', '')}'\n"
        "Extract any new persistent user preference, fact, or instruction if present. "
        "Return ONLY a revised, concise single-paragraph User Profile incorporating new learnings."
    )
    try:
        res = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        new_profile = res.get("message", {}).get("content", "").strip()
        if new_profile and len(new_profile) > 10:
            memory["user_profile"] = new_profile
            save_memory(memory)
            log.info("Jarvis updated its profile memory.")
    except Exception as e:
        log.warning("Self-evolution analysis skipped: %s", e)

# --- Voice / TTS Engine ------------------------------------------------------

def speak(text: str) -> None:
    """Speaks text using system TTS in a male voice."""
    if not HAS_TTS:
        log.warning("pyttsx3 not installed. Run `pip install pyttsx3`.")
        return

    def _tts_thread():
        try:
            engine = pyttsx3.init()
            engine.setProperty('rate', 170)
            engine.setProperty('volume', 1.0)
            voices = engine.getProperty('voices')
            for voice in voices:
                if 'male' in voice.name.lower() or 'david' in voice.name.lower():
                    engine.setProperty('voice', voice.id)
                    break
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            log.error("TTS output error: %s", e)

    threading.Thread(target=_tts_thread, daemon=True).start()

# --- Local Tools / System Execution ----------------------------------------

def execute_shell(command: str) -> str:
    """Executes shell or terminal commands."""
    try:
        res = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        return (res.stdout or res.stderr).strip() or "Command completed."
    except Exception as e:
        return f"Shell error: {e}"

def launch_application(app_name: str) -> str:
    """Launches local applications."""
    app = app_name.lower().strip()
    try:
        if app in ["cursor", "code", "vscode"]:
            exe = shutil.which("cursor") or shutil.which("code")
            if not exe and sys.platform == "win32":
                local = os.environ.get("LOCALAPPDATA", "")
                exe = os.path.join(local, "Programs", "cursor", "Cursor.exe")
            if exe and os.path.exists(exe):
                subprocess.Popen([exe])
                return f"Opened {app_name}."
            return f"Executable for {app_name} not found."
        elif app in ["chrome", "browser"]:
            webbrowser.open("https://google.com")
            return "Opened Web Browser."
        else:
            if sys.platform == "win32":
                os.startfile(app_name)
            else:
                subprocess.Popen([app_name])
            return f"Launched {app_name}."
    except Exception as e:
        return f"Failed to open {app_name}: {e}"

def manage_file(action: str, path: str, content: str = "") -> str:
    """Reads, writes, or verifies files."""
    p = Path(path).expanduser().resolve()
    try:
        if action == "read":
            return p.read_text(encoding="utf-8") if p.exists() else "File missing."
        elif action == "write":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Saved file to {p}"
        return "Invalid file action."
    except Exception as e:
        return f"File error: {e}"

AVAILABLE_TOOLS = {
    "execute_shell": execute_shell,
    "launch_application": launch_application,
    "manage_file": manage_file,
}

# --- Agent Processing Engine ------------------------------------------------

def process_user_query(query: str) -> None:
    """Processes user input using Ollama, tool calling, and long-term memory."""
    memory = load_memory()
    log.info("Processing Query: %s", query)

    system_prompt = (
        "You are Jarvis, an intelligent local AI assistant. "
        f"Learned User Context: {memory.get('user_profile', 'None')}\n"
        "Be helpful, direct, and concise in your responses."
    )

    messages = [{"role": "system", "content": system_prompt}]
    
    # Append recent chat history
    for msg in memory.get("history", [])[-6:]:
        messages.append(msg)
    
    messages.append({"role": "user", "content": query})

    tools_list = [execute_shell, launch_application, manage_file]

    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=messages,
            tools=tools_list
        )

        msg = response.get("message", {})
        tool_calls = msg.get("tool_calls", [])
        final_text = ""

        if tool_calls:
            for tool_call in tool_calls:
                func_name = tool_call["function"]["name"]
                func_args = tool_call["function"]["arguments"]

                if func_name in AVAILABLE_TOOLS:
                    log.info("Running tool %s", func_name)
                    res = AVAILABLE_TOOLS[func_name](**func_args)
                    final_text = f"Executed {func_name}. Result: {res}"
        else:
            final_text = msg.get("content", "")

        if final_text:
            print(f"\nJarvis: {final_text}\n")
            speak(final_text)

            # Save conversation to long-term memory
            memory["history"].append({"role": "user", "content": query})
            memory["history"].append({"role": "assistant", "content": final_text})
            save_memory(memory)

            # Trigger background evolution
            threading.Thread(
                target=analyze_and_evolve, args=(query, final_text), daemon=True
            ).start()

    except Exception as e:
        log.error("Agent processing failed: %s", e)

# --- Speech Recognition Loop ------------------------------------------------

def listen_and_run() -> None:
    """Continuously monitors microphone for 'Hey Jarvis' or direct input."""
    if not HAS_STT:
        log.error("SpeechRecognition library missing. Run `pip install SpeechRecognition pyaudio`.")
        return

    recognizer = sr.Recognizer()
    mic = sr.Microphone()

    with mic as source:
        recognizer.adjust_for_ambient_noise(source, duration=1)

    log.info("Jarvis voice interface online. Say 'Hey Jarvis'...")

    while True:
        try:
            with mic as source:
                audio = recognizer.listen(source, phrase_time_limit=5)
            
            text = recognizer.recognize_google(audio).lower()
            log.debug("Heard: %s", text)

            if WAKE_WORD in text:
                speak("Yes sir, how can I help?")
                print("\n[Jarvis Activated] Listening for your command...")

                with mic as source:
                    command_audio = recognizer.listen(source, phrase_time_limit=10)
                
                command = recognizer.recognize_google(command_audio)
                process_user_query(command)

        except sr.UnknownValueError:
            pass
        except sr.RequestError as e:
            log.error("Speech service error: %s", e)
        except Exception as e:
            log.error("Listening error: %s", e)

if __name__ == "__main__":
    if HAS_STT:
        listen_and_run()
    else:
        # Fallback interactive terminal mode
        print("SpeechRecognition not found. Running in interactive terminal mode.")
        speak("Jarvis terminal mode activated.")
        while True:
            try:
                user_in = input("You: ")
                if user_in.strip():
                    process_user_query(user_in)
            except (KeyboardInterrupt, EOFError):
                break
import time
import subprocess
import ollama
import sounddevice as sd
import numpy as np

# Audio configuration
SAMPLE_RATE = 44100
BLOCK_SIZE = 1024
THRESHOLD = 500  # Adjust based on your environment

def execute_shell_command(command: str):
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.stdout if result.returncode == 0 else result.stderr
    except Exception as e:
        return str(e)

def process_audio(indata, frames, time, status):
    volume_norm = np.linalg.norm(indata) * 10
    if volume_norm > THRESHOLD:
        print("Clap detected!")
        # Trigger the local AI model upon clap detection
        response = ollama.chat(
            model='llama3',
            messages=[{'role': 'user', 'content': 'Execute a quick daily briefing.'}]
        )
        print("AI Response:", response['message']['content'])

def start_listening():
    print("Listening for claps...")
    with sd.InputStream(callback=process_audio, blocksize=BLOCK_SIZE, samplerate=SAMPLE_RATE, channels=1):
        while True:
            time.sleep(1)

if __name__ == "__main__":
    start_listening()
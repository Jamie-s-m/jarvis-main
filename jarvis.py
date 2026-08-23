import time
import subprocess
import ollama
import sounddevice as sd
import numpy as np

SAMPLE_RATE = 44100
BLOCK_SIZE = 1024
# Initial threshold, will adapt to background noise
CURRENT_THRESHOLD = 500
HISTORY_LENGTH = 20
volume_history = []

def execute_shell_command(command: str):
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.stdout if result.returncode == 0 else result.stderr
    except Exception as e:
        return str(e)

def process_audio(indata, frames, time_info, status):
    global CURRENT_THRESHOLD, volume_history
    volume_norm = np.linalg.norm(indata) * 10

    volume_history.append(volume_norm)
    if len(volume_history) > HISTORY_LENGTH:
        volume_history.pop(0)

    # Adapt threshold based on average background noise
    avg_volume = np.mean(volume_history)
    dynamic_threshold = avg_volume * 1.5 + 100

    if volume_norm > dynamic_threshold:
        print("Clap detected! Processing...")
        try:
            response = ollama.chat(
                model='llama3',
                messages=[{'role': 'user', 'content': 'Provide a brief status update.'}]
            )
            print("AI Response:", response['message']['content'])
        except Exception as e:
            print(f"Error calling Ollama: {e}")

def start_listening():
    print("Listening for claps...")
    with sd.InputStream(callback=process_audio, blocksize=BLOCK_SIZE, samplerate=SAMPLE_RATE, channels=1):
        while True:
            time.sleep(1)

if __name__ == "__main__":
    start_listening()
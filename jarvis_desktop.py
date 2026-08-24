"""Desktop launcher for the JARVIS Windows application."""

from __future__ import annotations

import os
import threading
import time
import urllib.request
import webbrowser

from jarvis import JarvisAgent


def run_desktop_app() -> None:
    # Start the WebSocket broadcaster (used to push real-time state and audio data to the HUD)
    try:
        from ws_broadcaster import start_broadcaster

        start_broadcaster()
    except Exception:
        pass

    agent = JarvisAgent()
    # Ensure the assistant is enabled and listening by default in the desktop launcher
    try:
        agent.turn_on()
    except Exception:
        pass
    agent.start_listener_thread()
    app = agent.create_app()
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True, use_reloader=False)


def wait_for_server(port: int, timeout_seconds: int = 25) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1) as response:
                if 200 <= response.status < 500:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> None:
    port = int(os.getenv("PORT", "5000"))
    server_thread = threading.Thread(target=run_desktop_app, daemon=True)
    server_thread.start()

    if wait_for_server(port):
        url = f"http://127.0.0.1:{port}"
        try:
            webbrowser.open(url, new=2)
        except Exception:
            pass

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()

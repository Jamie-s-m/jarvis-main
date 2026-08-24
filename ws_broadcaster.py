"""
Lightweight WebSocket broadcaster for pushing agent state, audio levels,
and transcripts to connected UI clients.

Uses the `websockets` library if available, otherwise becomes a noop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Set

log = logging.getLogger("ws_broadcaster")

try:
    import websockets
except Exception:
    websockets = None


class WSBroadcaster:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._clients: Set = set()
        self._started = False
        self._lock = threading.RLock()

    def start(self) -> None:
        if websockets is None:
            log.info("websockets library not available; WS broadcaster disabled")
            return
        with self._lock:
            if self._started:
                return
            thread = threading.Thread(target=self._run_loop, daemon=True)
            thread.start()
            self._started = True
            log.info("WSBroadcaster starting on ws://%s:%s", self.host, self.port)

    def _run_loop(self) -> None:
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            server_coro = websockets.serve(self._handler, self.host, self.port, ping_interval=20, ping_timeout=20, max_queue=32)
            self._server = self.loop.run_until_complete(server_coro)
            log.info("WebSocket server running at ws://%s:%d", self.host, self.port)
            # broadcast a ready event
            asyncio.run_coroutine_threadsafe(self._broadcast_coro({"type": "server_ready", "payload": {}}), self.loop)
            self.loop.run_forever()
        except Exception as exc:
            log.exception("WebSocket server failed: %s", exc)
        finally:
            try:
                if self._server is not None:
                    self._server.close()
                    self.loop.run_until_complete(self._server.wait_closed())
            except Exception:
                pass

    async def _handler(self, websocket, path):
        # register
        self._clients.add(websocket)
        try:
            async for message in websocket:
                # handle simple incoming control messages from client (JSON expected)
                try:
                    data = json.loads(message)
                    if isinstance(data, dict) and data.get("type"):
                        await self._handle_client_message(websocket, data)
                except Exception:
                    # ignore malformed messages
                    pass
        except Exception:
            pass
        finally:
            try:
                self._clients.discard(websocket)
            except Exception:
                pass

    async def _handle_client_message(self, websocket, data: dict):
        # Provide small control hooks, e.g., client can request server to stop speech, ping, or toggle listening
        t = data.get("type")
        if t == "client:ping":
            await websocket.send(json.dumps({"type": "server:pong"}))
        elif t == "client:stop_speaking":
            # enqueue control for the Python process to act on
            self._controls.append({"action": "stop_speaking"})
            await websocket.send(json.dumps({"type": "server:ack", "payload": {"action": "stop_speaking"}}))
        elif t == "client:toggle_listening":
            self._controls.append({"action": "toggle_listening"})
            await websocket.send(json.dumps({"type": "server:ack", "payload": {"action": "toggle_listening"}}))
        else:
            # unknown control — echo
            await websocket.send(json.dumps({"type": "server:unknown", "payload": {"received": data}}))

    def broadcast(self, payload: dict) -> None:
        """Thread-safe broadcast: schedules the send on the server's event loop."""
        if websockets is None:
            return
        if not self.loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast_coro(payload), self.loop)
        except Exception as exc:
            log.debug("Failed to schedule broadcast: %s", exc)

    async def _broadcast_coro(self, payload: dict) -> None:
        data = json.dumps(payload)
        stale = []
        for ws in list(self._clients):
            try:
                await asyncio.wait_for(ws.send(data), timeout=3)
            except Exception:
                stale.append(ws)
        for s in stale:
            try:
                self._clients.discard(s)
            except Exception:
                pass


# single shared broadcaster instance
broadcaster = WSBroadcaster()

# convenience start function
def start_broadcaster():
    try:
        broadcaster.start()
    except Exception as exc:
        log.debug("Failed to start broadcaster: %s", exc)

# convenience broadcast
def broadcast_sync(payload: dict) -> None:
    try:
        broadcaster.broadcast(payload)
    except Exception:
        pass

# convenience to pop an inbound control message
def pop_control():
    try:
        return broadcaster.pop_control()
    except Exception:
        return None

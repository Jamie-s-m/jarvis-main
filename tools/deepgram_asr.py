"""
Deepgram streaming ASR adapter.

This adapter opens a websocket to Deepgram's Realtime Listen API and sends raw PCM audio frames.
It receives interim and final transcripts from Deepgram and invokes a provided callback for final transcripts,
and broadcasts interim results via ws_broadcaster.

Notes:
- Expects 16-bit PCM little-endian samples at 16000 Hz mono for best compatibility.
- If DEEPGRAM_API_KEY is not set, the adapter becomes a noop and higher-level code should fallback.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
from typing import Callable, Optional

try:
    import websockets
except Exception:
    websockets = None

from ws_broadcaster import broadcast_sync

log = logging.getLogger("deepgram_asr")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "").strip()
DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "general")
DEEPGRAM_LANGUAGE = os.getenv("DEEPGRAM_LANGUAGE", "en-US")


class DeepgramASR:
    def __init__(self, transcript_callback: Optional[Callable[[str, bool], None]] = None):
        self.callback = transcript_callback
        self._running = False
        self._ws = None
        self._loop = None
        self._thread = None
        self._queue = asyncio.Queue()

    def start(self):
        if not DEEPGRAM_API_KEY:
            log.info("Deepgram key not set — ASR adapter disabled")
            return False
        if websockets is None:
            log.warning("websockets package not available; Deepgram ASR disabled")
            return False
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        try:
            if self._loop:
                asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop)
        except Exception:
            pass

    def send_raw(self, pcm_bytes: bytes):
        """Queue raw PCM bytes to send to Deepgram."""
        if not self._running or not DEEPGRAM_API_KEY:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._queue.put(pcm_bytes), self._loop)
        except Exception:
            pass

    def _run(self):
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._run_async())
        except Exception as exc:
            log.exception("Deepgram ASR loop failed: %s", exc)
        finally:
            self._running = False

    async def _run_async(self):
        assert websockets is not None
        url = f"wss://api.deepgram.com/v1/listen?model={DEEPGRAM_MODEL}&language={DEEPGRAM_LANGUAGE}"
        headers = [("Authorization", f"Token {DEEPGRAM_API_KEY}")]
        try:
            async with websockets.connect(url, extra_headers=headers, ping_interval=20, ping_timeout=10, max_size=2**22) as ws:
                self._ws = ws
                log.info("Deepgram ASR connected")
                producer = asyncio.create_task(self._producer_loop(ws))
                consumer = asyncio.create_task(self._consumer_loop(ws))
                done, pending = await asyncio.wait([producer, consumer], return_when=asyncio.FIRST_EXCEPTION)
                for p in pending:
                    p.cancel()
        except Exception as exc:
            log.exception("Deepgram ASR connection failed: %s", exc)

    async def _producer_loop(self, ws):
        # send a small open message to set config
        cfg = {"type": "StartStream", "encoding": "linear16", "sample_rate": 16000, "channels": 1}
        try:
            await ws.send(json.dumps(cfg))
        except Exception:
            pass
        while self._running:
            item = await self._queue.get()
            if item is None:
                break
            try:
                # send binary audio frame directly
                await ws.send(item)
            except Exception as exc:
                log.debug("Failed to send audio chunk: %s", exc)
                break
        # signal end
        try:
            await ws.send(json.dumps({"type": "StopStream"}))
        except Exception:
            pass

    async def _consumer_loop(self, ws):
        async for message in ws:
            try:
                # Deepgram returns JSON messages for transcripts
                data = json.loads(message)
                # handle interim and final
                if 'channel' in data and 'alternatives' in data['channel']:
                    alt = data['channel']['alternatives'][0]
                    text = alt.get('transcript', '')
                    is_final = data.get('is_final', False) or data.get('is_final', None) is True
                    # broadcast interim (is_final False) and final
                    try:
                        broadcast_sync({"type": "transcript", "payload": {"text": text, "is_final": bool(is_final)}})
                    except Exception:
                        pass

                    # Backend-level immediate barge-in: if interim transcript while voice is playing, stop it
                    if not is_final and text.strip():
                        try:
                            import importlib
                            jarvis_mod = importlib.import_module('jarvis')
                            VoiceEngine = getattr(jarvis_mod, 'VoiceEngine', None)
                            if VoiceEngine is not None and getattr(VoiceEngine, '_is_playing', False):
                                try:
                                    VoiceEngine.stop()
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    if is_final and self.callback and text.strip():
                        try:
                            # call callback in a separate thread to avoid blocking
                            threading.Thread(target=self.callback, args=(text, True), daemon=True).start()
                        except Exception:
                            pass
            except Exception:
                # Some messages may be control messages — broadcast them
                try:
                    broadcast_sync({"type": "deepgram:event", "payload": message})
                except Exception:
                    pass


# module-level singleton
_asr = None


def get_deepgram_asr(callback: Optional[Callable[[str, bool], None]] = None):
    global _asr
    if _asr is None:
        _asr = DeepgramASR(transcript_callback=callback)
    return _asr

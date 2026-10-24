"""Cartesia streaming clients for the phone pipeline (one of each per call).

STT: Ink realtime STT with built-in turn detection (wss://api.cartesia.ai/stt/turns/websocket). We forward
     the caller's μ-law 8 kHz audio unchanged and get turn.start / turn.update / turn.eager_end /
     turn.resume / turn.end events, so there is no separate VAD or endpointing model on our side.
TTS: Sonic over a WebSocket (wss://api.cartesia.ai/tts/websocket), one context per utterance, output
     pcm_mulaw 8 kHz: chunks go straight into Twilio media messages without transcoding.
Both authenticate with the X-API-Key header and pin Cartesia-Version via the cartesia_version query param.
"""
import asyncio
import base64
import contextlib
import json
from collections.abc import AsyncIterator, Iterable
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.protocol import State

from .. import config
from ..integrations import cartesia  # VERSION read at call time: cartesia.py imports this package
from . import pronounce
from .voices import Voice

WS = "wss://api.cartesia.ai"
MULAW_8K = {"container": "raw", "encoding": "pcm_mulaw", "sample_rate": 8000}


def _headers() -> dict:
    return {"X-API-Key": config.CARTESIA_API_KEY}


def keyterm_params(terms: Iterable[str]) -> list[tuple[str, str]]:
    """Keyterm prompting boosts recall of names and jargon; the API allows 100 terms, 1200 characters."""
    out, total = [], 0
    for t in dict.fromkeys(t.strip() for t in terms if t and t.strip()):
        if len(out) == 100 or total + len(t) > 1200:
            break
        out.append(("keyterm", t))
        total += len(t)
    return out


class STT:
    def __init__(self, keyterms: Iterable[str] = (), **turn: float) -> None:
        self.keyterms, self.turn = list(keyterms), turn
        self.ws: ClientConnection | None = None

    def url(self) -> str:
        q = [("model", config.CARTESIA_STT_MODEL), ("encoding", "pcm_mulaw"), ("sample_rate", "8000"),
             ("cartesia_version", cartesia.VERSION), *((f"turn_{k}", str(v)) for k, v in self.turn.items()),
             *keyterm_params(self.keyterms)]
        return f"{WS}/stt/turns/websocket?{urlencode(q)}"

    async def connect(self) -> None:
        self.ws = await connect(self.url(), additional_headers=_headers(), max_size=None)

    async def send(self, audio: bytes) -> None:
        await self.ws.send(audio)

    async def events(self) -> AsyncIterator[dict]:
        async for raw in self.ws:
            if isinstance(raw, str):
                yield json.loads(raw)

    async def close(self) -> None:
        if self.ws and self.ws.state is State.OPEN:
            with contextlib.suppress(Exception):
                await self.ws.send(json.dumps({"type": "close"}))  # flush buffered audio, then the server closes
                await asyncio.wait_for(self.ws.wait_closed(), 2)
            await self.ws.close()


class TTS:
    def __init__(self, voice: Voice) -> None:
        self.voice = voice
        self.ws: ClientConnection | None = None
        self._queues: dict[str, asyncio.Queue] = {}
        self._reader: asyncio.Task | None = None

    async def connect(self) -> None:
        self.ws = await connect(f"{WS}/tts/websocket?cartesia_version={cartesia.VERSION}", additional_headers=_headers(),
                                max_size=None)
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                q = self._queues.get(msg.get("context_id", ""))
                if q:
                    q.put_nowait(msg)
        finally:
            for q in self._queues.values():
                q.put_nowait({"type": "error", "message": "Cartesia TTS connection closed"})

    def request(self, text: str, context_id: str) -> dict:
        return {"model_id": config.CARTESIA_TTS_MODEL, "transcript": pronounce.normalize(text, self.voice.language),
                **self.voice.tts_fields(), "output_format": MULAW_8K, "context_id": context_id, "continue": False}

    async def stream(self, text: str, context_id: str) -> AsyncIterator[bytes]:
        """μ-law 8 kHz audio for `text`, chunk by chunk as Sonic generates it."""
        if self.ws is None or self.ws.state is not State.OPEN:
            await self.connect()
        q: asyncio.Queue = asyncio.Queue()
        self._queues[context_id] = q
        try:
            await self.ws.send(json.dumps(self.request(text, context_id)))
            while True:
                msg = await q.get()
                if msg.get("type") == "error":
                    raise RuntimeError(f"Cartesia TTS: {msg.get('message') or msg.get('title') or msg}")
                if msg.get("type") == "chunk" and msg.get("data"):
                    yield base64.b64decode(msg["data"])
                if msg.get("type") == "done" or msg.get("done"):
                    return
        finally:
            self._queues.pop(context_id, None)

    async def cancel(self, context_id: str) -> None:
        """Barge-in: stop generation that hasn't started, and end our stream() for this context now
        (Cartesia keeps sending an utterance it already started; those chunks are dropped)."""
        q = self._queues.pop(context_id, None)
        if q:
            q.put_nowait({"type": "done", "context_id": context_id})
        if self.ws and self.ws.state is State.OPEN:
            with contextlib.suppress(Exception):
                await self.ws.send(json.dumps({"context_id": context_id, "cancel": True}))

    async def close(self) -> None:
        if self.ws:
            await self.ws.close()
        if self._reader:
            self._reader.cancel()

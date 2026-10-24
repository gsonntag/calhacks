"""One Twilio Media Stream = one MediaSession: the real-time loop between the phone line and the call brain.

    Twilio ──media (μ-law 8k)──► Cartesia Ink STT ──turn.end──► brain.turn(text) ──► Sonic TTS ──media──► Twilio
                                     │ turn.update while we speak = barge-in: Twilio "clear" + cancel TTS

Twilio stream events in: connected, start, media, mark, stop. Out: media, mark, clear.
A mark is sent after each utterance; Twilio echoes it when playback finishes, so we know when we've
stopped talking (for barge-in and for hanging up after the goodbye has been heard).

The brain is anything with `async turn(said: str | None) -> str` and an `ended` flag: the hospital call
uses agents.caller.CallSession (the same Claude brain as desk mode, which also emits the transcript
events the dashboard shows); the consent step uses voice.authorization.AuthorizationBrain.
"""
import asyncio
import base64
import contextlib
import json
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..events import bus
from .cartesia_ws import STT, TTS
from .voices import Voice

STT_CHUNK = 800  # bytes = 100 ms of μ-law, the chunk size Cartesia recommends
BACKCHANNEL = {"okay", "ok", "mm", "mhm", "mm-hmm", "uh-huh", "uh", "um", "yeah", "yep", "yes", "right", "sure",
               "alright", "got", "it", "i", "see", "hmm"}
INTERJECTIONS = {"wait", "stop", "sorry", "hold", "no", "excuse", "hello", "hey", "actually", "pardon", "what"}


class Brain(Protocol):
    ended: bool

    async def turn(self, said: str | None) -> str: ...


class Socket(Protocol):
    """The subset of starlette's WebSocket we use (tests pass a fake)."""
    async def receive_text(self) -> str: ...
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


def is_backchannel(text: str) -> bool:
    words = re.findall(r"[a-z'-]+", text.lower())
    return bool(words) and len(words) <= 3 and all(w in BACKCHANNEL for w in words)


def should_barge_in(partial: str) -> bool:
    """Interrupt on real speech: two or more words that aren't "mm-hmm, okay", or a one-word interjection.
    A lone fragment ("you?") is usually the tail of a turn the STT closed a beat early, not an interruption."""
    words = re.findall(r"[a-z'-]+", partial.lower())
    if not words or is_backchannel(partial):
        return False
    return len(words) >= 2 or words[0] in INTERJECTIONS


@dataclass
class Leg:
    """A media stream we expect: registered before dialing and matched by a secret token that Twilio
    passes back as a <Parameter> in the stream's start message."""
    case_id: str
    voice: Voice
    brain: Brain | None = None
    keyterms: list[str] = field(default_factory=list)
    greet_after: float | None = 3.0      # speak first if the other side is silent this long
    hangup_when_done: bool = True        # close the stream (Twilio hangs up) after the brain ends
    stt_turn: dict = field(default_factory=dict)
    label: str = ""
    token: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    call_sid: str = ""
    status: str = ""
    connected: asyncio.Event = field(default_factory=asyncio.Event)
    answered: asyncio.Event = field(default_factory=asyncio.Event)
    idle: asyncio.Event = field(default_factory=asyncio.Event)   # brain ended and its last words were heard
    done: asyncio.Event = field(default_factory=asyncio.Event)   # stream closed or call ended
    session: "MediaSession | None" = None
    stats: dict = field(default_factory=lambda: {"turns": 0, "latency_ms": [], "llm_ms": [], "tts_first_ms": [],
                                                 "barge_ins": 0, "audio_in_s": 0.0, "audio_out_s": 0.0})


class MediaSession:
    def __init__(self, ws: Socket, lookup: Callable[[str], Leg | None],
                 stt_factory: Callable[..., Any] | None = None, tts_factory: Callable[..., Any] | None = None) -> None:
        self.ws, self.lookup = ws, lookup
        self.stt_factory, self.tts_factory = stt_factory or STT, tts_factory or TTS
        self.leg: Leg | None = None
        self.stt = self.tts = None
        self.stream_sid = ""
        self.utterances: asyncio.Queue = asyncio.Queue()
        self.marks: set[str] = set()
        self.streaming = False
        self.heard = False
        self._n = 0
        self._ctx = ""
        self._interrupted = False
        self._inbuf = bytearray()
        self._turn_end_at: float | None = None
        self._tasks: list[asyncio.Task] = []
        self._greeter: asyncio.Task | None = None
        self._closed = False

    @property
    def speaking(self) -> bool:
        return self.streaming or bool(self.marks)

    async def run(self) -> None:
        try:
            while not self._closed:
                try:
                    msg = json.loads(await self.ws.receive_text())
                except Exception:  # disconnect
                    return
                ev = msg.get("event")
                if ev == "start" and not await self._start(msg):
                    return
                if ev == "media" and self.leg and msg["media"].get("track", "inbound") == "inbound":
                    await self._audio(base64.b64decode(msg["media"]["payload"]))
                elif ev == "mark":
                    self.marks.discard((msg.get("mark") or {}).get("name", ""))
                elif ev == "stop":
                    return
        finally:
            await self._shutdown()

    async def _start(self, msg: dict) -> bool:
        start = msg.get("start") or {}
        leg = self.lookup((start.get("customParameters") or {}).get("token", ""))
        if leg is None or leg.session is not None:
            await self.ws.close(1008)
            return False
        self.leg, leg.session = leg, self
        self.stream_sid = start.get("streamSid") or msg.get("streamSid", "")
        leg.call_sid = start.get("callSid", leg.call_sid)
        try:
            self.stt = self.stt_factory(keyterms=leg.keyterms, **leg.stt_turn)
            self.tts = self.tts_factory(leg.voice)
            await asyncio.gather(self.stt.connect(), self.tts.connect())
        except Exception as e:
            bus.emit(leg.case_id, "warning", "caller", text=f"Voice pipeline couldn't reach Cartesia: {e}")
            await self.ws.close(1011)
            return False
        self._tasks = [asyncio.create_task(self._listen()), asyncio.create_task(self._respond())]
        leg.connected.set()
        self.arm_greeting(leg.greet_after)
        return True

    def set_brain(self, brain: Brain, greet_after: float | None = 3.0) -> None:
        """Hand the line to a new brain (conference mode: consent step -> hospital call)."""
        self.leg.brain, self.heard = brain, False
        self.leg.idle.clear()
        self.arm_greeting(greet_after)

    def arm_greeting(self, after: float | None) -> None:
        if self._greeter:
            self._greeter.cancel()
        if after is not None:
            self._greeter = asyncio.create_task(self._greet(after))

    async def _greet(self, after: float) -> None:
        await asyncio.sleep(after)
        if not self.heard and not self.speaking:
            self.utterances.put_nowait(None)  # nobody said hello: speak first

    async def _audio(self, chunk: bytes) -> None:
        self.leg.stats["audio_in_s"] += len(chunk) / 8000
        self._inbuf += chunk
        if len(self._inbuf) >= STT_CHUNK:
            data, self._inbuf = bytes(self._inbuf), bytearray()
            with contextlib.suppress(Exception):
                await self.stt.send(data)

    async def _listen(self) -> None:
        try:
            async for ev in self.stt.events():
                kind, text = ev.get("type"), (ev.get("transcript") or "").strip()
                if kind == "turn.start":
                    self.heard = True
                    if self._greeter:
                        self._greeter.cancel()
                elif kind in ("turn.update", "turn.eager_end") and self.speaking and should_barge_in(text):
                    await self.barge_in()
                elif kind == "turn.end" and text:
                    self.heard = True
                    if self.speaking:
                        if not should_barge_in(text):
                            continue  # "mm-hmm" or a stray fragment while we talk: listening, not a turn
                        await self.barge_in()
                    self._turn_end_at = time.monotonic()
                    self.utterances.put_nowait(text)
                elif kind == "error":
                    bus.emit(self.leg.case_id, "warning", "caller", text=f"Speech-to-text error: {ev.get('message')}")
        except Exception as e:
            if not self._closed:
                bus.emit(self.leg.case_id, "warning", "caller", text=f"Speech-to-text stream ended: {e}")

    async def _respond(self) -> None:
        while True:
            said = await self.utterances.get()
            while not self.utterances.empty():  # the rep kept talking while we were thinking: one turn
                more = self.utterances.get_nowait()
                said = f"{said} {more}".strip() if said and more else (said or more)
            brain = self.leg.brain
            if brain is None or brain.ended:
                continue
            t0 = time.monotonic()
            try:
                say = await brain.turn(said)
            except Exception as e:
                bus.emit(self.leg.case_id, "warning", "caller", text=f"Call brain error: {type(e).__name__}")
                say = "Sorry, could you say that again?"
            self.leg.stats["turns"] += 1
            self.leg.stats["llm_ms"].append(round((time.monotonic() - t0) * 1000))
            if say:
                await self.speak(say)
            if brain.ended:
                await self.wait_quiet()
                self.leg.idle.set()
                if self.leg.hangup_when_done:
                    await self.close()
                    return

    async def speak(self, text: str) -> None:
        self._n += 1
        ctx, mark = f"{self.stream_sid[-10:]}-{self._n}", f"say-{self._n}"
        self._ctx, self._interrupted, self.streaming = ctx, False, True
        t0, first = time.monotonic(), True
        try:
            async for chunk in self.tts.stream(text, ctx):
                if self._interrupted:
                    break
                if first:
                    first = False
                    now = time.monotonic()
                    self.leg.stats["tts_first_ms"].append(round((now - t0) * 1000))
                    if self._turn_end_at is not None:
                        self.leg.stats["latency_ms"].append(round((now - self._turn_end_at) * 1000))
                        self._turn_end_at = None
                self.leg.stats["audio_out_s"] += len(chunk) / 8000
                await self._send({"event": "media", "streamSid": self.stream_sid,
                                  "media": {"payload": base64.b64encode(chunk).decode()}})
            if not self._interrupted:
                self.marks.add(mark)
                await self._send({"event": "mark", "streamSid": self.stream_sid, "mark": {"name": mark}})
        except Exception as e:
            bus.emit(self.leg.case_id, "warning", "caller", text=f"Text-to-speech failed: {e}")
        finally:
            self.streaming = False

    async def barge_in(self) -> None:
        """The rep started talking over us: flush Twilio's playback buffer and stop generating."""
        if not self.speaking:
            return
        self._interrupted = True
        self.marks.clear()
        self.leg.stats["barge_ins"] += 1
        await self._send({"event": "clear", "streamSid": self.stream_sid})
        await self.tts.cancel(self._ctx)

    async def wait_quiet(self, timeout: float = 30) -> None:
        """Until Twilio has played everything we sent (all marks echoed back)."""
        end = time.monotonic() + timeout
        while self.speaking and time.monotonic() < end and not self._closed:
            await asyncio.sleep(0.05)

    async def _send(self, msg: dict) -> None:
        if not self._closed:
            with contextlib.suppress(Exception):
                await self.ws.send_text(json.dumps(msg))

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(Exception):
                await self.ws.close()

    async def _shutdown(self) -> None:
        self._closed = True
        for t in [*self._tasks, self._greeter]:
            if t and t is not asyncio.current_task():
                t.cancel()
        for client in (self.stt, self.tts):
            if client:
                with contextlib.suppress(Exception):
                    await client.close()
        if self.leg:
            self.leg.done.set()

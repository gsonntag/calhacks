"""One Twilio Media Stream = one MediaSession: the real-time loop between the phone line and the call brain.

    Twilio ──media (μ-law 8k)──► Cartesia Ink STT ──turn.end──► brain.turn(text) ──► Sonic TTS ──media──► Twilio
                                     │ turn.update while we speak = barge-in: Twilio "clear" + cancel TTS

Twilio stream events in: connected, start, media, mark, stop. Out: media, mark, clear.
A mark is sent after each utterance; Twilio echoes it when playback finishes, so we know when we've
stopped talking (for barge-in and for hanging up after the goodbye has been heard).

The brain is anything with `async turn(said: str | None) -> str` and an `ended` flag: the hospital call
uses agents.caller.CallSession (the same Claude brain as desk mode, which also emits the transcript
events the dashboard shows); the consent step uses voice.authorization.AuthorizationBrain.

Latency (docs/voice.md, Latency): a brain that also has `reply(said, draft)` (an async generator of
sentences) and `draft(said)` gets two speedups.
  - Streaming: each sentence goes to Sonic as soon as Claude has written it (TTS.stream_many), so speech
    starts after the first sentence instead of the whole reply.
  - Speculation: on Ink's turn.eager_end we start Claude with the words so far (brain.draft). turn.resume
    (the rep kept talking) cancels it; on turn.end with the same words we use it, otherwise start over.
    A draft has no side effects until reply() uses it.
Each turn logs one "latency" line (logger billback.latency) with turn.end -> first sentence / first audio.
"""
import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..events import bus
from .cartesia_ws import STT, TTS
from .voices import Voice

log = logging.getLogger("billback.latency")
if not log.handlers:  # visible under uvicorn without any logging config
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(name)s +%(relativeCreated)dms %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.DEBUG if os.getenv("BILLBACK_LATENCY_DEBUG") else logging.INFO)  # DEBUG: STT events too
    log.propagate = False

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
    # latency_ms: rep turn.end -> first agent audio sent; llm_ms: turn start -> first sentence from the brain;
    # drafts: speculative replies started on turn.eager_end and whether turn.end used them
    stats: dict = field(default_factory=lambda: {"turns": 0, "latency_ms": [], "llm_ms": [], "tts_first_ms": [],
                                                 "barge_ins": 0, "audio_in_s": 0.0, "audio_out_s": 0.0,
                                                 "drafts": {"used": 0, "discarded": 0}})


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
        self._turn_end_at: float | None = None   # latest rep turn.end not answered yet
        self._reply_to_end: float | None = None  # the turn.end the reply being spoken answers
        self._draft: dict | None = None   # {"said", "at", "brain", "handle"}: a reply started on turn.eager_end
        self._busy = False                # the brain is working on a turn
        self._turn_log: dict = {}
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
                if kind != "turn.update":
                    log.debug("stt %s %r speaking=%s busy=%s", kind, text, self.speaking, self._busy)
                if kind == "turn.start":
                    self.heard = True
                    if self._greeter:
                        self._greeter.cancel()
                elif kind in ("turn.update", "turn.eager_end") and self.speaking and should_barge_in(text):
                    log.debug("stt %s %r while speaking", kind, text)
                    await self.barge_in()
                    if kind == "turn.eager_end":
                        self._speculate(text)
                elif kind == "turn.eager_end" and text and not self.speaking:
                    self._speculate(text)
                elif kind == "turn.resume":
                    self._drop_draft()
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

    @staticmethod
    def _words(text: str | None) -> list[str]:
        return re.findall(r"[a-z0-9']+", (text or "").lower())

    def _speculate(self, text: str) -> None:
        """Start the reply to `text` now; turn.end decides whether we use it."""
        brain = self.leg.brain if self.leg else None
        if brain is None or brain.ended or self._busy or not hasattr(brain, "draft") or not self.utterances.empty():
            return
        if self._draft and self._words(self._draft["said"]) == self._words(text):
            return  # same words again: keep the draft we have
        self._drop_draft()
        try:
            handle = brain.draft(text)
        except Exception:
            return
        if handle is not None:
            self._draft = {"said": text, "at": time.monotonic(), "brain": brain, "handle": handle}

    def _drop_draft(self) -> None:
        if self._draft:
            self._draft["brain"].cancel_draft(self._draft["handle"])
            self.leg.stats["drafts"]["discarded"] += 1
            self._draft = None

    def _take_draft(self, said: str | None, brain: Brain, merged: bool) -> tuple[str | None, Any]:
        """(what to answer, draft handle or None). A draft is used only for the same brain and the same words."""
        d, self._draft = self._draft, None
        if not d:
            return said, None
        if not merged and d["brain"] is brain and self._words(d["said"]) == self._words(said):
            self.leg.stats["drafts"]["used"] += 1
            self._turn_log["draft"] = "used"
            if self._reply_to_end is not None:
                self._turn_log["eager_lead_ms"] = round((self._reply_to_end - d["at"]) * 1000)
            return d["said"], d["handle"]
        d["brain"].cancel_draft(d["handle"])
        self.leg.stats["drafts"]["discarded"] += 1
        self._turn_log["draft"] = "discarded"
        return said, None

    async def _respond(self) -> None:
        while True:
            said = await self.utterances.get()
            merged = False
            while not self.utterances.empty():  # the rep kept talking while we were thinking: one turn
                more = self.utterances.get_nowait()
                said = f"{said} {more}".strip() if said and more else (said or more)
                merged = True
            brain = self.leg.brain
            if brain is None or brain.ended:
                self._drop_draft()
                continue
            self._turn_log = {"draft": "none"}
            self._reply_to_end, self._turn_end_at = self._turn_end_at, None
            said, draft = self._take_draft(said, brain, merged)
            t0 = time.monotonic()
            self._busy = True
            first: list[float] = []

            async def sentences():
                try:
                    if hasattr(brain, "reply"):
                        async for s in brain.reply(said, draft):
                            first.append(time.monotonic())
                            yield s
                    else:
                        say = await brain.turn(said)
                        first.append(time.monotonic())
                        yield say
                except Exception as e:
                    bus.emit(self.leg.case_id, "warning", "caller", text=f"Call brain error: {type(e).__name__}")
                    if not first:
                        first.append(time.monotonic())
                        yield "Sorry, could you say that again?"

            try:
                await self.speak_stream(sentences(), on_first_text=lambda: first and first[0])
            finally:
                self._busy = False
            self.leg.stats["turns"] += 1
            if first:
                self.leg.stats["llm_ms"].append(round((first[0] - t0) * 1000))
            if brain.ended:
                await self.wait_quiet()
                self.leg.idle.set()
                if self.leg.hangup_when_done:
                    await self.close()
                    return

    async def speak(self, text: str) -> None:
        async def one():
            yield text
        await self.speak_stream(one())

    async def _tts_audio(self, chunks, ctx: str):
        if hasattr(self.tts, "stream_many"):
            async for c in self.tts.stream_many(chunks, ctx):
                yield c
            return
        async for text in chunks:  # a TTS client without stream_many: one stream() per sentence
            if self._interrupted:
                continue  # keep draining the brain, say nothing more
            async for c in self.tts.stream(text, ctx):
                yield c

    async def speak_stream(self, chunks, on_first_text=None) -> None:
        """Speak sentences as they arrive. Always drains `chunks` (the brain finishes its turn) even after a
        barge-in; once interrupted, nothing more is sent to Twilio."""
        self._n += 1
        ctx, mark = f"{self.stream_sid[-10:]}-{self._n}", f"say-{self._n}"
        self._ctx, self._interrupted = ctx, False
        t0, first = time.monotonic(), True
        try:
            async for chunk in self._tts_audio(chunks, ctx):
                if self._interrupted:
                    continue
                if first:
                    first = False
                    self.streaming = True
                    now = time.monotonic()
                    t_text = on_first_text() if on_first_text else None
                    self.leg.stats["tts_first_ms"].append(round((now - (t_text or t0)) * 1000))
                    end, self._reply_to_end = self._reply_to_end, None
                    if end is not None:
                        ms = round((now - end) * 1000)
                        self.leg.stats["latency_ms"].append(ms)
                        tl = self._turn_log
                        log.info("turn=%d draft=%s%s first_text=%sms first_audio=%dms (from rep turn.end)",
                                 self.leg.stats["turns"] + 1, tl.get("draft", "none"),
                                 f" eager_lead={tl['eager_lead_ms']}ms" if "eager_lead_ms" in tl else "",
                                 round((t_text - end) * 1000) if t_text else "?", ms)
                self.leg.stats["audio_out_s"] += len(chunk) / 8000
                await self._send({"event": "media", "streamSid": self.stream_sid,
                                  "media": {"payload": base64.b64encode(chunk).decode()}})
            if not self._interrupted and not first:
                self.marks.add(mark)
                await self._send({"event": "mark", "streamSid": self.stream_sid, "mark": {"name": mark}})
        except Exception as e:
            bus.emit(self.leg.case_id, "warning", "caller", text=f"Text-to-speech failed: {e}")
            with contextlib.suppress(Exception):
                async for _ in chunks:  # still let the brain finish its turn
                    pass
        finally:
            self.streaming = False

    async def barge_in(self) -> None:
        """The rep started talking over us: flush Twilio's playback buffer and stop generating."""
        if not self.speaking or self._interrupted:
            return
        log.debug("barge-in")
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
        if self._draft:
            self._draft["brain"].cancel_draft(self._draft["handle"])
            self._draft = None
        for t in [*self._tasks, self._greeter]:
            if t and t is not asyncio.current_task():
                t.cancel()
        for client in (self.stt, self.tts):
            if client:
                with contextlib.suppress(Exception):
                    await client.close()
        if self.leg:
            self.leg.done.set()

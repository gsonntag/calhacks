"""Desk mode, streamed: one request per rep turn returns BillBack's reply as it's written and spoken.

    POST /api/calls/{call_id}/turn/stream   {"rep_said": "..."}   ->  NDJSON, one event per line:
        {"type": "text",  "text": "Thank you, Diane. "}          a sentence, as soon as Claude has written it
        {"type": "audio", "data": "<base64 pcm_s16le 24 kHz>"}  Sonic audio for the sentences so far, in order
        {"type": "done",  "say": "...", "ended": false, "outcome": {...}, "audio": true}

The original desk flow waited for Claude's whole reply (POST .../turn), then for Sonic's whole WAV (POST
/api/tts) before the browser could play anything: ~3 s. Here the first sentence goes to Sonic over a
WebSocket kept open between turns, and web/rep.html plays each chunk with Web Audio as it arrives.
Same brain (CallSession.reply), same transcript events, same pronunciation normalizer (TTS.request).
POST .../turn and /api/tts are unchanged and stay as the fallback.
"""
import asyncio
import base64
import contextlib
import json
import time

from fastapi.responses import JSONResponse, StreamingResponse

from .. import config
from ..agents import caller
from ..events import bus
from . import voices
from .cartesia_ws import PCM_24K, TTS
from .media import log

_tts: dict[str, TTS] = {}  # voice id -> Sonic socket, kept open across turns (one desk call at a time)


def _voice_for(s: caller.CallSession) -> voices.Voice:
    from .. import orchestrator  # late: orchestrator imports the voice package
    vid = s.brief.get("voice_id") or next((c.get("result", {}).get("brief", {}).get("voice_id")
                                          for c in orchestrator.cases.values() if c.get("call_id") == s.id), None)
    return voices.resolve(vid)


def _tts_for(voice: voices.Voice) -> TTS:
    if voice.id not in _tts:
        _tts[voice.id] = TTS(voice, PCM_24K)
    return _tts[voice.id]


def _line(ev: dict) -> bytes:
    return (json.dumps(ev) + "\n").encode()


async def events(s: caller.CallSession, rep_said: str | None, speak: bool = True):
    """The NDJSON event stream for one turn (also used directly by tests)."""
    out: asyncio.Queue = asyncio.Queue()
    to_tts: asyncio.Queue = asyncio.Queue()
    tts = _tts_for(_voice_for(s)) if speak else None
    if tts:
        asyncio.create_task(_warm(tts))
    t0, first = time.monotonic(), {}
    audio_ok = tts is not None

    async def think() -> None:
        try:
            async for sentence in s.reply(rep_said):
                first.setdefault("text", time.monotonic())
                out.put_nowait({"type": "text", "text": sentence})
                to_tts.put_nowait(sentence)
        except Exception as e:  # reply() handles model errors itself; this is a last resort
            bus.emit(s.case_id, "warning", "caller", text=f"Call turn failed: {type(e).__name__}")
        finally:
            to_tts.put_nowait(None)

    async def sentences():
        while (x := await to_tts.get()) is not None:
            yield x

    async def say_it() -> None:
        nonlocal audio_ok
        if tts is None:
            async for _ in sentences():
                pass
            return
        try:
            async for chunk in tts.stream_many(sentences(), f"desk-{s.id}-{len(s.messages)}-{int(t0 * 1000) % 100000}"):
                first.setdefault("audio", time.monotonic())
                out.put_nowait({"type": "audio", "data": base64.b64encode(chunk).decode()})
        except Exception as e:
            audio_ok = False
            bus.emit(s.case_id, "warning", "caller", text=f"Desk text-to-speech failed: {e}")
            with contextlib.suppress(Exception):
                async for _ in sentences():
                    pass

    tasks = [asyncio.create_task(think()), asyncio.create_task(say_it())]
    done = asyncio.gather(*tasks)
    done.add_done_callback(lambda _: out.put_nowait(None))
    while (ev := await out.get()) is not None:
        yield _line(ev)
    await done
    ms = {k: round((v - t0) * 1000) for k, v in first.items()}
    log.info("desk turn: first_text=%sms first_audio=%sms (from the rep's send)", ms.get("text", "-"), ms.get("audio", "-"))
    if s.ended:
        _call_done(s)
    yield _line({"type": "done", "say": s.last_say, "ended": s.ended, "outcome": s.outcome,
                 "audio": audio_ok and "audio" in first})


async def _warm(tts: TTS) -> None:
    with contextlib.suppress(Exception):
        await tts.ensure()


def _call_done(s: caller.CallSession) -> None:
    from .. import orchestrator
    case = orchestrator.cases.get(s.case_id)
    if case and case.get("call_done"):
        case["call_done"].set()


async def turn_stream(call_id: str, rep_said: str | None):
    s = caller.sessions.get(call_id)
    if not s:
        return JSONResponse({"error": "no such call"}, 404)
    return StreamingResponse(events(s, rep_said, speak=bool(config.CARTESIA_API_KEY)),
                             media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})

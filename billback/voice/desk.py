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

Listening and interrupting (so the whole desk demo runs on Cartesia, like the phone path):

    WS   /api/calls/{call_id}/listen      browser mic, pcm_s16le 16 kHz binary frames -> Cartesia Ink
        <- {"type": "ready"}                                    Ink is connected: stream audio
        <- {"type": "start"}                                    the rep started talking (turn.start)
        <- {"type": "partial", "text", "barge", "echo"}         turn.update / turn.eager_end
        <- {"type": "end", "text", "barge", "echo"}             turn.end: the rep's turn, send it as rep_said
        <- {"type": "unavailable", "reason"}                    no key, HTTP 402, Ink error: the page uses the
                                                                browser's SpeechRecognition instead
    POST /api/calls/{call_id}/interrupt   {"played_s": 1.84}  the rep talked over BillBack: stop Sonic and keep
                                                              only the words played so far in the history

"barge" is media.should_barge_in(text) (real speech, not "mm-hmm"), the same rule as the phone path; "echo"
says the words are BillBack's own reply coming back through the rep's speakers (heard.is_echo). The page
knows whether audio is playing, so it decides: stop playback, POST interrupt, answer on "end".
"""
import asyncio
import base64
import contextlib
import json
import time

from dataclasses import dataclass, field

from fastapi.responses import JSONResponse, StreamingResponse

from .. import config
from ..agents import caller
from ..events import bus
from . import voices
from .cartesia_ws import PCM_24K, STT, TTS
from .heard import Spoken, is_echo
from .media import log, should_barge_in

_tts: dict[str, TTS] = {}  # voice id -> Sonic socket, kept open across turns (one desk call at a time)
MIC_RATE = 16000           # what web/rep.html sends Ink: pcm_s16le, 16 kHz, mono
MIC_CHUNK = MIC_RATE // 10 * 2  # 100 ms, the chunk size Cartesia recommends


@dataclass
class DeskTurn:
    """The latest streamed reply of a desk call: what was generated, so an interrupt can cut it."""
    spoken: Spoken
    tts: TTS | None
    prefix: str
    said: list[str] = field(default_factory=list)
    done: bool = False
    interrupted: bool = False


_turns: dict[str, DeskTurn] = {}  # call id -> its latest turn


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
    prefix = f"desk-{s.id}-{len(s.messages)}-{int(t0 * 1000) % 100000}"
    turn = _turns[s.id] = DeskTurn(Spoken(PCM_24K["sample_rate"] * 2, _voice_for(s).language), tts, prefix)

    async def think() -> None:
        try:
            async for sentence in s.reply(rep_said):
                turn.said.append(sentence)
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
            async for chunk in tts.stream_many(sentences(), prefix, spoken=turn.spoken):
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
    turn.done = True
    ms = {k: round((v - t0) * 1000) for k, v in first.items()}
    log.info("desk turn: first_text=%sms first_audio=%sms (from the rep's send)", ms.get("text", "-"), ms.get("audio", "-"))
    if s.ended:
        _call_done(s)
    yield _line({"type": "done", "say": s.last_say, "ended": s.ended, "outcome": s.outcome,
                 "audio": audio_ok and "audio" in first, "interrupted": turn.interrupted})


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


async def interrupt(call_id: str, played_s: float | None):
    """The rep talked over the latest reply after `played_s` seconds of its audio had played (Web Audio clock).
    Stops Sonic for that reply and cuts BillBack's memory of it to the words played (heard.py). Without
    Sonic audio (browser voice fallback) nothing can be measured, so the history is left as it is."""
    s = caller.sessions.get(call_id)
    turn = _turns.get(call_id)
    if not s or not turn:
        return JSONResponse({"error": "no such call"}, 404)
    turn.interrupted = True
    if turn.tts is not None and not turn.done:
        with contextlib.suppress(Exception):
            await turn.tts.cancel(turn.prefix)
    heard = turn.spoken.heard(played_s) if played_s is not None and turn.spoken.segments else None
    if heard is not None:
        s.interrupted(heard)
    log.info("desk barge-in: played=%ss heard=%r", played_s, heard)
    return {"ok": True, "heard": heard}


def _reason(e: BaseException) -> str:
    status = getattr(getattr(e, "response", None), "status_code", None)
    return f"Cartesia Ink unavailable (HTTP {status})" if status else f"Cartesia Ink unavailable: {e}"


async def listen(ws, call_id: str) -> None:
    """Desk mode's ears: relay the browser mic to Cartesia Ink and Ink's turn events back (see the top)."""
    from .calls import keyterms  # late: calls imports media, like this module
    await ws.accept()

    async def send(ev: dict) -> None:
        with contextlib.suppress(Exception):
            await ws.send_text(json.dumps(ev))

    s = caller.sessions.get(call_id)
    if not s or not config.CARTESIA_API_KEY:
        await send({"type": "unavailable", "reason": "no such call" if not s else "no Cartesia key"})
        await ws.close(1008 if not s else 1000)
        return
    stt = STT(keyterms=keyterms(s.brief), encoding="pcm_s16le", sample_rate=MIC_RATE)
    try:
        await stt.connect()
    except Exception as e:  # HTTP 402 when out of credits, network, bad key
        await send({"type": "unavailable", "reason": _reason(e)})
        await ws.close(1011)
        return
    await send({"type": "ready"})

    def reply_text() -> str:
        t = _turns.get(call_id)
        return "".join(t.said) if t else s.last_say

    async def mic() -> None:
        buf = bytearray()
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("bytes"):
                buf += msg["bytes"]
                while len(buf) >= MIC_CHUNK:
                    data, buf = bytes(buf[:MIC_CHUNK]), buf[MIC_CHUNK:]
                    await stt.send(data)

    async def ink() -> None:
        async for ev in stt.events():
            kind, text = ev.get("type"), (ev.get("transcript") or "").strip()
            if kind == "turn.start":
                await send({"type": "start"})
            elif kind in ("turn.update", "turn.eager_end", "turn.end") and text:
                await send({"type": "end" if kind == "turn.end" else "partial", "text": text,
                            "barge": should_barge_in(text), "echo": is_echo(text, reply_text())})
            elif kind == "error":
                await send({"type": "unavailable", "reason": f"Cartesia Ink error: {ev.get('message') or ev}"})
                return

    tasks = [asyncio.create_task(mic()), asyncio.create_task(ink())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await stt.close()
        with contextlib.suppress(Exception):
            await ws.close()

"""Phone-call simulator: plays Twilio against our voice pipeline, without a phone or Twilio account.

    uv run python scripts/sim_twilio_stream.py                 # agent voice: priya (Indian English)
    uv run python scripts/sim_twilio_stream.py --voice gemma --rep-voice katie --barge-in

It starts the app (with billback.voice.router) on 127.0.0.1:8022, registers a hospital-call leg exactly as
run_twilio_call does, then connects to ws://127.0.0.1:8022/voice/twilio/stream as Twilio would: sends
"connected" and "start" (with the leg's token as a custom parameter), streams the billing rep's lines as
μ-law 8 kHz media frames in real time (silence in between, like a real line), plays back what the agent
sends at real-time speed and echoes each mark when its audio would have finished playing.

Everything downstream is real: Cartesia Ink STT with turn detection, Claude (CallSession), Cartesia Sonic
TTS. The rep's lines are synthesized once with Sonic and cached in data/sim/cache/. Recordings and a
transcript land in data/sim/<case>/. Uses no Cartesia agent minutes and places no phone call.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

from billback import config, finance, orchestrator  # noqa: E402
from billback.agents import caller  # noqa: E402
from billback.agents.policy import SAMPLE_TIERS  # noqa: E402
from billback.app import app  # noqa: E402
from billback.events import bus  # noqa: E402
from billback.integrations.cartesia import API, VERSION  # noqa: E402
from billback.voice import audio, calls, pronounce, router, voices  # noqa: E402
from billback.voice.media import Leg  # noqa: E402

PORT = 8022
OUT = config.DATA_DIR / "sim"
REP_LINES = [
    "Patient Financial Services, this is Diane. How can I help you?",
    "Okay, I have the account up. It shows eight thousand seven hundred dollars due.",
    "Alright, I can apply the insurance adjustment and the five hundred dollar payment.",
    "Okay. I can set up six payments of one hundred dollars, no interest, and I've paused collections.",
    "Your case number is F A two zero nine three one.",
    "You're welcome. Have a good day.",
]


def demo_brief(case: dict) -> dict:
    """The real brief for the demo patient, computed offline (sample policy, no Nessie/Bright Data)."""
    txns = [{"id": f"t{i}", "merchant": "", **t} for i, t in enumerate(case["transactions"])]
    rec = finance.reconcile(case["bill"], case["eob"], txns)
    inc = finance.income(txns, today=finance._d(case["today"]))
    elig = finance.charity_discount(inc["annual"], case["patient"]["household_size"], SAMPLE_TIERS["tiers"],
                                    insured=True, out_of_pocket=float(case["eob"]["patient_responsibility"]))
    st = finance.settle(rec["patient_responsibility"], rec["paid"], elig["discount_pct"])
    pol = {"policy": SAMPLE_TIERS, "eligibility": elig, "income": inc, "source": "sample policy",
           "target": st["balance"], "refund": st["refund"]}
    return caller.build_brief(case, {"reconciliation": rec}, pol)


async def rep_audio(text: str, voice: voices.Voice) -> tuple[bytes, bool]:
    """μ-law 8 kHz for one rep line (cached). Returns (audio, freshly_generated)."""
    cache = OUT / "cache" / f"{hashlib.sha1(f'{voice.id}|{text}'.encode()).hexdigest()[:16]}.ulaw"
    if cache.exists():
        return cache.read_bytes(), False
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/tts/bytes", headers={"Authorization": f"Bearer {config.CARTESIA_API_KEY}",
                                                      "Cartesia-Version": VERSION},
                         json={"model_id": config.CARTESIA_TTS_MODEL, "transcript": text, **voice.tts_fields(),
                               "output_format": {"container": "raw", "encoding": "pcm_mulaw", "sample_rate": 8000}})
    r.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(r.content)
    return r.content, True


class FakeTwilio:
    """Twilio's side of a bidirectional Media Stream, with a real-time clock for both directions."""

    def __init__(self, ws, stream_sid: str, barge_in: bool) -> None:
        self.ws, self.sid, self.barge_in = ws, stream_sid, barge_in
        self.outq: asyncio.Queue = asyncio.Queue()       # rep audio waiting to be "spoken" into the line
        self.inbound = bytearray()                        # everything we sent (rep + line silence)
        self.agent = bytearray()                          # everything the agent sent us
        self.play_until = 0.0                             # when the agent's queued audio finishes playing
        self.agent_idle = asyncio.Event()
        self.agent_started = asyncio.Event()
        self.closed = asyncio.Event()
        self.clears = 0

    async def send(self, msg: dict) -> None:
        await self.ws.send(json.dumps(msg))

    async def line(self) -> None:
        """20 ms frames forever: queued rep speech, else silence (STT expects a continuous stream)."""
        seq, t0, n = 2, time.monotonic(), 0
        pending = b""
        while not self.closed.is_set():
            if not pending and not self.outq.empty():
                pending = self.outq.get_nowait()
            frame, pending = (pending[:audio.FRAME], pending[audio.FRAME:]) if pending else (audio.silence(20), b"")
            frame = frame.ljust(audio.FRAME, bytes([audio.SILENCE]))
            self.inbound += frame
            seq, n = seq + 1, n + 1
            try:
                await self.send({"event": "media", "sequenceNumber": str(seq), "streamSid": self.sid,
                                 "media": {"track": "inbound", "chunk": str(n), "timestamp": str(n * 20),
                                           "payload": base64.b64encode(frame).decode()}})
            except Exception:
                return
            await asyncio.sleep(max(0, t0 + n * 0.02 - time.monotonic()))

    async def receive(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg["event"] == "media":
                    chunk = base64.b64decode(msg["media"]["payload"])
                    self.agent += chunk
                    now = time.monotonic()
                    self.play_until = max(self.play_until, now) + audio.seconds(len(chunk))
                    self.agent_idle.clear()
                    self.agent_started.set()
                elif msg["event"] == "mark":
                    asyncio.create_task(self._echo_mark(msg["mark"]["name"]))
                elif msg["event"] == "clear":
                    self.clears += 1
                    self.play_until = time.monotonic()
                    self.agent_idle.set()
        except Exception:
            pass
        finally:
            self.closed.set()
            self.agent_idle.set()

    async def _echo_mark(self, name: str) -> None:
        await asyncio.sleep(max(0, self.play_until - time.monotonic()))  # Twilio echoes after playback
        try:
            await self.send({"event": "mark", "streamSid": self.sid, "mark": {"name": name}})
        except Exception:
            pass
        if time.monotonic() >= self.play_until - 0.05:  # idle only once everything queued has played
            self.agent_idle.set()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="priya", help="agent voice: catalog key or Cartesia voice id")
    ap.add_argument("--rep-voice", default="tessa", help="voice playing the billing rep")
    ap.add_argument("--barge-in", action="store_true", help="rep talks over the agent's second reply")
    ap.add_argument("--short", action="store_true", help="3-turn call: hello, plan + collections paused, case number")
    ap.add_argument("--max-seconds", type=float, default=150, help="hard cap on the call (bounds STT usage)")
    args = ap.parse_args()
    if not config.CARTESIA_API_KEY:
        sys.exit("Set CARTESIA_API_KEY in .env")
    if not any(getattr(r, "path", "") == calls.STREAM_PATH for r in app.routes):  # until app.py mounts it
        app.include_router(router)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    serve = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    case = orchestrator.new_case()
    brief = demo_brief(case) | {"patient_authorization": calls.IN_APP}
    voice, rep_voice = voices.resolve(args.voice), voices.resolve(args.rep_voice)
    session = caller.CallSession(case["id"], brief)
    caller.sessions[session.id] = session
    leg = calls.register(Leg(case["id"], voice, brain=session, keyterms=calls.keyterms(brief), greet_after=4.0,
                             label="hospital"))
    print(f"case {case['id']}  agent voice {voice.name} ({voice.accent})  rep voice {rep_voice.name}  "
          f"claude={'live' if config.ANTHROPIC_API_KEY else 'scripted'}")

    rep_tts_s, lines = 0.0, [REP_LINES[i] for i in (0, 3, 4, 5)] if args.short else REP_LINES
    clips = []
    for text in lines:
        clip, fresh = await rep_audio(text, rep_voice)
        clips.append(clip)
        rep_tts_s += audio.seconds(len(clip)) if fresh else 0

    t_start = time.monotonic()
    async with connect(f"ws://127.0.0.1:{PORT}{calls.STREAM_PATH}", max_size=None) as ws:
        tw = FakeTwilio(ws, "MZsim" + case["id"], args.barge_in)
        await tw.send({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        await tw.send({"event": "start", "sequenceNumber": "1", "streamSid": tw.sid,
                       "start": {"streamSid": tw.sid, "callSid": "CAsim", "accountSid": "ACsim", "tracks": ["inbound"],
                                 "customParameters": {"token": leg.token},
                                 "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}})
        tasks = [asyncio.create_task(tw.line()), asyncio.create_task(tw.receive())]
        await asyncio.sleep(0.5)  # "ring", then the rep answers
        deadline = t_start + args.max_seconds
        for i, clip in enumerate(clips):
            if tw.closed.is_set() or time.monotonic() > deadline - 10:
                break
            if args.barge_in and i == 2:  # interrupt 1.5 s into the agent's reply
                await asyncio.wait_for(tw.agent_started.wait(), 30)
                tw.agent_started.clear()
                await asyncio.sleep(1.5)
                tw.outq.put_nowait(audio.silence(1) + clip)
            else:
                tw.agent_started.clear()  # before the line: a fast (speculative) reply can start as the rep finishes
                tw.outq.put_nowait(clip)
            await asyncio.sleep(audio.seconds(len(clip)) + 0.2)
            try:  # wait for the agent to answer and finish speaking
                await asyncio.wait_for(tw.agent_started.wait(), 15)
                while not tw.closed.is_set():
                    await asyncio.wait_for(tw.agent_idle.wait(), max(1, deadline - time.monotonic()))
                    await asyncio.sleep(0.4)
                    if tw.agent_idle.is_set():
                        break
            except asyncio.TimeoutError:
                print(f"  (no reply to rep line {i + 1})")
            await asyncio.sleep(0.6)  # the rep's pause before answering
        await asyncio.sleep(1.0)
        tw.closed.set()
        for t in tasks:
            t.cancel()
    elapsed = time.monotonic() - t_start
    await asyncio.sleep(0.3)

    out = OUT / case["id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "line_inbound.wav").write_bytes(audio.wav(bytes(tw.inbound)))
    (out / "agent_outbound.wav").write_bytes(audio.wav(bytes(tw.agent)))
    transcript = [(e["speaker"], e["text"]) for e in bus.history(case["id"]) if e["kind"] == "transcript"]
    (out / "transcript.json").write_text(json.dumps({"transcript": transcript, "outcome": session.outcome,
                                                     "stats": leg.stats}, indent=1))
    print("\nTRANSCRIPT (rep = Cartesia Ink STT of the synthesized rep; billback = Claude):")
    for who, text in transcript:
        print(f"  {who:8s} {text}")
        if who == "billback" and pronounce.normalize(text, voice.language) != text:
            print(f"  {'':8s} -> Sonic got: {pronounce.normalize(text, voice.language)}")
    s = leg.stats
    print(f"\noutcome: {session.outcome}")
    print(f"turns {s['turns']}  barge-ins {s['barge_ins']} (Twilio clears {tw.clears})")
    print(f"response latency ms (rep turn.end -> first agent audio): {s['latency_ms']}")
    print(f"  of which Claude turn ms: {s['llm_ms']}   Sonic first-chunk ms: {s['tts_first_ms']}")
    print(f"audio: STT streamed {s['audio_in_s']:.1f}s, agent TTS {s['audio_out_s']:.1f}s, "
          f"rep TTS generated {rep_tts_s:.1f}s (cached after first run); call {elapsed:.1f}s")
    print(f"recordings + transcript: {out}")
    server.should_exit = True
    await serve


if __name__ == "__main__":
    asyncio.run(main())

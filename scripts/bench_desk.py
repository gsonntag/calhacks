"""Desk-mode latency bench: what the rep console (web/rep.html) waits for, measured over real HTTP.

    uv run python scripts/bench_desk.py            # streaming path: POST /api/calls/{id}/turn/stream
    uv run python scripts/bench_desk.py --old      # the original path: POST .../turn, then POST /api/tts

It starts the app on 127.0.0.1:8023, opens a desk call for the demo patient (same brief as the phone
simulator) and plays the billing rep's lines. For each turn it prints the time from the rep's "send" to
the first audio the browser can play:
  --old: the turn reply, then the whole WAV from /api/tts (rep.html plays it once the blob has loaded)
  new:   the first audio line of the NDJSON stream (rep.html schedules it with Web Audio right away)
Live Claude and Cartesia; no phone call.
"""
import argparse
import asyncio
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from billback import orchestrator  # noqa: E402
from billback.agents import caller  # noqa: E402
from billback.app import app  # noqa: E402
from billback.voice import calls  # noqa: E402

PORT = 8023
_spec = importlib.util.spec_from_file_location("sim", ROOT / "scripts" / "sim_twilio_stream.py")
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)


async def old_turn(c: httpx.AsyncClient, call_id: str, said: str | None) -> dict:
    t0 = time.monotonic()
    r = (await c.post(f"/api/calls/{call_id}/turn", json={"rep_said": said})).json()
    t_text = time.monotonic()
    wav = await c.post("/api/tts", json={"text": r["say"], "call_id": call_id})
    t_audio = time.monotonic()
    return {"say": r["say"], "ended": r["ended"], "text_ms": round((t_text - t0) * 1000),
            "audio_ms": round((t_audio - t0) * 1000), "bytes": len(wav.content)}


async def new_turn(c: httpx.AsyncClient, call_id: str, said: str | None) -> dict:
    t0 = time.monotonic()
    out = {"text_ms": None, "audio_ms": None, "bytes": 0}
    async with c.stream("POST", f"/api/calls/{call_id}/turn/stream", json={"rep_said": said}) as r:
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            ev = json.loads(line)
            now = round((time.monotonic() - t0) * 1000)
            if ev["type"] == "text" and out["text_ms"] is None:
                out["text_ms"] = now
            elif ev["type"] == "audio":
                out["audio_ms"] = out["audio_ms"] or now
                out["bytes"] += len(ev["data"]) * 3 // 4
            elif ev["type"] == "done":
                out.update(say=ev["say"], ended=ev["ended"])
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", action="store_true", help="measure the original turn + /api/tts path")
    ap.add_argument("--short", action="store_true", help="hello, plan + collections paused, case number, bye")
    args = ap.parse_args()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    serve = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    case = orchestrator.new_case()
    session = caller.CallSession(case["id"], sim.demo_brief(case) | {"patient_authorization": calls.IN_APP})
    caller.sessions[session.id] = session
    lines = [sim.REP_LINES[i] for i in (0, 3, 4, 5)] if args.short else sim.REP_LINES
    run = old_turn if args.old else new_turn
    first_audio = []
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{PORT}", timeout=60) as c:
        for said in lines:
            r = await run(c, session.id, said)
            first_audio.append(r["audio_ms"])
            print(f"rep: {said}\n  billback: {r.get('say')}\n  first text {r['text_ms']} ms, first audio "
                  f"{r['audio_ms']} ms ({r['bytes']} audio bytes)")
            if r.get("ended"):
                break
    ok = [x for x in first_audio if x is not None]
    print(f"\n{'OLD (turn + /api/tts)' if args.old else 'STREAM (turn/stream)'} send -> first playable audio ms: "
          f"{first_audio}  median {statistics.median(ok) if ok else '-'}")
    server.should_exit = True
    await serve


if __name__ == "__main__":
    asyncio.run(main())

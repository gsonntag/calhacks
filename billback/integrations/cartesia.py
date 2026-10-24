"""Cartesia: Managed Agent phone calls + Sonic TTS.

Phone mode: a Cartesia-hosted agent (Claude Haiku on Cartesia) places the outbound call with the case
brief as a dynamic variable. It reports outcomes through a webhook tool on our server, and call events
(turns, completion) arrive at /webhooks/cartesia. We also poll the call as a backup in case webhooks lag.
"""
import asyncio
import json

import httpx

from .. import config

API = "https://api.cartesia.ai"
VERSION = "2026-08-14"


def headers() -> dict:
    return {"X-API-Key": config.CARTESIA_API_KEY, "Cartesia-Version": VERSION, "Content-Type": "application/json"}


def phone_ready() -> bool:
    return bool(config.CARTESIA_API_KEY and config.CARTESIA_AGENT_ID and config.CARTESIA_FROM_NUMBER_ID
                and config.REP_PHONE_NUMBER)


async def _req(method: str, path: str, body: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.request(method, f"{API}{path}", headers=headers(), json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"Cartesia {method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


async def place_call(case_id: str, patient_name: str, brief: dict) -> str:
    data = await _req("POST", "/agents/calls", {
        "from_number_id": config.CARTESIA_FROM_NUMBER_ID, "agent_id": config.CARTESIA_AGENT_ID,
        "ringing_timeout_seconds": 30,
        "outbound_calls": [{"to_number": config.REP_PHONE_NUMBER, "dynamic_variables": {
            "case_id": case_id, "patient_name": patient_name, "case_brief": json.dumps(brief)}}]})
    call = data["calls"][0]
    if call.get("error"):
        raise RuntimeError(f"Cartesia call failed: {call['error']}")
    return call["agent_call_id"]


async def get_call(call_id: str) -> dict:
    return await _req("GET", f"/agents/calls/{call_id}")


async def cancel_call(call_id: str) -> None:
    await _req("POST", f"/agents/calls/{call_id}/cancel")


async def poll_call(call_id: str, on_turn, on_done, every: float = 1.5, max_minutes: float = 15) -> None:
    """Backup for webhooks: emits transcript turns we haven't seen and the final state."""
    for _ in range(int(max_minutes * 60 / every)):
        try:
            call = await get_call(call_id)
        except Exception:
            await asyncio.sleep(every)
            continue
        for i, t in enumerate(call.get("transcript") or []):
            on_turn(i, t.get("role"), t.get("text", ""))
        if call.get("status") in ("completed", "failed"):
            on_done(call)
            return
        await asyncio.sleep(every)


async def tts(text: str) -> bytes:
    """Sonic TTS as WAV, used by the desk-mode call page."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/tts/bytes", headers={"Authorization": f"Bearer {config.CARTESIA_API_KEY}",
                                                      "Cartesia-Version": VERSION},
                         json={"model_id": "sonic-3.6", "transcript": text, "voice": config.CARTESIA_VOICE_ID,
                               "language": "en", "output_format": {"container": "wav", "encoding": "pcm_s16le",
                                                                   "sample_rate": 44100}})
    r.raise_for_status()
    return r.content

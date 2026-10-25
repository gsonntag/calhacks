"""Cartesia: Managed Agent phone calls + Sonic TTS.

(The self-hosted phone pipeline, Twilio + streaming Ink STT + Sonic TTS, is in billback/voice/.)

Phone mode: a Cartesia-hosted agent (Claude Haiku on Cartesia) places the outbound call with the case
brief as a dynamic variable. It reports outcomes through a webhook tool on our server, and call events
(turns, completion) arrive at /webhooks/cartesia. We also poll the call as a backup in case webhooks lag.
"""
import asyncio
import json
import re

import httpx

from .. import config
from ..voice import pronounce as pron, voices

API = "https://api.cartesia.ai"
VERSION = "2026-08-14"


def headers() -> dict:
    return {"X-API-Key": config.CARTESIA_API_KEY, "Cartesia-Version": VERSION, "Content-Type": "application/json"}


def phone_ready() -> bool:
    return bool(config.CARTESIA_API_KEY and config.CARTESIA_AGENT_ID and config.CARTESIA_FROM_NUMBER_ID
                and re.fullmatch(r"\+1\d{10}", config.REP_PHONE_NUMBER or ""))


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


async def poll_call(call_id: str, on_turn, on_done, every: float = 1.5, max_minutes: float = 15,
                    on_error=None) -> None:
    """Backup for webhooks: emits transcript turns we haven't seen and the final state.
    After 3 failed polls in a row, on_error(text) is called once so the dashboard shows why."""
    fails, warned = 0, False
    for _ in range(int(max_minutes * 60 / every)):
        try:
            call = await get_call(call_id)
            fails = 0
        except Exception as e:
            fails += 1
            if fails >= 3 and not warned and on_error:
                warned = True
                on_error(f"Can't read the call status from Cartesia: {e}")
            await asyncio.sleep(every)
            continue
        call = call.get("call", call) if isinstance(call.get("call"), dict) else call
        transcript = call.get("transcript") or []
        for i in range(len(transcript)):
            on_turn(transcript, i)
        if call.get("status") in ("completed", "failed"):
            on_done(call)
            return
        await asyncio.sleep(every)


async def transcribe(audio: bytes, filename: str = "speech.webm", content_type: str = "audio/webm") -> str:
    """Ink batch STT for one short recording (the mic on the Cases page). Written while the account had no
    credits (HTTP 402), so not yet run live; the page falls back to the browser's own transcript."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/stt", headers={"Authorization": f"Bearer {config.CARTESIA_API_KEY}",
                                                "Cartesia-Version": VERSION},
                         files={"file": (filename, audio, content_type)},
                         data={"model": config.CARTESIA_STT_BATCH_MODEL, "language": "en"})
    if r.status_code >= 400:
        raise RuntimeError(f"Cartesia STT -> {r.status_code}: {r.text[:200]}")
    return (r.json().get("text") or "").strip()


async def tts(text: str, voice_id: str | None = None, pronounce: bool = True) -> bytes:
    """Sonic TTS as WAV (desk-mode call page, voice previews). Text goes through the same pronunciation
    normalizer as the phone pipeline; pronounce=False sends it raw (used to demo before/after)."""
    v = voices.resolve(voice_id)
    body = {"model_id": config.CARTESIA_TTS_MODEL, "transcript": pron.normalize(text, v.language) if pronounce else text,
            **v.tts_fields(), "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 44100}}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/tts/bytes", headers={"Authorization": f"Bearer {config.CARTESIA_API_KEY}",
                                                      "Cartesia-Version": VERSION}, json=body)
    r.raise_for_status()
    return r.content

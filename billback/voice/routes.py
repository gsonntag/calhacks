"""HTTP + WebSocket routes for the voice pipeline. Mount with `app.include_router(voice.router)`.

Twilio-facing (PUBLIC_BASE_URL must be the public https URL Twilio reaches):
  POST /voice/twilio/status   call/participant status callback       (X-Twilio-Signature checked)
  POST /voice/twilio/agent    Voice URL of the TwiML App that dials BillBack into a conference (checked)
  WS   /voice/twilio/stream   bidirectional Media Stream; accepted only with a live per-call token
UI-facing:
  GET  /api/voices            voice catalog (accents) for the per-case picker
  POST /api/voices/sample     speak a line in a voice, with or without the pronunciation normalizer (WAV)
  POST /api/voices/normalize  the spoken form of a line, to show what the normalizer changed
"""
from fastapi import APIRouter, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import config
from ..integrations import cartesia, twilio
from . import calls, pronounce, voices
from .media import MediaSession

router = APIRouter()


async def _signed_form(request: Request) -> dict | None:
    """The POST params if X-Twilio-Signature matches, else None. Twilio signs the URL it called, which is
    our public URL (the tunnel), not the local one uvicorn sees."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    url = config.PUBLIC_BASE_URL.rstrip("/") + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return form if twilio.valid_signature(url, form, request.headers.get("x-twilio-signature", "")) else None


@router.post("/voice/twilio/status")
async def twilio_status(request: Request, token: str = ""):
    form = await _signed_form(request)
    if form is None:
        return JSONResponse({"error": "bad signature"}, 403)
    calls.on_status(token, form)
    return Response(status_code=204)


@router.post("/voice/twilio/agent")
async def twilio_agent(request: Request):
    form = await _signed_form(request)
    if form is None:
        return JSONResponse({"error": "bad signature"}, 403)
    leg = calls.legs.get(request.query_params.get("token") or form.get("token", ""))
    xml = calls.stream_twiml(leg) if leg else twilio.say_twiml("Sorry, this call has already ended. Goodbye.")
    return Response(xml, media_type="application/xml")


@router.websocket("/voice/twilio/stream")
async def twilio_stream(ws: WebSocket):
    await ws.accept()
    await MediaSession(ws, calls.legs.get).run()


@router.get("/api/voices")
async def list_voices():
    return {"voices": voices.catalog(), "default": voices.default().id}


class Line(BaseModel):
    text: str
    voice_id: str | None = None
    pronounce: bool = True


@router.post("/api/voices/sample")
async def sample(line: Line):
    if not config.CARTESIA_API_KEY:
        return JSONResponse({"error": "no cartesia key"}, 404)
    return Response(await cartesia.tts(line.text[:500], line.voice_id, line.pronounce), media_type="audio/wav")


@router.post("/api/voices/normalize")
async def normalize(line: Line):
    v = voices.resolve(line.voice_id)
    return {"text": line.text, "spoken": pronounce.normalize(line.text, v.language), "voice": v.name}

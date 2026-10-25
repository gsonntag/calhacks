"""HTTP + WebSocket routes for the voice pipeline. Mount with `app.include_router(voice.router)`.

Twilio-facing (PUBLIC_BASE_URL must be the public https URL Twilio reaches):
  POST /voice/twilio/status   call/participant status callback       (X-Twilio-Signature checked)
  POST /voice/twilio/agent    Voice URL of the TwiML App that dials Remy into a conference (checked)
  WS   /voice/twilio/stream   bidirectional Media Stream; accepted only with a live per-call token
UI-facing:
  POST /api/calls/{id}/turn/stream  desk mode: Remy's reply streamed as text + audio (NDJSON, voice/desk.py)
  WS   /api/calls/{id}/listen       desk mode: browser mic -> Cartesia Ink, turn events back (voice/desk.py)
  POST /api/calls/{id}/interrupt    desk mode: the rep talked over Remy; cut its reply to what played
  GET  /api/voices            voice catalog (accents) for the per-case picker
  POST /api/voices/sample     speak a line in a voice, with or without the pronunciation normalizer (WAV)
  POST /api/voices/normalize  the spoken form of a line, to show what the normalizer changed
"""
from fastapi import APIRouter, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import auth, config
from ..auth import ADMIN
from ..integrations import cartesia, twilio
from . import calls, desk, pronounce, voices
from .media import MediaSession

router = APIRouter()


async def _signed_form(request: Request) -> dict | None:
    """The POST params if X-Twilio-Signature matches, else None. Twilio signs the URL it called, which is
    our public URL (the tunnel), not the local one uvicorn sees."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    url = config.PUBLIC_BASE_URL.rstrip("/") + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    got = request.headers.get("x-twilio-signature", "")
    if twilio.valid_signature(url, form, got):
        return form
    _why_unsigned(request, url, form, got)
    return None


def _why_unsigned(request: Request, url: str, form: dict, got: str) -> None:
    """Diagnostics only (no effect on the answer): which Twilio account sent a request that didn't verify, and
    whether a variant of the URL or params would have matched our auth token."""
    token = config.TWILIO_AUTH_TOKEN or ""
    base = config.PUBLIC_BASE_URL.rstrip("/")
    variants = {"as configured": (url, form), "no query": (base + request.url.path, form),
                "http": (url.replace("https://", "http://", 1), form), "no params": (url, {}),
                "local url": (str(request.url), form)}
    hit = [k for k, (u, f) in variants.items() if got and token and twilio.signature(u, f, token) == got]
    print(f"twilio signature mismatch on {request.url.path}: account {'ours' if form.get('AccountSid') == config.TWILIO_ACCOUNT_SID else repr(form.get('AccountSid', '')[-6:])}, "
          f"header {'present' if got else 'missing'}, content-type {request.headers.get('content-type', '')!r}, "
          f"keys {sorted(form)[:12]}, a variant matches: {hit or 'none'}", flush=True)


@router.post("/voice/twilio/status")
async def twilio_status(request: Request, token: str = ""):
    form = await _signed_form(request)
    if form is None:
        return JSONResponse({"error": "bad signature"}, 403)
    calls.on_status(token, form)
    return Response(status_code=204)


def _our_call(token: str, form: dict) -> bool:
    """An unsigned TwiML fetch is still ours if it names both the leg's secret token and the CallSid Twilio
    returned when we placed that call. Twilio's free trial sends this one request with no X-Twilio-Signature
    (seen 2026-10-25: status callbacks for the same call are signed). Approved by the project owner."""
    leg = calls.legs.get(token)
    return bool(leg and leg.call_sid and form.get("CallSid") == leg.call_sid)


@router.post("/voice/twilio/agent")
async def twilio_agent(request: Request):
    form = await _signed_form(request)
    token = request.query_params.get("token") or (form or {}).get("token", "")
    if form is None:
        if request.headers.get("x-twilio-signature") or not _our_call(token, {k: str(v) for k, v in (await request.form()).items()}):
            return JSONResponse({"error": "bad signature"}, 403)  # a wrong signature is never accepted
        print("twilio agent: unsigned TwiML fetch accepted: token and CallSid match a call we placed", flush=True)
    leg = calls.legs.get(token)
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


@router.post("/api/voices/sample", dependencies=ADMIN)
async def sample(line: Line):
    if not config.CARTESIA_API_KEY:
        return JSONResponse({"error": "no cartesia key"}, 404)
    return Response(await cartesia.tts(line.text[:500], line.voice_id, line.pronounce), media_type="audio/wav")


@router.post("/api/voices/normalize")
async def normalize(line: Line):
    v = voices.resolve(line.voice_id)
    return {"text": line.text, "spoken": pronounce.normalize(line.text, v.language), "voice": v.name}


class DeskTurn(BaseModel):
    rep_said: str | None = None


@router.post("/api/calls/{call_id}/turn/stream", dependencies=ADMIN)
async def desk_turn_stream(call_id: str, t: DeskTurn):
    return await desk.turn_stream(call_id, t.rep_said)


class DeskInterrupt(BaseModel):
    played_s: float | None = None


@router.post("/api/calls/{call_id}/interrupt", dependencies=ADMIN)
async def desk_interrupt(call_id: str, i: DeskInterrupt):
    return await desk.interrupt(call_id, i.played_s)


@router.websocket("/api/calls/{call_id}/listen")
async def desk_listen(ws: WebSocket, call_id: str):
    if not auth.ws_allowed(ws):  # it streams the mic to Cartesia, so it spends credits like the POST routes
        await ws.close(1008)
        return
    await desk.listen(ws, call_id)

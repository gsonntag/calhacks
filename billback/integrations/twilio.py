"""Twilio Programmable Voice over plain REST (no SDK): outbound calls, conference participants, TwiML,
and webhook signature validation.

Calls:        POST /2010-04-01/Accounts/{sid}/Calls.json                    (inline TwiML, status callback)
Participants: POST /2010-04-01/Accounts/{sid}/Conferences/{name}/Participants.json
              (creates the conference on first use; To may be a phone number or "app:AP...?k=v",
              which dials one of our TwiML Apps into the conference: that's how the AI joins one)
Signatures:   X-Twilio-Signature = base64(HMAC-SHA1(auth_token, full_url + sorted(k + v for POST params)))
"""
import base64
import hashlib
import hmac
from urllib.parse import urlsplit
from xml.sax.saxutils import escape, quoteattr

import httpx

from .. import config

API = "https://api.twilio.com/2010-04-01"
CALL_EVENTS = ["initiated", "ringing", "answered", "completed"]


def configured() -> bool:
    return bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_FROM_NUMBER)


async def _req(method: str, path: str, data: dict | None = None) -> dict:
    data = {k: ("true" if v is True else "false" if v is False else v) for k, v in (data or {}).items() if v is not None}
    async with httpx.AsyncClient(timeout=20, auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)) as c:
        r = await c.request(method, f"{API}/Accounts/{config.TWILIO_ACCOUNT_SID}{path}", data=data)
    if r.status_code >= 400:
        raise RuntimeError(f"Twilio {method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


async def create_call(to: str, url: str, status_callback: str) -> str:
    """Outbound call that fetches its TwiML from `url` (POST) when answered. Returns the CallSid.
    Only Url, StatusCallback and StatusCallbackEvent: Twilio's free trial refuses inline Twiml, Timeout and
    StatusCallbackMethod ("trial accounts have limited parameter access"), and the defaults (POST, 60 s) suit us."""
    call = await _req("POST", "/Calls.json", {
        "To": to, "From": config.TWILIO_FROM_NUMBER, "Url": url,
        "StatusCallback": status_callback, "StatusCallbackEvent": CALL_EVENTS})
    return call["sid"]


async def hangup(call_sid: str) -> None:
    await _req("POST", f"/Calls/{call_sid}.json", {"Status": "completed"})


async def add_participant(conference: str, to: str, *, label: str, status_callback: str,
                          end_conference_on_exit: bool = False, timeout: int = 30) -> dict:
    """Dial `to` (a number, or "app:AP...?params" for a TwiML App) into the named conference.
    Returns Twilio's participant resource (has call_sid and conference_sid)."""
    return await _req("POST", f"/Conferences/{conference}/Participants.json", {
        "From": config.TWILIO_FROM_NUMBER, "To": to, "Label": label, "Beep": False, "EarlyMedia": True,
        "StartConferenceOnEnter": True, "EndConferenceOnExit": end_conference_on_exit, "Timeout": timeout,
        "StatusCallback": status_callback, "StatusCallbackMethod": "POST", "StatusCallbackEvent": CALL_EVENTS})


async def end_conference(conference_sid: str) -> None:
    await _req("POST", f"/Conferences/{conference_sid}.json", {"Status": "completed"})


def stream_twiml(ws_url: str, params: dict[str, str]) -> str:
    """Bidirectional Media Stream: Twilio sends the callee's audio to ws_url and plays what we send back.
    <Parameter>s arrive in the stream's "start" message as customParameters."""
    ps = "".join(f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in params.items())
    return f"<Response><Connect><Stream url={quoteattr(ws_url)}>{ps}</Stream></Connect></Response>"


def say_twiml(text: str) -> str:
    return f"<Response><Say>{escape(text)}</Say><Hangup/></Response>"


def ws_url(path: str) -> str:
    return f"wss://{urlsplit(config.PUBLIC_BASE_URL).netloc}{path}"


def signature(url: str, params: dict[str, str], token: str) -> str:
    payload = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def valid_signature(url: str, params: dict[str, str], got: str) -> bool:
    """An unset auth token rejects everything (same rule as the Cartesia webhook secret)."""
    token = config.TWILIO_AUTH_TOKEN
    return bool(token and got) and hmac.compare_digest(signature(url, params, token), got)

"""Places Remy's calls over Twilio and runs them through the media pipeline.

Entry point for the orchestrator: `await run_twilio_call(case, brief)` -> the call outcome dict.

Two call plans, chosen per case (see docs/voice.md for why):

Conference (TWILIO_TWIML_APP_SID set and the patient has a phone): a warm three-way handoff.
  1. dial the patient into conference "remy-<case>"; when they answer, add Remy as a participant
     (To="app:<TwiML App>?token=..." -> our TwiML App returns <Connect><Stream>)
  2. Remy asks the patient for verbal authorization (AuthorizationBrain); the answer goes to the audit log
  3. dial the hospital into the same conference; when billing answers, the same media stream switches to the
     Claude call brain. The patient stays on the line and can confirm the authorization to the rep directly.
Direct (no TwiML App): two plain <Connect><Stream> calls.
  1. call the patient, capture the verbal authorization, hang up
  2. call the hospital; the brief carries the recorded authorization so the agent can cite it

If the patient can't be reached, the in-app approval (already in the audit log) is the recorded agreement.
"""
import asyncio
import contextlib
import re
import statistics

from .. import config
from ..agents import caller
from ..events import bus
from ..integrations import twilio
from . import voices
from .authorization import AuthorizationBrain
from .media import Leg

E164 = re.compile(r"\+[1-9]\d{7,14}")
STREAM_PATH = "/voice/twilio/stream"
IN_APP = {"given": True, "how": "in writing, in the Remy app (approval screen), before this call",
          "patient_on_the_line": False}
DOMAIN_TERMS = ["explanation of benefits", "EOB", "financial assistance", "charity care", "collections",
                "case number", "reference number", "patient responsibility", "adjustment", "account number"]

legs: dict[str, Leg] = {}


class DialError(RuntimeError):
    """The hospital call couldn't be placed; nothing is on the line, so falling back to desk mode is safe."""


def twilio_ready() -> bool:
    return (twilio.configured() and bool(config.CARTESIA_API_KEY) and config.PUBLIC_BASE_URL.startswith("https://")
            and bool(E164.fullmatch(config.REP_PHONE_NUMBER or "")))


def patient_phone(case: dict) -> str:
    p = (case.get("patient") or {}).get("phone") or config.PATIENT_PHONE_NUMBER
    return p if E164.fullmatch(p or "") else ""


def register(leg: Leg) -> Leg:
    legs[leg.token] = leg
    return leg


def stream_twiml(leg: Leg) -> str:
    return twilio.stream_twiml(twilio.ws_url(STREAM_PATH), {"token": leg.token})


def twiml_url(leg: Leg) -> str:
    """Where Twilio fetches the leg's stream TwiML when the call is answered (routes.twilio_agent)."""
    return f"{config.PUBLIC_BASE_URL.rstrip('/')}/voice/twilio/agent?token={leg.token}"


def status_url(leg: Leg) -> str:
    return f"{config.PUBLIC_BASE_URL.rstrip('/')}/voice/twilio/status?token={leg.token}"


def keyterms(brief: dict) -> list[str]:
    names = [brief.get("patient", ""), brief.get("hospital", ""), brief.get("insurer", "")]
    return [*names, *(n.split()[0] for n in names if n), *DOMAIN_TERMS]


def on_status(token: str, params: dict) -> None:
    """Twilio call status callback (calls and conference participants)."""
    leg = legs.get(token)
    if not leg:
        return
    leg.status = params.get("CallStatus", "")
    leg.call_sid = params.get("CallSid") or leg.call_sid
    if leg.status == "in-progress":
        leg.answered.set()
    if leg.status in ("busy", "failed", "no-answer", "canceled"):
        bus.emit(leg.case_id, "warning", "caller", text=f"{leg.label.capitalize() or 'Call'}: {leg.status}")
    if leg.status in ("completed", "busy", "failed", "no-answer", "canceled"):
        leg.done.set()


async def _first(*events: asyncio.Event, timeout: float) -> bool:
    """Wait until any event is set. False on timeout."""
    tasks = [asyncio.create_task(e.wait()) for e in events]
    try:
        done, _ = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        return bool(done)
    finally:
        for t in tasks:
            t.cancel()


async def run_twilio_call(case: dict, brief: dict) -> dict:
    """Run Remy's call(s) for this case. Same contract as the desk path: returns case["call_outcome"],
    emits transcript/call_outcome/call_ended events via the CallSession, and sets case["call_done"].
    Raises DialError (before anything rang) so the caller can fall back to desk mode."""
    voice = voices.resolve(brief.get("voice_id") or case.get("voice_id"))
    session = caller.CallSession(case["id"], brief)
    caller.sessions[session.id] = session
    case["call_id"] = session.id  # the dashboard's hang-up button reaches the call through this
    used: list[Leg] = []
    conference_sid = ""
    try:
        if config.TWILIO_TWIML_APP_SID and patient_phone(case):
            conference_sid = await _conference_call(case, session, voice, used)
        else:
            await _direct_call(case, session, voice, used)
    except DialError:
        caller.sessions.pop(session.id, None)
        case.pop("call_id", None)
        await _hang_up(used, "")
        raise
    except Exception as e:
        bus.emit(case["id"], "warning", "caller", text=f"Phone call error: {type(e).__name__}: {e}")
    await _hang_up(used, conference_sid)
    if not session.ended:
        await session._end("call ended")
    case["call_outcome"].update(session.outcome)
    _emit_metrics(case["id"], voice, used)
    case["call_done"].set()
    return case["call_outcome"]


async def _authorize_direct(case: dict, voice: voices.Voice, used: list[Leg]) -> dict | None:
    """Call the patient for a verbal OK. None if unreachable or unclear (in-app approval stands)."""
    phone = patient_phone(case)
    if not phone:
        return None
    brain = AuthorizationBrain(case["id"], case["patient"]["first_name"], case["hospital"]["name"])
    leg = register(Leg(case["id"], voice, brain=brain, keyterms=[case["patient"]["first_name"]], greet_after=2.5,
                       label="patient"))
    used.append(leg)
    try:
        brain.call_sid = leg.call_sid = await twilio.create_call(phone, twiml_url(leg), status_url(leg))
    except Exception as e:
        bus.emit(case["id"], "warning", "caller", text=f"Couldn't call the patient for verbal authorization: {e}")
        return None
    bus.emit(case["id"], "authorization_call", "caller", to=phone[-4:])
    await _first(leg.done, case["call_done"], timeout=150)
    if brain.granted is None:
        bus.emit(case["id"], "warning", "caller",
                 text="No verbal authorization on the phone; using the patient's in-app approval")
        return None
    return brain.record()


async def _direct_call(case: dict, session: caller.CallSession, voice: voices.Voice, used: list[Leg],
                       ask_patient: bool = True) -> None:
    auth = await _authorize_direct(case, voice, used) if ask_patient else None
    if auth and not auth["given"]:
        bus.emit(case["id"], "warning", "caller", text="The patient declined; Remy did not call the hospital")
        return
    session.brief["patient_authorization"] = auth or IN_APP
    leg = register(Leg(case["id"], voice, brain=session, keyterms=keyterms(session.brief), greet_after=4.0,
                       label="hospital"))
    used.append(leg)
    try:
        leg.call_sid = await twilio.create_call(config.REP_PHONE_NUMBER, twiml_url(leg), status_url(leg))
    except Exception as e:
        raise DialError(str(e)) from e
    bus.emit(case["id"], "call_ready", "caller", mode="phone", transport="twilio", call_id=session.id,
             to=config.REP_PHONE_NUMBER[-4:], voice=voice.name)
    await _first(leg.done, case["call_done"], timeout=900)


async def _conference_call(case: dict, session: caller.CallSession, voice: voices.Voice, used: list[Leg]) -> str:
    """Warm handoff: patient + Remy + hospital in one Twilio conference. Returns the conference SID."""
    name = f"remy-{case['id']}"
    patient = register(Leg(case["id"], voice, label="patient"))
    brain = AuthorizationBrain(case["id"], case["patient"]["first_name"], case["hospital"]["name"], conference=True)
    agent = register(Leg(case["id"], voice, brain=brain, keyterms=keyterms(session.brief), greet_after=2.0,
                         hangup_when_done=False, label="Remy"))
    used += [patient, agent]
    try:
        p = await twilio.add_participant(name, patient_phone(case), label="patient", status_callback=status_url(patient))
    except Exception as e:
        raise DialError(f"couldn't dial the patient: {e}") from e
    conference_sid = p.get("conference_sid", "")
    patient.call_sid = p.get("call_sid", "")
    bus.emit(case["id"], "authorization_call", "caller", to=patient_phone(case)[-4:], conference=True)
    if not await _first(patient.answered, patient.done, timeout=45) or not patient.answered.is_set():
        bus.emit(case["id"], "warning", "caller", text="The patient didn't pick up; calling billing directly")
        await _hang_up([patient], conference_sid)
        used.remove(patient)
        used.remove(agent)
        await _direct_call(case, session, voice, used, ask_patient=False)
        return ""

    a = await twilio.add_participant(name, f"app:{config.TWILIO_TWIML_APP_SID}?token={agent.token}", label="remy",
                                     status_callback=status_url(agent))
    agent.call_sid = brain.call_sid = a.get("call_sid", "")
    await _first(agent.idle, agent.done, patient.done, case["call_done"], timeout=150)
    if not brain.granted or not agent.session:
        if brain.granted is None:
            bus.emit(case["id"], "warning", "caller", text="Didn't get the patient's verbal authorization")
        return conference_sid

    session.brief["patient_authorization"] = brain.record()
    hospital = register(Leg(case["id"], voice, label="hospital"))
    used.append(hospital)
    h = await twilio.add_participant(name, config.REP_PHONE_NUMBER, label="hospital",
                                     status_callback=status_url(hospital), end_conference_on_exit=True)
    hospital.call_sid = h.get("call_sid", "")
    bus.emit(case["id"], "call_ready", "caller", mode="phone", transport="twilio", conference=True,
             call_id=session.id, to=config.REP_PHONE_NUMBER[-4:], voice=voice.name)
    if not await _first(hospital.answered, hospital.done, case["call_done"], timeout=45) or not hospital.answered.is_set():
        return conference_sid
    agent.session.set_brain(session, greet_after=4.0)
    await _first(agent.idle, agent.done, hospital.done, case["call_done"], timeout=900)
    return conference_sid


async def _hang_up(used: list[Leg], conference_sid: str) -> None:
    if conference_sid and twilio.configured():
        with contextlib.suppress(Exception):
            await twilio.end_conference(conference_sid)
    for leg in used:
        if leg.session:
            await leg.session.close()
        if leg.call_sid and not leg.done.is_set() and twilio.configured():
            with contextlib.suppress(Exception):
                await twilio.hangup(leg.call_sid)
        legs.pop(leg.token, None)


def _emit_metrics(case_id: str, voice: voices.Voice, used: list[Leg]) -> None:
    lat = [ms for leg in used for ms in leg.stats["latency_ms"]]
    bus.emit(case_id, "voice_metrics", "caller", voice=voice.name, accent=voice.accent,
             turns=sum(leg.stats["turns"] for leg in used), barge_ins=sum(leg.stats["barge_ins"] for leg in used),
             median_response_ms=round(statistics.median(lat)) if lat else None,
             audio_in_s=round(sum(leg.stats["audio_in_s"] for leg in used), 1),
             audio_out_s=round(sum(leg.stats["audio_out_s"] for leg in used), 1))

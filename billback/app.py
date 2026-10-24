"""FastAPI server: chat intake, case runner, live events, approvals, the call-turn endpoint, and the
case dashboard (persisted cases, follow-ups, "what's the status?", call again)."""
import asyncio
import hmac
import json
from contextlib import asynccontextmanager

import anthropic

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, events, followups, llm, orchestrator
from .agents import caller, intake
from .integrations import cartesia
from .events import bus
from .harness import gate


@asynccontextmanager
async def lifespan(_: FastAPI):
    n = orchestrator.load_cases()
    if n:
        print(f"loaded {n} saved case(s) from {events.DATA_DIR / 'cases'}")
    yield


app = FastAPI(title="BillBack", lifespan=lifespan)
WEB = config.ROOT / "web"
_tasks: set[asyncio.Task] = set()

CHAT_SYSTEM = """You are Billie, BillBack's assistant. BillBack fights incorrect or unaffordable hospital bills:
it checks what was already paid, finds duplicate charges, looks up the hospital's own financial-assistance
policy, calls billing on the patient's behalf, and never pays anything without the patient's approval.
Talk like a calm, warm friend. 1-3 short sentences. Ask at most one question per reply.
Gather: who the bill is from, the amount and any deadline/collections notice, whether insurance paid part,
and ask them to attach the bill and the insurance EOB if they haven't.
As soon as you have the bill (attached or described) and the patient says go or has nothing to add,
call start_case. Don't give legal advice."""

START_TOOL = {"name": "start_case", "description": "Open a case and start working on the bill",
              "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}}}}

CANNED = [
    "Got it. How much is the hospital asking for, and is there a deadline or a collections notice?",
    "Thanks. Did insurance pay part of it? If you have the EOB (Explanation of Benefits), attach it — that's where most mistakes show up.",
    "I have what I need. I'll check what's already been paid, look for duplicate charges, look up the hospital's charity-care policy, and call billing if it makes sense. Nothing gets paid without your approval.",
]


ALREADY = "I'm already on it. Follow along in your case."
NUMERIC = {"bill": ("total_charges", "amount_due"), "eob": ("billed", "allowed", "plan_paid", "patient_responsibility")}


def _launch(case: dict, coro=None) -> dict:
    t = asyncio.create_task(coro or orchestrator.run_case(case))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return case


def _start() -> dict:
    return _launch(orchestrator.new_case())


async def _apply_uploads(case: dict, uploads: list[tuple[str, bytes]]) -> None:
    """Merge Claude's reading of the uploads into the case field by field. Missing, null or non-numeric
    values keep the sample values, unknown keys are ignored, and a failed parse falls back to the sample."""
    try:
        parsed = await intake.parse(case["id"], uploads)
    except Exception as e:
        bus.emit(case["id"], "warning", "intake", text=f"Couldn't read the files, using the sample bill ({type(e).__name__})")
        return
    if not isinstance(parsed, dict):
        return
    for key in ("bill", "eob"):
        part = parsed.get(key)
        if not isinstance(part, dict):
            continue
        clean = {f: v for f, v in part.items() if f in case[key] and v not in (None, "", [])}
        for f in NUMERIC[key]:
            if f in clean:
                try:
                    clean[f] = float(str(clean[f]).replace("$", "").replace(",", ""))
                except ValueError:
                    clean.pop(f)
        case[key] = {**case[key], **clean}
    if isinstance(parsed.get("bill"), dict) and parsed["bill"].get("provider"):
        case["hospital"] = {**case["hospital"], "name": case["bill"]["provider"]}


@app.get("/api/mode")
async def mode():
    return config.mode()


@app.post("/api/chat")
async def chat(history: str = Form("[]"), message: str = Form(""), case_id: str = Form(""),
               files: list[UploadFile] = File(default=[])):
    """history: JSON list of {role, content} (text only). Returns {reply, case_id?}.
    Once a case exists, or when someone asks for an update, Billie answers from the case's stored history."""
    if case_id in orchestrator.cases:  # one chat, one case: never start a second pipeline
        case = orchestrator.cases[case_id]
        if not followups.is_status_question(message, in_case=True):
            return {"reply": ALREADY, "case_id": case_id}
        return {"reply": (await orchestrator.answer_status(case, message))["answer"], "case_id": case_id}
    if followups.is_status_question(message) and orchestrator.cases:
        case = max(orchestrator.cases.values(), key=lambda c: c.get("created_at") or 0)
        return {"reply": (await orchestrator.answer_status(case, message))["answer"], "case_id": case["id"]}
    try:
        msgs = json.loads(history)
    except json.JSONDecodeError:
        msgs = []
    msgs = [m for m in msgs if isinstance(m, dict) and m.get("role") in ("user", "assistant")
            and str(m.get("content", "")).strip()]
    uploads = [(f.filename, await f.read()) for f in files if f.filename]
    note = f" [attached: {', '.join(n for n, _ in uploads)}]" if uploads else ""
    msgs.append({"role": "user", "content": (message or "(sent files)") + note})

    async def launch() -> dict:
        case = orchestrator.new_case()
        if uploads:
            case["documents"] = [n for n, _ in uploads]
            if llm.available():
                await _apply_uploads(case, uploads)
        return _launch(case)

    if llm.available():
        try:
            resp = await llm.create(system=CHAT_SYSTEM, messages=msgs, tools=[START_TOOL], max_tokens=2000, effort="low")
        except anthropic.APIError as e:  # key/rate-limit trouble: keep the demo going on the canned script
            print("chat LLM error, using canned replies:", type(e).__name__)
            resp = None
        if resp is not None:
            reply = " ".join(b.text for b in resp.content if b.type == "text").strip()
            if any(b.type == "tool_use" for b in resp.content):
                case = await launch()
                return {"reply": reply or CANNED[-1], "case_id": case["id"]}
            return {"reply": reply or "Sorry, could you say that again?"}

    turn = sum(1 for m in msgs if m["role"] == "user") - 1
    if turn == len(CANNED) - 1:
        case = await launch()
        return {"reply": CANNED[-1], "case_id": case["id"]}
    return {"reply": CANNED[turn] if turn < len(CANNED) else ALREADY}


@app.post("/api/gmail/scan")
async def gmail_scan():
    """Simulated inbox scan (demo inbox) — real Gmail OAuth is out of scope for the hackathon."""
    c = orchestrator.demo_case()
    return {"simulated": True, "found": [
        {"type": "bill", "from": c["bill"]["provider"], "subject": c["bill"]["notice"], "amount": c["bill"]["amount_due"]},
        {"type": "eob", "from": c["eob"]["insurer"], "subject": f"Explanation of Benefits - claim {c['eob']['claim_number']}"}]}


@app.post("/api/cases")
async def start_case(files: list[UploadFile] = File(default=[]), scenario: str = "default"):
    """Start a case from uploaded bill/EOB files, or the sample bill when none are sent.
    ?scenario=freecare picks the second demo patient (bank history + sample policy)."""
    case = orchestrator.new_case(scenario=scenario)
    uploads = [(f.filename, await f.read()) for f in files if f.filename]
    case["documents"] = [n for n, _ in uploads] or ["sample-hospital-bill.pdf", "sample-insurance-eob.pdf"]
    if uploads and llm.available():
        await _apply_uploads(case, uploads)
    _launch(case)
    return {"case_id": case["id"]}


@app.get("/api/cases")
async def list_cases():
    return [{"id": c["id"], "provider": c["bill"]["provider"], "amount_due": c["bill"]["amount_due"],
             "status": c["status"], "case_number": orchestrator.summary(c)["case_number"],
             "next": orchestrator.next_action(c)} for c in orchestrator.cases.values()]


@app.get("/api/dashboard")
async def dashboard():
    """Every case, newest first: balances, calls, follow-ups and the next action with its due date."""
    rows = [orchestrator.summary(c) for c in orchestrator.cases.values()]
    return sorted(rows, key=lambda r: r.get("created_at") or 0, reverse=True)


def _case_or_404(case_id: str) -> dict | None:
    return orchestrator.cases.get(case_id)


@app.get("/api/cases/{case_id}/summary")
async def case_summary(case_id: str):
    case = _case_or_404(case_id)
    return orchestrator.summary(case) if case else JSONResponse({"error": "not found"}, 404)


class Question(BaseModel):
    question: str = "What's the status?"


@app.post("/api/cases/{case_id}/ask")
async def ask(case_id: str, q: Question):
    """"What's the status?" answered from the case's stored calls, promises, follow-ups and amounts."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    return await orchestrator.answer_status(case, q.question.strip()[:500] or "What's the status?")


@app.post("/api/cases/{case_id}/call-again")
async def call_again(case_id: str):
    """Start a follow-up call; it waits on the patient's approval like the first call."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    if not orchestrator.can_call_again(case):
        return JSONResponse({"error": f"can't call again while the case is {case['status']}"}, 409)
    case["status"] = "awaiting_call_approval"  # claim it now so a double click can't start two calls
    _launch(case, orchestrator.call_again(case))
    return {"ok": True, "case_id": case_id}


class Clock(BaseModel):
    days: int | None = 1  # null resets the clock to today


@app.post("/api/cases/{case_id}/clock")
async def clock(case_id: str, c: Clock):
    """Demo control: simulate days passing on this case so a follow-up comes due on stage."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    orchestrator.advance_clock(case, c.days)
    return orchestrator.summary(case)


@app.get("/api/cases/{case_id}")
async def get_case(case_id: str):
    case = orchestrator.cases.get(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    return {"case": orchestrator.public(case), "events": bus.history(case_id), "approvals": gate.pending_for(case_id)}


@app.websocket("/ws/cases/{case_id}")
async def case_ws(ws: WebSocket, case_id: str):
    await ws.accept()
    q = bus.subscribe(case_id)
    try:
        for e in bus.history(case_id):
            await ws.send_text(json.dumps(e, default=str))
        while True:
            e = await q.get()
            await ws.send_text(json.dumps(e, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(case_id, q)


@app.get("/api/approvals")
async def approvals(case: str):
    return gate.pending_for(case)


class Decision(BaseModel):
    approved: bool


@app.post("/api/approvals/{approval_id}")
async def decide(approval_id: str, d: Decision):
    return {"ok": gate.resolve(approval_id, d.approved)}


class Turn(BaseModel):
    rep_said: str | None = None


@app.post("/api/calls/{call_id}/turn")
async def call_turn(call_id: str, t: Turn):
    s = caller.sessions.get(call_id)
    if not s:
        return JSONResponse({"error": "no such call"}, 404)
    try:
        say = await s.turn(t.rep_said)
    except Exception as e:  # never leave the rep console without a JSON answer
        if s.messages and s.messages[-1]["role"] == "user" and isinstance(s.messages[-1]["content"], str):
            s.messages.pop()
        bus.emit(s.case_id, "warning", "caller", text=f"Call turn failed: {type(e).__name__}")
        return {"say": "Sorry, could you repeat that?", "ended": s.ended, "outcome": s.outcome}
    if s.ended:
        orchestrator.cases[s.case_id]["call_done"].set()
    return {"say": say, "ended": s.ended, "outcome": s.outcome}


@app.post("/api/calls/{call_id}/hangup")
async def hangup(call_id: str):
    s = caller.sessions.get(call_id)
    if s and not s.ended:
        await s._end("hung up")
        orchestrator.cases[s.case_id]["call_done"].set()
    return {"ok": True}


@app.post("/api/cases/{case_id}/end-call")
async def end_call(case_id: str):
    """Manual hang-up for either call mode, so a call that never ends can't stall the case."""
    case = orchestrator.cases.get(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    await orchestrator.end_call(case)
    return {"ok": True}


@app.get("/api/calls/{call_id}")
async def call_info(call_id: str):
    s = caller.sessions.get(call_id)
    return {"brief": s.brief, "ended": s.ended, "outcome": s.outcome} if s else JSONResponse({}, 404)


@app.post("/api/cartesia/tools/record_outcome")
async def cartesia_record_outcome(request: Request):
    """Webhook tool the Cartesia-hosted caller agent calls mid-call."""
    if not _check(request.headers.get("authorization", ""), f"Bearer {config.CARTESIA_TOOL_TOKEN}",
                  config.CARTESIA_TOOL_TOKEN):
        return JSONResponse({"error": "unauthorized"}, 401)
    body = await request.json()
    case = orchestrator.cases.get(body.get("case_id", ""))
    if not case:
        return {"ok": False, "note": "unknown case"}
    orchestrator.record_outcome(case, body)
    return {"ok": True, "recorded": case["call_outcome"]}


_seen_webhooks: set[str] = set()
_logged_shape = False


def _check(got: str, want: str, configured: str) -> bool:
    """Constant-time compare; an unset secret rejects everything."""
    return bool(configured) and hmac.compare_digest(got.encode(), want.encode())


@app.post("/webhooks/cartesia")
async def cartesia_events(request: Request):
    global _logged_shape
    if not _check(request.headers.get("x-webhook-secret", ""), config.CARTESIA_WEBHOOK_SECRET,
                  config.CARTESIA_WEBHOOK_SECRET):
        return JSONResponse({"error": "bad secret"}, 401)
    ev = await request.json()
    if not _logged_shape:  # first delivery: log the payload shape (names only, no values)
        _logged_shape = True
        print("cartesia webhook keys:", sorted(ev.keys()), "headers:", sorted(request.headers.keys()))
    rid = (ev.get("webhook_request_id") or request.headers.get("webhook-request-id")
           or request.headers.get("x-webhook-request-id"))
    if rid:  # dedupe only real ids; record_turn and finish_call are already idempotent
        if rid in _seen_webhooks:
            return {"ok": True}
        _seen_webhooks.add(rid)
    call = ev.get("call") or {}
    turn = ev.get("turn") or {}
    call_id = (call.get("id") or ev.get("call_id") or ev.get("agent_call_id") or call.get("agent_call_id")
               or turn.get("call_id"))
    case_id = (call.get("dynamic_variables") or {}).get("case_id") or orchestrator.calls_by_cartesia_id.get(call_id)
    if not case_id and len(orchestrator.calls_by_cartesia_id) == 1:
        case_id = next(iter(orchestrator.calls_by_cartesia_id.values()))  # only one call runs during the demo
    case = orchestrator.cases.get(case_id or "")
    kind = ev.get("type") or ev.get("event")
    if not case:
        print("cartesia webhook unmatched:", kind, sorted(ev.keys()))
        return {"ok": True}
    if kind == "call_turn":
        orchestrator.record_webhook_turn(case, turn)
    elif kind in ("call_completed", "call_failed"):
        orchestrator.finish_call(case, call)
    elif kind == "post_call_analysis":
        bus.emit(case["id"], "evidence", "caller", label="Call summary",
                 value=(ev.get("analysis") or {}).get("summary", ""), source="Cartesia post-call analysis")
    return {"ok": True}


class VoicePick(BaseModel):
    voice_id: str


@app.post("/api/cases/{case_id}/voice")
async def pick_voice(case_id: str, v: VoicePick):
    """The caller voice/accent for this case's calls (phone pipeline and desk-mode speech)."""
    case = orchestrator.cases.get(case_id)
    if not case:
        return JSONResponse({"error": "no such case"}, 404)
    case["voice_id"] = v.voice_id
    if case.get("result", {}).get("brief") is not None:
        case["result"]["brief"]["voice_id"] = v.voice_id
    return {"ok": True}


class Speak(BaseModel):
    text: str
    voice_id: str | None = None
    call_id: str | None = None


@app.post("/api/tts")
async def speak(s: Speak):
    """Desk-mode voice: Cartesia Sonic if configured, else the browser speaks it."""
    if not config.CARTESIA_API_KEY:
        return JSONResponse({"error": "no cartesia key"}, 404)
    voice_id = s.voice_id
    if not voice_id and s.call_id:  # desk mode speaks in the voice picked for that case
        voice_id = next((c.get("result", {}).get("brief", {}).get("voice_id") for c in orchestrator.cases.values()
                         if c.get("call_id") == s.call_id), None)
    return Response(await cartesia.tts(s.text, voice_id), media_type="audio/wav")


@app.post("/api/reset")
async def reset():
    for t in list(_tasks):
        t.cancel()
    orchestrator.cases.clear()
    caller.sessions.clear()
    orchestrator.calls_by_cartesia_id.clear()
    _seen_webhooks.clear()
    bus.reset()
    events.delete_cases()  # audit logs stay as the audit trail
    return {"ok": True}


@app.get("/api/replay")
async def replay(scenario: str = "default"):
    """Latest recorded run (data/), else the committed fallback recording (demo/)."""
    for f in (events.DATA_DIR / f"replay_{scenario}.json", config.DEMO_DIR / f"replay_{scenario}.json"):
        if f.exists():
            return Response(f.read_text(), media_type="application/json")
    return JSONResponse({"error": "no recording yet: finish one case first"}, 404)


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


# Routers from the policy (Bright Data) and voice agents; optional until both modules land.
try:
    from .policies.routes import router as policies_router
    app.include_router(policies_router)
except ImportError as e:
    print("policies router not mounted:", e)
try:
    from .voice.routes import router as voice_router
    app.include_router(voice_router)
except ImportError as e:
    print("voice router not mounted:", e)


app.mount("/", StaticFiles(directory=WEB), name="web")

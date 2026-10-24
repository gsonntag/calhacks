"""FastAPI server: chat intake, case runner, live events, approvals, and the call-turn endpoint."""
import asyncio
import json

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, llm, orchestrator
from .agents import caller, intake
from .integrations import cartesia
from .events import bus
from .harness import gate

app = FastAPI(title="BillBack")
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


def _launch(case: dict) -> dict:
    t = asyncio.create_task(orchestrator.run_case(case))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return case


def _start() -> dict:
    return _launch(orchestrator.new_case())


@app.get("/api/mode")
async def mode():
    return config.mode()


@app.post("/api/chat")
async def chat(history: str = Form("[]"), message: str = Form(""), files: list[UploadFile] = File(default=[])):
    """history: JSON list of {role, content} (text only). Returns {reply, case_id?}."""
    msgs = json.loads(history)
    uploads = [(f.filename, await f.read()) for f in files if f.filename]
    note = f" [attached: {', '.join(n for n, _ in uploads)}]" if uploads else ""
    msgs.append({"role": "user", "content": (message or "(sent files)") + note})

    if not llm.available():
        turn = sum(1 for m in msgs if m["role"] == "user") - 1
        if turn >= len(CANNED) - 1:
            case = _start()
            return {"reply": CANNED[-1], "case_id": case["id"]}
        return {"reply": CANNED[turn]}

    resp = await llm.create(system=CHAT_SYSTEM, messages=msgs, tools=[START_TOOL], max_tokens=300)
    reply = " ".join(b.text for b in resp.content if b.type == "text").strip()
    if any(b.type == "tool_use" for b in resp.content):
        case = orchestrator.new_case()
        if uploads:
            parsed = await intake.parse(case["id"], uploads)
            case.update({k: v for k, v in parsed.items() if v})
        _launch(case)
        return {"reply": reply or CANNED[-1], "case_id": case["id"]}
    return {"reply": reply}


@app.post("/api/gmail/scan")
async def gmail_scan():
    """Simulated inbox scan (demo inbox) — real Gmail OAuth is out of scope for the hackathon."""
    c = orchestrator.demo_case()
    return {"simulated": True, "found": [
        {"type": "bill", "from": c["bill"]["provider"], "subject": c["bill"]["notice"], "amount": c["bill"]["amount_due"]},
        {"type": "eob", "from": c["eob"]["insurer"], "subject": f"Explanation of Benefits - claim {c['eob']['claim_number']}"}]}


@app.post("/api/cases")
async def start_case(files: list[UploadFile] = File(default=[])):
    """Start a case from uploaded bill/EOB files, or the sample bill when none are sent."""
    case = orchestrator.new_case()
    uploads = [(f.filename, await f.read()) for f in files if f.filename]
    case["documents"] = [n for n, _ in uploads] or ["sample-hospital-bill.pdf", "sample-insurance-eob.pdf"]
    if uploads and llm.available():
        parsed = await intake.parse(case["id"], uploads)
        case.update({k: v for k, v in parsed.items() if v})
    _launch(case)
    return {"case_id": case["id"]}


@app.get("/api/cases")
async def list_cases():
    return [{"id": c["id"], "provider": c["bill"]["provider"], "amount_due": c["bill"]["amount_due"],
             "status": c["status"]} for c in orchestrator.cases.values()]


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
    say = await s.turn(t.rep_said)
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


@app.get("/api/calls/{call_id}")
async def call_info(call_id: str):
    s = caller.sessions.get(call_id)
    return {"brief": s.brief, "ended": s.ended, "outcome": s.outcome} if s else JSONResponse({}, 404)


@app.post("/api/cartesia/tools/record_outcome")
async def cartesia_record_outcome(request: Request):
    """Webhook tool the Cartesia-hosted caller agent calls mid-call."""
    if request.headers.get("authorization") != f"Bearer {config.CARTESIA_TOOL_TOKEN}":
        return JSONResponse({"error": "unauthorized"}, 401)
    body = await request.json()
    case = orchestrator.cases.get(body.get("case_id", ""))
    if not case:
        return {"ok": False, "note": "unknown case"}
    orchestrator.record_outcome(case, body)
    return {"ok": True, "recorded": case["call_outcome"]}


_seen_webhooks: set[str] = set()


@app.post("/webhooks/cartesia")
async def cartesia_events(request: Request):
    if request.headers.get("x-webhook-secret") != config.CARTESIA_WEBHOOK_SECRET:
        return JSONResponse({"error": "bad secret"}, 401)
    ev = await request.json()
    rid = ev.get("webhook_request_id")
    if rid in _seen_webhooks:
        return {"ok": True}
    _seen_webhooks.add(rid)
    call = ev.get("call") or {}
    call_id = call.get("id") or ev.get("call_id") or call.get("agent_call_id")
    case_id = (call.get("dynamic_variables") or {}).get("case_id") or orchestrator.calls_by_cartesia_id.get(call_id)
    case = orchestrator.cases.get(case_id or "")
    if not case:
        return {"ok": True}
    kind = ev.get("type") or ev.get("event")
    if kind == "call_turn":
        t = ev.get("turn") or {}
        orchestrator.record_turn(case, t.get("role"), t.get("text", ""))
    elif kind in ("call_completed", "call_failed"):
        orchestrator.finish_call(case, call)
    elif kind == "post_call_analysis":
        bus.emit(case["id"], "evidence", "caller", label="Call summary",
                 value=(ev.get("analysis") or {}).get("summary", ""), source="Cartesia post-call analysis")
    return {"ok": True}


class Speak(BaseModel):
    text: str


@app.post("/api/tts")
async def speak(s: Speak):
    """Desk-mode voice: Cartesia Sonic if configured, else the browser speaks it."""
    if not config.CARTESIA_API_KEY:
        return JSONResponse({"error": "no cartesia key"}, 404)
    return Response(await cartesia.tts(s.text), media_type="audio/wav")


@app.post("/api/reset")
async def reset():
    for t in list(_tasks):
        t.cancel()
    orchestrator.cases.clear()
    caller.sessions.clear()
    bus.reset()
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")

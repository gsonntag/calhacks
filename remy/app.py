"""FastAPI server: chat intake, case runner, live events, approvals, the call-turn endpoint, and the
case dashboard (persisted cases, follow-ups, "what's the status?", call again)."""
import asyncio
import hmac
import json
import time
from contextlib import asynccontextmanager

import anthropic

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import askremy, auth, config, events, finance, followups, llm, offline, onboarding, orchestrator, tiger
from .agents import caller, decide, intake, policy, talk
from .auth import ADMIN
from .integrations import cartesia, nessie
from .events import bus
from .harness import gate
from .policies import registry
from .policies.store import PolicyStore


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.check_live()  # judging mode: refuse to start on sample data
    if warning := auth.startup_warning():
        print(warning)
    n = orchestrator.load_cases()
    if n:
        print(f"loaded {n} saved case(s) from {events.DATA_DIR / 'cases'}")
    yield


app = FastAPI(title="Remy", lifespan=lifespan)
WEB = config.ROOT / "web"
_tasks: set[asyncio.Task] = set()

CHAT_SYSTEM = """You are Billie, Remy's assistant. Remy fights incorrect or unaffordable hospital bills:
it checks what was already paid, finds duplicate charges, looks up the hospital's own financial-assistance
policy, calls billing on the patient's behalf, and never pays anything without the patient's approval.
Talk like a calm, warm friend. 1-3 short sentences. Ask at most one question per reply.
Gather: who the bill is from, the amount and any deadline/collections notice, whether insurance paid part,
how many people are in their household (it decides charity-care eligibility), roughly what the household earns
a year before taxes (optional: they may skip it; Remy double-checks it against their bank), and ask them to
attach the bill and the insurance EOB if they haven't.
As soon as you have the bill (attached or described) and the patient says go or has nothing to add,
call start_case. Don't give legal advice."""

START_TOOL = {"name": "start_case", "description": "Open a case and start working on the bill",
              "input_schema": {"type": "object", "properties": {
                  "summary": {"type": "string"},
                  "household_size": {"type": "integer"},
                  "income_annual": {"type": "number", "description": "What the household earns a year before "
                                                                     "taxes, if the patient said"}}}}

CANNED = [
    "Got it. How much is the hospital asking for, and is there a deadline or a collections notice?",
    "Thanks. Did insurance pay part of it? If you have the EOB (Explanation of Benefits), attach it — that's where most mistakes show up.",
    "I have what I need. I'll check what's already been paid, look for duplicate charges, look up the hospital's charity-care policy, and call billing if it makes sense. Nothing gets paid without your approval.",
]


ALREADY = "I'm already on it. Follow along in your case."
NUMERIC = {"bill": ("total_charges", "amount_due"), "eob": ("billed", "allowed", "plan_paid", "patient_responsibility")}


def _launch(case: dict, coro=None) -> dict:
    t = asyncio.create_task(coro or orchestrator.run_case(case), name=case["id"])  # named: Stop/Delete find it
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return case


SAMPLE_DOCS = {"default": ("sample-hospital-bill.pdf", "sample-insurance-eob.pdf"),
               "freecare": ("sample-hospital-bill-freecare.pdf", "sample-insurance-eob-freecare.pdf"),
               "raj": ("scenarios/raj-cpmc/bill.pdf", "scenarios/raj-cpmc/eob.pdf")}
REQUIRED = (("patient", "first_name"), ("patient", "last_name"), ("bill", "provider"), ("bill", "amount_due"),
            ("eob", "patient_responsibility"))


class IntakeError(RuntimeError):
    pass


def _merge(base: dict, part: dict) -> dict:
    """Known keys only; skips missing/null values; nested dicts merged the same way."""
    out = dict(base)
    for f, v in part.items():
        if f not in base or v in (None, "", []):
            continue
        out[f] = _merge(base[f], v) if isinstance(base[f], dict) and isinstance(v, dict) else v
    return out


async def _apply_uploads(case: dict, uploads: list[tuple[str, bytes]]) -> None:
    """Live mode: the case is filled only from Claude's reading of the patient's documents. Anything required
    that the documents don't show is an error the patient sees, never a silent sample value."""
    try:
        parsed = await intake.parse(case["id"], uploads)
    except Exception as e:
        raise IntakeError(f"Couldn't read the documents ({type(e).__name__}: {e})") from e
    if not isinstance(parsed, dict):
        raise IntakeError("Couldn't read the documents")
    for key in ("patient", "bill", "eob"):
        part = parsed.get(key)
        if not isinstance(part, dict):
            continue
        clean = _merge(case[key], part)
        for f in NUMERIC.get(key, ()):
            try:
                clean[f] = float(str(clean[f]).replace("$", "").replace(",", ""))
            except ValueError:
                clean[f] = case[key][f]
        case[key] = clean
    missing = [f"{k}.{f}" for k, f in REQUIRED if case[k].get(f) in (None, "", 0.0)]
    if missing:
        raise IntakeError("These weren't on the documents: " + ", ".join(missing) +
                          ". Upload the hospital bill and the insurance statement (EOB).")
    db = PolicyStore()
    h = registry.match(case["bill"]["provider"], db.hospitals())
    case["hospital"] = {**case["hospital"], "name": h.name if h else case["bill"]["provider"],
                        "system": h.system if h else "", "city": h.city if h else ""}
    orchestrator.save(case)


def _sample_uploads(scenario: str) -> list[tuple[str, bytes]]:
    # a generated scenario's files are scenarios/<name>/bill.pdf: named <name>-bill.pdf for the case
    return [(n.removeprefix("scenarios/").replace("/", "-"), (config.DEMO_DIR / n).read_bytes())
            for n in SAMPLE_DOCS.get(scenario, SAMPLE_DOCS["default"])]


def _stated_income(v) -> float | None:
    """A yearly before-tax income the patient gave, or None when missing or implausible."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v, 2) if 0 < v <= 10_000_000 else None


async def _new_case(uploads: list[tuple[str, bytes]], scenario: str = "default", household_size: int | None = None,
                    income_annual: float | None = None) -> dict:
    """Live (Claude available): an empty case filled from the documents; "use the sample bill" sends the sample
    PDFs through the same reading. Offline: the sample case. Starts the pipeline unless intake failed.
    household_size, income_annual (before taxes): what the patient told us in chat."""
    live = llm.available()
    case = orchestrator.new_case(scenario=scenario, from_documents=live)
    if household_size and 1 <= household_size <= 20:  # not on a bill or EOB: the patient tells us
        case["patient"]["household_size"] = int(household_size)
    else:
        household_size = None
    if (income_annual := _stated_income(income_annual)) is not None:  # checked against the bank by the policy agent
        case["patient"].update(stated_income_annual=income_annual, stated_income_source="chat")
    if live and not uploads:
        uploads = _sample_uploads(scenario)
    case["documents"] = [n for n, _ in uploads] or [n.removeprefix("scenarios/").replace("/", "-")
                                                    for n in SAMPLE_DOCS.get(scenario, SAMPLE_DOCS["default"])]
    if live:
        try:
            await _apply_uploads(case, uploads)
        except IntakeError as e:
            orchestrator.set_status(case, "error")
            bus.emit(case["id"], "error", text=str(e))
            return case
    await _from_onboarding(case, household_size is not None, income_annual is not None)
    return _launch(case)


async def _from_onboarding(case: dict, has_household: bool, has_income: bool) -> None:
    """Fill in what the patient told us at onboarding (onboarding.find_profile: Tiger Data, or the local copy
    when Tiger is off; matched on name + date of birth) and didn't already say in chat: household size, income
    before taxes (double-checked against the bank by the policy agent), insurance and medical bills as they
    described them (the documents still decide insured). Never blocks the case."""
    try:
        prof = await onboarding.find_profile(case["patient"])
    except Exception as e:
        print("profile lookup failed:", type(e).__name__)
        return
    if not prof:
        return
    p = case["patient"]
    if not has_household and prof.get("household_size"):
        p.update(household_size=int(prof["household_size"]), household_source="onboarding")
    if not has_income and (income := _stated_income(prof.get("income"))) is not None:
        p.update(stated_income_annual=income, stated_income_source="onboarding")
    if isinstance(prof.get("insured"), bool):
        p["insured_stated"] = prof["insured"]
    if prof.get("medical_oop") is not None:
        p["medical_oop_stated"] = float(prof["medical_oop"])
    orchestrator.save(case)


@app.get("/api/mode")
async def mode():
    return {**config.mode(), "live_crawl": policy.can_crawl()}


@app.post("/api/chat", dependencies=ADMIN)
async def chat(history: str = Form("[]"), message: str = Form(""), case_id: str = Form(""),
               files: list[UploadFile] = File(default=[])):
    """history: JSON list of {role, content} (text only). Returns {reply, case_id?, started?}; started is
    set on the turn that starts the case, so the chat can open the case page right away.
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

    async def launch(household_size: int | None = None, income_annual: float | None = None) -> dict:
        return await _new_case(uploads, household_size=household_size, income_annual=income_annual)

    if llm.available():
        try:
            resp = await llm.create(system=CHAT_SYSTEM, messages=msgs, tools=[START_TOOL], max_tokens=2000, effort="low")
        except anthropic.APIError as e:  # key/rate-limit trouble: keep the demo going on the canned script
            print("chat LLM error, using canned replies:", type(e).__name__)
            resp = None
        if resp is not None:
            reply = " ".join(b.text for b in resp.content if b.type == "text").strip()
            use = next((b for b in resp.content if b.type == "tool_use"), None)
            if use:
                args = use.input if isinstance(use.input, dict) else {}
                hh = args.get("household_size")
                case = await launch(hh if isinstance(hh, int) else None, args.get("income_annual"))
                return {"reply": reply or CANNED[-1], "case_id": case["id"], "started": True}
            return {"reply": reply or "Sorry, could you say that again?"}

    turn = sum(1 for m in msgs if m["role"] == "user") - 1
    if turn == len(CANNED) - 1:
        case = await launch()
        return {"reply": CANNED[-1], "case_id": case["id"], "started": True}
    return {"reply": CANNED[turn] if turn < len(CANNED) else ALREADY}


@app.post("/api/gmail/scan")
async def gmail_scan():
    """Simulated inbox scan (demo inbox) — real Gmail OAuth is out of scope for the hackathon."""
    c = orchestrator.demo_case()
    return {"simulated": True, "found": [
        {"type": "bill", "from": c["bill"]["provider"], "subject": c["bill"]["notice"], "amount": c["bill"]["amount_due"]},
        {"type": "eob", "from": c["eob"]["insurer"], "subject": f"Explanation of Benefits - claim {c['eob']['claim_number']}"}]}


@app.post("/api/cases", dependencies=ADMIN)
async def start_case(files: list[UploadFile] = File(default=[]), scenario: str = "default",
                     household_size: int | None = None, income_annual: float | None = None):
    """Start a case from uploaded bill/EOB files, or the sample bill when none are sent.
    ?scenario=freecare picks the second demo patient (bank history + sample policy); ?scenario=raj the hero
    case (demo/scenarios/raj-cpmc: bill errors, a missed payment, and a bank history that qualifies him).
    ?income_annual: what the household earns a year before taxes, if the patient said."""
    uploads = [(f.filename, await f.read()) for f in files if f.filename]
    case = await _new_case(uploads, scenario, household_size, income_annual)
    return {"case_id": case["id"]}


@app.get("/api/cases")
async def list_cases():
    return [{"id": c["id"], "provider": c["bill"]["provider"], "amount_due": c["bill"]["amount_due"],
             "status": c["status"], "case_number": orchestrator.summary(c)["case_number"],
             "next": orchestrator.next_action(c)} for c in orchestrator.cases.values()]


class Onboarding(BaseModel):
    answers: dict = {}
    skipped: list[str] = []


_bank_cache: dict[tuple[str, str], dict | None] = {}


async def _bank_income(first: str | None, last: str | None) -> dict | None:
    """Take-home income from the patient's Nessie account (same finance.income a case uses), or None."""
    if config.OFFLINE:
        return offline.bank_income(first, last)
    if not (config.NESSIE_API_KEY and first and last):
        return None
    key = (first.lower(), last.lower())
    if key not in _bank_cache:
        try:
            client = nessie.NessieClient(config.NESSIE_API_KEY, config.NESSIE_BASE_URL)
            found = await client.find_account(first, last)
            inc = finance.income(await client.transactions(found["account_id"])) if found else None
            _bank_cache[key] = ({"annual": inc["annual"], "source": inc["source"]}
                                if inc and inc["annual"] else None)
        except Exception as e:
            print("onboarding bank lookup failed:", type(e).__name__)
            return None
    return _bank_cache[key]


async def _assess(o: Onboarding) -> tuple[dict, dict]:
    a = onboarding.clean(o.answers)
    bank = await _bank_income(a.get("first_name"), a.get("last_name"))
    hs = await asyncio.to_thread(onboarding.hospitals_with_tiers)
    # answers that were sent but didn't pass clean(): the page says so instead of silently asking again
    invalid = [k for k in onboarding.FIELDS if o.answers.get(k) is not None and k not in a and k != "hospital_id"]
    return a, {**onboarding.assess(a, bank, o.skipped, hs), "bank": bank, "invalid": invalid}


@app.post("/api/onboarding/assess", dependencies=ADMIN)
async def onboarding_assess(o: Onboarding):
    """The answers so far -> how certain the outcome is, and the one question that would narrow it most."""
    return (await _assess(o))[1]


@app.post("/api/onboarding", dependencies=ADMIN)
async def onboarding_save(o: Onboarding):
    """Save the household's profile (Tiger Data, or locally when it's off). A later case for the same name + DOB
    uses its household size, income before taxes (checked against the bank) and insurance answer."""
    a, out = await _assess(o)
    if not (a.get("first_name") and a.get("last_name")):
        raise HTTPException(400, "First and last name are required")
    hosp = next((h["name"] for h in out["per_hospital"] if h["id"] == a.get("hospital_id")), None)
    kept = await onboarding.save_profile(a, {k: v for k, v in out.items() if k != "bank"}, hosp)
    return {**out, **kept}


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
    finding: str | None = None  # one finding: answered only from that agent's evidence
    agent: str | None = None  # the agent chat: remy | auditor | policy | decide
    page: str = "case"  # the tab the chat was opened on (Remy sees every tab; askremy.PAGES)


@app.post("/api/cases/{case_id}/ask", dependencies=ADMIN)
async def ask(case_id: str, q: Question):
    """"What's the status?" answered from the case's stored calls, promises, follow-ups and amounts; with
    `finding`, the agent that found it answers from its own evidence (changes nothing). With `agent` (the case
    page's agent chat): Remy answers about the whole case and hands plan changes to the planner; the Nessie
    and Bright Data agents answer from everything they checked; the planner is the planner chat."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    raw = q.question.strip()[:500]
    question = askremy.redact(raw)  # SSN / card / account digits never reach the log, memory or a model
    if q.agent == "remy":  # Remy sees the raw text only to refuse it, and redacts it itself
        out = await _decide_call(orchestrator.ask_remy(case, raw or "What's the status?", q.page))
        orchestrator.save(case)  # a handoff may have made a new plan version
        return out
    if q.agent == "decide":
        if not question:
            return JSONResponse({"error": "empty message"}, 400)
        out = await _decide_call(decide.chat(case, question))
        orchestrator.save(case)
        return out if isinstance(out, JSONResponse) else {**out, "answer": out["reply"], "agent": "decide", "agent_name": "Planner"}
    if q.agent:
        return await _decide_call(decide.answer_agent(case, q.agent, question or "What did you find?"))
    if q.finding:
        return await _decide_call(decide.answer_finding(case, q.finding, question or "Tell me more about this"))
    return await orchestrator.answer_status(case, question or "What's the status?")


class RemyQuestion(BaseModel):
    question: str = "What's the status?"
    page: str = "cases"
    case_id: str | None = None
    history: list[dict] | None = None  # [{role, content}]: overrides the stored memory for this call
    session_id: str | None = None  # keys the case-less memory
    profile: dict | None = None  # an onboarding profile, for Get started questions


@app.post("/api/remy/ask", dependencies=ADMIN)
async def ask_remy_anywhere(q: RemyQuestion):
    """Ask Remy from any tab. With an open case, the same answer path as the case page's chat; without one,
    Remy answers from every tab's data and keeps the turns per session or page (data/remy_chat.json).
    Returns {answer, agent (the route used: remy | auditor | policy | decide), route, sources}, plus
    refused: true when Remy refused."""
    question = q.question.strip()[:500] or "What's the status?"
    case = _case_or_404(q.case_id) if q.case_id else None
    if q.case_id and not case:
        return JSONResponse({"error": "not found"}, 404)
    if case:
        out = await _decide_call(orchestrator.ask_remy(case, question, q.page, q.history, q.profile))
        orchestrator.save(case)
        return out
    return await orchestrator.ask_general(question, q.page, q.history, q.session_id, q.profile)


@app.get("/api/remy/chat", dependencies=ADMIN)
async def remy_chat(session_id: str = ""):
    """One browser session's case-less chat (oldest first); none without a session id. Admin key: it's what the
    patient typed."""
    return askremy.chat_log(session_id or None)


async def _decide_call(coro):
    """Step 3 actions: a rule refusal is a 409 with the reason the patient sees."""
    try:
        out = await coro
    except decide.Refused as e:
        return JSONResponse({"error": str(e)}, 409)
    return out if out is not None else {"ok": True}


class PlannerMsg(BaseModel):
    message: str


@app.post("/api/cases/{case_id}/planner", dependencies=ADMIN)
async def planner_chat(case_id: str, m: PlannerMsg):
    """Talk to the planner: questions are answered here; change requests make a new plan version."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    if not m.message.strip():
        return JSONResponse({"error": "empty message"}, 400)
    out = await _decide_call(decide.chat(case, m.message))
    orchestrator.save(case)
    return out


class PlanPick(BaseModel):
    v: int


@app.post("/api/cases/{case_id}/plan/approve", dependencies=ADMIN)
async def plan_approve(case_id: str, p: PlanPick):
    """"Call now": approves the version on screen (must be the latest) and lets the call start."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    pending = next((a for a in gate.pending_for(case_id) if a["agent"] == "caller"), None)
    if not pending:
        return JSONResponse({"error": "no call is waiting for approval"}, 409)
    try:
        decide.approve(case, p.v)
    except decide.Refused as e:
        return JSONResponse({"error": str(e)}, 409)
    orchestrator.save(case)
    return {"ok": gate.resolve(pending["approval_id"], True), "v": p.v}


@app.post("/api/cases/{case_id}/plan/decline", dependencies=ADMIN)
async def plan_decline(case_id: str):
    """"Not now": no call; the case can still call again later."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    pending = next((a for a in gate.pending_for(case_id) if a["agent"] == "caller"), None)
    return {"ok": bool(pending) and gate.resolve(pending["approval_id"], False)}


@app.post("/api/cases/{case_id}/plan/restore", dependencies=ADMIN)
async def plan_restore(case_id: str, p: PlanPick):
    """Go back to an older version: a new version with its choices (facts stay current)."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    out = await _decide_call(decide.restore(case, p.v))
    orchestrator.save(case)
    return out


class Correction(BaseModel):
    finding: str
    note: str = ""


@app.post("/api/cases/{case_id}/corrections", dependencies=ADMIN)
async def add_correction(case_id: str, c: Correction):
    """"This is wrong" on a finding: saved as a correction, and the plan is recalculated. Until the call starts."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    out = await _decide_call(decide.correct(case, c.finding, c.note))
    orchestrator.save(case)
    return out


@app.delete("/api/cases/{case_id}/corrections/{correction_id}", dependencies=ADMIN)
async def undo_correction(case_id: str, correction_id: str):
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    out = await _decide_call(decide.undo_correction(case, correction_id))
    orchestrator.save(case)
    return out


_rechecks: dict[str, asyncio.Lock] = {}
MAX_RECHECKS_PER_CASE = 5  # each is capped at policy.LIVE_MAX_REQUESTS Bright Data requests


@app.post("/api/cases/{case_id}/policy/recheck", dependencies=ADMIN)
async def policy_recheck(case_id: str):
    """"Recheck now" on the Bright Data agent's panel: check the case's hospital live and stream every request
    into the panel. Needs Bright Data + Claude keys; one at a time per case, at most MAX_RECHECKS_PER_CASE."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    lock = _rechecks.setdefault(case_id, asyncio.Lock())
    if lock.locked():
        return JSONResponse({"error": "A recheck is already running"}, 409)
    if len(case.get("policy_rechecks") or []) >= MAX_RECHECKS_PER_CASE:
        return JSONResponse({"error": f"This case already used its {MAX_RECHECKS_PER_CASE} live rechecks"}, 429)
    async with lock:
        try:
            out = await policy.recheck_case(case)
        except policy.RecheckRefused as e:
            return JSONResponse({"error": str(e)}, 409 if "Review" in str(e) else 503)
    orchestrator.save(case)
    return out


class CallAgain(BaseModel):
    v: int | None = None


@app.post("/api/cases/{case_id}/call-again", dependencies=ADMIN)
async def call_again(case_id: str, p: CallAgain | None = None):
    """Start a follow-up call. With v ("Call now" on step 5's plan) that version is approved and the call
    starts; without it, it waits on the patient's approval like the first call."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    if not orchestrator.can_call_again(case):
        return JSONResponse({"error": f"can't call again while the case is {case['status']}"}, 409)
    v = p.v if p else None
    cur = decide.latest(case)
    if v is not None and (not cur or cur["v"] != v):
        return JSONResponse({"error": "That isn't the latest plan: go back to it first"}, 409)
    case["status"] = "awaiting_call_approval"  # claim it now so a double click can't start two calls
    _launch(case, orchestrator.call_again(case, v))
    return {"ok": True, "case_id": case_id}


_stt_off_until = 0.0  # after Cartesia refuses (e.g. 402, no credits), use the browser's transcript for a while


@app.post("/api/cases/{case_id}/talk", dependencies=ADMIN)
async def talk_turn(case_id: str, audio: UploadFile | None = File(None), heard: str = Form("")):
    """The mic on the Cases page: one spoken turn. Cartesia Ink transcribes the recording; `heard` is the
    browser's own transcript, used when Cartesia can't. Actions run only after a spoken yes (agents/talk.py),
    through the same code as the buttons."""
    global _stt_off_until
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    text, by = "", None
    if audio is not None and config.CARTESIA_API_KEY and time.time() > _stt_off_until:
        try:
            text, by = await cartesia.transcribe(await audio.read(), audio.filename or "speech.webm",
                                                 audio.content_type or "audio/webm"), "cartesia"
        except Exception as e:
            print("Cartesia STT failed, using the browser's transcript:", e)
            _stt_off_until = time.time() + 600
    if not text.strip() and heard.strip():
        text, by = heard.strip(), "browser"
    if not text.strip() and config.OFFLINE:  # no-wifi demo: no speech service, so a demo patient line
        text, by = offline.demo_utterance(case), "demo"
    text = text.strip()[:500]
    if not text:
        return {"heard": "", "heard_by": None, "reply": "Sorry, I didn't catch that. Hold the button and try again.",
                "did": None, "pending": None}

    async def run(kind: str, opt: dict) -> str | None:
        if kind == "start_call":
            out = await plan_approve(case_id, PlanPick(v=opt["v"]))
        elif kind == "not_now":
            out = await plan_decline(case_id)
        elif kind == "call_again":
            out = await call_again(case_id, CallAgain(v=opt["v"]))
        else:  # the payment plan after the call
            out = {"ok": gate.resolve(opt["approval_id"], kind == "approve_payment_plan")}
        if isinstance(out, JSONResponse):
            return json.loads(out.body).get("error") or "something went wrong"
        return None if out.get("ok", True) else "it was already decided"

    out = await talk.turn(case, text, run)
    orchestrator.save(case)
    return {"heard": text, "heard_by": by, **out}


class Clock(BaseModel):
    days: int | None = 1  # null resets the clock to today


@app.post("/api/cases/{case_id}/clock", dependencies=ADMIN)
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


@app.post("/api/approvals/{approval_id}", dependencies=ADMIN)
async def resolve_approval(approval_id: str, d: Decision):
    return {"ok": gate.resolve(approval_id, d.approved)}


class Turn(BaseModel):
    rep_said: str | None = None


@app.post("/api/calls/{call_id}/turn", dependencies=ADMIN)
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


@app.post("/api/calls/{call_id}/hangup", dependencies=ADMIN)
async def hangup(call_id: str):
    s = caller.sessions.get(call_id)
    if s and not s.ended:
        await s._end("hung up")
        orchestrator.cases[s.case_id]["call_done"].set()
    return {"ok": True}


@app.post("/api/cases/{case_id}/end-call", dependencies=ADMIN)
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


@app.post("/api/cases/{case_id}/voice", dependencies=ADMIN)
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


@app.post("/api/tts", dependencies=ADMIN)
async def speak(s: Speak):
    """Desk-mode voice: Cartesia Sonic if configured, else the browser speaks it."""
    if not config.CARTESIA_API_KEY:
        return JSONResponse({"error": "no cartesia key"}, 404)
    voice_id = s.voice_id
    if not voice_id and s.call_id:  # desk mode speaks in the voice picked for that case
        voice_id = next((c.get("result", {}).get("brief", {}).get("voice_id") for c in orchestrator.cases.values()
                         if c.get("call_id") == s.call_id), None)
    return Response(await cartesia.tts(s.text, voice_id), media_type="audio/wav")


async def _stop_runs(case_id: str) -> None:
    """Cancel the agents working on one case and drop the approvals they were waiting on."""
    running = [t for t in _tasks if t.get_name() == case_id and not t.done()]
    for t in running:
        t.cancel()
    if running:
        await asyncio.wait(running, timeout=5)
    gate.drop(case_id)


def _not_on_a_call(case: dict) -> JSONResponse | None:
    if case["status"] == "calling":  # the line is open: hang up from the case page first
        return JSONResponse({"error": "Remy is on a call for this case: end the call first"}, 409)
    return None


@app.post("/api/cases/{case_id}/stop", dependencies=ADMIN)
async def stop_case(case_id: str):
    """Stop the agents. A run can't resume mid-step, so the case is left interrupted (as after a restart);
    once its review finished it can still be called again."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    if busy := _not_on_a_call(case):
        return busy
    if case["status"] not in orchestrator.ACTIVE:
        return JSONResponse({"error": f"nothing is running: the case is {case['status']}"}, 409)
    await _stop_runs(case_id)
    orchestrator.set_status(case, "interrupted")
    bus.emit(case_id, "warning", text="Stopped by you. Nothing more runs on this case unless you call again.")
    return {"ok": True, "status": case["status"]}


class Archive(BaseModel):
    archived: bool = True


@app.post("/api/cases/{case_id}/archive", dependencies=ADMIN)
async def archive_case(case_id: str, a: Archive | None = None):
    """Move a case out of the rail's main list (or back). Nothing about the case itself changes."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    case["archived"] = a.archived if a else True
    orchestrator.save(case)
    return {"ok": True, "archived": case["archived"]}


@app.delete("/api/cases/{case_id}", dependencies=ADMIN)
async def delete_case(case_id: str):
    """Stop the case's agents and delete it. Its audit log stays as the audit trail, as with reset."""
    case = _case_or_404(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, 404)
    if busy := _not_on_a_call(case):
        return busy
    await _stop_runs(case_id)
    orchestrator.cases.pop(case_id, None)
    orchestrator.banks.pop(case_id, None)
    caller.sessions.pop(case.get("call_id") or "", None)
    for call_id in [k for k, v in orchestrator.calls_by_cartesia_id.items() if v == case_id]:
        del orchestrator.calls_by_cartesia_id[call_id]
    bus.forget(case_id)
    events.delete_case(case_id)
    return {"ok": True}


@app.post("/api/reset", dependencies=ADMIN)
async def reset():
    for t in list(_tasks):
        t.cancel()
    orchestrator.cases.clear()
    caller.sessions.clear()
    orchestrator.calls_by_cartesia_id.clear()
    _seen_webhooks.clear()
    bus.reset()
    events.delete_cases()  # audit logs stay as the audit trail
    onboarding.clear_local_profiles()  # a saved profile would otherwise change every later demo case for that patient
    askremy.clear_log()
    return {"ok": True}


@app.get("/api/replay")
async def replay(scenario: str = "default"):
    """Latest recorded run (data/), else the committed fallback recording (demo/)."""
    for f in (events.DATA_DIR / f"replay_{scenario}.json", config.ROOT / "data" / f"replay_{scenario}.json",
              config.DEMO_DIR / f"replay_{scenario}.json"):
        if f.exists():
            return Response(f.read_text(), media_type="application/json")
    return JSONResponse({"error": "no recording yet: finish one case first"}, 404)


@app.get("/")
async def index():
    """Cases (the case page with its rail) is home; with no cases yet, home is the chat intake."""
    return FileResponse(WEB / ("case.html" if orchestrator.cases else "new.html"))


# Routers: hospital policy database (Bright Data) and the Twilio/Cartesia voice pipeline.
from .policies.routes import router as policies_router  # noqa: E402
from .voice.routes import router as voice_router  # noqa: E402

app.include_router(policies_router)
app.include_router(voice_router)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")

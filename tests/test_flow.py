"""Offline end-to-end: sample case -> approve call -> desk-mode call -> case_done at $0 with $500 back."""
import asyncio
import json
from datetime import date

import httpx
import pytest

from remy import app as app_mod
from remy import config, orchestrator
from remy.agents import caller
from remy.events import bus

# Kinds web/case.html renders from the /ws/cases/{id} stream.
CASE_HTML_KINDS = {"case_started", "agent_started", "agent_finished", "step", "review_done", "balance",
                   "approval_requested", "approval_resolved", "call_ready", "transcript", "call_outcome",
                   "call_ended", "tool_result", "policy_quote", "case_done", "finding"}


class _Today(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 24)


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(orchestrator, "date", _Today)
    transport = httpx.ASGITransport(app=app_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
        await c.post("/api/reset")


async def _wait_for(case_id: str, pred, timeout: float = 20):
    for _ in range(int(timeout / 0.05)):
        for e in bus.history(case_id):
            if e["kind"] == "error":
                pytest.fail(f"case errored: {e['text']}")
            if pred(e):
                return e
        await asyncio.sleep(0.05)
    pytest.fail("timed out waiting for event")


async def _approve(c, case_id: str, agent: str) -> None:
    e = await _wait_for(case_id, lambda e: e["kind"] == "approval_requested" and e["agent"] == agent)
    r = await c.post(f"/api/approvals/{e['approval_id']}", json={"approved": True})
    assert r.json() == {"ok": True}
    return e


async def test_offline_end_to_end(client):
    c = client
    case_id = (await c.post("/api/cases")).json()["case_id"]

    await _approve(c, case_id, "caller")
    ready = await _wait_for(case_id, lambda e: e["kind"] == "call_ready")
    assert ready["mode"] == "desk" and ready["rep_url"] == f"/rep.html?call={ready['call_id']}"

    rep_lines = ["Patient Financial Services, this is Diane, how can I help you?",
                 "Okay, let me pull that up.", "I see it.", "Okay.",
                 "Yes, I've opened a financial assistance review and put the account on hold.",
                 "Your case number is 58213.", "Yes, that's right."]
    for line in rep_lines:
        r = (await c.post(f"/api/calls/{ready['call_id']}/turn", json={"rep_said": line})).json()
        assert r["say"]
    assert r["ended"] is True
    assert r["outcome"]["case_number"] == "58213"

    # $600 is still owed: the interest-free plan (Sutter's terms) is proposed; the patient decides on it, no OK asked
    done = await _wait_for(case_id, lambda e: e["kind"] == "case_done")
    s = done["summary"]
    # a review and a hold are not a reduction: billing still shows $8,700 until it confirms the $600
    assert s["started_at"] == 8700 and s["now"] == 8700 and s["refund"] == 0
    assert s["expected"] == 600 and s["confirmed"] is False
    assert s["case_number"] == "58213"
    assert s["plan"]["months"] == 0 and s["plan_created"] is False  # payment plans are left off entirely

    hist = bus.history(case_id)
    kinds = {e["kind"] for e in hist}
    assert CASE_HTML_KINDS <= kinds, CASE_HTML_KINDS - kinds

    # both app agents report in the shared format (agents/report.py): named panels, steps with proof, findings
    started = {e["agent"]: e for e in hist if e["kind"] == "agent_started" and e["agent"] in ("auditor", "policy")}
    assert started["auditor"]["name"] == "Nessie agent" and started["policy"]["name"] == "Bright Data agent"
    assert all(started[a]["covers"] for a in started)
    steps = [e for e in hist if e["kind"] == "step"]
    assert all(e["status"] in {"running", "ok", "flag", "clear", "win", "wait"} and isinstance(e["proof"], list) for e in steps)
    paid = next(e for e in steps if e["key"] == "auditor:payments" and e["status"] != "running")
    assert paid["status"] == "flag" and any("$500.00" in p["text"] and p["source"] for p in paid["proof"])
    finds = {e["title"]: e for e in hist if e["kind"] == "finding"}
    assert finds["Insurance wasn’t applied"]["saved"] == 7600
    assert finds["A payment you made was missed"]["saved"] == 500
    assert not any("payment plan" in t.lower() for t in finds)  # payment plans are left off entirely
    assert all(f["proof"] and f["source"] for f in finds.values())
    assert [e["amount"] for e in hist if e["kind"] == "balance"] == [8700, 600, 8700]
    assert [e for e in hist if e["kind"] == "balance"][-1]["label"] == "Billing still shows"
    # nothing asks to set the plan up in the bank: the patient decides at the end
    assert not any(e["kind"] == "approval_requested" and e["agent"] == "planner" for e in hist)
    assert not any(e["kind"] == "tool_call" and e.get("tool") == "create_payment_plan" for e in hist)
    assert orchestrator.cases[case_id]["status"] == "done"
    # the "Okay." repeats from the rep all show in the transcript
    assert sum(1 for e in hist if e["kind"] == "transcript" and e["speaker"] == "rep") == len(rep_lines)


async def _offline_call(c, scenario: str) -> tuple[dict, list[str], dict]:
    """Runs a demo case's offline call; returns (call brief, what Remy said, the call's outcome)."""
    case_id = (await c.post(f"/api/cases?scenario={scenario}")).json()["case_id"]
    await _approve(c, case_id, "caller")
    ready = await _wait_for(case_id, lambda e: e["kind"] == "call_ready")
    for _ in range(10):
        r = (await c.post(f"/api/calls/{ready['call_id']}/turn", json={"rep_said": "Your case number is 58213."})).json()
        if r["ended"]:
            break
    said = [e["text"] for e in bus.history(case_id) if e["kind"] == "transcript" and e["speaker"] == "remy"]
    return orchestrator.cases[case_id]["result"]["brief"], said, r["outcome"]


async def test_offline_caller_says_only_the_plans_asks(client):
    """Every line on the call is backed by an ask in the approved plan (code-enforced guardrails)."""
    # Rosa: 228% of the poverty line, not eligible at her hospital; the plan asks insurance and payment (never a plan)
    brief, said, outcome = await _offline_call(client, "default")
    assert "discount" not in brief["ask_kinds"] and "plan" not in brief["ask_kinds"]
    assert brief["income_pct_fpl"] is None and brief["policy_quotes"] == [] and brief["policy_tier"] is None
    text = " ".join(said).lower()
    assert "qualif" not in text and "financial assistance" not in text and "poverty" not in text
    assert "insurance adjustment" in text and "$500 payment" in text and "payment plan" not in text
    assert outcome["case_number"] == "58213" and outcome["account_on_hold"] is True
    assert not outcome.get("assistance_review_opened")
    await client.post("/api/reset")

    # Marcus: the bill already matches his insurance; the plan asks for free care only
    brief, said, outcome = await _offline_call(client, "freecare")
    assert "insurance" not in brief["ask_kinds"] and "discount" in brief["ask_kinds"]
    assert brief["income_pct_fpl"] is not None and brief["policy_quotes"]
    text = " ".join(said).lower()
    assert "insurance adjustment" not in text and "corrected balance" not in text
    assert "qualifies under your own financial assistance policy" in text
    assert outcome["case_number"] == "58213" and outcome["assistance_review_opened"] is True


async def test_chat_starts_exactly_one_case(client):
    c = client
    case_id, history, started = None, [], []
    for msg in ["hi", "4000", "yes", "thanks!"]:
        r = (await c.post("/api/chat", data={"history": json.dumps(history), "message": msg,
                                             "case_id": case_id or ""})).json()
        assert r["reply"]
        started.append(r.get("started", False))
        history += [{"role": "user", "content": msg}, {"role": "assistant", "content": r["reply"]}]
        case_id = r.get("case_id") or case_id
    assert case_id and list(orchestrator.cases) == [case_id]
    assert started.count(True) == 1  # only the turn that starts the case sends the chat to the case page
    # an old client that doesn't send case_id still can't start a second one
    hist = '[{"role":"user","content":"a"},{"role":"assistant","content":"b"},{"role":"user","content":"c"},' \
           '{"role":"assistant","content":""},{"role":"user","content":"d"}]'
    r = (await c.post("/api/chat", data={"history": hist, "message": "thanks"})).json()
    assert "case_id" not in r and len(orchestrator.cases) == 1


async def test_webhooks_without_request_id_are_not_dropped(client, monkeypatch):
    c = client
    monkeypatch.setattr(config, "CARTESIA_WEBHOOK_SECRET", "s3cret")
    case = orchestrator.new_case()
    orchestrator.calls_by_cartesia_id["ac_1"] = case["id"]
    h = {"x-webhook-secret": "s3cret"}
    assert (await c.post("/webhooks/cartesia", json={"type": "call_turn"}, headers={})).status_code == 401
    for text in ("hello", "Okay.", "Okay."):
        await c.post("/webhooks/cartesia", headers=h, json={"type": "call_turn", "turn": {"role": "user", "text": text}})
    await c.post("/webhooks/cartesia", headers=h, json={"type": "call_completed", "call": {"id": "ac_1"}})
    texts = [e["text"] for e in bus.history(case["id"]) if e["kind"] == "transcript"]
    assert texts == ["hello", "Okay.", "Okay."]
    assert case["call_done"].is_set()
    orchestrator.calls_by_cartesia_id.clear()


async def test_placeholder_tool_token_is_rejected(client, monkeypatch):
    monkeypatch.setattr(config, "CARTESIA_TOOL_TOKEN", "")
    r = await client.post("/api/cartesia/tools/record_outcome", headers={"authorization": "Bearer "},
                          json={"case_id": "x"})
    assert r.status_code == 401


@pytest.mark.parametrize("said,expected", [
    ("Your case number is 58213.", "58213"), ("case number BB 4471", "BB4471"),
    ("it's fa-2291", "FA-2291"), ("FA2291", "FA2291"), ("no number, sorry", "no number, sorry")])
def test_scripted_case_number(said, expected):
    from remy.agents.caller import _case_number
    assert _case_number(said) == expected


async def _run_policy(case):
    from remy.agents import policy
    loop = asyncio.get_running_loop()
    txns, owed = loop.create_future(), loop.create_future()
    txns.set_result([{"id": f"t{i}", "merchant": "", **t} for i, t in enumerate(case["transactions"])])
    owed.set_result({"owed": 600.0, "responsibility": 1100.0, "paid": 500.0})
    return await policy.run(case, txns, owed)


@pytest.mark.parametrize("reply", [
    {"tiers": [{"max_fpl_pct": None, "discount_pct": 50}, {"max_fpl_pct": "400%", "discount_pct": "AGB"}]},
    ValueError("no JSON in model output"),
])
async def test_bad_policy_extraction_never_crashes(monkeypatch, reply):
    from remy import llm
    from remy.agents import policy
    async def fake_json_out(*a, **k):
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "json_out", fake_json_out)
    monkeypatch.setattr(orchestrator, "date", _Today)
    case = orchestrator.new_case()
    # a hospital that isn't in the policy DB, with Bright Data off -> the offline test fixture path
    case["hospital"] = {**case["hospital"], "name": "Nowhere General Hospital"}
    out = await _run_policy(case)
    assert out["origin"] == "offline_fixture" and out["live"] is False
    if isinstance(reply, Exception):  # fixture's known tiers, labelled as the fixture
        assert out["target"] == 0 and out["refund"] == 225 and out["source"] == policy.FIXTURE_LABEL  # 75% tier
    else:  # nothing usable: honest "no discount", no crash
        assert out["policy"]["tiers"] == [] and out["target"] == 600


async def test_policy_comes_from_the_policy_db_offline():
    """Rosa's hospital is in the committed policy DB: no network, real verified Sutter policy. Her $1,100 share
    is 3% of income, under the 10% the write-off needs, so $600 stays, on Sutter's interest-free plan terms."""
    case = orchestrator.new_case()
    out = await _run_policy(case)
    assert out["origin"] == "policy_db" and out["live"] is True and out["fetched_at"]
    assert "sutterhealth" in out["source"] or "sutterhealt" in out["source"]
    assert out["target"] == 600 and out["refund"] == 0 and out["eligibility"]["discount_pct"] == 0
    assert (out["plan"]["months"], out["plan"]["monthly"]) == (6, 100.0)
    terms = out["plan_terms"]
    assert terms["interest_free"] is True and terms["max_pct_monthly_income"] == 10 and "interest-free" in terms["quote"]
    assert all(t["verified"] and t["source_url"] for t in out["policy"]["tiers"])
    kinds = [e for e in bus.history(case["id"]) if e["kind"] == "policy_source"]
    assert kinds and kinds[0]["origin"] == "policy_db" and kinds[0]["hospital_id"] == "sutter-cpmc"


async def test_caller_llm_error_keeps_history_valid(monkeypatch):
    from remy import llm
    from remy.agents.caller import CallSession
    async def boom(**k):
        raise RuntimeError("overloaded")
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "create", boom)
    s = CallSession("c1", {})
    say = await s.turn("hello?")
    assert say == "Sorry, could you say that again?" and s.messages == [] and not s.ended
    assert any(e["kind"] == "warning" for e in bus.history("c1"))


class _RajDay(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 25)  # the as_of of demo/scenarios/raj-cpmc/expected.json


def test_discount_line_says_the_tiers_own_reason():
    """A high-medical-cost tier qualifies on 12 months of medical costs; an income-only tier on the poverty line."""
    high = {"policy_tier": {"min_oop_pct_income": 10.0}, "medical_costs_pct_income": 14.1, "income_pct_fpl": 134.5,
            "medical_costs_12_months": {"this_bill": 4200.0, "other_medical_bills_in_bank": 2053.35}}
    said = caller._qualifies_line(high)
    assert "medical expenses over the past 12 months are about 14% of family income" in said
    assert "over your policy's 10%" in said and "$2,053" in said and "poverty" not in said
    income = {"policy_tier": {"min_oop_pct_income": None}, "income_pct_fpl": 180.0}
    assert "about 180 percent of the poverty line, which qualifies" in caller._qualifies_line(income)


def test_bill_line_asks_are_said_from_the_auditors_line_errors():
    rec = {"line_errors": [
        {"kind": "duplicate_line", "line": 7, "code": "96360", "amount": 1160.0, "source": "bill · lines 6 and 7",
         "detail": "Line 7 repeats line 6: HCHG INFUSION HYDRATION INITIAL UP TO 1 HR, $1,160.00."},
        {"kind": "price_above_posted", "line": 2, "code": "74176", "amount": 776.0, "billed": 7150.0, "posted": 6374.0,
         "source": "CPMC price file, 2026-04-01", "detail": "Line 2 charges $7,150.00; the posted price is $6,374.00."}]}
    asks = [{"kind": "insurance", "findings": ["auditor:insurance"]},
            {"kind": "bill_line", "findings": ["auditor:price_above_posted-2"]},
            {"kind": "bill_line", "findings": ["auditor:duplicate_line-7"]}]
    got = caller._bill_corrections(asks, rec)
    assert [c["line"] for c in got] == [2, 7]  # the plan's order
    ct, dup = (caller._correction_line(c) for c in got)
    assert "Line 2" in ct and "$7,150" in ct and "$6,374" in ct and "$776" in ct and "price file dated 2026-04-01" in ct
    assert "Line 7" in dup and "repeats line 6" in dup and "$1,160" in dup and "duplicate" in dup


async def test_raj_hero_case_end_to_end(client, monkeypatch):
    """?scenario=raj: both app agents + the Decide agent, offline, reach the answer key, and the plan asks for every
    correction, free care and the refund; the offline call ends at $0 + the $400 back."""
    monkeypatch.setattr(orchestrator, "date", _RajDay)
    exp = json.loads((config.DEMO_DIR / "scenarios" / "raj-cpmc" / "expected.json").read_text())
    c = client
    case_id = (await c.post("/api/cases?scenario=raj&household_size=4")).json()["case_id"]
    plan = (await _wait_for(case_id, lambda e: e["kind"] == "plan_version"))["plan"]
    hist = bus.history(case_id)
    finds = {e["key"].split(":", 1)[1]: e for e in hist if e["kind"] == "finding" and e["agent"] == "auditor"}
    assert finds["duplicate_line-7"]["saved"] == 1160 and finds["price_above_posted-2"]["saved"] == 776
    assert finds["insurance"]["saved"] == next(f["money"] for f in exp["findings"] if f["id"] == "insurance_not_applied")
    assert [k for k in finds if k.startswith("paid-")] == [f["id"] for f in exp["findings"] if f["id"].startswith("paid-")]

    m = plan["money"]
    assert m["billed"] == exp["totals"]["amount_due"] and m["owed"] == exp["waterfall"]["owed_before_discount"]
    assert m["target"] == 0 and m["refund"] == exp["correct"]["refund"] == 400
    asks = {a["id"]: a for a in plan["asks"]}
    assert {"bill_line-7", "bill_line-2", "insurance", "payment", "discount", "refund", "hold", "case_number"} <= set(asks)
    assert "bills 96360 a second time" in asks["bill_line-7"]["text"]
    assert "74176 above the hospital's posted price" in asks["bill_line-2"]["text"]
    assert "free care" in asks["discount"]["text"] and asks["refund"]["money"] == 400

    elig = orchestrator.cases[case_id]["facts"]["pol"]["eligibility"]
    assert elig["discount_pct"] == 100 and not elig.get("gross_check")
    _assert_raj_review_reads_right(case_id, plan, exp)

    await _approve(c, case_id, "caller")
    ready = await _wait_for(case_id, lambda e: e["kind"] == "call_ready")
    remy = await _billing_plays(c, ready["call_id"])
    said = " ".join(e["text"] for e in bus.history(case_id) if e["kind"] == "transcript" and e["speaker"] == "remy")
    assert "$400" in said
    # both line errors are said, in the plan's order, each with its line, amount and source
    dup = next(i for i, x in enumerate(remy) if "Line 7" in x)
    ct = next(i for i, x in enumerate(remy) if "Line 2" in x)
    assert (dup < ct) == ([a["id"] for a in plan["asks"]].index("bill_line-7") < [a["id"] for a in plan["asks"]].index("bill_line-2"))
    assert "repeats line 6" in remy[dup] and "96360" in remy[dup] and "$1,160" in remy[dup]
    assert "$7,150" in remy[ct] and "$6,374" in remy[ct] and "$776" in remy[ct] and "price file" in remy[ct]
    # free care for the real reason: an insured high-medical-cost write-off, not income alone
    assert "medical expenses over the past 12 months are about 14% of family income" in said
    assert "over your policy's 10%" in said and "$2,053 of other medical bills" in said
    assert "poverty" not in said.lower()
    brief = orchestrator.cases[case_id]["result"]["brief"]
    assert brief["medical_costs_pct_income"] == exp["discount"]["oop_pct_income"]
    assert [f"bill_line-{x['line']}" for x in brief["bill_corrections"]] == [
        a["id"] for a in plan["asks"] if a["kind"] == "bill_line"]
    assert all(any(x["detail"] in g for g in brief["goals"]) for x in brief["bill_corrections"])  # a live call asks too
    s = (await _wait_for(case_id, lambda e: e["kind"] == "case_done"))["summary"]
    # not confirmed by billing yet: $0 and the $400 refund are what Remy expects once the corrections land
    assert s["expected"] == 0 and s["expected_refund"] == 400 and s["case_number"] == "58213"
    # billing promised every correction for later; nothing billing answered is "no clear answer"
    marks = {m["id"]: m["status"] for m in next(e for e in bus.history(case_id) if e["kind"] == "scorecard")["marks"]}
    assert marks == {"bill_line-7": "later", "bill_line-2": "later", "insurance": "later", "payment": "later",
                     "discount": "later", "refund": "later", "hold": "got", "case_number": "got"}


async def _billing_plays(c, call_id: str) -> list[str]:
    """The offline billing rep (offline.billing_reply) answers each of Remy's lines; returns what Remy said."""
    from remy import offline
    said, out, n = "", [], 0
    for i in range(offline.MAX_TURNS):
        line = offline.billing_reply(i, said, n)
        n += line in (offline.REP_CORRECTION, offline.REP_CORRECTION_MORE)
        r = (await c.post(f"/api/calls/{call_id}/turn", json={"rep_said": line})).json()
        said = r["say"]
        out.append(said)
        if r["ended"]:
            break
    return out


def _assert_raj_review_reads_right(case_id: str, plan: dict, exp: dict) -> None:
    """What the Review and the plan say about Raj: the mistakes found, that his bank history is what qualifies him,
    and money that reads the same in the findings, the asks and the math."""
    hist = bus.history(case_id)
    done = {e["agent"]: e for e in hist if e["kind"] == "agent_finished"}
    review = next(e for e in hist if e["kind"] == "review_done")
    # 1. the Nessie headline counts what it found (2 line errors, insurance, the missed $400), never "no errors"
    for note in (done["auditor"]["note"], review["auditor"]["note"]):
        assert note == "4 mistakes found" and "no errors" not in note
    # 2. the bank's other medical bills tip him over the 10% line: the headline of the step, finding and Review line
    bank = ("Your bank history qualifies you: this bill alone is 9.5% of income; with $2,053 of other medical bills "
            "it's 14.1% — over Sutter Health's 10% limit")
    free = [e for e in hist if e["kind"] == "step" and e["key"] == "policy:free"][-1]
    assert free["status"] == "win" and free["result"] == bank
    disc = next(e for e in hist if e["kind"] == "finding" and e["key"] == "policy:discount")
    assert disc["title"] == "Free care: your bank history qualifies you" and disc["proof"][0] == bank + "."
    assert any(exp["discount"]["tier_quote"] in p for p in disc["proof"])  # the policy quote stays in the proof
    assert review["policy"]["result"] == "Your bank history qualifies you: free care → $0 + $400 back"
    assert done["policy"]["summary"] == review["policy"]["result"]
    # 3. the finding says what's written off; the ask says what that does to what he owes
    assert disc["value"] == "$4,200 share written off"
    ask = next(a for a in plan["asks"] if a["id"] == "discount")
    assert ask["text"] == "Ask for free care under their own policy: it brings the $3,800 you owe to $0, and $400 back"
    assert ask["value"] == "$3,800 owed → $0"
    # the math: the same split as the findings (insurance −$9,952.70, each bill error apart), and it adds up
    lines = plan["math"]
    owe = next(i for i, ln in enumerate(lines) if ln["label"] == "You owe")
    assert round(sum(ln["amount"] for ln in lines[:owe]), 2) == lines[owe]["amount"] == 3800
    assert next(ln for ln in lines if ln["label"] == "Insurance not applied")["amount"] == -9952.70
    assert lines[owe + 1]["label"] == "Free care: your $4,200 share written off"
    assert round(lines[owe]["amount"] + lines[owe + 1]["amount"], 2) == -lines[-1]["amount"] == -400
    m = plan["decided"]["math"]
    assert m[0] == "$16,088.70 billed − $9,952.70 insurance − $776 bill error − $1,160 bill error − $400 paid = $3,800 owed"
    assert m[1] == "$4,200 share, 100% written off under their policy = $0 − $400 paid → they owe you $400"
    assert "11,889" not in " ".join(m) and "×" not in " ".join(m)


async def test_marcus_review_stays_plain(client):
    """Marcus (?scenario=freecare) qualifies on this bill alone: no "bank history qualifies you", no mistakes."""
    case_id = (await client.post("/api/cases?scenario=freecare&household_size=4")).json()["case_id"]
    plan = (await _wait_for(case_id, lambda e: e["kind"] == "plan_version"))["plan"]
    hist = bus.history(case_id)
    assert orchestrator.cases[case_id]["facts"]["pol"]["eligibility"]["needs_bank_medical"] is False
    text = json.dumps([e for e in hist if e["agent"] == "policy"] + [plan], ensure_ascii=False)
    assert "bank history qualifies" not in text
    free = [e for e in hist if e["kind"] == "step" and e["key"] == "policy:free"][-1]
    assert free["result"].startswith("You qualify for free care (your out-of-pocket is ")
    disc = next(e for e in hist if e["kind"] == "finding" and e["key"] == "policy:discount")
    assert disc["title"] == "Free care under their policy"
    assert next(e for e in hist if e["kind"] == "agent_finished" and e["agent"] == "auditor")["note"] == "no errors on the bill"
    assert plan["decided"]["math"] == ["$5,600 billed = $5,600 owed", "$5,600 share, 100% written off under their policy = $0"]


class _Later(date):
    @classmethod
    def today(cls):
        return date(2026, 12, 15)  # weeks after the answer key's as_of


async def test_raj_numbers_are_date_stable(client, monkeypatch):
    """Started on a later day, Raj's case reads his bank as of the scenario's as_of: same eligibility, $0, $400."""
    monkeypatch.setattr(orchestrator, "date", _Later)
    exp = json.loads((config.DEMO_DIR / "scenarios" / "raj-cpmc" / "expected.json").read_text())
    case_id = (await client.post("/api/cases?scenario=raj&household_size=4")).json()["case_id"]
    plan = (await _wait_for(case_id, lambda e: e["kind"] == "plan_version"))["plan"]
    case = orchestrator.cases[case_id]
    assert case["today"] == exp["as_of"] and orchestrator.case_now(case) == date(2026, 12, 15)
    elig = case["facts"]["pol"]["eligibility"]
    assert elig["fpl_pct"] == exp["discount"]["fpl_pct"] and elig["oop_pct_income"] == exp["discount"]["oop_pct_income"]
    assert elig["discount_pct"] == 100 and not elig.get("gross_check")
    assert plan["money"]["target"] == 0 and plan["money"]["refund"] == 400
    # hand-made demo cases still run on today's date
    assert orchestrator.scenario_as_of("freecare") is None and orchestrator.scenario_as_of("default") is None

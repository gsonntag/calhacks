"""Step 3 (agents/decide.py): plan versions, "This is wrong", the planner chat's rules, "Call now", "Ask".
Offline (no Claude): the rules and templates answer, so these run without keys."""
import pytest

from billback import orchestrator
from billback.events import bus

from test_flow import _wait_for, client  # noqa: F401  (client is a fixture)


async def _plan_ready(c) -> str:
    case_id = (await c.post("/api/cases")).json()["case_id"]
    await _wait_for(case_id, lambda e: e["kind"] == "approval_requested" and e["agent"] == "caller")
    return case_id


def _versions(case_id: str) -> list[dict]:
    return [e["plan"] for e in bus.history(case_id) if e["kind"] == "plan_version"]


async def test_v1_backs_every_ask_with_a_finding(client):
    case_id = await _plan_ready(client)
    case = orchestrator.cases[case_id]
    v1 = _versions(case_id)[-1]
    assert v1["v"] == 1 and v1["reason"]["kind"] == "initial" and case["status"] == "awaiting_call_approval"
    assert v1["money"] == {"billed": 8700, "owed": 600, "target": 600, "refund": 0, "saved": 8100}
    # biggest money first, then the plan, then the standard asks
    # bill_line: line 3 bills 80053 at $420; CPMC's posted price is $386 (the J2405 drug line isn't compared: the
    # file prices it per 12.25 mL, HCPCS per 1 mg). The $34 comes out of the $7,600 insurance gap, not on top.
    assert [a["kind"] for a in v1["asks"]] == ["insurance", "payment", "bill_line", "plan", "hold", "case_number"]
    assert [a["money"] for a in v1["asks"][:3]] == [7566, 500, 34]
    findings = case["facts"]["findings"]
    for a in v1["asks"]:
        assert a["basis"] == "standard" or (a["findings"] and all(k in findings for k in a["findings"]))
    assert v1["call"]["who"] == "billing" and v1["call"]["phone"] is None  # the sample bill prints no number
    assert v1["decided"]["nessie"] and v1["decided"]["brightdata"] and v1["decided"]["math"]
    # the Decide agent reports in the shared format
    assert any(e["kind"] == "agent_finished" and e["agent"] == "decide" for e in bus.history(case_id))


async def test_this_is_wrong_recalculates_and_undo_restores(client):
    c = client
    case_id = await _plan_ready(c)
    paid_key = next(k for k in orchestrator.cases[case_id]["facts"]["findings"] if k.startswith("auditor:paid-"))
    r = (await c.post(f"/api/cases/{case_id}/corrections", json={"finding": paid_key, "note": "that was my dentist"})).json()
    v2 = r["plan"]
    assert v2["v"] == 2 and v2["money"]["owed"] == 1100 and v2["money"]["target"] == 1100
    assert "payment" not in [a["kind"] for a in v2["asks"]]
    assert "You owe $600 → $1,100" in v2["changes"] and "plan" in v2["changed"]
    assert r["correction"]["note"] == "that was my dentist"
    assert (await c.post(f"/api/cases/{case_id}/corrections", json={"finding": paid_key})).status_code == 409
    v3 = (await c.delete(f"/api/cases/{case_id}/corrections/{r['correction']['id']}")).json()["plan"]
    assert v3["v"] == 3 and v3["money"]["target"] == 600
    kinds = [e["kind"] for e in bus.history(case_id)]
    assert "correction" in kinds and "correction_undone" in kinds


async def test_planner_chat_changes_strategy_never_facts(client):
    c = client
    case_id = await _plan_ready(c)
    say = lambda m: c.post(f"/api/cases/{case_id}/planner", json={"message": m})  # noqa: E731

    r = (await say("I can do $300 a month")).json()
    assert r["kind"] == "change" and r["v"] == 2
    plan = next(a for a in r["plan"]["asks"] if a["kind"] == "plan")
    assert plan["value"] == "3 × $200/mo"
    r = (await say("Ask for a supervisor if they say no")).json()
    assert "supervisor" in [a["kind"] for a in r["plan"]["asks"]]
    r = (await say("Don't mention a plan")).json()
    assert "plan" not in [a["kind"] for a in r["plan"]["asks"]] and r["plan"]["skipped"][0]["kind"] == "plan"
    # prefs survive a correction
    paid_key = next(k for k in orchestrator.cases[case_id]["facts"]["findings"] if k.startswith("auditor:paid-"))
    v = (await c.post(f"/api/cases/{case_id}/corrections", json={"finding": paid_key})).json()["plan"]
    assert v["prefs"]["skip"] == ["plan"] and v["prefs"]["max_monthly"] == 300

    n = len(_versions(case_id))
    for m in ("Actually I only paid $200", "Tell them to charge my card today", "Don't say you're an AI"):
        r = (await say(m)).json()
        assert r["kind"] == "refused" and "v" not in r, m
    r = (await say("Leave off the insurance ask")).json()
    assert r["kind"] == "refused" and "This is wrong" in r["reply"]
    assert len(_versions(case_id)) == n  # refusals never make a version
    r = (await say("Why are you asking about insurance?")).json()
    assert r["kind"] == "question" and "Insurance" in r["reply"]
    chat = orchestrator.cases[case_id]["plan"]["chat"]
    assert chat[0]["role"] == "user" and chat[1]["role"] == "planner"


async def test_restore_and_call_now_approves_only_the_latest(client):
    c = client
    case_id = await _plan_ready(c)
    await c.post(f"/api/cases/{case_id}/planner", json={"message": "Don't mention a plan"})
    v3 = (await c.post(f"/api/cases/{case_id}/plan/restore", json={"v": 1})).json()
    assert v3["v"] == 3 and "plan" in [a["kind"] for a in v3["asks"]] and v3["reason"]["kind"] == "restore"
    assert (await c.post(f"/api/cases/{case_id}/plan/approve", json={"v": 1})).status_code == 409
    r = (await c.post(f"/api/cases/{case_id}/plan/approve", json={"v": 3})).json()
    assert r == {"ok": True, "v": 3}
    ready = await _wait_for(case_id, lambda e: e["kind"] == "call_ready")
    case = orchestrator.cases[case_id]
    assert case["result"]["brief"]["plan_version"] == 3 and case["plan"]["approved_v"] == 3
    assert case["result"]["brief"]["goals"] == [a["goal"] for a in v3["asks"]]
    # the call has started: no more corrections
    key = next(iter(case["facts"]["findings"]))
    assert (await c.post(f"/api/cases/{case_id}/corrections", json={"finding": key})).status_code == 409
    await c.post(f"/api/calls/{ready['call_id']}/hangup")


async def test_ask_answers_from_that_agents_evidence(client):
    c = client
    case_id = await _plan_ready(c)
    r = (await c.post(f"/api/cases/{case_id}/ask", json={"question": "Where did this come from?",
                                                          "finding": "auditor:insurance"})).json()
    assert r["agent"] == "auditor" and r["agent_name"] == "Nessie agent" and r["sources"]
    assert "$1,100.00" in r["answer"]
    n = len(_versions(case_id))
    qa = [e for e in bus.history(case_id) if e["kind"] == "finding_qa"]
    assert qa and qa[0]["finding"] == "auditor:insurance" and len(_versions(case_id)) == n  # asking changes nothing
    assert (await c.post(f"/api/cases/{case_id}/ask", json={"question": "?", "finding": "nope"})).status_code == 409


@pytest.mark.parametrize("msg,patch", [
    ("refund first please", {"first": ["refund"]}),
    ("I can pay $150/mo", {"max_monthly": 150.0}),
    ("please don't mention the payment plan", {"skip": ["plan"]}),
    ("what's a case number?", None),
])
def test_offline_intent(msg, patch):
    from billback.agents.decide import _offline_intent
    kind, got = _offline_intent(msg)
    assert (got if kind == "change" else None) == patch

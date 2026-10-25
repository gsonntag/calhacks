"""Follow-ups: what billing promised, when to check back, persistence, status answers, and calling again."""
import asyncio
from datetime import date

import httpx
import pytest

from remy import app as app_mod
from remy import events, followups, orchestrator
from remy.agents import policy
from remy.events import bus

FIRST_CALL = ["Patient Financial Services, this is Diane, how can I help you?", "Okay, let me pull that up.",
              "I see it.", "Okay.",
              "I've opened a financial assistance review and put the account on hold. "
              "We'll send a corrected statement within 5 business days.",
              "Your case number is FA 20931.", "Yes, that's right."]
SECOND_CALL = ["Billing, this is Marco.", "Let me look.", "Yes, the adjustment is on there.", "Okay.",
               "The account is still on hold and the refund goes out in two weeks.", "Reference number 77120."]


class _Today(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 24)  # a Saturday


async def _fake_policy(case, txns_ready, owed_ready):
    """Stable policy result, so these tests don't depend on the policy agent's live/sample sources."""
    await txns_ready
    got = await owed_ready
    return {"policy": {"tiers": [], "collections_quote": None}, "source": "test policy", "live": False,
            "eligibility": {"discount_pct": 100, "fpl_pct": 216, "tier": None}, "income": {"monthly": 3000},
            "target": 0, "refund": got["paid"], "discounted_share": 0}


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(orchestrator, "date", _Today)
    monkeypatch.setattr(policy, "run", _fake_policy)
    transport = httpx.ASGITransport(app=app_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
        await c.post("/api/reset")


async def _wait_for(case_id: str, pred, timeout: float = 20, after: int = 0):
    for _ in range(int(timeout / 0.05)):
        for e in bus.history(case_id)[after:]:
            if e["kind"] == "error":
                pytest.fail(f"case errored: {e['text']}")
            if pred(e):
                return e
        await asyncio.sleep(0.05)
    pytest.fail("timed out waiting for event")


async def _approve(c, case_id: str, after: int = 0) -> dict:
    e = await _wait_for(case_id, lambda e: e["kind"] == "approval_requested" and e["agent"] == "caller", after=after)
    assert (await c.post(f"/api/approvals/{e['approval_id']}", json={"approved": True})).json() == {"ok": True}
    return e


async def _desk_call(c, case_id: str, lines: list[str], after: int = 0) -> dict:
    ready = await _wait_for(case_id, lambda e: e["kind"] == "call_ready", after=after)
    for line in lines:
        r = (await c.post(f"/api/calls/{ready['call_id']}/turn", json={"rep_said": line})).json()
    assert r["ended"] is True
    return ready


def test_business_days_skip_weekends():
    assert followups.add_business_days(date(2026, 10, 24), 5) == date(2026, 10, 30)  # Sat -> Fri
    assert followups.add_business_days(date(2026, 10, 30), 1) == date(2026, 11, 2)   # Fri -> Mon
    assert followups.add_business_days(date(2026, 10, 26), 0) == date(2026, 10, 26)
    assert followups.fmt("2026-10-30") == "Fri Oct 30"


@pytest.mark.parametrize("said,days", [("within 5 business days", 5), ("in two weeks", 10), ("a few days", 3),
                                       ("7-10 business days", 7), ("sometime next week", 5), ("we'll see", None)])
def test_timeframe_parsing(said, days):
    assert followups.business_days(said) == days


def test_fallback_extraction_reads_the_rep():
    info = followups.fallback_extract(FIRST_CALL)
    assert info["rep_name"] == "Diane" and info["case_number"] == "FA20931"
    assert info["account_on_hold"] and info["assistance_review_opened"]
    assert info["promised_actions"][0] == "Send a corrected statement"
    assert info["timeframe_business_days"] == 5 and info["timeframe_text"] == "within 5 business days"
    assert followups.fallback_extract(["Okay.", "Bye."])["promised_actions"] == []


def test_call_record_and_followup_dates():
    info = followups.merge({"rep_name": "Diane", "summary": ""}, followups.fallback_extract(FIRST_CALL))
    call = followups.build_call(1, "initial", date(2026, 10, 24), "desk", info, {"case_number": "FA20931"})
    assert call["follow_up_date"] == "2026-10-30" and not call["timeframe_assumed"]
    fu = followups.followup_for(call, "Mercy General")
    assert fu["due"] == "2026-10-30" and fu["action"] == "Call Mercy General back if the corrected bill hasn't arrived"
    # nothing promised and no timeframe: no follow-up; a review with no timeframe gets the default
    assert followups.followup_for(followups.build_call(2, "followup", date(2026, 10, 24), "desk", {}, {}), "X") is None
    c = followups.build_call(1, "initial", date(2026, 10, 24), "desk", {}, {"assistance_review_opened": True})
    assert c["timeframe_assumed"] and c["business_days"] == followups.DEFAULT_BUSINESS_DAYS


def test_status_fallback_without_calls():
    ctx = {"calls": [], "hospital": "Mercy", "status": "awaiting_call_approval", "current_balance": 2100}
    assert "need your OK" in followups.fallback_status(ctx)


async def test_persistence_round_trip(client):
    case = orchestrator.new_case()
    case["calls"].append({"n": 1, "date": "2026-10-24"})
    case["status"] = "calling"
    orchestrator.save(case)
    bus.emit(case["id"], "balance", amount=2100, label="x")
    assert (events.DATA_DIR / "cases" / f"{case['id']}.json").exists()

    orchestrator.cases.clear()
    bus.reset()
    assert orchestrator.load_cases() == 1
    back = orchestrator.cases[case["id"]]
    assert back["calls"] == [{"n": 1, "date": "2026-10-24"}]
    assert back["status"] == "interrupted"  # a run cut off by a restart can't resume mid-call
    assert back["call_done"].is_set() and back["turns_seen"] == set()
    assert [e["amount"] for e in bus.history(case["id"]) if e["kind"] == "balance"] == [2100]
    assert orchestrator.load_cases() == 0  # idempotent


async def test_follow_up_then_call_again_end_to_end(client):
    c = client
    cid = (await c.post("/api/cases")).json()["case_id"]
    await _approve(c, cid)
    await _desk_call(c, cid, FIRST_CALL)
    await _wait_for(cid, lambda e: e["kind"] == "case_done")

    sched = await _wait_for(cid, lambda e: e["kind"] == "followup_scheduled")
    assert sched["followup"]["due"] == "2026-10-30"
    row = (await c.get(f"/api/cases/{cid}/summary")).json()
    # a review, a hold and a promised statement: nothing is lower yet, so the row shows billing's $8,700
    assert row["status"] == "done" and row["original"] == 8700 and row["current_balance"] == 8700
    assert row["target"] == 0 and row["confirmed"] is False
    assert row["case_number"] == "FA20931" and row["refund"] == 0 and row["expected_refund"] == 500
    call = row["last_call"]
    assert call["rep"] == "Diane" and call["date"] == "2026-10-24" and "Send a corrected statement" in call["promised"]
    assert row["next"]["due"] == "2026-10-30" and row["next"]["overdue"] is False
    assert [r["id"] for r in (await c.get("/api/dashboard")).json()] == [cid]

    ans = (await c.post(f"/api/cases/{cid}/ask", json={"question": "What's the status?"})).json()
    assert ans["source"] == "history"
    assert "Diane" in ans["answer"] and "Sat Oct 24" in ans["answer"] and "Fri Oct 30" in ans["answer"]

    # a week passes on the case clock: the follow-up is overdue
    row = (await c.post(f"/api/cases/{cid}/clock", json={"days": 7})).json()
    assert row["today"] == "2026-10-31" and row["next"]["overdue"] is True
    ans = (await c.post(f"/api/cases/{cid}/ask", json={"question": "any update?"})).json()
    assert "overdue" in ans["answer"]
    # the landing chat answers status questions about the latest case
    r = (await c.post("/api/chat", data={"history": "[]", "message": "what's the status of my bill?"})).json()
    assert r["case_id"] == cid and "Diane" in r["reply"] and "started" not in r  # an answer, not a new case

    n = len(bus.history(cid))
    assert (await c.post(f"/api/cases/{cid}/call-again")).json()["ok"] is True
    assert (await c.post(f"/api/cases/{cid}/call-again")).status_code == 409  # one call at a time
    ask = await _approve(c, cid, after=n)
    assert ask["title"].endswith("billing again?") and any("refund" in a for a in ask["detail"]["asks"])
    ready = await _wait_for(cid, lambda e: e["kind"] == "call_ready", after=n)
    brief = (await c.get(f"/api/calls/{ready['call_id']}")).json()["brief"]
    assert brief["call_type"] == "follow_up"
    assert brief["previous_calls"][0]["rep"] == "Diane" and brief["previous_calls"][0]["case_number"] == "FA20931"
    assert any("refund" in g for g in brief["goals"])
    await _desk_call(c, cid, SECOND_CALL, after=n)
    await _wait_for(cid, lambda e: e["kind"] == "case_done", after=n)

    case = orchestrator.cases[cid]
    assert case["status"] == "done" and [x["kind"] for x in case["calls"]] == ["initial", "followup"]
    # "the adjustment is on there": the insurance ask is a "got", so only that comes off; the free care isn't confirmed
    marks = {m["kind"]: m["status"] for m in case["calls"][1]["scorecard"]}
    assert marks.get("insurance") == "got" and marks.get("discount") != "got"
    assert orchestrator.current_balance(case) == 1100 and case["result"]["confirmed"] is False
    second = case["calls"][1]
    assert second["rep"] == "Marco" and second["date"] == "2026-10-31" and second["business_days"] == 10
    assert case["followups"][0]["status"] == "done" and case["followups"][0]["closed_by_call"] == 2
    assert case["followups"][-1]["status"] == "open" and case["followups"][-1]["due"] == "2026-11-13"
    # the second call's transcript is its own
    assert orchestrator._call_transcript(case)[0]["text"] == SECOND_CALL[0]

    # survives a restart
    orchestrator.cases.clear()
    bus.reset()
    orchestrator.load_cases()
    again = orchestrator.cases[cid]
    assert len(again["calls"]) == 2 and again["status"] == "done" and again["clock_days"] == 7
    assert any(e["kind"] == "followup_started" for e in bus.history(cid))

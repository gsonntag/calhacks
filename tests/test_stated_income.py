"""The income the patient tells us (onboarding or chat), double-checked against the bank (finance.stated_check)."""
import asyncio
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from billback import app as app_module, finance, llm, orchestrator
from billback.agents import policy
from billback.events import bus
from billback.orchestrator import demo_case

BANK = {"annual": 36_400.0}  # Rosa's take-home: about $42,824-$48,533 before taxes


@pytest.mark.parametrize("stated, outcome, used, ok", [
    (40_000, "consistent", 40_000, True),   # under $42,824 but within 10%
    (46_000, "consistent", 46_000, True),
    (25_000, "below", 42_824, False),       # never less than the bank's lowest gross
    (90_000, "above", 90_000, True),        # more than the bank: only lowers the discount
])
def test_stated_check_outcomes(stated, outcome, used, ok):
    c = finance.stated_check(BANK, stated)
    assert (c["outcome"], c["used"], c["ok"]) == (outcome, used, ok)
    assert c["range"] == [42_824, 48_533]
    assert c["line"].startswith(f"You said ${stated:,}") and "$36,400 take-home" in c["line"]


def test_stated_check_texts():
    assert finance.stated_check(BANK, 40_000)["text"] == ("You said about $40,000 a year before taxes; your bank "
                                                          "deposits ($36,400 take-home) fit that.")
    assert finance.stated_check(BANK, 25_000)["text"] == ("You said $25,000 a year before taxes; your bank deposits "
                                                          "($36,400 take-home) suggest at least $42,824 before taxes, "
                                                          "so I used that. Have pay stubs ready.")
    assert "another earner" in finance.stated_check(BANK, 90_000)["text"]


def test_stated_check_without_bank_income():
    c = finance.stated_check({"annual": 0.0}, 30_000)
    assert c["outcome"] == "no_bank" and c["used"] == 30_000 and not c["ok"] and c["range"] is None
    assert "couldn't check" in c["text"]


async def _policy(stated=None, txns=None):
    c = {**demo_case(), "id": f"stated-{stated}-{txns is not None}", "today": "2026-10-24"}
    c["patient"] = dict(c["patient"])
    if stated:
        c["patient"].update(stated_income_annual=stated, stated_income_source="onboarding")
    c["transactions"] = txns if txns is not None else [{"id": f"t{i}", "merchant": "", **t}
                                                       for i, t in enumerate(c["transactions"])]
    loop = asyncio.get_running_loop()
    t, o = loop.create_future(), loop.create_future()
    t.set_result(c["transactions"])
    o.set_result({"owed": 600.0, "responsibility": 1100.0, "paid": 500.0})
    out = await policy.run(c, t, o)
    step = [e for e in bus.history(c["id"]) if e["kind"] == "step" and e["key"] == "policy:income"][-1]
    return out, step


async def test_policy_without_stated_income_is_unchanged():
    out, step = await _policy()
    elig, inc = out["eligibility"], out["income"]
    assert "check" not in inc and "income_annual" not in elig
    assert elig["fpl_pct"] == round(100 * inc["annual"] / finance.fpl(1), 1)  # bank take-home, as before
    assert step["label"] == "Estimate your income from payroll deposits"


async def test_policy_uses_consistent_stated_income():
    base, _ = await _policy()
    out, step = await _policy(40_000)
    elig, inc = out["eligibility"], out["income"]
    assert inc["check"]["outcome"] == "consistent" and inc["stated"] == {"annual": 40_000, "source": "onboarding"}
    assert elig["income_annual"] == 40_000 and elig["fpl_pct"] == round(100 * 40_000 / finance.fpl(1), 1)
    assert elig["gross_check"] is None
    assert step["status"] == "ok" and step["result"].startswith("You said about $40,000")
    assert step["proof"][0]["text"].endswith("✓ fits")
    assert out["plan"] == base["plan"]  # the payment plan is still sized on take-home
    ev = [e for e in bus.history("stated-40000-False") if e["kind"] == "evidence"]
    assert any(e["label"] == "Income you told us vs your bank" for e in ev)


async def test_policy_stated_below_bank_uses_bank_low_gross():
    out, step = await _policy(25_000)
    elig = out["eligibility"]
    assert out["income"]["check"]["outcome"] == "below" and elig["income_annual"] == 42_824
    assert elig["fpl_pct"] == round(100 * 42_824 / finance.fpl(1), 1)
    assert step["status"] == "flag" and "Have pay stubs ready" in step["result"]
    assert any(e["kind"] == "warning" and "$42,824" in e["text"] for e in bus.history("stated-25000-False"))


async def test_policy_stated_above_bank_uses_stated():
    out, step = await _policy(90_000)
    assert out["income"]["check"]["outcome"] == "above" and out["eligibility"]["income_annual"] == 90_000
    assert step["status"] == "ok" and "another earner" in step["result"]


async def test_policy_stated_without_bank_income():
    out, step = await _policy(30_000, txns=[])
    assert out["income"]["check"]["outcome"] == "no_bank" and out["eligibility"]["income_annual"] == 30_000
    assert step["status"] == "flag" and "couldn't check" in step["result"]


def test_decide_lists_stated_vs_bank():
    from billback.agents import decide
    check = finance.stated_check(BANK, 40_000)
    case = {**demo_case(), "facts": {
        "rec": {"payments": [], "billed_due": 600.0}, "findings": {},
        "pol": {"policy": {}, "eligibility": {"discount_pct": 0, "fpl_pct": 250.6, "household_size": 1,
                                              "income_annual": 40_000},
                "income": {"annual": 36_400, "monthly": 3033, "check": check}}}}
    d = decide.decided(case, 600.0, 0.0, 0, 600.0, 600.0, 0.0, None, set())
    assert d["brightdata"][0].startswith("Your income is about $40,000/yr before taxes")
    assert d["brightdata"][1] == check["line"]


class _Today(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 24)


async def test_onboarding_answers_reach_the_next_case_without_tiger(monkeypatch):
    monkeypatch.setattr(orchestrator, "date", _Today)
    transport = httpx.ASGITransport(app=app_module.app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        who = {"first_name": "Rosa", "last_name": "Martinez", "dob": "1991-05-22"}
        r = (await c.post("/api/onboarding", json={"answers": {**who, "household_size": 2, "income": 40_000,
                                                                "insured": True, "medical_oop": 300}})).json()
        assert r["saved"] is False and r["stored"] == "local" and r["profile_id"]
        cid = (await c.post("/api/cases")).json()["case_id"]  # the sample case is Rosa Martinez, same DOB
        p = orchestrator.cases[cid]["patient"]
        assert p["household_size"] == 2 and p["household_source"] == "onboarding"
        assert p["stated_income_annual"] == 40_000 and p["stated_income_source"] == "onboarding"
        assert p["insured_stated"] is True and p["medical_oop_stated"] == 300
        for _ in range(400):
            done = [e for e in bus.history(cid) if e["kind"] == "step" and e["key"] == "policy:income"
                    and e["status"] != "running"]
            if done:
                break
            await asyncio.sleep(0.05)
        assert done and done[0]["result"].startswith("You said about $40,000")  # 2 people: $40,000 still fits
        await c.post("/api/reset")


async def test_chat_start_case_passes_income(monkeypatch):
    got = {}

    async def fake_new_case(uploads, scenario="default", household_size=None, income_annual=None):
        got.update(household_size=household_size, income_annual=income_annual)
        return {"id": "c1"}

    async def fake_create(**kw):
        assert "income_annual" in kw["tools"][0]["input_schema"]["properties"]
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"household_size": 3,
                                                                                "income_annual": 52_000})])
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "create", fake_create)
    monkeypatch.setattr(app_module, "_new_case", fake_new_case)
    transport = httpx.ASGITransport(app=app_module.app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = (await c.post("/api/chat", data={"history": "[]", "message": "go"})).json()
    assert r["started"] and got == {"household_size": 3, "income_annual": 52_000}


async def test_new_case_records_chat_income(monkeypatch):
    monkeypatch.setattr(orchestrator, "date", _Today)
    transport = httpx.ASGITransport(app=app_module.app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        cid = (await c.post("/api/cases", params={"household_size": 2, "income_annual": 48_000})).json()["case_id"]
        p = orchestrator.cases[cid]["patient"]
        assert p["stated_income_annual"] == 48_000 and p["stated_income_source"] == "chat"
        cid = (await c.post("/api/cases", params={"income_annual": -5})).json()["case_id"]
        assert "stated_income_annual" not in orchestrator.cases[cid]["patient"]
        await c.post("/api/reset")


def test_onboarding_checks_stated_income_against_the_bank():
    from billback import onboarding
    hs = [{"id": "a", "name": "A", "city": "X", "tiers": [{"max_fpl_pct": 200, "discount_pct": 100, "applies_to": "all"}]}]
    bank = {"annual": 36_400.0, "source": "test"}
    r = onboarding.assess({"hospital_id": "a", "household_size": 2, "income": 25_000}, bank=bank, hospitals=hs)
    assert r["income_check"]["outcome"] == "below" and r["fpl_range"][0] == round(100 * 42_824 / finance.fpl(2), 1)
    r = onboarding.assess({"hospital_id": "a", "household_size": 2, "income": 25_000}, hospitals=hs)
    assert r["income_check"] is None and r["discount_pct"] == 100  # no bank to check against: what they said

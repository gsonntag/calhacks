"""Cases list at a glance: step + attention per case, sorting, archive / restore / delete and the running-case guard."""
import asyncio
from datetime import date, timedelta

import httpx
import pytest

from billback import app as app_mod
from billback import caselist, events, orchestrator
from billback.agents import policy
from billback.events import bus
from billback.harness import gate


async def _fake_policy(case, txns_ready, owed_ready):
    await txns_ready
    got = await owed_ready
    return {"policy": {"tiers": [], "collections_quote": None}, "source": "test policy", "live": False,
            "eligibility": {"discount_pct": 100, "fpl_pct": 216, "tier": None}, "income": {"monthly": 3000},
            "target": 0, "refund": got["paid"], "discounted_share": 0}


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(policy, "run", _fake_policy)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test") as c:
        yield c
        await c.post("/api/reset")


@pytest.fixture
def fresh():
    made = []

    def make(status="created", **extra):
        case = orchestrator.new_case()
        case.update(status=status, **extra)
        made.append(case["id"])
        return case
    yield make
    for cid in made:
        orchestrator.cases.pop(cid, None)
        gate.forget(cid)


def _followup(case, due: date):
    case["calls"] = [{"n": 1, "date": date.today().isoformat(), "rep": "Diane", "promised": ["Corrected bill"]}]
    case["result"]["brief"] = {"x": 1}
    case["followups"] = [{"id": "f1", "from_call": 1, "created": date.today().isoformat(), "due": due.isoformat(),
                          "check": ["Corrected bill"], "condition": "the bill hasn't arrived",
                          "action": "Call back if the bill hasn't arrived", "status": "open"}]


def _pending(case, agent):
    gate._requests[f"t-{case['id']}-{agent}"] = {"approval_id": "x", "case_id": case["id"], "agent": agent,
                                                  "title": "", "detail": {}}


# ---------- status derivation ----------
@pytest.mark.parametrize("status,step,att", [("created", 1, "running"), ("reviewing", 2, "running"),
                                             ("deciding", 3, "running"), ("calling", 4, "running"),
                                             ("planning", 5, "running"), ("awaiting_call_approval", 3, "needs_you")])
def test_step_and_attention_while_working(fresh, status, step, att):
    o = caselist.overview(fresh(status))
    assert (o["step"], o["attention"]) == (step, att)
    assert o["step_state"] == ("waiting" if att == "needs_you" else "active")


def test_calling_headline_names_the_hospital(fresh):
    c = fresh("calling")
    assert caselist.overview(c)["headline"] == f"Calling {c['hospital']['name']} billing…"


def test_plan_ready_and_payment_plan_need_the_patient(fresh):
    assert "Call now" in caselist.overview(fresh("awaiting_call_approval"))["headline"]
    c = fresh("planning")
    _pending(c, "planner")
    o = caselist.overview(c)
    assert (o["attention"], o["step"], o["step_state"]) == ("needs_you", 5, "waiting")
    assert "payment plan" in o["headline"].lower()


def test_followup_call_waiting_for_ok_is_on_step_5(fresh):
    c = fresh("awaiting_call_approval")
    _followup(c, date.today() + timedelta(days=5))
    assert (caselist.overview(c)["step"], caselist.overview(c)["attention"]) == (5, "needs_you")


def test_failed_steps_need_the_patient(fresh):
    o = caselist.overview(fresh("error"))
    assert (o["attention"], o["step_state"]) == ("needs_you", "failed")
    c = fresh("interrupted")
    c["result"]["brief"] = {"x": 1}
    o = caselist.overview(c)
    assert (o["attention"], o["step"], o["step_state"]) == ("needs_you", 4, "failed")
    assert "call again" in o["headline"]


def test_done_cases_wait_on_the_hospital_until_the_followup_is_due(fresh):
    c = fresh("done")
    _followup(c, date.today() + timedelta(days=5))
    o = caselist.overview(c)
    assert (o["attention"], o["step"], o["step_state"]) == ("waiting", 5, "waiting")
    assert "check back" in o["headline"]
    c["clock_days"] = 6  # the promised date passed
    assert caselist.overview(c)["attention"] == "needs_you"
    c["followups"][0]["status"] = "done"
    assert caselist.overview(c)["attention"] == "done"
    assert fresh("done")["status"] == "done" and caselist.overview(fresh("done"))["attention"] == "done"


# ---------- sorting ----------
def test_rank_needs_you_then_running_then_waiting_then_done_newest_first():
    rows = [{"id": "d", "attention": "done", "created_at": 5}, {"id": "w", "attention": "waiting", "created_at": 1},
            {"id": "r", "attention": "running", "created_at": 9}, {"id": "n1", "attention": "needs_you", "created_at": 1},
            {"id": "n2", "attention": "needs_you", "created_at": 3}]
    assert [r["id"] for r in sorted(rows, key=caselist.rank)] == ["n2", "n1", "r", "w", "d"]


async def test_dashboard_rows_carry_the_overview_and_sort(fresh):
    done, call, ok = fresh("done"), fresh("calling"), fresh("awaiting_call_approval")
    rows = await app_mod.dashboard()
    ids = [r["id"] for r in rows if r["id"] in (done["id"], call["id"], ok["id"])]
    assert ids == [ok["id"], call["id"], done["id"]]
    row = next(r for r in rows if r["id"] == call["id"])
    assert row["step"] == 4 and row["attention_label"] == "Running" and row["archived"] is False
    tabs = {t["id"]: t for t in await app_mod.list_cases()}
    assert tabs[ok["id"]]["attention"] == "needs_you"


# ---------- archive / restore ----------
async def test_archive_and_restore_persist(client, fresh):
    c = fresh("done")
    r = await client.post(f"/api/cases/{c['id']}/archive")
    assert r.status_code == 200 and r.json()["archived"] is True
    saved = next(x for x in events.load_cases() if x["id"] == c["id"])
    assert saved["archived"] is True and saved["archived_at"]
    row = next(x for x in (await client.get("/api/dashboard")).json() if x["id"] == c["id"])
    assert row["archived"] is True  # still listed, flagged: the page shows it under "Archived"
    r = await client.post(f"/api/cases/{c['id']}/restore")
    assert r.status_code == 200 and r.json()["archived"] is False
    assert next(x for x in events.load_cases() if x["id"] == c["id"])["archived"] is False


async def test_archive_survives_a_restart(client, fresh):
    c = fresh("done")
    await client.post(f"/api/cases/{c['id']}/archive")
    orchestrator.cases.pop(c["id"])
    orchestrator.load_cases()
    assert orchestrator.cases[c["id"]]["archived"] is True


@pytest.mark.parametrize("status,words", [("calling", "on a call with"), ("reviewing", "still running"),
                                          ("awaiting_call_approval", "still needs you")])
async def test_archiving_a_live_case_needs_an_explicit_confirm(client, fresh, status, words):
    c = fresh(status)
    r = await client.post(f"/api/cases/{c['id']}/archive")
    assert r.status_code == 409 and r.json()["needs_confirm"] is True and words in r.json()["error"]
    assert not c.get("archived")
    r = await client.post(f"/api/cases/{c['id']}/archive", json={"confirm": True})
    assert r.status_code == 200 and c["archived"] is True and c["status"] == status  # it keeps running


# ---------- delete ----------
async def test_delete_a_finished_case(client, fresh):
    c = fresh("done")
    assert (await client.delete(f"/api/cases/{c['id']}")).json() == {"ok": True}
    assert c["id"] not in orchestrator.cases and bus.history(c["id"]) == []
    assert all(x["id"] != c["id"] for x in events.load_cases())
    assert (await client.delete(f"/api/cases/{c['id']}")).status_code == 404


async def test_delete_is_blocked_mid_call_even_with_confirm(client, fresh):
    c = fresh("calling")
    for url in (f"/api/cases/{c['id']}", f"/api/cases/{c['id']}?confirm=true"):
        r = await client.delete(url)
        assert r.status_code == 409 and r.json()["blocked"] is True and "End the call" in r.json()["error"]
    assert c["id"] in orchestrator.cases


async def test_delete_a_running_case_stops_its_run_first(client):
    cid = (await client.post("/api/cases")).json()["case_id"]
    for _ in range(400):  # the offline run reaches "Call now" and waits on the patient
        if any(a["agent"] == "caller" for a in gate.pending_for(cid)):
            break
        await asyncio.sleep(0.05)
    task = app_mod._case_tasks[cid]
    r = await client.delete(f"/api/cases/{cid}")
    assert r.status_code == 409 and r.json()["needs_confirm"] is True and "still needs you" in r.json()["error"]
    assert (await client.delete(f"/api/cases/{cid}?confirm=true")).json() == {"ok": True}
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    assert gate.pending_for(cid) == [] and cid not in orchestrator.cases and cid not in app_mod._case_tasks
    assert all(x["id"] != cid for x in events.load_cases())


async def test_new_routes_need_the_admin_key(monkeypatch, fresh):
    from billback import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "k" * 24)
    c = fresh("done")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_mod.app), base_url="http://t") as cl:
        assert (await cl.post(f"/api/cases/{c['id']}/archive")).status_code == 401
        assert (await cl.post(f"/api/cases/{c['id']}/restore")).status_code == 401
        assert (await cl.delete(f"/api/cases/{c['id']}")).status_code == 401
        assert (await cl.post(f"/api/cases/{c['id']}/archive", headers={"X-BillBack-Key": "k" * 24})).status_code == 200


# ---------- an archived case that newly needs the patient comes back ----------
async def test_archived_case_comes_back_when_it_newly_needs_you(client, fresh):
    c = fresh("reviewing")
    assert (await client.post(f"/api/cases/{c['id']}/archive", json={"confirm": True})).json()["archived"] is True
    orchestrator.set_status(c, "awaiting_call_approval")  # the plan is ready: the patient has to look
    assert c["archived"] is False
    assert any(e["kind"] == "unarchived" and "needs you" in e["text"] for e in bus.history(c["id"]))
    assert next(x for x in events.load_cases() if x["id"] == c["id"])["archived"] is False
    tab = next(t for t in (await client.get("/api/cases")).json() if t["id"] == c["id"])
    assert tab["archived"] is False and tab["attention"] == "needs_you"


async def test_archived_case_comes_back_when_a_followup_goes_overdue_or_it_fails(client, fresh):
    c = fresh("done")
    _followup(c, date.today() + timedelta(days=2))
    await client.post(f"/api/cases/{c['id']}/archive")
    assert c["archived"] is True
    c["clock_days"] = 5  # nothing emitted: the dashboard read notices it
    row = next(r for r in (await client.get("/api/dashboard")).json() if r["id"] == c["id"])
    assert row["archived"] is False and row["attention"] == "needs_you"
    d = fresh("calling")
    await client.post(f"/api/cases/{d['id']}/archive", json={"confirm": True})
    orchestrator.set_status(d, "error")
    assert d["archived"] is False


async def test_archived_while_it_needed_you_stays_archived_until_something_new(client, fresh):
    c = fresh("awaiting_call_approval")
    await client.post(f"/api/cases/{c['id']}/archive", json={"confirm": True})
    await client.get("/api/dashboard")
    assert c["archived"] is True  # the patient saw this one and archived it anyway
    orchestrator.set_status(c, "error")
    assert c["archived"] is False

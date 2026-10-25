"""No-wifi demo (REMY_OFFLINE=1, remy/offline.py): every feature runs with no network and no second person."""
import asyncio
import os
import subprocess
import sys

import httpx
import pytest

from remy import app as app_mod, config, offline, orchestrator
from remy.events import bus


def test_offline_switch_blanks_every_live_setting():
    env = {**os.environ, "REMY_OFFLINE": "1", "ANTHROPIC_API_KEY": "sk-ant-x", "TIGER_DATABASE_URL": "postgres://x",
           "CARTESIA_API_KEY": "c", "REMY_REQUIRE_LIVE": "1"}
    out = subprocess.run([sys.executable, "-c", "from remy import config; m = config.mode(); "
                          "print(m['offline'], any(v for k, v in m.items() if k != 'offline'), config.REQUIRE_LIVE, "
                          "config.DATA_DIR.name)"], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["True", "False", "False", "offline"]


def test_bank_income_comes_from_the_sample_patients():
    got = offline.bank_income("rosa", "MARTINEZ")
    assert got["annual"] > 0 and "offline demo" in got["source"]
    assert offline.bank_income("Someone", "Else") is None


def test_monthly_income_counts_income_only():
    txns = [{"type": "deposit", "date": "2026-08-01", "amount": 1400, "description": "PAYROLL DIRECT DEP"},
            {"type": "deposit", "date": "2026-08-15", "amount": 1400, "description": "PAYROLL DIRECT DEP"},
            {"type": "deposit", "date": "2026-08-20", "amount": 900, "description": "Transfer from savings"},
            {"type": "purchase", "date": "2026-09-02", "amount": 50, "description": "payroll"}]
    assert offline.monthly_income(txns) == [{"month": "2026-08", "income": 2800.0}]


@pytest.fixture
async def offline_client(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", True)
    monkeypatch.setattr(offline, "FIRST", 0)
    monkeypatch.setattr(offline, "PAUSE", 0)
    monkeypatch.setattr(offline, "READ_SPEED", 0)
    transport = httpx.ASGITransport(app=app_mod.app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
        await c.post("/api/reset")


async def _wait(case_id, pred, timeout=30):
    for _ in range(int(timeout / 0.05)):
        for e in bus.history(case_id):
            if e["kind"] == "error":
                pytest.fail(e["text"])
            if pred(e):
                return e
        await asyncio.sleep(0.05)
    pytest.fail("timed out")


async def test_whole_case_runs_with_nobody_playing_billing(offline_client):
    c = offline_client
    case_id = (await c.post("/api/cases")).json()["case_id"]
    await _wait(case_id, lambda e: e["kind"] == "approval_requested" and e["agent"] == "caller")
    assert any(e["kind"] == "evidence" and e["label"] == "Income by month" for e in bus.history(case_id))

    # the mic with no speech service: the demo voice asks for the call, then says yes
    t = (await c.post(f"/api/cases/{case_id}/talk", data={"heard": ""})).json()
    assert t["heard_by"] == "demo" and t["pending"] == "start_call"
    t = (await c.post(f"/api/cases/{case_id}/talk", data={"heard": ""})).json()
    assert t["did"] == "start_call"

    ready = await _wait(case_id, lambda e: e["kind"] == "call_ready")
    assert ready["auto_rep"] is True
    done = await _wait(case_id, lambda e: e["kind"] == "case_done")
    assert done["summary"]["case_number"] == "58213"
    rep = [e["text"] for e in bus.history(case_id) if e["kind"] == "transcript" and e["speaker"] == "rep"]
    assert rep[0] == offline.REP_SCRIPT[0]

    # after the call: status first, then the follow-up
    t = (await c.post(f"/api/cases/{case_id}/talk", data={"heard": ""})).json()
    assert t["heard"] == "Where do things stand?"

    r = (await c.post(f"/api/cases/{case_id}/policy/recheck")).json()
    assert r["ok"] and r["changed"] is False and r["offline"] is True
    assert orchestrator.cases[case_id]["status"] == "done"


async def test_hospital_recheck_uses_the_stored_copy(offline_client):
    r = (await offline_client.post("/api/hospitals/sutter-cpmc/recheck")).json()
    assert r["changed"] is False and r["requests"] == 0 and r["offline"] is True
    assert (await offline_client.get("/api/hospitals")).json()["can_recheck"] is True


def test_pages_load_nothing_from_the_internet():
    import re
    for f in [*config.ROOT.glob("web/*.html"), *config.ROOT.glob("web/flows/*.html")]:
        s = f.read_text()
        assert not re.search(r'(src|href)="https?://(?!localhost|127\.0\.0\.1)', s.split("<body", 1)[0]), f.name
        assert "fonts.googleapis" not in s and "fonts.gstatic" not in s, f.name

"""Admin key on routes that place a call, spend credits or wipe state; judging mode (no sample policies)."""
import json
import httpx
import pytest
from fastapi.routing import APIRoute

from remy import app as app_mod
from remy import auth, config
from remy.agents import policy

TOKEN = "s3cret-admin-token-0123456789"
# Mutating routes that stay open: own signature/secret (webhooks, Twilio), or harmless (simulated, pure text).
OPEN = {"/webhooks/cartesia", "/api/cartesia/tools/record_outcome", "/voice/twilio/status", "/voice/twilio/agent",
        "/api/gmail/scan", "/api/voices/normalize"}


def _client(client=("127.0.0.1", 50000)):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app_mod.app, client=client), base_url="http://t")


def test_every_mutating_route_needs_the_key_unless_listed():
    unprotected = [r.path for r in app_mod.app.routes if isinstance(r, APIRoute)
                   and r.methods & {"POST", "PUT", "PATCH", "DELETE"} and r.path not in OPEN
                   and auth.require_admin not in [d.call for d in r.dependant.dependencies]]
    assert unprotected == []
    for must in ("/api/approvals/{approval_id}", "/api/reset", "/api/cases/{case_id}/call-again",
                 "/api/cases/{case_id}/plan/approve", "/api/hospitals/{hospital_id}/recheck"):
        assert must not in OPEN


@pytest.mark.parametrize("path", ["/api/reset", "/api/approvals/nope"])
async def test_token_set_refuses_missing_or_wrong_key(monkeypatch, path):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    async with _client() as c:
        for headers in ({}, {"X-Remy-Key": "wrong"}, {"X-Remy-Key": TOKEN + "x"}):
            r = await c.post(path, json={"approved": True}, headers=headers)
            assert r.status_code == 401 and r.headers["x-remy-auth"] == "required"
        r = await c.post(path, json={"approved": True}, headers={"X-Remy-Key": TOKEN})
        assert r.status_code == 200


async def test_reads_stay_open_with_a_token(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    async with _client(("203.0.113.9", 1)) as c:
        assert (await c.get("/api/dashboard")).status_code == 200
        assert (await c.get("/api/replay")).status_code in (200, 404)
        assert (await c.get("/api/mode")).status_code == 200


async def test_no_token_allows_only_direct_localhost(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    async with _client() as c:
        assert (await c.post("/api/reset")).status_code == 200
        # a tunnel on the same box connects from localhost but adds forwarding headers
        for h in ({"X-Forwarded-For": "198.51.100.7"}, {"CF-Connecting-IP": "198.51.100.7"}, {"Forwarded": "for=x"}):
            assert (await c.post("/api/reset", headers=h)).status_code == 401
    async with _client(("203.0.113.9", 1)) as c:
        assert (await c.post("/api/reset")).status_code == 401
        assert (await c.post("/api/reset", headers={"X-Remy-Key": ""})).status_code == 401


def test_startup_warning_only_without_a_token(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    assert "REMY_ADMIN_TOKEN" in auth.startup_warning()
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    assert auth.startup_warning() is None


def _live_keys(monkeypatch):
    for k, v in {"ANTHROPIC_API_KEY": "sk-ant-api03-abc", "BRIGHTDATA_API_KEY": "bd-key", "NESSIE_API_KEY": "nk",
                 "BRIGHTDATA_UNLOCKER_ZONE": "agent_unlocker", "BRIGHTDATA_SERP_ZONE": "agent_serp",
                 "ADMIN_TOKEN": TOKEN}.items():
        monkeypatch.setattr(config, k, v)


def test_missing_live_lists_every_blank_key():
    names = " ".join(config.missing_live())
    for k in ("ANTHROPIC_API_KEY", "BRIGHTDATA_API_KEY", "BRIGHTDATA_UNLOCKER_ZONE", "BRIGHTDATA_SERP_ZONE",
              "NESSIE_API_KEY", "REMY_ADMIN_TOKEN"):
        assert k in names


def test_missing_live_checks_format_without_calling_anything(monkeypatch):
    _live_keys(monkeypatch)
    assert config.missing_live() == []
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "not-an-anthropic-key")
    monkeypatch.setattr(config, "BRIGHTDATA_SERP_ZONE", "change-me")
    monkeypatch.setattr(config, "ADMIN_TOKEN", "short")
    names = " ".join(config.missing_live())
    assert "ANTHROPIC_API_KEY" in names and "BRIGHTDATA_SERP_ZONE" in names and "16 characters" in names
    assert "NESSIE_API_KEY" not in names


async def test_judging_mode_refuses_to_start_without_keys(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_LIVE", False)
    config.check_live()  # off by default: offline development runs on fixtures
    monkeypatch.setattr(config, "REQUIRE_LIVE", True)
    with pytest.raises(RuntimeError, match="BRIGHTDATA_API_KEY"):
        async with app_mod.app.router.lifespan_context(app_mod.app):
            pass
    _live_keys(monkeypatch)
    config.check_live()


async def test_judging_mode_never_uses_a_sample_policy(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_LIVE", True)
    hosp = {"name": "Nowhere General Hospital", "sample_policy": "policy_sample.md"}
    got = await policy.find_policy("judging-test", policy.report("judging-test"), hosp)
    assert got["found"]["tiers"] == [] and got["origin"] == "failed" and got["source"] != policy.FIXTURE_LABEL
    with pytest.raises(RuntimeError):
        await policy._fixture("judging-test", hosp)
    monkeypatch.setattr(config, "REQUIRE_LIVE", False)  # default: the fixture still backs offline runs
    got = await policy.find_policy("judging-test", policy.report("judging-test"), hosp)
    assert got["origin"] == "offline_fixture" and got["found"]["tiers"]


async def test_desk_interrupt_needs_the_key(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    async with _client() as c:
        assert (await c.post("/api/calls/nope/interrupt", json={"played_s": 1.0})).status_code == 401


def test_desk_listen_websocket_needs_the_key(monkeypatch):
    """It streams the mic to Cartesia (credits): the key comes as ?key= since a browser can't set the header."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    with TestClient(app_mod.app) as tc:
        for url in ("/api/calls/nope/listen", "/api/calls/nope/listen?key=wrong"):
            with pytest.raises(WebSocketDisconnect) as e:
                with tc.websocket_connect(url) as ws:
                    ws.receive_text()
            assert e.value.code == 1008
        with tc.websocket_connect(f"/api/calls/nope/listen?key={TOKEN}") as ws:  # let in: no such call
            assert json.loads(ws.receive_text())["type"] == "unavailable"


@pytest.fixture
def two_cases(monkeypatch):
    """A built-in sample case and one made from uploaded documents (a real patient's details)."""
    from remy import orchestrator
    monkeypatch.setattr(orchestrator, "cases", {})
    sample, mine = orchestrator.new_case(), orchestrator.new_case()
    sample["sample"] = True
    return sample, mine


async def test_uploaded_case_needs_the_key_to_read(monkeypatch, two_cases):
    sample, mine = two_cases
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    async with _client(("203.0.113.9", 1)) as c:
        assert [r["id"] for r in (await c.get("/api/dashboard")).json()] == [sample["id"]]
        assert [r["id"] for r in (await c.get("/api/cases")).json()] == [sample["id"]]
        for path in (f"/api/cases/{sample['id']}", f"/api/cases/{sample['id']}/summary",
                     f"/api/approvals?case={sample['id']}"):
            assert (await c.get(path)).status_code == 200, path
        for path in (f"/api/cases/{mine['id']}", f"/api/cases/{mine['id']}/summary", f"/api/approvals?case={mine['id']}"):
            r = await c.get(path)
            assert r.status_code == 401 and r.headers["x-remy-auth"] == "required", path
            assert (await c.get(path, headers={"X-Remy-Key": TOKEN})).status_code == 200, path
        assert len((await c.get("/api/dashboard", headers={"X-Remy-Key": TOKEN})).json()) == 2


def test_uploaded_case_websocket_needs_the_key(monkeypatch, two_cases):
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    sample, mine = two_cases
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    with TestClient(app_mod.app) as tc:
        from remy import orchestrator
        orchestrator.cases.update({sample["id"]: sample, mine["id"]: mine})  # lifespan may reload saved cases
        with pytest.raises(WebSocketDisconnect) as e:
            with tc.websocket_connect(f"/ws/cases/{mine['id']}") as ws:
                ws.receive_text()
        assert e.value.code == 1008
        with tc.websocket_connect(f"/ws/cases/{mine['id']}?key={TOKEN}"):
            pass
        with tc.websocket_connect(f"/ws/cases/{sample['id']}"):
            pass


async def test_upload_refused_before_the_body_is_read(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    async with _client(("203.0.113.9", 1)) as c:
        for path in ("/api/chat", "/api/cases", "/api/cases/abc123/talk"):  # any multipart body
            r = await c.post(path, files={"files": ("bill.pdf", b"%PDF-1.4 x", "application/pdf")})
            assert r.status_code == 401 and r.headers["x-remy-auth"] == "required"


async def test_oversized_body_is_refused(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    monkeypatch.setattr(app_mod, "MAX_BODY", 1000)
    big = {"files": ("bill.pdf", b"x" * 5000, "application/pdf")}
    async with _client(("203.0.113.9", 1)) as c:
        assert (await c.post("/api/cases", files=big, headers={"X-Remy-Key": TOKEN})).status_code == 413

        async def chunks():  # no Content-Length: counted as it arrives
            for _ in range(10):
                yield b"y" * 500
        r = await c.post("/api/voices/normalize", content=chunks(), headers={"content-type": "application/json"})
        assert r.status_code == 413


async def test_recorded_replay_needs_the_key(monkeypatch, tmp_path):
    from remy import events
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    (events.DATA_DIR / "replay_default.json").write_text('[{"recorded": true}]')
    async with _client(("203.0.113.9", 1)) as c:
        assert (await c.get("/api/replay")).json() != [{"recorded": True}]
        assert (await c.get("/api/replay", headers={"X-Remy-Key": TOKEN})).json() == [{"recorded": True}]

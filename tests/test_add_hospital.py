"""POST /api/hospitals ("Add a hospital" on the Hospitals page), offline: Bright Data and Claude are faked
(test_live_crawl.py's fixtures), the database is a copy of the committed one."""
import json

import httpx
import pytest

from remy import app as app_mod
from remy import config, llm
from remy.integrations import brightdata
from remy.integrations.brightdata import BrightDataError
from remy.policies import routes

from test_live_crawl import (COMMITTED, UCSF_POLICY_PDF, _fake_claude, _resp, _serp_body,  # noqa: F401
                             db, live_keys)

POLICY_PDF = "https://www.seattlechildrens.org/globalassets/documents/financial-assistance-policy.pdf"


def _client(**kw):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app_mod.app, client=("127.0.0.1", 50000)),
                             base_url="http://t", **kw)


@pytest.fixture
def api_db(db, monkeypatch):
    monkeypatch.setattr(routes, "PolicyStore", lambda: db)
    return db


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for chunk in text.split("\n\n"):
        lines = dict(ln.split(": ", 1) for ln in chunk.splitlines() if ln.startswith(("event: ", "data: ")))
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def _fake_bright_data(monkeypatch) -> list[str]:
    """Google finds the hospital's policy PDF; its bytes are UCSF Health's real policy PDF from the committed crawl
    (this tests the path, not Seattle Children's policy)."""
    calls = []

    async def serp(query, attempts=2):
        calls.append(f"google: {query}")
        body = _serp_body([{"link": POLICY_PDF, "title": "Financial Assistance Policy | Seattle Children's",
                            "description": "Financial assistance and charity care policy (PDF)", "rank": 1}])
        return brightdata.parse_serp(body.decode()), _resp("serp", calls[-1], body)

    async def document(url, attempts=2):
        calls.append(url)
        return _resp("unlocker", url, (COMMITTED.root / UCSF_POLICY_PDF).read_bytes())

    async def page(url, attempts=2):
        calls.append(url)
        raise BrightDataError("unlocker", url, "HTTP 404", 404)
    monkeypatch.setattr(brightdata, "serp", serp)
    monkeypatch.setattr(brightdata, "document", document)
    monkeypatch.setattr(brightdata, "page", page)
    return calls


async def test_needs_the_admin_key(monkeypatch, api_db):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret-admin-token-0123456789")
    async with _client() as c:
        r = await c.post("/api/hospitals", json={"name": "Seattle Children's Hospital"})
    assert r.status_code == 401


@pytest.mark.parametrize("name", ["", "  ", "ab", "x" * 121])
async def test_rejects_empty_or_huge_names(api_db, live_keys, name):
    async with _client() as c:
        r = await c.post("/api/hospitals", json={"name": name})
    assert r.status_code == 400 and "3 to 120 characters" in r.json()["detail"]


async def test_rejects_a_website_that_is_not_one(api_db, live_keys):
    async with _client() as c:
        r = await c.post("/api/hospitals", json={"name": "Seattle Children's Hospital", "website": "not a site"})
    assert r.status_code == 400


async def test_without_keys_it_says_so(api_db):
    async with _client() as c:
        listed = (await c.get("/api/hospitals")).json()
        r = await c.post("/api/hospitals", json={"name": "Seattle Children's Hospital"})
    assert listed["can_add"] is False
    assert r.status_code == 503 and "Bright Data keys" in r.json()["detail"]


async def test_a_new_hospital_is_crawled_streamed_and_saved(api_db, live_keys, monkeypatch):
    calls = _fake_bright_data(monkeypatch)
    monkeypatch.setattr(llm, "json_out", _fake_claude)
    before = {h.id for h in api_db.hospitals()}
    async with _client() as c:
        assert (await c.get("/api/hospitals")).json()["can_add"] is True
        r = await c.post("/api/hospitals", json={"name": "Seattle Children's  Hospital", "city": "Seattle, WA",
                                                 "website": "https://www.seattlechildrens.org/"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        events = _events(r.text)
        listed = (await c.get("/api/hospitals")).json()["hospitals"]

    assert events[0][0] == "start" and events[-1][0] == "done"
    lines = [d for k, d in events if k == "line"]
    assert any(d["key"] == "bd-1" and d["label"].startswith("Bright Data SERP API · google:") for d in lines)
    assert any(d["key"] == "store" and d["status"] == "ok" for d in lines)
    done = events[-1][1]
    assert done["hospital_id"] == "seattle-children-s-hospital" and done["matched"] is False
    assert done["tiers_verified"] == 1 and done["sources"] == [POLICY_PDF]
    assert done["requests"] == len(calls) <= done["cap"] == 10
    new = next(h for h in listed if h["id"] == done["hospital_id"])
    assert new["name"] == "Seattle Children's Hospital" and new["city"] == "Seattle, WA"
    assert new["current"]["tiers_verified"] == 1
    assert api_db.hospital(done["hospital_id"]).domains == ("seattlechildrens.org",)
    assert {h.id for h in api_db.hospitals()} - before == {done["hospital_id"]}
    assert "seattle-children-s-hospital" in (api_db.root / "policies.json").read_text()
    assert COMMITTED.hospital("seattle-children-s-hospital") is None  # committed DB untouched


async def test_an_existing_hospital_is_matched_not_recrawled(api_db, live_keys, monkeypatch):
    async def no_bright_data(*a, **k):
        raise AssertionError("must not crawl a hospital already on record")
    for fn in ("serp", "page", "document"):
        monkeypatch.setattr(brightdata, fn, no_bright_data)
    async with _client() as c:
        r = await c.post("/api/hospitals", json={"name": "ucsf  health"})
    out = r.json()
    assert r.status_code == 200 and out["matched"] is True and out["hospital_id"] == "ucsf-health"
    assert out["version"] == api_db.current("ucsf-health")["version"]


async def test_a_failing_crawl_returns_a_clear_error(api_db, live_keys, monkeypatch):
    async def down(*a, **k):
        raise BrightDataError("serp", "q", "HTTP 503", 503)
    for fn in ("serp", "page", "document"):
        monkeypatch.setattr(brightdata, fn, down)
    async with _client() as c:
        r = await c.post("/api/hospitals", json={"name": "Nowhere General Hospital", "city": "Nowhere, KS"})
    events = _events(r.text)
    assert r.status_code == 200 and events[-1][0] == "error"
    assert events[-1][1]["detail"] == ("Couldn't add Nowhere General Hospital: Bright Data couldn't be reached "
                                       "(HTTP 503). Please try again in a minute.")
    assert any(d["status"] == "flag" for k, d in events if k == "line")  # the failed requests were shown
    assert api_db.current("nowhere-general-hospital") is None

"""The live Bright Data path, offline: a crawl streamed into the case page's Bright Data panel, with visible
retries and fallbacks, a hard request cap, the staleness rule that re-crawls "no tiers" hospitals, and
"Recheck now". No network: Bright Data is replaced by responses in the shapes the real API returns (the
committed crawl artifacts in policydb/raw/, or JSON built like them), and Claude by a fixed answer.

Also the unit-aware posted-price check (billcheck.posted_unit_price) for insured and self-pay bills."""
import json
import shutil
from datetime import timedelta

import pytest

from billback import billcheck, config, llm, orchestrator
from billback.agents import policy
from billback.agents.report import Report
from billback.events import bus
from billback.integrations import brightdata
from billback.integrations.brightdata import BDResponse, BrightDataError
from billback.policies import crawl as crawl_mod
from billback.policies import discover, fetch, pipeline, registry
from billback.policies.store import DEFAULT_DIR, PolicyStore

from test_flow import _wait_for, client  # noqa: F401  (client is a fixture)

SEED = {h.id: h for h in registry.SEED}
COMMITTED = PolicyStore(DEFAULT_DIR)


def _resp(product: str, url: str, body: bytes) -> BDResponse:
    return BDResponse(product, url, 200, body, "application/pdf" if body[:5] == b"%PDF-" else "text/plain", 1200)


def _serp_body(organic: list[dict]) -> bytes:
    """A SERP API reply as Bright Data sends it with format=json: {status_code, headers, body: <json string>}."""
    inner = {"general": {"search_engine": "google"}, "organic": organic}
    return json.dumps({"status_code": 200, "headers": {}, "body": json.dumps(inner)}).encode()


def _events(case_id: str, kind: str) -> list[dict]:
    return [e for e in bus.history(case_id) if e["kind"] == kind]


def _steps(case_id: str) -> dict[str, dict]:
    """Latest state of each step line in the Bright Data panel."""
    out = {}
    for e in _events(case_id, "step"):
        if e["agent"] == "policy":
            out[e["key"].split(":", 1)[1]] = e
    return out


@pytest.fixture
def live_keys(monkeypatch):
    """Make the app believe Bright Data and Claude are configured (every call below is faked)."""
    monkeypatch.setattr(config, "BRIGHTDATA_API_KEY", "test-key")
    monkeypatch.setattr(config, "BRIGHTDATA_UNLOCKER_ZONE", "test_unlocker")
    monkeypatch.setattr(config, "BRIGHTDATA_SERP_ZONE", "test_serp")
    monkeypatch.setattr(llm, "_client", object())
    monkeypatch.setattr(crawl_mod, "pause", _no_sleep)

    async def no_claude(*a, **k):
        raise AssertionError("Claude must not be called here")
    monkeypatch.setattr(llm, "json_out", no_claude)


async def _no_sleep(s):
    return None


@pytest.fixture
def db(tmp_path, monkeypatch) -> PolicyStore:
    """A copy of the committed policy DB; the committed one is never written."""
    shutil.copytree(DEFAULT_DIR, tmp_path / "db")
    store = PolicyStore(tmp_path / "db")
    monkeypatch.setattr(policy, "store", lambda: store)
    return store


# ---- the crawl session: visible retries, outcomes, the hard cap ----

def test_hard_cap_is_ten_whatever_the_caller_asks(tmp_path):
    c = crawl_mod.Crawl(PolicyStore(tmp_path), "run", "h", max_requests=50)
    assert c.max_requests == crawl_mod.HARD_CAP == policy.LIVE_MAX_REQUESTS == 10


@pytest.mark.parametrize("err, kind", [
    (BrightDataError("unlocker", "u", "ReadTimeout", 0), "timeout"),
    (BrightDataError("unlocker", "u", "HTTP 502: Navigation timeout", 502), "timeout"),
    (BrightDataError("unlocker", "u", "HTTP 403: Forbidden", 403), "blocked"),
    (BrightDataError("unlocker", "u", "HTTP 400: classified as Government and blocked", 400), "blocked"),
    (BrightDataError("serp", "u", "Google returned 429: retry", 429), "rate_limited"),
    (BrightDataError("unlocker", "u", "HTTP 404: not found", 404), "not_found"),
    (BrightDataError("unlocker", "u", "HTTP 503: unavailable", 503), "server_error")])
def test_failures_are_named(err, kind):
    assert crawl_mod.classify(err) == kind


async def test_timeout_is_retried_once_in_the_open(tmp_path, monkeypatch):
    monkeypatch.setattr(crawl_mod, "pause", _no_sleep)
    seen, calls = [], []

    async def page(url, attempts=2):
        calls.append(attempts)
        if len(calls) == 1:
            raise BrightDataError("unlocker", url, "ReadTimeout", 0, 60000)
        return _resp("unlocker", url, b"# Financial assistance\n" + b"x" * 400)
    monkeypatch.setattr(brightdata, "page", page)
    store = PolicyStore(tmp_path)
    c = crawl_mod.Crawl(store, "run", "h", lambda k, **e: seen.append((k, e)))
    resp = await c.page("https://example.org/fa")
    assert resp.status == 200 and calls == [1, 1]  # the client never retries behind the crawl's back
    done = [e for k, e in seen if k == "brightdata"]
    assert [(e["n"], e["outcome"], e["retrying"]) for e in done] == [(1, "timeout", True), (2, "found", False)]
    assert [e["attempt"] for k, e in seen if k == "brightdata_request"] == [1, 2]
    assert [r["ok"] for r in store.crawl_log("h")] == [0, 1] and "(retrying)" in store.crawl_log("h")[0]["note"]


async def test_blocked_is_not_retried_and_empty_is_named(tmp_path, monkeypatch):
    seen = []

    async def page(url, attempts=2):
        if "blocked" in url:
            raise BrightDataError("unlocker", url, "HTTP 403: Access denied", 403, 900)
        return _resp("unlocker", url, b"   ")
    monkeypatch.setattr(brightdata, "page", page)
    c = crawl_mod.Crawl(PolicyStore(tmp_path), "run", "h", lambda k, **e: seen.append((k, e)))
    with pytest.raises(BrightDataError):
        await c.page("https://blocked.example.org/")
    await c.page("https://empty.example.org/")
    assert [(e["outcome"], e["retrying"]) for k, e in seen if k == "brightdata"] == [("blocked", False), ("empty", False)]
    assert c.requests == 2


async def test_the_cap_stops_the_crawl(tmp_path, monkeypatch):
    async def page(url, attempts=2):
        raise BrightDataError("unlocker", url, "HTTP 503: unavailable", 503)
    monkeypatch.setattr(brightdata, "page", page)
    monkeypatch.setattr(crawl_mod, "pause", _no_sleep)
    seen = []
    c = crawl_mod.Crawl(PolicyStore(tmp_path), "run", "h", lambda k, **e: seen.append(k), max_requests=3)
    with pytest.raises(BrightDataError):
        await c.page("https://a.example.org/")  # request 1 fails, request 2 is its retry
    with pytest.raises(BrightDataError):
        await c.page("https://b.example.org/")  # request 3; no budget left for a retry
    with pytest.raises(crawl_mod.BudgetExceeded):
        await c.page("https://c.example.org/")
    assert c.requests == 3 and seen.count("brightdata_request") == 3 and "budget" in seen


# ---- fallbacks between sources and tools ----

async def test_blocked_page_falls_back_to_the_next_candidate(tmp_path, monkeypatch):
    row = COMMITTED.current("sutter-cpmc")
    page_md = (COMMITTED.root / next(a for d in row["documents"] if d["kind"] == "page"
                                     for a in d["artifacts"])).read_bytes()

    async def page(url, attempts=2):
        if "first" in url:
            raise BrightDataError("unlocker", url, "HTTP 403: Access denied", 403)
        return _resp("unlocker", url, page_md)

    async def document(url, attempts=2):
        raise BrightDataError("unlocker", url, "ReadTimeout", 0)
    monkeypatch.setattr(brightdata, "page", page)
    monkeypatch.setattr(brightdata, "document", document)
    monkeypatch.setattr(crawl_mod, "pause", _no_sleep)
    seen = []
    c = crawl_mod.Crawl(PolicyStore(tmp_path), "run", "sutter-cpmc", lambda k, **e: seen.append((k, e)))
    cand = {"title": "t", "reasons": [], "is_pdf": False}
    docs = await fetch.run(c, {"pages": [{**cand, "url": "https://www.sutterhealth.org/first"},
                                         {**cand, "url": "https://www.sutterhealth.org/second"}]})
    assert [d.url for d in docs] == ["https://www.sutterhealth.org/second"]  # its PDFs timed out (twice each)
    falls = [e["text"] for k, e in seen if k == "crawl_fallback"]
    assert "blocked → next candidate" in falls[0]
    assert any("timeout → skipped" in t for t in falls[1:])
    assert c.requests <= crawl_mod.HARD_CAP


async def test_unreachable_google_falls_back_to_the_urls_on_record(tmp_path, monkeypatch):
    async def serp(query, attempts=2):
        raise BrightDataError("serp", query, "HTTP 403: blocked", 403)
    monkeypatch.setattr(brightdata, "serp", serp)
    seen = []
    known = COMMITTED.current("sutter-cpmc")["discovery"]
    c = crawl_mod.Crawl(PolicyStore(tmp_path), "run", "sutter-cpmc", lambda k, **e: seen.append((k, e)))
    found = await discover.run(c, SEED["sutter-cpmc"], known)
    assert found["pages"] == known["pages"] and found["fallback"] == "SERP API blocked"
    assert any(k == "crawl_fallback" and "URL" in e["text"] and "Web Unlocker" in e["text"] for k, e in seen)
    with pytest.raises(BrightDataError):  # nothing on record: a blocked Google is an error, said so
        await discover.run(crawl_mod.Crawl(PolicyStore(tmp_path), "run2", "x"), SEED["sutter-cpmc"])


# ---- staleness: "no tiers" and old policies are refreshed ----

def _row(store: PolicyStore, hid: str, status: str) -> dict:
    store.upsert_hospital(SEED[hid])
    return store.add_version(hid, content_sha256="x", documents=[], discovery={}, extracted={"tiers": []},
                             verification={}, status=status, run_id=None)


def test_staleness_rule(tmp_path):
    store = PolicyStore(tmp_path)
    none = _row(store, "ucsf-saint-francis", "no_tiers")
    t0 = policy._when(none["fetched_at"])
    assert policy.staleness(store, none, t0 + timedelta(hours=1)) is None  # just tried: don't pay again
    assert "no verified discount tiers" in policy.staleness(store, none, t0 + timedelta(hours=7))
    ok = _row(store, "sutter-cpmc", "ok")
    t1 = policy._when(ok["fetched_at"])
    assert policy.staleness(store, ok, t1 + timedelta(days=29)) is None
    assert policy.staleness(store, ok, t1 + timedelta(days=31)) == "last checked 31 days ago"
    # a failed or unchanged check counts as "last tried"
    store.add_check("ucsf-saint-francis", None, False, None, "live refresh failed: timeout", None)
    later = policy._when(store.checks("ucsf-saint-francis")[0]["checked_at"])
    assert policy.staleness(store, none, later + timedelta(hours=1)) is None


def test_committed_no_tiers_hospitals_are_refreshable():
    """Saint Francis, Stanford and ZSFG are stored as "no tiers": the rule re-crawls them (6 hours after the
    last try) instead of keeping a wrong answer forever."""
    for hid in ("ucsf-saint-francis", "stanford-health", "zsfg"):
        row = COMMITTED.current(hid)
        assert row["status"] == "no_tiers"
        assert policy.staleness(COMMITTED, row, policy._when(row["fetched_at"]) + timedelta(days=1))


# ---- the live moment: a stale "no tiers" hospital re-crawled, streamed into the panel ----

UCSF_POLICY_PDF = "raw/ucsf-health/pdf-db570fe1a4ce.pdf"
SF_POLICY_URL = "https://sfcommunityhospitals.ucsfhealth.org/-/media/project/ucsf/sfch/pdf/financial-assistance-policy.pdf"


def _fake_saint_francis(monkeypatch) -> list[str]:
    """Bright Data for a Saint Francis re-crawl. Query 1 times out once, then returns the REAL results Google
    gave in the committed crawl (no English policy: the ranking now asks for a second search). The second
    search (site:) returns an English policy PDF. Its bytes are UCSF Health's real policy PDF from the committed
    crawl, standing in for a document we don't have: this tests the path, not Saint Francis's policy."""
    real = COMMITTED.read_artifact(COMMITTED.current("ucsf-saint-francis")["discovery"]["serp_artifacts"][0])
    first = json.dumps({"status_code": 200, "headers": {}, "body": real}).encode()
    second = _serp_body([{"link": SF_POLICY_URL, "title": "Financial Assistance Policy | UCSF Health Saint Francis",
                          "description": "Financial assistance and charity care policy (PDF)", "rank": 1}])
    calls = []

    async def serp(query, attempts=2):
        calls.append(f"google: {query}")
        if len(calls) == 1:
            raise BrightDataError("serp", query, "ReadTimeout", 0, 60000)
        body = second if query.startswith("site:") else first
        return brightdata.parse_serp(body.decode()), _resp("serp", calls[-1], body)

    async def document(url, attempts=2):
        calls.append(url)
        assert url == SF_POLICY_URL
        return _resp("unlocker", url, (COMMITTED.root / UCSF_POLICY_PDF).read_bytes())

    async def page(url, attempts=2):
        calls.append(url)
        raise BrightDataError("unlocker", url, "HTTP 404", 404)
    monkeypatch.setattr(brightdata, "serp", serp)
    monkeypatch.setattr(brightdata, "document", document)
    monkeypatch.setattr(brightdata, "page", page)
    return calls


UCSF_TIER = "Is a Self-Pay Patient; and 2. Has Family Income at or below 400% of the Federal Poverty Level (FPL)."


async def _fake_claude(system, prompt, **k):
    return {"tiers": [{"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "uninsured", "min_oop_pct_income": None,
                       "quote": UCSF_TIER}]}


async def test_stale_no_tiers_hospital_is_recrawled_live_and_streamed(db, live_keys, monkeypatch):
    calls = _fake_saint_francis(monkeypatch)
    monkeypatch.setattr(llm, "json_out", _fake_claude)
    monkeypatch.setattr(policy, "staleness", lambda store, row, now=None: "no verified discount tiers on file")
    before = db.current("ucsf-saint-francis")
    r = policy.report("live-sf")
    got = await policy.find_policy("live-sf", r, {"name": "Saint Francis Memorial Hospital"})

    assert got["origin"] == "live_crawl" and got["version"] == before["version"] + 1
    assert [t["max_fpl_pct"] for t in got["found"]["tiers"]] == [400]
    assert db.current("ucsf-saint-francis")["status"] == "ok"
    assert len(calls) == 4 <= policy.LIVE_MAX_REQUESTS  # SERP timeout + SERP retry + site: SERP + the PDF
    steps = _steps("live-sf")
    # one line per Bright Data request: which tool, which URL, what came back
    assert steps["bd-1"]["label"].startswith("Bright Data SERP API · google:")
    assert steps["bd-1"]["status"] == "flag" and "Timed out" in steps["bd-1"]["result"]
    assert "retrying once" in steps["bd-1"]["result"] and "of at most 10" in steps["bd-1"]["result"]
    assert steps["bd-2"]["status"] == "ok" and steps["bd-2"]["result"].startswith("Found")
    assert steps["bd-4"]["label"].startswith("Bright Data Web Unlocker · sfcommunityhospitals.ucsfhealth.org")
    assert "second search: site:sfcommunityhospitals.ucsfhealth.org" in steps["fallback-1"]["result"]
    # the verified quote is shown with its source
    assert any(UCSF_TIER[:40] in p["text"] and p["source"] == SF_POLICY_URL for p in steps["verify"]["proof"])
    assert steps["find"]["status"] == "ok" and "Checked live via Bright Data" in steps["find"]["result"]
    assert COMMITTED.current("ucsf-saint-francis")["version"] == before["version"]  # committed DB untouched


async def test_failed_refresh_falls_back_to_the_stored_copy(db, live_keys, monkeypatch):
    async def serp(query, attempts=2):
        raise BrightDataError("serp", query, "HTTP 502: Navigation timeout", 502, 30000)

    async def unreachable(url, attempts=2):
        raise BrightDataError("unlocker", url, "HTTP 403: Access denied", 403)
    monkeypatch.setattr(brightdata, "serp", serp)
    monkeypatch.setattr(brightdata, "page", unreachable)
    monkeypatch.setattr(brightdata, "document", unreachable)
    monkeypatch.setattr(policy, "staleness", lambda store, row, now=None: "no verified discount tiers on file")
    stored = db.current("ucsf-saint-francis")
    got = await policy.find_policy("live-fail", policy.report("live-fail"), {"name": "UCSF Health Saint Francis Hospital"})
    assert got["origin"] == "policy_db" and got["version"] == stored["version"]
    steps = _steps("live-fail")
    assert "Live check failed" in steps["refresh"]["result"] and steps["refresh"]["status"] == "flag"
    assert steps["find"]["status"] == "flag" and "the live check failed" in steps["find"]["result"]
    assert db.checks("ucsf-saint-francis")[0]["note"].startswith("live refresh failed")  # starts the cooldown
    log = [l for l in db.crawl_log("ucsf-saint-francis") if l["run_id"] and l["product"]][-4:]
    assert len([l for l in db.crawl_log("ucsf-saint-francis") if l["product"]]) - len(
        [l for l in COMMITTED.crawl_log("ucsf-saint-francis") if l["product"]]) <= policy.LIVE_MAX_REQUESTS
    assert all(not l["ok"] for l in log)


async def test_offline_stale_policy_is_used_and_labelled(db):
    """No keys: the stored copy is used, and the panel says it's stale and why it wasn't refreshed."""
    row = db.current("ucsf-saint-francis")
    r = policy.report("offline-sf")
    got = await policy.find_policy("offline-sf", r, {"name": "UCSF Health Saint Francis Hospital"})
    assert got["origin"] == "policy_db" and got["version"] == row["version"]
    stale = policy.staleness(db, row)
    if stale:  # true once 6 hours have passed since the committed crawl
        assert "a live check needs Bright Data keys" in _steps("offline-sf")["find"]["result"]


# ---- "Recheck now" on the case page ----

async def test_recheck_now_needs_keys(client):  # noqa: F811
    cid = (await client.post("/api/cases")).json()["case_id"]
    await _wait_for(cid, lambda e: e["kind"] == "approval_requested" and e["agent"] == "caller")
    r = await client.post(f"/api/cases/{cid}/policy/recheck")
    assert r.status_code == 503 and "Bright Data" in r.json()["error"]
    assert (await client.get("/api/mode")).json()["live_crawl"] is False


async def test_recheck_now_streams_into_the_panel(client, db, live_keys, monkeypatch):  # noqa: F811
    """The sample case is at CPMC. Recheck: CPMC's stored documents are refetched (served from the committed
    crawl, so unchanged), and the plan's rules still hold."""
    monkeypatch.setattr(llm, "_client", None)  # the case itself runs offline
    cid = (await client.post("/api/cases")).json()["case_id"]
    await _wait_for(cid, lambda e: e["kind"] == "approval_requested" and e["agent"] == "caller")
    monkeypatch.setattr(llm, "_client", object())
    row = db.current("sutter-cpmc")
    by_url = {d["url"]: (db.root / next(a for a in d["artifacts"] if a.endswith((".md", ".pdf")))).read_bytes()
              for d in row["documents"]}

    async def fetched(url, attempts=2):
        return _resp("unlocker", url, by_url[url])
    monkeypatch.setattr(brightdata, "page", fetched)
    monkeypatch.setattr(brightdata, "document", fetched)

    n = len(bus.history(cid))
    r = await client.post(f"/api/cases/{cid}/policy/recheck")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["changed"] is False and out["origin"] == "live_recheck"
    new = bus.history(cid)[n:]
    assert new[0]["kind"] == "agent_reopened" and new[-1]["kind"] == "agent_finished"
    steps = _steps(cid)
    assert steps["re1-recheck-start"]["status"] == "ok" and "Still holds" in steps["re1-recheck-start"]["result"]
    reqs = [k for k in steps if k.startswith("re1-bd-")]
    assert len(reqs) == len(row["documents"]) and all(steps[k]["status"] == "ok" for k in reqs)
    assert "Unchanged since" in steps["re1-recheck"]["result"]
    assert db.checks("sutter-cpmc")[0]["note"] == "unchanged"
    assert orchestrator.cases[cid]["policy_rechecks"][0]["changed"] is False
    # a second recheck gets its own lines, keeping the first one's
    await client.post(f"/api/cases/{cid}/policy/recheck")
    assert "re2-recheck-start" in _steps(cid) and "re1-recheck-start" in _steps(cid)


async def test_recheck_now_waits_for_the_review(live_keys):
    case = {"id": "early", "hospital": {"name": "California Pacific Medical Center"}}
    with pytest.raises(policy.RecheckRefused, match="Review"):
        await policy.recheck_case(case)


# ---- the posted-price check: unit-aware, insured and self-pay ----

def _entry(gross, unit=None, typ=None, common="x"):
    return {"gross": gross, "drug_unit": unit, "drug_unit_type": typ, "common_name": common}


def test_posted_unit_price_converts_only_like_units():
    assert billcheck.posted_unit_price("80053", _entry(386.0)) == (386.0, "per service")
    # Chinese Hospital: ondansetron $44.63 per 4 mg -> $11.16 per 1 mg (the HCPCS J2405 unit)
    price, how = billcheck.posted_unit_price("J2405", _entry(44.63, 4.0, "ME"))
    assert price == 11.16 and "per 1 mg (the HCPCS unit)" in how
    assert billcheck.posted_unit_price("J3010", _entry(205.0, 0.1, "ME")) == (205.0, "per HCPCS unit")
    assert billcheck.posted_unit_price("J0696", _entry(10.0, 1.0, "GR"))[0] == 2.5  # 1 g -> per 250 mg
    assert billcheck.posted_unit_price("J7030", _entry(70.25, 250.0, "ML"))[0] == 281.0  # per 1000 cc
    # CPMC: per 12.25 mL; HCPCS J2405 is per 1 mg: needs the concentration, so not compared
    price, why = billcheck.posted_unit_price("J2405", _entry(16.33, 12.25, "ML", "Ondansetron (Zofran) injection, per 1 mg"))
    assert price is None and "12.25 mL" in why and "per 1 mg" in why
    assert billcheck.posted_unit_price("J2270", _entry(40.0, 1.0, "EA"))[0] is None
    assert billcheck.posted_unit_price("J1885", _entry(12.0))[0] is None  # no unit in the file


def test_default_demo_case_has_no_price_above_posted():
    """demo/case.json (CPMC, insured) must match the replays and pitch: no line flag. J2405 $440 vs the file's
    $16.33 per 12.25 mL is not compared (per 1 mg in HCPCS). 80053 is billed at CPMC's posted $386 (it was $420,
    $34 above; the $34 moved to 99284, $3,184 vs CPMC's posted $5,214), so the total is still $8,700."""
    case = json.loads((config.DEMO_DIR / "case.json").read_text())
    assert sum(li["amount"] for li in case["bill"]["line_items"]) == case["bill"]["total_charges"] == 8700
    review = billcheck.line_review(case["bill"], case["hospital"]["name"])
    assert review["corrections"] == []
    assert [(s["line"], s["code"]) for s in review["skipped"]] == [(6, "J2405")]
    assert "12.25 mL" in review["skipped"][0]["why"]
    assert review["compared"] == 5


def test_lena_insured_overcharge_is_found():
    case = json.loads((config.ROOT / "demo/scenarios/lena-johnmuir/case.json").read_text())
    review = billcheck.line_review(case["bill"], case["hospital"]["name"])
    assert [(c["kind"], c["line"], c["amount"]) for c in review["corrections"]] == [("price_above_posted", 2, 822.5)]
    assert [s["code"] for s in review["skipped"]] == ["J2270"]  # morphine priced per 1 mL, HCPCS per 10 mg


async def test_insured_lines_step_says_what_was_compared():
    case = {**orchestrator.demo_case(), "id": "lines-step"}
    from billback.agents import auditor  # noqa: PLC0415
    from test_scenarios import LocalMirror  # noqa: PLC0415
    import asyncio  # noqa: PLC0415
    loop = asyncio.get_running_loop()
    aud = await auditor.run(case, LocalMirror(case), "mirror", "Offline bank (mirror)", loop.create_future(),
                            loop.create_future())
    lines = next(e for e in reversed(bus.history("lines-step")) if e["kind"] == "step" and e["key"] == "auditor:lines")
    texts = [p["text"] for p in lines["proof"]]
    assert lines["status"] == "clear" and texts[0] == "5 lines compared with the hospital's posted prices"
    assert not any(e["kind"] == "finding" and "posted price" in e["title"] for e in bus.history("lines-step"))
    assert any(t.startswith("Line 6 (J2405) not compared") for t in texts)
    assert aud["reconciliation"]["owed"] == 600  # insured: the share is still the insurance statement's


def test_report_prefix_keeps_runs_apart():
    r = policy._Run(Report("x", "policy", "Bright Data agent", ""), "re1-")
    r.step("bd-1", "label", "ok")
    assert bus.history("x")[-1]["key"] == "policy:re1-bd-1"

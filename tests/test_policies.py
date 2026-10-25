"""Policy pipeline tests, offline, using the COMMITTED real crawl artifacts in policydb/ as fixtures:
the SERP JSON, page markdown and PDFs that Bright Data actually returned."""
import json
import shutil
from pathlib import Path

import pytest

from remy import config
from remy.integrations import brightdata
from remy.integrations.brightdata import BDResponse, BrightDataError
from remy.policies import compare, discover, extract, fetch, monitor, pipeline, registry
from remy.policies.store import DEFAULT_DIR, PolicyStore

DB = PolicyStore(DEFAULT_DIR)
SEED = {h.id: h for h in registry.SEED}


def current(hid: str) -> dict:
    return DB.current(hid)


def artifact(hid: str, prefix: str) -> str:
    row = current(hid)
    paths = row["discovery"]["serp_artifacts"] + [a for d in row["documents"] for a in d["artifacts"]]
    return next(p for p in paths if p.split("/")[-1].startswith(prefix))


def text(rel: str) -> str:
    return DB.read_artifact(rel)


# ---- the committed evidence itself ----

def test_committed_db_has_real_crawls():
    ids = {h.id for h in DB.hospitals()}
    assert {"sutter-cpmc", "ucsf-health", "zsfg", "chinese-hospital"} <= ids
    counts = DB.request_counts()
    assert counts["serp"] >= 4 and counts["unlocker"] >= 8
    for hid in ids:
        row = current(hid)
        assert row and row["documents"], hid
        for d in row["documents"]:
            for a in d["artifacts"]:
                assert (DB.root / a).is_file(), a
        log = DB.crawl_log(hid)
        logged = {l["url"] for l in log if l["product"] == "unlocker" and l["ok"]}
        plain = {l["url"] for l in log if l["product"] is None and l["ok"] and l["note"] == "plain HTTPS (not Bright Data)"}
        for d in row["documents"]:  # every stored document was fetched via Web Unlocker, or HCAI's over plain HTTPS
            assert d["url"] in (plain if d["role"] == "state_filed" else logged), d["url"]
    assert json.loads((DB.root / "policies.json").read_text())["hospitals"]


# ---- discover ----

def test_serp_ranking_prefers_official_and_demotes_aggregators():
    results = brightdata.parse_serp(text(artifact("sutter-cpmc", "serp-")))
    ranked = discover.rank(results, SEED["sutter-cpmc"])
    assert ranked[0].url == "https://www.sutterhealth.org/billing-insurance/financial-assistance"
    assert ranked[0].kind == "official" and any("financial assistance" in r for r in ranked[0].reasons)
    aggs = [c for c in ranked if c.kind == "aggregator"]
    assert aggs and all(c.score < 0 for c in aggs)
    assert {"careroute.ai", "dollarfor.org"} & {discover.host(c.url) for c in aggs}
    picked = discover.pick(ranked)
    assert [c.url for c in picked["pages"]] == [ranked[0].url]
    assert picked["regulator_doc"] and "hcai.ca.gov" in picked["regulator_doc"].url


def test_ranking_skips_google_redirects_and_lookalike_domains():
    results = [
        {"link": "https://www.google.com/goto?url=CAES123", "title": "Help Paying Your Bill", "rank": 1},
        {"link": "https://sfcommunityhospitals.ucsfhealth.org/saint-francis/help-paying-your-bill",
         "title": "Help Paying Your Bill | Saint Francis", "rank": 2},
        {"link": "https://www.careroute.ai/hospital-financial-assistance/ucsf-health", "title": "UCSF Financial Assistance", "rank": 3},
    ]
    h = SEED["ucsf-health"]
    ranked = discover.rank(results, h)
    assert all("google.com" not in c.url for c in ranked)
    assert {c.kind for c in ranked} == {"other", "aggregator"}  # a different hospital's subdomain isn't official
    assert discover.needs_retry(results, h) and discover.retry_query(h).startswith("site:ucsfhealth.org")


# ---- fetch ----

def test_policy_pdf_link_discovery_on_real_pages():
    sutter = discover_links("sutter-cpmc")
    assert sutter[0].url.endswith("hospital-financial-assistance-policy-english.pdf")
    assert all("application" not in d.url.split("/")[-1] for d in sutter)  # forms are not the policy
    ucsf = discover_links("ucsf-health")
    assert [d.url.split("/")[-1] for d in ucsf] == ["patient_financial_assistance_policy_30407.pdf",
                                                    "self_pay_discount_policy_30406.pdf"]
    assert discover_links("zsfg") == []  # ZSFG's page links to HCAI, not to a policy PDF


def discover_links(hid: str) -> list[fetch.DocLink]:
    page = next(d for d in current(hid)["documents"] if d["kind"] == "page")
    return fetch.policy_doc_links(text(page["artifacts"][0]), page["url"])


def test_doc_link_scoring_rejects_other_languages_and_media():
    assert fetch.score_doc_link("Spanish", "https://x.org/policy-financial-assistance-spanish.pdf").score < 0
    assert fetch.score_doc_link("Wall posting (English)", "https://x.org/notice-english.mp3").score < 0
    assert fetch.score_doc_link("Financial Assistance Application", "https://x.org/fa_application.pdf").score < 6
    assert fetch.score_doc_link("Financial Assistance Policy (PDF)", "https://x.org/fap.pdf").score >= 6


def test_pdf_text_matches_committed_extraction():
    rel = artifact("chinese-hospital", "pdf-")
    pdf = (DB.root / rel.replace(".txt", ".pdf")).read_bytes()
    fetch.check_pdf(pdf, "x")
    got, pages = fetch.pdf_text(pdf)
    assert pages == 11 and got == text(rel.replace(".pdf", ".txt"))
    with pytest.raises(ValueError):
        fetch.check_pdf(b"<html>blocked</html>", "x")


def test_markdown_text_ignores_per_request_link_tokens():
    a = "Call us or [\\[email protected\\]](/cdn-cgi/l/email-protection#f0809184) [Policy (PDF)](/fap.pdf \"t\")"
    b = "Call us or [\\[email protected\\]](/cdn-cgi/l/email-protection#a2d2c3d6) [Policy (PDF)](/fap.pdf \"t\")"
    assert fetch.markdown_text(a) == fetch.markdown_text(b) == "Call us or email protected Policy (PDF)"


# ---- extract + verify ----

SUTTER_HIGH_COST = ("A write-off of the Patient Responsibility amount for Hospital Services to insured patients earning "
                    "400% or less of the FPIG and with high medical costs (medical expenses over the past 12 months "
                    "exceeding 10% of family income).")


def sutter_docs() -> list[fetch.Document]:
    return pipeline.load_documents(DB, current("sutter-cpmc"))


def test_verification_accepts_real_quote_and_rejects_fabricated_one():
    docs = sutter_docs()
    found = {"tiers": [
        {"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "insured", "min_oop_pct_income": 10,
         # same sentence as on the page, with typographic noise a model might introduce
         "quote": SUTTER_HIGH_COST.replace("  ", " ").replace("(", "( ").upper()},
        {"max_fpl_pct": 600, "discount_pct": 100, "applies_to": "all",  # fabricated: Sutter says nothing like this
         "quote": "Patients with family income at or below 600% of the Federal Poverty Level receive free care."},
        {"max_fpl_pct": 300, "discount_pct": 100, "applies_to": "insured",  # real sentence, wrong number
         "quote": SUTTER_HIGH_COST},
    ], "collections": {"no_collections_while_pending": True, "quote": "We never send anyone to collections."}}
    policy, v = extract.verify(found, docs)
    assert [t["max_fpl_pct"] for t in policy["tiers"]] == [400]
    assert policy["tiers"][0]["source_url"] == "https://www.sutterhealth.org/billing-insurance/financial-assistance"
    reasons = [c["reason"] for c in v["checks"] if c["what"] == "tier"]
    assert reasons == ["", "quote not found in any fetched document", "quotes do not state the tier's FPL limit"]
    assert v["tiers_verified"] == 1 and v["tiers_rejected"] == 2
    assert policy["no_collections_while_pending"] is None and policy["collections_quote"] == ""


def test_limit_quote_lets_a_split_rule_verify():
    docs = pipeline.load_documents(DB, current("ucsf-health"))
    rule = "Has out-of-pocket medical expenses in the prior twelve months"
    t = {"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "insured", "min_oop_pct_income": 10, "quote": rule}
    assert extract.verify({"tiers": [t]}, docs)[0]["tiers"] == []  # no FPL number in the quote
    t["limit_quote"] = "Has Family Income at or below 400% of the Federal Poverty Level (FPL)."
    assert len(extract.verify({"tiers": [t]}, docs)[0]["tiers"]) == 1
    t["limit_quote"] = "Has Family Income at or below 400% of the Federal Poverty Level, always."
    assert extract.verify({"tiers": [t]}, docs)[0]["tiers"] == []  # limit_quote must be real too


def test_stored_policies_are_all_verified_against_committed_text():
    for h in DB.hospitals():
        row = current(h.id)
        docs = pipeline.load_documents(DB, row)
        assert pipeline.content_hash(docs) == row["content_sha256"]  # artifacts are exactly what was hashed
        for t in row["extracted"]["tiers"]:
            assert t["verified"] and extract.locate(t["quote"], docs).url == t["source_url"]


# ---- store ----

def test_db_round_trip(tmp_path):
    db = PolicyStore(tmp_path)
    h = registry.Hospital("test-h", "Test Hospital", "Test Health", "SF", ("test.org",), ("TH",))
    db.upsert_hospital(h)
    assert db.hospital("test-h") == h
    run = db.start_run("crawl", ["test-h"])
    db.log(run_id=run, hospital_id="test-h", stage="fetch", product="unlocker", url="https://test.org", status=200,
           nbytes=10, latency_ms=5, ok=True)
    rel = db.save_artifact("test-h", "page-x.md", "hello")
    tiers = [{"max_fpl_pct": 200.0, "discount_pct": 100.0, "applies_to": "all", "min_oop_pct_income": None, "quote": "q"}]
    v1 = db.add_version("test-h", content_sha256="a" * 64, documents=[{"url": "https://test.org", "artifacts": [rel]}],
                        discovery={"pages": []}, extracted={"tiers": tiers}, verification={"tiers_verified": 1},
                        status="ok", run_id=run)
    v2 = db.add_version("test-h", content_sha256="b" * 64, documents=[], discovery=None, extracted={"tiers": []},
                        verification={}, status="no_tiers", run_id=run)
    assert (v1["version"], v2["version"]) == (1, 2) and db.current("test-h")["id"] == v2["id"]
    assert [v["is_current"] for v in db.versions("test-h")] == [1, 0]
    assert db.versions("test-h")[1]["extracted"]["tiers"] == tiers and db.read_artifact(rel) == "hello"
    assert db.request_counts() == {"unlocker": 1}
    db.finish_run(run, {"ok": True})
    out = json.loads(db.write_export().read_text())
    assert out["hospitals"][0]["id"] == "test-h" and len(out["hospitals"][0]["versions"]) == 2


# ---- full pipeline offline (Bright Data replaced by the committed responses) ----

def replay_brightdata(monkeypatch, hid: str) -> list[str]:
    """Serve the committed SERP/page/PDF bytes for this hospital in place of live Bright Data calls."""
    row, calls = current(hid), []
    by_url = {d["url"]: (DB.root / next(a for a in d["artifacts"] if a.endswith((".md", ".pdf")))).read_bytes()
              for d in row["documents"]}
    serp_body = (DB.root / row["discovery"]["serp_artifacts"][0]).read_bytes()

    def resp(product, url, body):
        calls.append(url)
        return BDResponse(product, url, 200, body, "application/pdf" if body[:5] == b"%PDF-" else "text/plain", 1)

    async def serp(query, **k):
        return brightdata.parse_serp(serp_body.decode()), resp("serp", f"google: {query}", serp_body)

    async def page(url, **k):
        if url not in by_url:
            raise BrightDataError("unlocker", url, "HTTP 404", 404)
        return resp("unlocker", url, by_url[url])

    monkeypatch.setattr(brightdata, "serp", serp)
    monkeypatch.setattr(brightdata, "page", page)
    monkeypatch.setattr(brightdata, "document", page)
    return calls


async def test_crawl_pipeline_end_to_end_offline(monkeypatch, tmp_path):
    calls = replay_brightdata(monkeypatch, "sutter-cpmc")
    seen = []

    async def fake_claude(system, prompt, **k):
        assert "HOSPITAL: California Pacific Medical Center" in prompt and "=== DOCUMENT 2" in prompt
        return {"tiers": [{"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "insured", "min_oop_pct_income": 10,
                           "quote": SUTTER_HIGH_COST},
                          {"max_fpl_pct": 500, "discount_pct": 100, "quote": "Everyone under 500% FPL is free."}]}
    db = PolicyStore(tmp_path)
    row = await pipeline.crawl_hospital(db, SEED["sutter-cpmc"], on_event=lambda k, **e: seen.append((k, e)),
                                        json_out=fake_claude)
    assert row["content_sha256"] == current("sutter-cpmc")["content_sha256"]  # same bytes -> same hash
    assert row["tiers_verified"] == 1 and row["tiers_rejected"] == 1 and row["status"] == "ok"
    assert calls[0].startswith("google:") and len(calls) == 4  # 1 SERP + page + 2 PDFs (HCAI is not fetched)
    products = [l["product"] for l in db.crawl_log("sutter-cpmc") if l["product"]]
    assert products == ["serp", "unlocker", "unlocker", "unlocker"]
    assert any("REJECTED" in l["note"] for l in db.crawl_log("sutter-cpmc"))
    assert {k for k, _ in seen} == {"crawl_stage", "brightdata_request", "brightdata"}
    done = [e for k, e in seen if k == "brightdata"]
    assert [e["n"] for e in done] == [1, 2, 3, 4] and {e["outcome"] for e in done} == {"found"}
    assert all(e["cap"] == 10 for e in done)
    assert {e["stage"] for k, e in seen if k == "crawl_stage"} >= {"discover", "fetch", "extract", "verify", "store"}


# ---- the policy as filed with HCAI ----

def hcai_item(date: str, url: str, label: str) -> str:
    return (f'<li><span class="date-label"><strong>Effective Date:</strong></span>\n<span class="date">\n {date} </span>'
            f'<span class="separator">&bull;</span><span class="link"><a href="{url}">\n {label} </a></span></li>')


ATT = "https://api.hdc.hcai.ca.gov/Public/Extract/Attachment?id="
HCAI_PAGE = (
    '<h2 class="policy-section__title">\n Charity Care Policy </h2><div class="policy-section__policies"><ul>'
    + hcai_item("01/31/2025", ATT + "0ld", "Charity Care Policy") + hcai_item("03/12/2025", ATT + "aaa1", "Charity Care Policy")
    + '</ul><div class="policy-section__application"><ul>' + hcai_item("06/01/2026", ATT + "app", "Application")
    + '</ul></div></div><h2 class="policy-section__title">Discount Payment Policy</h2><ul>'
    + hcai_item("03/12/2025", ATT + "aaa1", "Discount Payment Policy") + hcai_item("12/01/2024", ATT + "bbb2", "x")
    + '</ul><h2 class="policy-section__title">Debt Collection Policy</h2><ul>' + hcai_item("09/09/2026", ATT + "debt", "x")
    + "</ul>")


def test_hcai_page_gives_newest_policy_per_section_not_forms():
    from billback.policies import hcai
    got = hcai.filed_policies(HCAI_PAGE)
    # the newest charity care filing (not the older one, not the application form); the discount section's newest
    # is the same PDF, listed once; debt collection is not a discount policy
    assert got == [{"section": "Charity Care Policy", "effective": "2025-03-12", "url": ATT + "aaa1"}]


async def test_crawl_falls_back_to_hcai_when_site_links_no_policy_pdf(monkeypatch, tmp_path):
    """ZSFG: Google + Web Unlocker give only web pages (committed responses); the policy then comes from HCAI
    over plain HTTPS, logged without a Bright Data product, and goes to extraction as a state_filed PDF."""
    from billback.policies import hcai
    calls = replay_brightdata(monkeypatch, "zsfg")
    pdf = (DB.root / "raw/chinese-hospital/pdf-6bde4c69e798.pdf").read_bytes()
    plain = []

    async def fake_get(crawl, url, timeout=60):
        plain.append(url)
        crawl.store.log(run_id=crawl.run_id, hospital_id=crawl.hospital_id, stage="fetch", product=None, url=url,
                        status=200, nbytes=1, latency_ms=1, ok=True, note="plain HTTPS (not Bright Data)")
        return HCAI_PAGE.encode() if "hcai.ca.gov/affordability" in url else pdf
    monkeypatch.setattr(hcai, "_get", fake_get)
    seen = []

    async def fake_claude(system, prompt, **k):
        seen.append(prompt)
        return {"tiers": []}
    db = PolicyStore(tmp_path)
    row = await pipeline.crawl_hospital(db, SEED["zsfg"], json_out=fake_claude)
    assert plain == [hcai.page_url(SEED["zsfg"].hcai), ATT + "aaa1"]
    assert [d["role"] for d in row["documents"]][-1] == "state_filed" and row["documents"][-1]["via"] == "https"
    assert row["discovery"]["hcai_slug"] == SEED["zsfg"].hcai and "policy as filed with California HCAI" in seen[0]
    assert all(c.startswith(("google:", "https://www.zuckerberg")) for c in calls)  # Bright Data never sent to .gov
    assert any(l["note"] == "plain HTTPS (not Bright Data)" and l["product"] is None for l in db.crawl_log("zsfg"))


def replay_hcai(monkeypatch, hid: str) -> list[str]:
    """Serve the committed HCAI lookup page and filed PDF(s) for this hospital in place of plain HTTPS."""
    from billback.policies import hcai
    filed = {d["url"]: (DB.root / next(a for a in d["artifacts"] if a.endswith(".pdf"))).read_bytes()
             for d in current(hid)["documents"] if d["role"] == "state_filed"}
    page = next((DB.raw / hid).glob("hcai-page-*.html")).read_bytes()
    plain = []

    async def fake_get(crawl, url, timeout=60):
        plain.append(url)
        return page if url == hcai.page_url(SEED[hid].hcai) else filed[url]
    monkeypatch.setattr(hcai, "_get", fake_get)
    return plain


async def test_recheck_keeps_the_hcai_copy_even_when_the_site_links_a_pdf(monkeypatch, tmp_path):
    """Saint Francis's site links a PDF (a Spanish application form), so "only when no policy document" would
    drop the HCAI copy on recheck, hash the policy as changed and re-extract it without its tier."""
    shutil.copytree(DEFAULT_DIR, tmp_path / "db")
    db = PolicyStore(tmp_path / "db")
    calls = replay_brightdata(monkeypatch, "ucsf-saint-francis")
    plain = replay_hcai(monkeypatch, "ucsf-saint-francis")

    async def no_claude(system, prompt, **k):
        raise AssertionError("an unchanged policy must not be re-extracted")
    out = await monitor.recheck(db, "ucsf-saint-francis", json_out=no_claude)
    assert out["changed"] is False and len(plain) == 2
    assert not any("hcai.ca.gov" in c for c in calls)  # Bright Data never sent to .gov
    assert db.current("ucsf-saint-francis")["status"] == "ok"


# ---- monitor ----

async def test_recheck_detects_change_only_when_content_changes(monkeypatch, tmp_path):
    shutil.copytree(DEFAULT_DIR, tmp_path / "db")
    db = PolicyStore(tmp_path / "db")
    replay_brightdata(monkeypatch, "sutter-cpmc")
    claude_calls = []

    async def fake_claude(system, prompt, **k):
        claude_calls.append(prompt)
        return {"tiers": [{"max_fpl_pct": 300, "discount_pct": 100, "applies_to": "insured", "min_oop_pct_income": 10,
                           "quote": SUTTER_HIGH_COST.replace("400%", "300%")}]}

    same = await monitor.recheck(db, "sutter-cpmc", json_out=fake_claude)
    assert same["changed"] is False and claude_calls == []  # unchanged: no re-extraction
    assert db.checks("sutter-cpmc")[0]["note"] == "unchanged"

    real_markdown_text = fetch.markdown_text
    monkeypatch.setattr(fetch, "markdown_text", lambda md: real_markdown_text(md).replace(
        "earning 400% or less of the FPIG and with high", "earning 300% or less of the FPIG and with high"))
    changed = await monitor.recheck(db, "sutter-cpmc", json_out=fake_claude)
    assert changed["changed"] is True and len(claude_calls) == 1
    assert changed["version"] == current("sutter-cpmc")["version"] + 1
    assert "added: insured <= 300% FPL" in changed["diff"] and "removed: insured <= 400% FPL" in changed["diff"]
    assert db.checks("sutter-cpmc")[0]["note"].startswith("policy changed")
    assert DB.current("sutter-cpmc")["version"] == current("sutter-cpmc")["version"]  # committed DB untouched


# ---- compare ----

def test_same_bill_different_hospitals():
    rosa = {r["hospital_id"]: r for r in compare.compare(DB, "default")["results"]}
    # Rosa: insured, 228% FPL, her $1,100 share is 3% of income. CPMC and UCSF write off insured patients'
    # shares only above 10% of income, Chinese Hospital's free care is self-pay only, ZSFG has no tier: $600 everywhere
    assert rosa["sutter-cpmc"]["balance"] == 600 and rosa["sutter-cpmc"]["refund"] == 0
    assert rosa["sutter-cpmc"]["fpl_pct"] == pytest.approx(228.1, abs=0.1)
    # her $1,100 share + $93 other medical bills in her bank (urgent-care copay, a prescription; not the double charge)
    assert rosa["sutter-cpmc"]["oop_pct_income"] == pytest.approx(3.3, abs=0.1)
    assert "not above 10%" in rosa["sutter-cpmc"]["why_not"]
    assert rosa["chinese-hospital"]["balance"] == 600 and rosa["chinese-hospital"]["discount_pct"] == 0
    assert rosa["zsfg"]["balance"] == 600  # ZSFG publishes no fixed-percent tier on its own site
    marcus = {r["hospital_id"]: r for r in compare.compare(DB, "freecare")["results"]}
    assert marcus["ucsf-health"]["balance"] == 0 and marcus["ucsf-health"]["refund"] == 0
    assert marcus["sutter-cpmc"]["balance"] == 0 and marcus["sutter-cpmc"]["quote"] and marcus["sutter-cpmc"]["quote_source"]
    assert "if out-of-pocket > 10% of income" in marcus["sutter-cpmc"]["rule"]
    assert marcus["chinese-hospital"]["balance"] == 5600 and "uninsured" in marcus["chinese-hospital"]["why_not"]
    assert marcus["zsfg"]["balance"] == 5600


def test_evaluate_math_matches_finance():
    facts = {"annual_income": 30000, "household_size": 2, "insured": False, "patient_responsibility": 1000, "paid": 100}
    policy = {"tiers": [{"max_fpl_pct": 200.0, "discount_pct": 100.0, "applies_to": "all", "min_oop_pct_income": None, "quote": "a"},
                        {"max_fpl_pct": 400.0, "discount_pct": 50.0, "applies_to": "all", "min_oop_pct_income": None, "quote": "b"}]}
    r = compare.evaluate(facts, policy)  # 30000 / 21150 = 141.8% FPL -> free -> the 100 paid comes back
    assert (r["discount_pct"], r["balance"], r["refund"]) == (100.0, 0.0, 100.0)
    facts["annual_income"] = 60000  # 283.7% FPL -> 50% of 1000 = 500 - 100 paid
    r = compare.evaluate(facts, policy)
    assert (r["discount_pct"], r["balance"], r["refund"], r["quote"]) == (50.0, 400.0, 0.0, "b")


# ---- registry + client ----

@pytest.mark.parametrize("name,hid", [("California Pacific Medical Center", "sutter-cpmc"), ("Sutter CPMC", "sutter-cpmc"),
                                      ("UCSF Medical Center", "ucsf-health"), ("ZSFG", "zsfg"),
                                      ("Priscilla Chan and Mark Zuckerberg San Francisco General Hospital", "zsfg"),
                                      ("Nowhere General Hospital", None), ("", None)])
def test_registry_match(name, hid):
    h = registry.match(name, DB.hospitals())
    assert (h.id if h else None) == hid


async def test_serp_inner_status_is_an_error(monkeypatch):
    async def fake_request(product, url, **k):
        body = json.dumps({"status_code": 502, "headers": {}, "body": "redirect location was rejected"}).encode()
        return BDResponse(product, url, 200, body, "application/json", 3)
    monkeypatch.setattr(brightdata, "request", fake_request)
    monkeypatch.setattr(brightdata.asyncio, "sleep", lambda s: _noop())
    with pytest.raises(BrightDataError) as e:
        await brightdata.serp("x")
    assert e.value.status == 502


async def _noop():
    return None


def test_brightdata_not_configured_is_a_clear_error():
    assert not brightdata.live() and config.BRIGHTDATA_API_KEY == ""
    with pytest.raises(BrightDataError, match="not configured"):
        brightdata._zone(brightdata.UNLOCKER)


def test_policydb_is_tracked_not_ignored():
    ignored = (Path(config.ROOT) / ".gitignore").read_text().split()
    assert "policydb/" not in ignored and "policydb" not in ignored

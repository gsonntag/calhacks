"""Price-table tests, offline: small fixtures in the three CMS template layouts, the cms-hpt.txt index parser,
the truncation check, and the committed tables in policydb/prices/."""
import json
from pathlib import Path

import pytest

from billback import price_sources, prices
from billback.policies import discover, registry

FIX = Path(__file__).parent / "fixtures" / "prices"


def build(name: str, tmp_path: Path) -> dict:
    t = prices.build_table("test-h", FIX / name, {"file_url": "https://example.org/x", "sha256": "abc"})
    (tmp_path / "test-h.json").write_text(json.dumps(t))
    prices.table.cache_clear()
    return t


def test_tall_csv_merges_charge_line_and_rate_line(tmp_path):
    t = build("tall.csv", tmp_path)
    assert (t["layout"], t["template_version"], t["last_updated_on"]) == ("csv-tall", "3.0.0", "2026-04-01")
    assert t["location_name"] == "Test Medical Center- Van Ness Campus"
    ct = t["prices"]["74177"]
    # gross from the chargemaster line, payer rates from the separate HCPCS line (as in Sutter's file)
    assert ct["gross"] == 8287 and ct["description"] == "HCHG CT ABD & PELVIS W/CON"
    assert ct["rates_line"] == "Ct abd & pelv w/contrast"
    assert (ct["negotiated_min"], ct["negotiated_max"]) == (4717, 6302)
    assert ct["min_max_source"].startswith("file")
    fams = {p["family"]: p["dollar"] for p in ct["payers"]}
    assert fams == {"Aetna": 4717, "Blue Shield of California": 6302, "Medi-Cal": 610}
    assert any("lists them separately" in n for n in ct["notes"])


def test_tall_csv_prefers_outpatient_line_without_modifiers(tmp_path):
    ed = build("tall.csv", tmp_path)["prices"]["99284"]
    assert ed["setting"] == "outpatient" and "modifiers" not in ed and ed["gross"] == 5214
    assert ed["line_items"] == 3 and "payers" not in ed
    assert any("equals the gross" in n for n in ed["notes"])


def test_only_cpt_hcpcs_typed_codes_match(tmp_path):
    t = build("tall.csv", tmp_path)
    assert "99285" not in t["prices"]          # '99285' in a CDM column is not the CPT code
    assert set(t["prices"]) == {"99284", "74177", "J2405"}
    assert "99285" in t["missing_codes"]


def test_drug_units_are_noted(tmp_path):
    j = build("tall.csv", tmp_path)["prices"]["J2405"]
    assert j["drug_unit"] == 2 and j["drug_unit_type"] == "ML"
    assert any("compare billed units" in n for n in j["notes"])


def test_wide_csv(tmp_path):
    t = build("wide.csv", tmp_path)
    assert t["layout"] == "csv-wide" and t["hospital_name"] == "Wide Test Hospital"
    ed = t["prices"]["99285"]
    assert ed["gross"] == 7410 and ed["discounted_cash"] == 4446
    assert {p["payer"]: (p.get("dollar"), p.get("percentage")) for p in ed["payers"]} == {
        "Aetna": (3705, None), "Blue Shield": (None, 62.5)}
    assert ed["payers"][0]["median"] == 3500
    cbc = t["prices"]["85025"]
    assert cbc["payers"][0]["family"] == "Aetna" and cbc["payers"][0]["algorithm"] == "Percent of Medicare 100"
    # no min/max columns filled -> computed from the dollar rates listed
    assert cbc["negotiated_min"] == cbc["negotiated_max"] == 46.59 and cbc["min_max_source"].startswith("computed")


def test_json_v3(tmp_path):
    t = build("v3.json", tmp_path)
    assert t["layout"] == "json" and t["location_name"] == "JSON Test Hospital | JSON Test Hospital Annex"
    assert set(t["prices"]) == {"74177", "J2405"}
    ct = t["prices"]["74177"]
    assert ct["setting"] == "outpatient" and ct["gross"] == 9200 and ct["discounted_cash"] == 4600
    assert (ct["negotiated_min"], ct["negotiated_max"]) == (900, 4800) and ct["payer_rates"] == 4
    anthem = next(p for p in ct["payers"] if p["family"] == "Anthem Blue Cross")
    assert anthem["plan"] == "PPO" and "dollar" not in anthem   # commercial plan preferred over Medicare Advantage
    assert next(p for p in ct["payers"] if p["family"] == "UnitedHealthcare")["percentage"] == 52
    j = t["prices"]["J2405"]
    assert (j["drug_unit"], j["drug_unit_type"], j["setting"]) == (1, "ME", "both")


def test_lookup_load_hospitals(tmp_path):
    build("tall.csv", tmp_path)
    root = str(tmp_path)
    assert prices.hospitals(root) == ["test-h"]
    assert prices.lookup("test-h", "74177", root)["gross"] == 8287
    assert prices.lookup("test-h", " j2405 ", root)["code"] == "J2405"
    assert prices.lookup("test-h", "27447", root) is None
    assert prices.lookup("no-such-hospital", "74177", root) is None
    assert set(prices.load("test-h", root)) == {"99284", "74177", "J2405"}


def test_parse_hpt_utf16_with_campuses():
    text = ("location-name: A CAMPUS\r\nsource-page-url: https://h.org/p\r\nmrf-url: https://h.org/a.csv\r\n\r\n"
            "location-name: B CAMPUS - HAWTHORNE\r\nmrf-url: https://h.org/b.csv\r\n\r\n"
            "location-name: B CAMPUS\r\nmrf-url: https://h.org/c.csv\r\ncontact-email: x@h.org\r\n")
    raw = text.encode("utf-16")      # BOM + UTF-16, like Sutter's index
    entries = price_sources.parse_hpt(prices.decode(raw))
    assert [e["mrf-url"] for e in entries] == ["https://h.org/a.csv", "https://h.org/b.csv", "https://h.org/c.csv"]
    assert price_sources.choose(entries, r"B CAMPUS$")["mrf-url"] == "https://h.org/c.csv"
    with pytest.raises(LookupError):
        price_sources.choose(entries, "Nowhere")


def test_truncation_check():
    assert price_sources.truncated(b"hospital_name,x\nrow,1\n") is None
    assert "newline" in price_sources.truncated(b"hospital_name,x\nrow,1,Reimbursement Rate ")
    assert price_sources.truncated(b'{"a": [1, 2]}\n') is None
    assert price_sources.truncated(b'{"a": [1, 2') is not None
    assert price_sources.truncated(b"PK\x03\x04garbage") is not None
    assert price_sources.mrf_ok(b"hospital_name,x\nrow,1\n") and not price_sources.mrf_ok(b"<html>blocked</html>\n")


def test_sources_cover_registry():
    assert set(price_sources.SOURCES) == {h.id for h in registry.SEED}


def test_discover_demotes_translated_documents_and_flyers():
    h = next(h for h in registry.SEED if h.id == "john-muir-wc")
    res = [{"link": "https://www.johnmuirhealth.com/docs/English_JMH_Financial_Assistance_Program_Flyer.pdf",
            "title": "Financial Assistance Program", "rank": 1},
           {"link": "https://www.johnmuirhealth.com/docs/Patient-Financial-Assistance-Policy.pdf",
            "title": "Patient Financial Assistance Policy", "rank": 3},
           {"link": "https://www.johnmuirhealth.com/docs/financial-assistance-policy-spanish.pdf",
            "title": "Financial Assistance Policy", "rank": 2}]
    ranked = discover.rank(res, h)
    assert ranked[0].url.endswith("Patient-Financial-Assistance-Policy.pdf")
    assert ranked[-1].url.endswith("spanish.pdf")


@pytest.mark.parametrize("hid", prices.hospitals())
def test_committed_tables(hid):
    t = prices.table(hid)
    path = prices.PRICES_DIR / f"{hid}.json"
    assert path.stat().st_size < 200 * 1024
    assert t["hospital_id"] == hid and t["source"]["file_url"].startswith("http")
    assert len(t["source"]["sha256"]) == 64 and t["source"]["fetched_at"] and t["template_version"]
    assert t["codes_found"] == len(t["prices"]) > 0
    for code, e in t["prices"].items():
        assert code in prices.CODE_SET and e["code"] == code

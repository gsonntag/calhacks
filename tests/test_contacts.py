"""Who-to-call contacts, offline: extraction on the committed crawl artifacts, the verification that keeps
invented numbers out, the committed policydb/contacts data, and for_case precedence (bill first)."""
import importlib.util
import json

import pytest

from remy import config, contacts, llm
from remy.policies import registry
from remy.policies.fetch import markdown_text
from remy.policies.store import DEFAULT_DIR

spec = importlib.util.spec_from_file_location("build_contacts", config.ROOT / "scripts" / "build_contacts.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

UCSF_PAGE = "raw/ucsf-health/page-9cfaeac215d7.md"


def source_text(rel: str) -> str:
    raw = (DEFAULT_DIR / rel).read_text(errors="replace")
    return markdown_text(raw) if rel.endswith(".md") else raw


# ---- regex extraction ----

def test_candidates_on_crawled_page_find_billing_and_skip_fax():
    cands = contacts.candidates(source_text(UCSF_PAGE))
    phones = {c["phone"] for c in cands}
    assert {"866-433-4035", "415-353-3333", "415-353-1966"} <= phones
    assert "415-353-3914" not in phones  # "Fax: 415-353-3914"
    counsel = next(c for c in cands if c["phone"] == "415-353-1966")
    assert counsel["hours"] == "M-F, 9 a.m. - 5 p.m." and "Financial Counseling" in counsel["context"]


def test_candidates_skip_fax_and_unrelated_numbers():
    filler = " Parking is in the garage on Main Street." * 12
    text = ("For appointments call 650-555-0101." + filler + " Fax your completed financial assistance "
            "application to 1-800-687-9901. Questions about your bill? Call Patient Financial Services at "
            "(415) 555-0123, Monday - Friday, 8 a.m. - 5 p.m.")
    cands = contacts.candidates(text)
    assert [c["phone"] for c in cands] == ["(415) 555-0123"]
    assert cands[0]["hours"] == "Monday - Friday, 8 a.m. - 5 p.m."


def test_hours_stay_with_their_own_number():
    jmh = "(925) 947-3336, excluding holidays. Contact a Financial Counselor Monday - Friday, 8:00 a.m. - 4:30 p.m."
    assert contacts.hours_near(jmh.split("(925) 947-3336", 1)[1]) is None
    zsfg = source_text("raw/zsfg/page-3dd45ec4b556.md")
    after = zsfg.split("628-206-3275", 1)[1][:300]
    assert contacts.hours_near(after).startswith("Monday: 8:00 am - 4:30 pm Tuesday")


# ---- verification ----

TEXT = "Questions? Call Patient Financial Services at 855-398-1633, 8:00 am to 5:00 pm, Monday through Friday."


@pytest.mark.parametrize("item,problem", [
    ({"department": "billing", "phone": "855-398-1633", "quote": "Call Patient Financial Services at 855-398-1633",
      "hours": "8:00 am to 5:00 pm, Monday through Friday"}, ""),
    ({"department": "billing", "phone": "855-398-1634", "quote": "Call Patient Financial Services at 855-398-1634"},
     "phone number not verbatim in the source"),
    ({"department": "billing", "phone": "855-398-1633", "quote": "Call the billing office at 855-398-1633"},
     "quote not found in the source"),
    ({"department": "billing", "phone": "855-398-1633", "quote": "Call Patient Financial Services at"},
     "quote does not contain the phone number"),
    ({"department": "billing", "phone": "855-398-1633", "quote": "Call Patient Financial Services at 855-398-1633",
      "hours": "24/7"}, "hours not found in the source"),
    ({"department": "pharmacy", "phone": "855-398-1633", "quote": "Call Patient Financial Services at 855-398-1633"},
     "unknown department 'pharmacy'"),
])
def test_verify(item, problem):
    assert contacts.verify(item, TEXT) == problem


async def test_build_keeps_only_verified_classifications(monkeypatch, tmp_path):
    """The classifier can only label regex-found numbers; a made-up quote is rejected, not kept."""
    src = {"url": "https://example.org/billing", "artifact": "raw/x/page.md", "text": TEXT +
           " Financial counselors: 415-555-0199."}
    monkeypatch.setattr(build, "sources", lambda hid: [src])
    monkeypatch.setattr(build, "OUT", tmp_path)

    async def fake_json_out(system, prompt, **kw):
        assert "855-398-1633" in prompt and "415-555-0199" in prompt
        return {"items": [
            {"id": 1, "department": "billing", "label": "Patient Financial Services",
             "quote": "Call Patient Financial Services at 855-398-1633", "hours": ""},
            {"id": 2, "department": "financial_assistance", "label": "Financial counselors",
             "quote": "Call our financial counselors any time at 415-555-0199"}]}  # not in the text
    monkeypatch.setattr(llm, "json_out", fake_json_out)
    data = await build.build_one("sutter-cpmc")
    assert [(c["department"], c["phone"]) for c in data["contacts"]] == [("billing", "855-398-1633")]
    assert data["contacts"][0]["hours"] == "8:00 am to 5:00 pm, Monday through Friday"  # regex fallback
    assert data["rejected"][0]["reason"] == "quote not found in the source"
    assert [g["department"] for g in data["gaps"]] == ["financial_assistance"]
    assert data["gaps"][0]["phone"] is None and data["gaps"][0]["why"]
    assert json.loads((tmp_path / "sutter-cpmc.json").read_text())["contacts"] == data["contacts"]


# ---- the committed data ----

@pytest.mark.parametrize("hid", [h.id for h in registry.SEED])
def test_committed_contacts_are_verbatim_in_their_sources(hid):
    data = contacts.load(hid)
    assert data, f"policydb/contacts/{hid}.json missing: run scripts/build_contacts.py build {hid}"
    for c in data["contacts"]:
        assert contacts.verify(c, source_text(c["artifact"])) == "", c
        assert contacts.dial(c["phone"])
    entries = contacts.for_hospital(hid)
    for dept in contacts.REQUIRED:  # a number, or an explicit gap with a reason
        rows = [e for e in entries if e["department"] == dept]
        assert rows and all(e["phone"] or e["why"] for e in rows)


def test_committed_contacts_spot_checks():
    first = {hid: contacts.for_hospital(hid)[0] for hid in ("sutter-cpmc", "zsfg", "stanford-health")}
    assert first["sutter-cpmc"]["phone"] == "855-398-1633" and first["sutter-cpmc"]["department"] == "billing"
    assert first["zsfg"]["phone"] == "628-206-8448"
    assert first["stanford-health"]["phone"] == "1-800-549-3720"
    kaiser = {e["department"]: e for e in contacts.for_hospital("kaiser-oakland")}
    assert kaiser["billing"]["phone"] is None and "number printed on their bill" in kaiser["billing"]["why"]
    sf = contacts.for_hospital("ucsf-saint-francis")
    assert sf[0]["via"]["hospital_id"] == "ucsf-health" and "October 4, 2025" in sf[0]["label"]


def test_bright_data_requests_for_contacts_stay_under_cap():
    log = json.loads((contacts.CONTACTS_DIR / "fetched.json").read_text())
    assert len(log["requests"]) <= build.BD_CAP
    for d in log["documents"]:
        assert (DEFAULT_DIR / d["artifact"]).exists()


# ---- for_case precedence ----

def case(bill: dict, hospital: dict | None = None) -> dict:
    return {"hospital": hospital or {"name": "California Pacific Medical Center",
                                     "billing_phone_label": "Patient Financial Services"},
            "bill": {"provider": "California Pacific Medical Center", **bill}}


def test_for_case_prefers_number_printed_on_the_bill():
    out = contacts.for_case(case({"billing_phone": "(415) 600-1234"}))
    assert out[0] == {"department": "billing", "label": "Patient Financial Services", "phone": "(415) 600-1234",
                      "hours": None, "source_url": "bill", "quote": None}
    assert "855-398-1633" in [c["phone"] for c in out[1:]]  # hospital's own numbers follow


def test_for_case_reads_questions_line_in_notice():
    out = contacts.for_case(case({"notice": "Questions about this statement? Call 1-800-555-0142 today."}))
    assert out[0]["phone"] == "1-800-555-0142" and out[0]["source_url"] == "bill"


def test_for_case_falls_back_to_hospital_and_dedupes():
    out = contacts.for_case(case({}))
    assert out == contacts.for_hospital("sutter-cpmc")
    same = contacts.for_case(case({"billing_phone": "855-398-1633"}))
    assert [c["phone"] for c in same].count("855-398-1633") == 1 and same[0]["source_url"] == "bill"


def test_for_case_bill_number_fills_hospital_gap():
    k = {"name": "Kaiser Permanente Oakland Medical Center"}
    no_phone = contacts.for_case({"hospital": k, "bill": {"provider": k["name"]}})
    assert any(c["department"] == "billing" and c["phone"] is None for c in no_phone)
    with_phone = contacts.for_case({"hospital": k, "bill": {"provider": k["name"], "billing_phone": "1-800-555-0177"}})
    billing = [c for c in with_phone if c["department"] == "billing"]
    assert [c["phone"] for c in billing] == ["1-800-555-0177"]


def test_for_case_unknown_hospital_says_so():
    out = contacts.for_case({"hospital": {"name": "Nowhere General"}, "bill": {"provider": "Nowhere General"}})
    assert out == [{"department": "billing", "label": contacts.LABELS["billing"], "phone": None, "hours": None,
                    "source_url": None, "quote": None, "why": "hospital not in the policy database"}]


def test_demo_scenarios_get_a_billing_line_or_reason():
    for path in sorted((config.ROOT / "demo" / "scenarios").glob("*/case.json")):
        out = contacts.for_case(json.loads(path.read_text()))
        assert out and out[0]["department"] in contacts.DEPARTMENTS
        assert all(c["phone"] or c.get("why") for c in out), path

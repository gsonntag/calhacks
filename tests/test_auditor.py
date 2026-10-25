"""The Nessie agent's checks on the patient's own records (billcheck.py + finance.reconcile): which bank payments
went to this hospital, which the bill already credits, and self-pay bills with no insurance statement."""
import asyncio
import json

import pytest

from remy import billcheck, config, finance
from remy.agents import auditor, policy
from remy.events import bus
from remy.integrations.nessie import LocalMirror
from remy.orchestrator import demo_case

CPMC = {"provider": "California Pacific Medical Center", "account_number": "CPMC-48213977",
        "service_date": "2026-08-02", "statement_date": "2026-10-10", "total_charges": 8700.0, "amount_due": 8700.0}
EOB = {"patient_responsibility": 1100.0, "claim_number": "C-1"}


def txn(i, merchant, amount=500.0, date="2026-09-14", description="", type_="purchase"):
    return {"id": f"t{i}", "type": type_, "date": date, "amount": amount, "merchant": merchant,
            "description": description}


# ---- bug 1: loose payment matching -----------------------------------------------------------------------

NEAR_MISSES = [
    "Pacific Medical Group",               # another clinic sharing two words
    "PG&E Pacific Gas & Electric",         # utility sharing "Pacific"
    "Golden Gate Medical Center",          # another hospital sharing "Medical Center"
    "California Physicians Service",       # shares "California"
    "Medical Center Pharmacy",
    "Pacific Heights Dental",
    "CPMCX Storage",                       # alias only as part of a longer word
]


@pytest.mark.parametrize("merchant", NEAR_MISSES)
def test_near_miss_merchants_are_not_hospital_payments(merchant):
    rec = finance.reconcile(CPMC, EOB, [txn(1, merchant)])
    assert rec["payments"] == [] and rec["paid"] == 0 and rec["owed"] == 1100


@pytest.mark.parametrize("merchant,description", [
    ("California Pacific Medical Center", "Payment"),
    ("CPMC PATIENT PAY", ""),                          # registry alias, Rosa-style bank descriptor
    ("SUTTER CPMC*PATIENT PAY", "Online payment"),
    ("CPMC-VAN NESS", ""),                             # alias with punctuation
    ("Online Bill Pay", "Payment acct CPMC-48213977"),  # unrelated merchant, but the bill's account number
    ("Online Bill Pay", "ACCT CPMC48213977"),
])
def test_real_hospital_payments_still_match(merchant, description):
    rec = finance.reconcile(CPMC, EOB, [txn(1, merchant, description=description)])
    assert [p["id"] for p in rec["payments"]] == ["t1"] and rec["owed"] == 600


def test_payments_before_the_service_date_do_not_count():
    txns = [txn(1, "CPMC PATIENT PAY", 200.0, date="2026-07-01"),   # before the visit: an older bill
            txn(2, "CPMC PATIENT PAY", 300.0, date="2026-08-02")]   # same day as the visit counts
    rec = finance.reconcile(CPMC, EOB, txns)
    assert [p["id"] for p in rec["payments"]] == ["t2"] and rec["paid"] == 300 and rec["owed"] == 800


def test_eob_service_date_used_when_bill_has_none():
    bill = {k: v for k, v in CPMC.items() if k != "service_date"}
    rec = finance.reconcile(bill, {**EOB, "service_date": "2026-08-02"}, [txn(1, "CPMC", date="2026-07-30")])
    assert rec["payments"] == []


def test_deposits_from_the_hospital_are_not_payments():
    rec = finance.reconcile(CPMC, EOB, [txn(1, "CPMC", type_="deposit", description="CPMC refund")])
    assert rec["payments"] == []


def test_unregistered_hospital_needs_its_full_name():
    bill = {**CPMC, "provider": "Bayview Pacific Medical Center", "account_number": "BV-1"}
    assert billcheck.payee_names(bill["provider"]) == ["bayview pacific medical center"]
    txns = [txn(1, "Pacific Medical Group"), txn(2, "Bayview Pacific Medical Center", 250.0)]
    assert [p["id"] for p in finance.reconcile(bill, EOB, txns)["payments"]] == ["t2"]


def test_demo_outcomes_unchanged():
    rosa, marcus = demo_case(), demo_case("freecare")
    for c in (rosa, marcus):
        c["transactions"] = [{"id": f"t{i}", "merchant": "", **t} for i, t in enumerate(c["transactions"])]
    r = finance.reconcile(rosa["bill"], rosa["eob"], rosa["transactions"])
    assert (r["paid"], r["owed"], [p["amount"] for p in r["payments"]]) == (500, 600, [500])
    assert [ln["amount"] for ln in r["lines"]] == [8700, -7600, -500, 600]
    m = finance.reconcile(marcus["bill"], marcus["eob"], marcus["transactions"])
    assert (m["paid"], m["owed"]) == (0, 5600)


# ---- bug 2: uninsured (no EOB) ----------------------------------------------------------------------------

def test_self_pay_responsibility_is_the_billed_amount():
    bill = {**CPMC, "total_charges": 5000.0, "amount_due": 5000.0}
    for eob in ({}, None):
        rec = finance.reconcile(bill, eob, [txn(1, "CPMC PATIENT PAY", 300.0)])
        assert rec["insured"] is False and rec["patient_responsibility"] == 5000 and rec["owed"] == 4700
        assert [ln["label"] for ln in rec["lines"]] == ["Hospital says you owe", "Payment already made 2026-09-14",
                                                        "Actually owed"]


def test_self_pay_line_corrections_on_the_kaiser_bill():
    case = json.loads((config.DEMO_DIR / "scenarios" / "amara-kaiser-uninsured" / "case.json").read_text())
    fixes = billcheck.line_corrections(case["bill"], case["hospital"]["name"])
    assert [(f["kind"], f["line"], f["code"], f["amount"]) for f in fixes] == [
        ("price_above_posted", 2, "73610", 270.0), ("duplicate_line", 4, "73630", 1010.0)]
    assert fixes[0]["posted"] == 1080.0 and "price file" in fixes[0]["source"]
    rec = finance.reconcile(case["bill"], {}, [], corrections=fixes)
    assert rec["patient_responsibility"] == 5398.57
    assert sum(ln["amount"] for ln in rec["lines"][:-1]) == pytest.approx(rec["owed"])
    # corrections never apply on top of an EOB (the insurer's share already reflects the real charges)
    assert finance.reconcile(case["bill"], EOB, [], corrections=fixes)["patient_responsibility"] == 1100


def test_line_corrections_without_a_price_table():
    bill = {"provider": "Some Small Clinic", "line_items": [{"code": "99283", "amount": 100.0},
                                                            {"code": "99283", "amount": 100.0},
                                                            {"code": "99283", "amount": 90.0}]}
    assert [(f["kind"], f["line"]) for f in billcheck.line_corrections(bill)] == [("duplicate_line", 2)]


@pytest.mark.parametrize("case,want", [
    ({"eob": {}}, False), ({}, False), ({"eob": None}, False), ({"eob": {"insurer": "X"}}, False),
    ({"eob": {"patient_responsibility": 0}}, True), ({"eob": EOB}, True),
    ({"eob": EOB, "insured": False}, False),
])
def test_insured_is_explicit(case, want):
    assert billcheck.insured(case) is want


async def test_policy_agent_treats_no_eob_as_uninsured():
    c = {**demo_case(), "id": "uninsured-policy", "today": "2026-10-24", "eob": {}}
    c["transactions"] = [{"id": f"t{i}", "merchant": "", **t} for i, t in enumerate(c["transactions"])]
    loop = asyncio.get_running_loop()
    txns, owed = loop.create_future(), loop.create_future()
    txns.set_result(c["transactions"])
    owed.set_result({"owed": 8200.0, "responsibility": 8700.0, "paid": 500.0, "insured": False})
    out = await policy.run(c, txns, owed)
    elig = out["eligibility"]
    assert elig["insured"] is False and elig["bill_out_of_pocket"] == 0  # no insurance statement share counted
    # only the bank's other medical bills (the urgent-care copays) count toward out-of-pocket
    assert elig["oop_pct_income"] == round(100 * elig["bank_medical"] / out["income"]["annual"], 1)


# ---- bug 3: payments the bill already credits ---------------------------------------------------------------

def test_credited_payments_are_not_reported_missed():
    bill = {**CPMC, "patient_payments": 500.0, "amount_due": 8200.0}
    rec = finance.reconcile(bill, EOB, [txn(1, "CPMC PATIENT PAY", 500.0)])
    assert rec["paid"] == 500 and rec["owed"] == 600          # still counts toward what's owed
    assert [t["id"] for t in rec["credited_payments"]] == ["t1"] and rec["uncredited_payments"] == []
    assert not any(ln["label"].startswith("Payment") for ln in rec["lines"])
    assert sum(ln["amount"] for ln in rec["lines"][:-1]) == pytest.approx(600)


def test_only_the_uncredited_part_is_reported():
    bill = {**CPMC, "patient_payments": 300.0, "amount_due": 8400.0}
    txns = [txn(1, "CPMC", 250.0, date="2026-08-20"), txn(2, "CPMC", 250.0, date="2026-09-20"),
            txn(3, "CPMC", 100.0, date="2026-10-15")]   # after the statement date: can't be on it
    rec = finance.reconcile(bill, EOB, txns)
    assert [t["id"] for t in rec["credited_payments"]] == ["t1"]
    assert [(t["id"], t["not_on_bill"]) for t in rec["uncredited_payments"]] == [("t2", 200.0), ("t3", 100.0)]
    assert rec["paid"] == 600 and rec["owed"] == 500 and rec["uncredited_paid"] == 300
    assert sum(ln["amount"] for ln in rec["lines"][:-1]) == pytest.approx(500)


def test_itemized_bill_payments_are_matched_by_amount_and_date():
    bill = {**CPMC, "payments": [{"date": "2026-08-21", "amount": 250.0}], "amount_due": 8450.0}
    txns = [txn(1, "CPMC", 250.0, date="2026-08-20"), txn(2, "CPMC", 250.0, date="2026-09-20")]
    rec = finance.reconcile(bill, EOB, txns)
    assert [t["id"] for t in rec["credited_payments"]] == ["t1"]
    assert [t["id"] for t in rec["uncredited_payments"]] == ["t2"] and rec["owed"] == 600


def test_bill_credits_missing_from_the_bank_still_count():
    bill = {**CPMC, "patient_payments": 200.0, "amount_due": 8500.0}   # paid in cash at the desk
    rec = finance.reconcile(bill, EOB, [])
    assert rec["paid"] == 200 and rec["owed"] == 900 and rec["uncredited_payments"] == []


async def test_auditor_on_the_refund_case_reports_no_missed_payments():
    d = config.DEMO_DIR / "scenarios" / "dana-altabates-refund"
    case = {**json.loads((d / "case.json").read_text()), "id": "dana-auditor", "today": "2026-10-25"}
    loop = asyncio.get_running_loop()
    txns, owed = loop.create_future(), loop.create_future()
    out = await auditor.run(case, LocalMirror(case), "mirror", "Offline bank (mirror)", txns, owed)
    hist = bus.history(case["id"])
    assert not [e for e in hist if e["kind"] == "finding" and e["key"].startswith("auditor:paid-")]
    assert not [e for e in hist if e["kind"] == "evidence" and e["label"] == "Payment hospital may not see"]
    step = [e for e in hist if e["kind"] == "step" and e["key"] == "auditor:payments"][-1]
    assert step["status"] == "clear" and len(step["proof"]) == 3
    assert (await owed)["paid"] == 750 and out["reconciliation"]["owed"] == 3646.2

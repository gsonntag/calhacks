from datetime import date

import pytest

from billback import finance
from billback.orchestrator import demo_case

DEMO_TODAY = date(2026, 10, 24)


@pytest.fixture
def demo():
    c = demo_case()
    c["transactions"] = [{"id": f"t{i}", "merchant": "", **t} for i, t in enumerate(c["transactions"])]
    return c


def test_demo_reconciliation(demo):
    rec = finance.reconcile(demo["bill"], demo["eob"], demo["transactions"])
    assert rec["billed_due"] == 8700
    assert rec["patient_responsibility"] == 1100
    assert rec["paid"] == 500
    assert rec["owed"] == 600
    assert rec["overbilled_by"] == 8100
    assert [p["amount"] for p in rec["payments"]] == [500]


def test_demo_duplicate(demo):
    dups = finance.find_duplicates(demo["transactions"])
    assert len(dups) == 1
    assert dups[0]["amount"] == 75
    assert dups[0]["duplicate"]["merchant"] == "Berkeley Urgent Care"


def test_demo_income_fpl_and_discount(demo):
    inc = finance.income(demo["transactions"], today=DEMO_TODAY)
    assert len(inc["deposits"]) == 7
    elig = finance.charity_discount(inc["annual"], demo["patient"]["household_size"],
                                    finance.clean_tiers([{"max_fpl_pct": 200, "discount_pct": 100},
                                                         {"max_fpl_pct": 400, "discount_pct": 75}]))
    assert elig["fpl_pct"] == pytest.approx(210.4, abs=0.1)
    assert elig["discount_pct"] == 75
    assert finance.apply_discount(600, elig["discount_pct"]) == 150


def test_demo_plan(demo):
    inc = finance.income(demo["transactions"], today=DEMO_TODAY)
    plan = finance.payment_plan(150, inc["monthly"])
    assert plan == {"months": 3, "monthly": 50.0, "reason": plan["reason"]}
    assert finance.payment_plan(0, inc["monthly"])["months"] == 0


@pytest.mark.parametrize("tiers", [
    [{"max_fpl_pct": None, "discount_pct": 50}],
    [{"max_fpl_pct": "400%", "discount_pct": "AGB"}],
    [{"discount_pct": 100}],
    [{"max_fpl_pct": 400, "discount_pct": None}],
])
def test_charity_discount_ignores_malformed_tiers(tiers):
    elig = finance.charity_discount(30000, 1, tiers)  # must not raise
    assert elig["discount_pct"] == 0 and elig["tier"] is None


def test_clean_tiers_coerces_and_drops():
    out = finance.clean_tiers([
        {"max_fpl_pct": "200%", "discount_pct": "free", "quote": "q1"},
        {"max_fpl_pct": "up to 400", "discount_pct": "75"},
        {"max_fpl_pct": 500, "discount_pct": 150},          # clamped
        {"max_fpl_pct": None, "discount_pct": 50},           # open-ended: dropped
        {"max_fpl_pct": 600, "discount_pct": "AGB"},         # not a percent: dropped
        {"max_fpl_pct": True, "discount_pct": 10},           # bool is not a number
        "junk",
    ])
    assert out == [
        {"max_fpl_pct": 200.0, "discount_pct": 100.0, "quote": "q1"},
        {"max_fpl_pct": 400.0, "discount_pct": 75.0},
        {"max_fpl_pct": 500.0, "discount_pct": 100.0},
    ]
    assert finance.clean_tiers(None) == []
    # string tiers now match: free care is recognised at 150% FPL
    assert finance.charity_discount(15650 * 1.5, 1, out)["discount_pct"] == 100


def test_reconcile_without_payments(demo):
    txns = [t for t in demo["transactions"] if "Bayview" not in t.get("merchant", "")]
    rec = finance.reconcile(demo["bill"], demo["eob"], txns)
    assert rec["paid"] == 0 and rec["owed"] == 1100

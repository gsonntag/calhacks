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
    assert rec["patient_responsibility"] == 2600
    assert rec["paid"] == 500
    assert rec["owed"] == 2100
    assert rec["overbilled_by"] == 6600
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
    assert inc["annual"] == 1300 * 26  # biweekly pay annualized by frequency, not by the 90-day window
    assert elig["fpl_pct"] == pytest.approx(216.0, abs=0.1)
    assert elig["discount_pct"] == 75
    # the discount applies to her share ($2,600), then her $500 payment counts against it
    assert finance.settle(2600, 500, elig["discount_pct"]) == {"discounted_share": 650.0, "balance": 150.0, "refund": 0.0}


def test_settle_refund_when_overpaid():
    assert finance.settle(1100, 500, 75) == {"discounted_share": 275.0, "balance": 0.0, "refund": 225.0}


def test_high_medical_cost_tier():
    tiers = finance.clean_tiers([
        {"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "uninsured"},
        {"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "insured", "min_oop_pct_income": 10}])
    # insured, out-of-pocket 12% of income -> free care
    assert finance.charity_discount(46332, 3, tiers, insured=True, out_of_pocket=5600)["discount_pct"] == 100
    # insured, out-of-pocket under 10% -> nothing
    assert finance.charity_discount(46332, 3, tiers, insured=True, out_of_pocket=2000)["discount_pct"] == 0
    # uninsured -> free care regardless of out-of-pocket
    assert finance.charity_discount(46332, 3, tiers, insured=False)["discount_pct"] == 100


def test_freecare_scenario():
    c = demo_case("freecare")
    tx = [{"id": f"t{i}", "merchant": "", **t} for i, t in enumerate(c["transactions"])]
    rec = finance.reconcile(c["bill"], c["eob"], tx)
    inc = finance.income(tx, today=DEMO_TODAY)
    from billback.agents.policy import HIGH_COST_TIERS
    elig = finance.charity_discount(inc["annual"], 3, finance.clean_tiers(HIGH_COST_TIERS["tiers"]),
                                    insured=True, out_of_pocket=c["eob"]["patient_responsibility"])
    assert rec["owed"] == 5600 and elig["discount_pct"] == 100
    assert finance.settle(rec["patient_responsibility"], rec["paid"], 100)["balance"] == 0


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
    base = {"applies_to": "all", "min_oop_pct_income": None}
    assert out == [
        {"max_fpl_pct": 200.0, "discount_pct": 100.0, "quote": "q1", **base},
        {"max_fpl_pct": 400.0, "discount_pct": 75.0, **base},
        {"max_fpl_pct": 500.0, "discount_pct": 100.0, **base},
    ]
    assert finance.clean_tiers(None) == []
    # string tiers now match: free care is recognised at 150% FPL
    assert finance.charity_discount(15650 * 1.5, 1, out)["discount_pct"] == 100


def test_reconcile_without_payments(demo):
    txns = [t for t in demo["transactions"] if "Bayview" not in t.get("merchant", "")]
    rec = finance.reconcile(demo["bill"], demo["eob"], txns)
    assert rec["paid"] == 0 and rec["owed"] == 2600

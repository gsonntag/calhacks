from datetime import date

import pytest

from billback import finance
from billback.orchestrator import demo_case
from billback.policies.store import PolicyStore


def stored_tiers(hospital_id: str) -> list[dict]:
    """Verified tiers from the committed policy DB (real Bright Data crawl)."""
    return PolicyStore().current(hospital_id)["extracted"]["tiers"]

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
    assert inc["annual"] == 1400 * 26  # biweekly pay annualized by frequency, not by the 90-day window
    assert elig["fpl_pct"] == pytest.approx(232.6, abs=0.1)
    assert elig["discount_pct"] == 75
    # her hospital's real policy (Sutter CPMC, from policydb/): insured, <= 400% FPL, but the write-off needs
    # out-of-pocket over 10% of income and her $1,100 share is 3% -> no discount; $600 left, on a plan
    assert demo["hospital"]["name"] == demo["bill"]["provider"] == "California Pacific Medical Center"
    sutter = finance.charity_discount(inc["annual"], 1, stored_tiers("sutter-cpmc"), insured=True, out_of_pocket=1100)
    assert sutter["discount_pct"] == 0 and sutter["oop_pct_income"] == pytest.approx(3.0, abs=0.1)
    assert finance.settle(1100, 500, 0) == {"discounted_share": 1100.0, "balance": 600.0, "refund": 0.0}


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
    assert c["hospital"]["name"] == c["bill"]["provider"] == "UCSF Medical Center"
    elig = finance.charity_discount(inc["annual"], 3, stored_tiers("ucsf-health"),  # UCSF's real policy
                                    insured=True, out_of_pocket=c["eob"]["patient_responsibility"])
    assert rec["owed"] == 5600 and elig["discount_pct"] == 100
    assert elig["fpl_pct"] == pytest.approx(174.0, abs=0.5) and elig["oop_pct_income"] == pytest.approx(12.1, abs=0.1)
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
    txns = [t for t in demo["transactions"] if "Pacific" not in t.get("merchant", "")]
    rec = finance.reconcile(demo["bill"], demo["eob"], txns)
    assert rec["paid"] == 0 and rec["owed"] == 1100


def test_payment_plan_respects_policy_cap():
    # Sutter's billing policy (policydb/): monthly payment at most 10% of monthly income
    plan = finance.payment_plan(600, 3033.33, policy_cap_pct=10)
    assert (plan["months"], plan["monthly"]) == (6, 100.0)
    tight = finance.payment_plan(600, 3033.33, max_share=0.2, policy_cap_pct=2)  # the policy's cap wins when lower
    assert (tight["months"], tight["monthly"]) == (12, 50.0) and "caps it at 2%" in tight["reason"]


def test_payment_plan_says_when_the_cap_is_billbacks_default():
    default = finance.payment_plan(150, 3033.33)
    assert "under 4% of monthly income" in default["reason"] and "BillBack's default" in default["reason"]
    roomy = finance.payment_plan(600, 3033.33, policy_cap_pct=10)  # policy allows more, so our 4% still binds
    assert "BillBack's default" in roomy["reason"] and "allows up to 10%" in roomy["reason"]
    tight = finance.payment_plan(600, 3033.33, policy_cap_pct=2)
    assert "BillBack's default" not in tight["reason"] and "caps it at 2%" in tight["reason"]
    mine = finance.payment_plan(600, 3033.33, max_monthly=75)
    assert "BillBack's default" not in mine["reason"]

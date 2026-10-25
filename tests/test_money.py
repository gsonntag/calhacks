"""The money never overclaims: plans add up and end, income is labelled take-home, a hold is not a win."""
import json
from datetime import date

import pytest

from billback import config, finance, orchestrator
from billback.agents import planner
from billback.events import bus
from billback.integrations.nessie import LocalMirror

TODAY = date(2026, 10, 24)


def _txn(i, kind, day, amount, desc, merchant=""):
    return {"id": f"t{i}", "type": kind, "date": day, "amount": amount, "description": desc, "merchant": merchant}


def _pay(n=6, amount=1400.0):
    return [_txn(i, "deposit", f"2026-{8 + i // 3:02d}-{1 + (i % 3) * 14:02d}", amount, "PAYROLL DIRECT DEP - ACME")
            for i in range(n)]


# --- 3: the plan adds up to exactly what she owes ---

def test_plan_adds_up_with_a_smaller_last_payment():
    plan = finance.payment_plan(1574.98, 3033.33, policy_cap_pct=10)  # Rosa (rosa-real)
    assert (plan["months"], plan["monthly"], plan["last"]) == (18, 88.0, 78.98)
    assert round(plan["monthly"] * 17 + plan["last"], 2) == plan["total"] == 1574.98
    assert plan["text"] == "17 × $88/mo, then $78.98"
    assert finance.plan_said(plan) == "18 monthly payments: 17 of $88 and a last one of $78.98"


def test_even_plan_and_single_payment_wording():
    even = finance.payment_plan(600, 3033.33, policy_cap_pct=10)
    assert even["text"] == "6 × $100/mo" and even["last"] == 100 and finance.plan_said(even) == "6 monthly payments of $100"
    one = finance.payment_plan(99.5, 3033.33)
    assert (one["months"], one["monthly"], one["last"]) == (1, 99.5, 99.5) and one["text"] == "one payment of $99.50"


def test_longest_plan_never_overpays():
    # $400 with only $10/mo of room: 36 x $12 would be $432; the plan stops once the $400 is paid
    plan = finance.payment_plan(400, 100)
    assert plan["reason"] == "longest standard plan"
    assert plan["months"] == 34 and plan["last"] == 4 and round(plan["monthly"] * 33 + plan["last"], 2) == 400


# --- 5: the plan is sized on the bank account ---

def test_plan_is_smaller_when_regular_bills_leave_little():
    roomy = finance.payment_plan(1574.98, 3033.33, policy_cap_pct=10, monthly_bills=1391.33)
    assert (roomy["months"], roomy["monthly"]) == (18, 88.0) and "left after regular bills" in roomy["reason"]
    tight = finance.payment_plan(1574.98, 3033.33, policy_cap_pct=10, monthly_bills=2500)  # $533 left: 10% = $53
    assert (tight["months"], tight["monthly"]) == (36, 44.0)
    assert "what's left after your regular bills" in tight["reason"] and "$2,500 bills" in tight["reason"]
    # what the patient says they can pay still wins
    assert finance.payment_plan(1574.98, 3033.33, monthly_bills=2500, max_monthly=150)["monthly"] == 132


def test_monthly_bills_counts_regular_spending_only():
    bill = {"provider": "California Pacific Medical Center", "account_number": "CPMC-1"}
    txns = [_txn(i, "purchase", f"2026-{m:02d}-01", 1000, "Rent", "Apts") for i, m in enumerate((8, 9, 10))]
    txns += [_txn(10, "purchase", "2026-09-05", 900, "TV", "Electronics Store"),               # one-off
             _txn(11, "purchase", "2026-08-14", 250, "Payment acct CPMC-1", "California Pacific Medical Center"),
             _txn(12, "purchase", "2026-09-14", 250, "Payment acct CPMC-1", "California Pacific Medical Center"),
             _txn(13, "purchase", "2026-06-01", 1000, "Rent", "Apts")]                         # too old
    out = finance.monthly_bills(txns, today=TODAY, bill=bill)
    assert out["monthly"] == 1000 and out["count"] == 3 and "Apts" in out["source"]


# --- 4: income is take-home, gig and payment-app income counts ---

def test_income_counts_gig_and_payment_app_deposits():
    txns = _pay() + [_txn(20, "deposit", "2026-09-10", 300, "DOORDASH PAYOUT"),
                     _txn(21, "deposit", "2026-10-02", 150, "VENMO CASHOUT"),
                     _txn(22, "deposit", "2026-10-03", 40, "Mobile deposit")]  # unknown: not counted
    inc = finance.income(txns, today=TODAY)
    assert inc["payroll_annual"] == 36400 and inc["other_annual"] == round(450 * 365 / 90, 2)
    assert inc["annual"] == round(36400 + 450 * 365 / 90, 2)
    assert [t["id"] for t in inc["other_deposits"]] == ["t20", "t21"]
    assert "2 gig/payment-app deposits" in inc["source"] and inc["source"].endswith("take-home pay")


def test_income_is_labelled_take_home_with_a_gross_estimate():
    inc = finance.income(_pay(), today=TODAY)
    assert inc["basis"] == "take-home" and inc["annual"] == 36400
    assert inc["gross_annual_est"] == round(36400 / finance.TAKE_HOME_SHARE, 2)


def test_gross_check_flags_a_borderline_case_without_changing_it():
    # Marcus (marcus-real): his share is 10.3% of take-home pay, over UCSF's 10% rule; at estimated gross it isn't
    tiers = [{"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "insured", "min_oop_pct_income": 10}]
    inc = {"annual": 40560.0, "gross_annual_est": 50700.0}
    elig = finance.charity_discount(inc["annual"], 3, tiers, insured=True, out_of_pocket=4174.43)
    assert elig["discount_pct"] == 100  # the answer stays what the bank shows
    g = finance.gross_check(inc, 3, tiers, True, 4174.43, elig)
    assert g["discount_pct"] == 0 and "gross pay" in g["text"] and "pay stubs" in g["text"]
    assert finance.gross_check({"annual": 20000.0, "gross_annual_est": 25000.0}, 3, tiers, True, 4174.43,
                               finance.charity_discount(20000, 3, tiers, True, 4174.43)) is None


# --- 2: the plan in the bank ends ---

def test_schedule_ends_and_rolls_over_the_year():
    s = planner.schedule("2026-11-20", 3, 88.0, 78.98)
    assert s == [("2026-12-15", 88.0), ("2027-01-15", 88.0), ("2027-02-15", 78.98)]


async def test_planner_only_proposes_the_plan():
    """The patient decides at the end: no approval card, nothing written to the bank."""
    case = {"id": "money-plan", "today": "2026-10-24", "bill": {"account_number": "CPMC-1"},
            "hospital": {"name": "CPMC"}}
    bank = LocalMirror({"transactions": []})
    out = await planner.run(case, bank, "mirror", 1574.98, 3033.33, policy_cap_pct=10)
    assert out["created"] is None and (out["plan"]["months"], out["plan"]["last"]) == (18, 78.98)
    assert await bank.list_bills("mirror") == []
    assert not any(e["kind"] == "approval_requested" for e in bus.history("money-plan"))


# --- 1: a hold is not a win ---

def _case_with_call(scorecard, confirmed=None):
    return {"calls": [{"scorecard": scorecard, "confirmed_balance": confirmed}]}


PLAN_V = {"asks": [{"id": "insurance", "kind": "insurance", "money": 7600}, {"id": "payment", "kind": "payment", "money": 500},
                   {"id": "hold", "kind": "hold", "money": 0}]}


@pytest.mark.parametrize("marks, confirmed, want", [
    ([{"id": "hold", "status": "got"}, {"id": "insurance", "status": "later"}], None, (8700, False)),  # hold only
    ([{"id": "insurance", "status": "got"}], None, (1100, False)),                                    # part of it
    ([{"id": "insurance", "status": "got"}, {"id": "payment", "status": "got"}], None, (600, True)),  # all of it
    ([], 600, (600, True)),                                                                           # rep said so
    ([], 950.5, (950.5, False)),                                                                      # still above
])
def test_confirmed_balance(marks, confirmed, want):
    assert orchestrator.confirmed_balance(_case_with_call(marks, confirmed), 8700, 600, PLAN_V) == want


def test_summary_shows_billing_balance_until_confirmed():
    case = json.loads((config.DEMO_DIR / "case.json").read_text())
    case.update(id="money-sum", status="done", today="2026-10-24", clock_days=0, calls=[], followups=[],
                call_outcome={}, result={"target": 600, "refund": 0})
    assert orchestrator.current_balance(case) == 8700  # nothing called yet: the bill's amount, not the target
    case["result"].update(final_balance=8700, expected_balance=600, confirmed=False, refund=50)
    s = orchestrator.summary(case)
    assert (s["current_balance"], s["target"], s["confirmed"], s["refund"], s["expected_refund"]) == (8700, 600, False, 0, 50)

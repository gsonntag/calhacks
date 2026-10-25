"""Deterministic money math. Claude explains these numbers; it never invents them.

Every figure carries its source (bill line, EOB field, or Nessie transaction id) so the
dashboard and the caller can cite it.
"""
import math
import re
from datetime import date, datetime, timedelta

from . import billcheck

# HHS poverty guidelines, 48 contiguous states and D.C., 2026 (91 FR 1797, Jan 15 2026):
# https://www.govinfo.gov/content/pkg/FR-2026-01-15/html/2026-00755.htm
# https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines
FPL_BASE = 15_960
FPL_PER_EXTRA = 5_680
FPL_YEAR = 2026


def fpl(household_size: int) -> int:
    return FPL_BASE + FPL_PER_EXTRA * max(0, household_size - 1)


def _d(s: str) -> date:
    return datetime.fromisoformat(s[:10]).date()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower())


def _matches_payee(txn: dict, payee: str, account: str | None = None) -> bool:
    """A payment to this hospital: its own name or a registry alias as a whole phrase, or the bill's account
    number. Generic words a payee shares with other merchants ("Pacific", "Medical", "Center") never count."""
    return billcheck.is_payment_to(txn, payee, account)


def reconcile(bill: dict, eob: dict, txns: list[dict], corrections: list[dict] | None = None) -> dict:
    """eob {} (or None) = self-pay: the patient's share is the bill's own charges, less any `corrections`
    (bill-line errors from billcheck.line_corrections). Insured: the EOB's patient responsibility.
    `payments` are all bank payments to the hospital since the date of service (they all count toward `paid`);
    `uncredited_payments` are the ones the bill doesn't already show."""
    eob = eob or {}
    payee = bill["provider"]
    payments = [t for t in txns if t["type"] in ("purchase", "withdrawal", "transfer")
                and billcheck.on_or_after_service(t, bill, eob)
                and _matches_payee(t, payee, bill.get("account_number"))]
    credited, uncredited = billcheck.split_credited(bill, payments)
    not_on_bill = round(sum(t["not_on_bill"] for t in uncredited), 2)
    bill_credits = billcheck.bill_credits(bill)
    bank_paid = round(sum(t["amount"] for t in payments), 2)
    # payments the bill credits that the bank history doesn't show (cash, another card) still count as paid
    paid = round(bank_paid + max(0.0, bill_credits - (bank_paid - not_on_bill)), 2)
    insured = eob.get("patient_responsibility") not in (None, "")
    fixes = [] if insured else list(corrections or [])
    billed_due = float(bill["amount_due"])
    charges = float(bill.get("total_charges", billed_due))
    if insured:
        responsibility = float(eob["patient_responsibility"])
    else:
        responsibility = round(charges - float(bill.get("insurance_adjustments") or 0)
                               - sum(float(c["amount"]) for c in fixes), 2)
    owed = round(max(0.0, responsibility - paid), 2)
    insurance_adj = round(charges - responsibility, 2)

    lines = [{"label": "Hospital says you owe", "amount": billed_due, "source": f"bill · acct {bill.get('account_number', '?')}"}]
    if insured:
        lines.append({"label": "Insurance adjustment not applied", "amount": -round(billed_due + bill_credits - responsibility, 2) or 0.0,
                      "source": f"EOB · claim {eob.get('claim_number', '?')} · patient responsibility ${responsibility:,.2f}"})
    for c in fixes:
        lines.append({"label": c.get("label", "Bill error"), "amount": -round(float(c["amount"]), 2),
                      "source": c.get("source", "bill")})
    for t in uncredited:
        lines.append({"label": f"Payment already made {t['date'][:10]}", "amount": -t["not_on_bill"],
                      "source": f"Nessie txn {t['id']}"})
    lines.append({"label": "Actually owed", "amount": owed, "source": "computed", "total": True})

    return {
        "billed_due": billed_due,
        "patient_responsibility": responsibility,
        "insurance_adjustment": insurance_adj,
        "insured": insured,
        "corrections": fixes,
        "payments": payments,
        "credited_payments": credited,
        "uncredited_payments": uncredited,
        "credited_on_bill": bill_credits,
        "uncredited_paid": not_on_bill,
        "paid": paid,
        "owed": owed,
        "overbilled_by": round(billed_due - owed, 2),
        "duplicates": find_duplicates(txns),
        "lines": lines,
    }


def find_duplicates(txns: list[dict], window_days: int = 3) -> list[dict]:
    """Same merchant + same amount within a few days = likely double charge."""
    out, seen = [], set()
    purchases = sorted([t for t in txns if t["type"] == "purchase"], key=lambda t: t["date"])
    for i, a in enumerate(purchases):
        for b in purchases[i + 1:]:
            if (b["id"] not in seen and a["amount"] == b["amount"]
                    and _norm(a.get("merchant", "")) == _norm(b.get("merchant", ""))
                    and abs((_d(b["date"]) - _d(a["date"])).days) <= window_days):
                out.append({"first": a, "duplicate": b, "amount": b["amount"],
                            "source": f"Nessie txns {a['id']} + {b['id']}"})
                seen.add(b["id"])
    return out


PAY_PERIODS = [(7, 52, "weekly"), (14, 26, "every two weeks"), (15, 24, "twice a month"), (30, 12, "monthly")]


PAYROLL = r"payroll|salary|direct dep|paycheck|wages"
# Gig-platform payouts and money received through payment apps. Both count as income: leaving them out made
# gig workers look poorer (more eligible) than they are. A payment app also carries splits and reimbursements,
# so the source line lists them separately for the patient to correct.
OTHER_INCOME = (r"\b(uber|lyft|doordash|door dash|instacart|grubhub|postmates|shipt|taskrabbit|upwork|fiverr|etsy|"
                r"amazon flex|rover|venmo|zelle|cash app|cashapp|square cash|paypal)\b")

# Assumption: bank deposits are TAKE-HOME pay, but hospital assistance policies test GROSS family income.
# Take-home is roughly 75-85% of gross at these incomes (FICA 7.65% + federal and CA withholding + benefits).
# Eligibility still uses the bank figure (what we can prove); the estimate below, take-home / 0.80, is shown
# next to it and flags any case where gross pay could change the answer, so billing's request for pay stubs
# is no surprise. It is an estimate, never used for the money.
TAKE_HOME_SHARE = 0.80


def income(txns: list[dict], today: date | None = None, lookback_days: int = 90) -> dict:
    """Annualize payroll-like deposits from the last ~90 days by pay frequency (average paycheck x paychecks
    per year). Summing the window and scaling by 365/90 overstates income when an extra payday lands in it.
    Gig payouts and payment-app transfers received in the window are added, scaled to a year.
    All of it is take-home pay as the bank sees it (see TAKE_HOME_SHARE)."""
    today = today or date.today()
    start = today - timedelta(days=lookback_days)
    recent = [t for t in txns if t["type"] == "deposit" and _d(t["date"]) >= start]
    deposits = sorted([t for t in recent if re.search(PAYROLL, _norm(t.get("description", "")))],
                      key=lambda t: t["date"])
    other = sorted([t for t in recent if t not in deposits
                    and re.search(OTHER_INCOME, _norm(f"{t.get('description', '')} {t.get('merchant', '')}"))],
                   key=lambda t: t["date"])
    total = round(sum(t["amount"] for t in deposits), 2)
    gaps = sorted((_d(b["date"]) - _d(a["date"])).days for a, b in zip(deposits, deposits[1:]))
    if gaps:
        median = gaps[len(gaps) // 2]
        _, per_year, freq = min(PAY_PERIODS, key=lambda p: abs(p[0] - median))
        payroll = round(total / len(deposits) * per_year, 2)
        how = f"{len(deposits)} Nessie payroll deposits, paid {freq} (avg ${total / len(deposits):,.0f} x {per_year})"
    else:
        payroll = round(total * 365 / lookback_days, 2)
        how = f"{len(deposits)} Nessie payroll deposits, last {lookback_days} days"
    other_annual = round(sum(t["amount"] for t in other) * 365 / lookback_days, 2)
    if other:
        how += f" + {len(other)} gig/payment-app deposits (${other_annual:,.0f}/yr)"
    annual = round(payroll + other_annual, 2)
    return {"deposits": deposits, "other_deposits": other, "window_total": total, "payroll_annual": payroll,
            "other_annual": other_annual, "annual": annual, "monthly": round(annual / 12, 2),
            "basis": "take-home", "gross_annual_est": round(annual / TAKE_HOME_SHARE, 2),
            "source": how + "; take-home pay"}


def gross_check(inc: dict, household_size: int, tiers: list[dict], insured: bool, out_of_pocket: float,
                elig: dict) -> dict | None:
    """Would the estimated gross income (take-home / TAKE_HOME_SHARE) change the discount? None if not."""
    at_gross = charity_discount(inc["gross_annual_est"], household_size, tiers, insured=insured,
                                out_of_pocket=out_of_pocket)
    if at_gross["discount_pct"] == elig["discount_pct"]:
        return None
    return {"gross_annual_est": inc["gross_annual_est"], "fpl_pct": at_gross["fpl_pct"],
            "oop_pct_income": at_gross["oop_pct_income"], "discount_pct": at_gross["discount_pct"],
            "text": f"Based on take-home pay. If the hospital counts gross pay (about ${inc['gross_annual_est']:,.0f}/yr, "
                    f"{at_gross['fpl_pct']:.0f}% of the poverty line"
                    + (f", medical bills {at_gross['oop_pct_income']:.0f}% of income" if out_of_pocket else "")
                    + f"), the discount would be {at_gross['discount_pct']:g}%, not {elig['discount_pct']:g}%. "
                      "Have pay stubs ready."}


def _merchant_key(t: dict) -> str:
    return _norm(t.get("merchant") or t.get("description") or "")


def monthly_bills(txns: list[dict], today: date | None = None, bill: dict | None = None,
                  lookback_days: int = 90) -> dict:
    """Regular spending from the bank history: money out to a merchant seen in at least two different months
    of the last ~90 days (rent, phone, groceries, gas...), as a monthly average. One-offs don't count, and
    neither do payments to this hospital (the plan replaces them). Used to size the payment plan."""
    today = today or date.today()
    start = today - timedelta(days=lookback_days)
    out = [t for t in txns if t["type"] in ("purchase", "withdrawal", "transfer") and _d(t["date"]) >= start
           and not (bill and _matches_payee(t, bill.get("provider", ""), bill.get("account_number")))]
    months: dict[str, set] = {}
    for t in out:
        months.setdefault(_merchant_key(t), set()).add(t["date"][:7])
    regular = [t for t in out if len(months[_merchant_key(t)]) >= 2]
    monthly = round(sum(t["amount"] for t in regular) * 30 / lookback_days, 2)
    names = sorted({t.get("merchant") or t.get("description") or "?" for t in regular})
    return {"monthly": monthly, "count": len(regular),
            "source": f"{len(regular)} regular Nessie payments in the last {lookback_days} days"
                      + (f" ({', '.join(names[:4])}{'…' if len(names) > 4 else ''})" if names else "")}


def _num(v, discount: bool = False) -> float | None:
    """Coerce an LLM-extracted tier value: 200, "200%", "up to 400" -> float; "free"/"full" discount -> 100."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        if discount and re.search(r"\b(free|full)\b", v, re.I):
            return 100.0
        m = re.search(r"\d+(\.\d+)?", v.replace(",", ""))
        return float(m.group(0)) if m else None
    return None


def clean_tiers(tiers) -> list[dict]:
    """Keep only tiers with a numeric FPL limit and percent discount (clamped to 0-100)."""
    out = []
    for t in tiers if isinstance(tiers, list) else []:
        if not isinstance(t, dict):
            continue
        mx, disc = _num(t.get("max_fpl_pct")), _num(t.get("discount_pct"), discount=True)
        if mx is None or disc is None:
            continue
        applies = str(t.get("applies_to") or "all").lower()
        oop = _num(t.get("min_oop_pct_income"))
        out.append({**t, "max_fpl_pct": mx, "discount_pct": min(100.0, max(0.0, disc)),
                    "applies_to": applies if applies in ("all", "insured", "uninsured") else "all",
                    "min_oop_pct_income": oop})
    return out


def charity_discount(annual_income: float, household_size: int, tiers: list[dict],
                     insured: bool = True, out_of_pocket: float = 0.0) -> dict:
    """tiers: [{"max_fpl_pct": 200, "discount_pct": 100, "applies_to": "all"|"insured"|"uninsured",
    "min_oop_pct_income": 10 | None}, ...] extracted from the hospital's own policy. Picks the best
    discount among the tiers the patient meets."""
    pct = round(100 * annual_income / fpl(household_size), 1)
    oop_pct = round(100 * out_of_pocket / annual_income, 1) if annual_income else 0.0
    tiers = [t for t in tiers if isinstance(t.get("max_fpl_pct"), (int, float))
             and isinstance(t.get("discount_pct"), (int, float))]

    def meets(t: dict) -> bool:
        who = t.get("applies_to") or "all"
        if (who == "insured" and not insured) or (who == "uninsured" and insured):
            return False
        need = t.get("min_oop_pct_income")
        return pct <= t["max_fpl_pct"] and (need is None or oop_pct > need)

    eligible = sorted([t for t in tiers if meets(t)], key=lambda t: (-t["discount_pct"], t["max_fpl_pct"]))
    tier = eligible[0] if eligible else None
    return {"fpl_pct": pct, "fpl_amount": fpl(household_size), "fpl_year": FPL_YEAR, "household_size": household_size,
            "insured": insured, "oop_pct_income": oop_pct, "tier": tier,
            "discount_pct": tier["discount_pct"] if tier else 0}


def settle(responsibility: float, paid: float, discount_pct: float) -> dict:
    """The discount applies to the patient's share of the bill; payments already made count against the
    discounted share. A negative remainder is a refund owed to the patient."""
    share = round(responsibility * (1 - discount_pct / 100), 2)
    remainder = round(share - paid, 2)
    return {"discounted_share": share, "balance": max(0.0, remainder), "refund": max(0.0, -remainder)}


def apply_discount(owed: float, discount_pct: float) -> float:
    return round(owed * (1 - discount_pct / 100), 2)


def _usd(x: float) -> str:
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"


def plan_text(plan: dict) -> str:
    """The plan as the patient sees it, adding up exactly: '6 × $100/mo' or '17 × $88/mo, then $78.98'."""
    n, pay, last = plan.get("months") or 0, plan.get("monthly") or 0.0, plan.get("last", plan.get("monthly"))
    if not n:
        return "nothing owed"
    if n == 1:
        return f"one payment of {_usd(last)}"
    return f"{n} × {_usd(pay)}/mo" if last == pay else f"{n - 1} × {_usd(pay)}/mo, then {_usd(last)}"


def plan_said(plan: dict) -> str:
    """The same plan, for the call: '18 monthly payments: 17 of $88 and a last one of $78.98'."""
    n, pay, last = plan["months"], plan["monthly"], plan.get("last", plan["monthly"])
    if n == 1:
        return f"one payment of {_usd(last)}"
    return (f"{n} monthly payments of {_usd(pay)}" if last == pay
            else f"{n} monthly payments: {n - 1} of {_usd(pay)} and a last one of {_usd(last)}")


def _schedule(balance: float, pay: int, reason: str) -> dict:
    """Whole-dollar payments of `pay`, and a smaller last one so the plan adds up to the balance exactly."""
    n = math.ceil(round(balance / pay, 6))
    last = round(balance - pay * (n - 1), 2)
    if n == 1:
        pay = last
    plan = {"months": n, "monthly": float(pay), "last": float(last), "total": float(round(balance, 2)), "reason": reason}
    return {**plan, "text": plan_text(plan)}


def payment_plan(balance: float, monthly_income: float, max_share: float = 0.04,
                 policy_cap_pct: float | None = None, max_monthly: float | None = None,
                 monthly_bills: float | None = None) -> dict:
    """Shortest interest-free plan whose payment stays under max_share of monthly income (and under the
    hospital policy's own cap, when its policy states one). max_monthly: what the patient says they can pay;
    it replaces both caps (the policy's cap limits what the hospital can ask, not what the patient offers).
    monthly_bills: regular spending from the bank (monthly_bills()). The payment then also stays under the
    policy's cap (10% when it states none) of what is left after those bills, the way California policies word
    it ("after excluding essential living expenses"), so a tight budget gets a longer, smaller plan.
    The payments are whole dollars; the last one is smaller so the total is exactly the balance."""
    if balance <= 0:
        return {"months": 0, "monthly": 0.0, "last": 0.0, "total": 0.0, "text": "nothing owed", "reason": "nothing owed"}
    share = min(max_share, policy_cap_pct / 100) if policy_cap_pct else max_share
    cap = max(10.0, monthly_income * share)
    room = None
    if monthly_bills is not None:
        left = max(0.0, monthly_income - monthly_bills)
        room = max(10.0, left * (policy_cap_pct or 10) / 100)
    bank = room is not None and room < cap and not max_monthly
    if bank:
        cap = room
    if max_monthly:
        cap = max_monthly
    # the share is BillBack's own default unless the hospital policy's cap is the lower one
    ours = not (policy_cap_pct and policy_cap_pct / 100 < max_share)
    policy = (f"; the hospital's policy allows up to {policy_cap_pct:g}%" if policy_cap_pct and ours
              else f"; the hospital's policy caps it at {policy_cap_pct:g}%" if policy_cap_pct else "")
    default = ": BillBack's default, not the hospital's rule" if ours else ""
    for months in (1, 3, 6, 12, 18, 24, 36):
        pay = math.ceil(balance / months)
        if pay <= cap:
            if max_monthly:
                why = f"${pay}/mo is within the ${max_monthly:,.0f}/mo you said you can pay"
            elif bank:
                why = (f"${pay}/mo stays under {policy_cap_pct or 10:g}% of what's left after your regular bills "
                       f"(${monthly_income:,.0f} income − ${monthly_bills:,.0f} bills a month, from your bank)")
            else:
                why = (f"${pay}/mo stays under {share * 100:g}% of monthly income (${monthly_income:,.0f})" + default + policy
                       + (f"; you have about ${monthly_income - monthly_bills:,.0f}/mo left after regular bills"
                          if monthly_bills is not None else ""))
            return _schedule(balance, pay, why)
    return _schedule(balance, math.ceil(balance / 36), "longest standard plan")

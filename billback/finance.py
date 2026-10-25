"""Deterministic money math. Claude explains these numbers; it never invents them.

Every figure carries its source (bill line, EOB field, or Nessie transaction id) so the
dashboard and the caller can cite it.
"""
import math
import re
from datetime import date, datetime, timedelta

from . import billcheck

# HHS poverty guidelines, 48 contiguous states. 2025 values; update when the
# 2026 guidelines are confirmed.
FPL_BASE = 15_650
FPL_PER_EXTRA = 5_500
FPL_YEAR = 2025


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


def income(txns: list[dict], today: date | None = None, lookback_days: int = 90) -> dict:
    """Annualize payroll-like deposits from the last ~90 days by pay frequency (average paycheck x paychecks
    per year). Summing the window and scaling by 365/90 overstates income when an extra payday lands in it."""
    today = today or date.today()
    start = today - timedelta(days=lookback_days)
    deposits = sorted([t for t in txns if t["type"] == "deposit" and _d(t["date"]) >= start
                       and re.search(r"payroll|salary|direct dep|paycheck|wages", _norm(t.get("description", "")))],
                      key=lambda t: t["date"])
    total = round(sum(t["amount"] for t in deposits), 2)
    gaps = sorted((_d(b["date"]) - _d(a["date"])).days for a, b in zip(deposits, deposits[1:]))
    if gaps:
        median = gaps[len(gaps) // 2]
        _, per_year, freq = min(PAY_PERIODS, key=lambda p: abs(p[0] - median))
        annual = round(total / len(deposits) * per_year, 2)
        how = f"{len(deposits)} Nessie payroll deposits, paid {freq} (avg ${total / len(deposits):,.0f} x {per_year})"
    else:
        annual = round(total * 365 / lookback_days, 2)
        how = f"{len(deposits)} Nessie payroll deposits, last {lookback_days} days"
    return {"deposits": deposits, "window_total": total, "annual": annual, "monthly": round(annual / 12, 2),
            "source": how}


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


def payment_plan(balance: float, monthly_income: float, max_share: float = 0.04,
                 policy_cap_pct: float | None = None, max_monthly: float | None = None) -> dict:
    """Shortest interest-free plan whose payment stays under max_share of monthly income (and under the
    hospital policy's own cap, when its policy states one). max_monthly: what the patient says they can pay;
    it replaces both caps (the policy's cap limits what the hospital can ask, not what the patient offers)."""
    if balance <= 0:
        return {"months": 0, "monthly": 0.0, "reason": "nothing owed"}
    share = min(max_share, policy_cap_pct / 100) if policy_cap_pct else max_share
    cap = max(10.0, monthly_income * share)
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
            why = (f"${pay}/mo is within the ${max_monthly:,.0f}/mo you said you can pay" if max_monthly
                   else f"${pay}/mo stays under {share * 100:g}% of monthly income (${monthly_income:,.0f})")
            return {"months": months, "monthly": float(pay), "reason": why + ("" if max_monthly else default + policy)}
    return {"months": 36, "monthly": float(math.ceil(balance / 36)), "reason": "longest standard plan"}

"""Deterministic money math. Claude explains these numbers; it never invents them.

Every figure carries its source (bill line, EOB field, or Nessie transaction id) so the
dashboard and the caller can cite it.
"""
import math
import re
from datetime import date, datetime, timedelta

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


def _matches_payee(txn: dict, payee: str) -> bool:
    words = [w for w in _norm(payee).split() if len(w) > 3]
    hay = _norm(f"{txn.get('merchant', '')} {txn.get('description', '')}")
    return any(w in hay for w in words)


def reconcile(bill: dict, eob: dict, txns: list[dict]) -> dict:
    payee = bill["provider"]
    payments = [t for t in txns if t["type"] in ("purchase", "withdrawal", "transfer") and _matches_payee(t, payee)]
    paid = round(sum(t["amount"] for t in payments), 2)
    responsibility = float(eob["patient_responsibility"])
    owed = round(max(0.0, responsibility - paid), 2)
    billed_due = float(bill["amount_due"])
    insurance_adj = round(float(bill.get("total_charges", billed_due)) - responsibility, 2)

    lines = [
        {"label": "Hospital says you owe", "amount": billed_due, "source": f"bill · acct {bill.get('account_number', '?')}"},
        {"label": "Insurance adjustment not applied", "amount": -round(billed_due - responsibility, 2),
         "source": f"EOB · claim {eob.get('claim_number', '?')} · patient responsibility ${responsibility:,.2f}"},
    ]
    for t in payments:
        lines.append({"label": f"Payment already made {t['date'][:10]}", "amount": -t["amount"],
                      "source": f"Nessie txn {t['id']}"})
    lines.append({"label": "Actually owed", "amount": owed, "source": "computed", "total": True})

    return {
        "billed_due": billed_due,
        "patient_responsibility": responsibility,
        "insurance_adjustment": insurance_adj,
        "payments": payments,
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


def income(txns: list[dict], today: date | None = None, lookback_days: int = 90) -> dict:
    """Annualize payroll-like deposits from the last ~90 days."""
    today = today or date.today()
    start = today - timedelta(days=lookback_days)
    deposits = [t for t in txns if t["type"] == "deposit" and _d(t["date"]) >= start
                and re.search(r"payroll|salary|direct dep|paycheck|wages", _norm(t.get("description", "")))]
    total = round(sum(t["amount"] for t in deposits), 2)
    annual = round(total * 365 / lookback_days, 2)
    return {"deposits": deposits, "window_total": total, "annual": annual, "monthly": round(annual / 12, 2),
            "source": f"{len(deposits)} Nessie payroll deposits, last {lookback_days} days"}


def charity_discount(annual_income: float, household_size: int, tiers: list[dict]) -> dict:
    """tiers: [{"max_fpl_pct": 200, "discount_pct": 100}, ...] extracted from the hospital's own policy."""
    pct = round(100 * annual_income / fpl(household_size), 1)
    eligible = sorted([t for t in tiers if pct <= t["max_fpl_pct"]], key=lambda t: t["max_fpl_pct"])
    tier = eligible[0] if eligible else None
    return {"fpl_pct": pct, "fpl_amount": fpl(household_size), "fpl_year": FPL_YEAR,
            "household_size": household_size, "tier": tier, "discount_pct": tier["discount_pct"] if tier else 0}


def apply_discount(owed: float, discount_pct: float) -> float:
    return round(owed * (1 - discount_pct / 100), 2)


def payment_plan(balance: float, monthly_income: float, max_share: float = 0.04) -> dict:
    """Shortest interest-free plan whose payment stays under max_share of monthly income."""
    if balance <= 0:
        return {"months": 0, "monthly": 0.0, "reason": "nothing owed"}
    cap = max(10.0, monthly_income * max_share)
    for months in (1, 3, 6, 12, 18, 24, 36):
        pay = math.ceil(balance / months)
        if pay <= cap:
            return {"months": months, "monthly": float(pay),
                    "reason": f"${pay}/mo stays under {int(max_share * 100)}% of monthly income (${monthly_income:,.0f})"}
    return {"months": 36, "monthly": float(math.ceil(balance / 36)), "reason": "longest standard plan"}

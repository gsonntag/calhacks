"""Planner: proposes an interest-free plan for whatever is still owed: N payments, the last one smaller so they
add up to exactly the balance. The patient decides at the end whether to take it and sets up the payments
themselves; Remy never asks to write it to their bank. `schedule` lays out the payment dates."""
from datetime import date

from .. import finance
from ..events import bus


def schedule(today: str, months: int, monthly: float, last: float) -> list[tuple[str, float]]:
    """(payment date, amount) for each payment: the 15th of every month, starting next month."""
    start = date.fromisoformat(today).replace(day=1)
    out = []
    for k in range(months):
        m = start.month + k  # 0-based offset from this month, +1 below for "next month"
        out.append((date(start.year + m // 12, m % 12 + 1, 15).isoformat(), last if k == months - 1 else monthly))
    return out


async def run(case: dict, bank, account_id: str, balance: float, monthly_income: float,
              policy_cap_pct: float | None = None, max_monthly: float | None = None,
              monthly_bills: float | None = None) -> dict:
    cid = case["id"]
    plan = finance.payment_plan(balance, monthly_income, policy_cap_pct=policy_cap_pct, max_monthly=max_monthly,
                                monthly_bills=monthly_bills)
    bus.emit(cid, "plan_proposed", "planner", balance=balance, **plan)
    return {"plan": plan, "created": None}

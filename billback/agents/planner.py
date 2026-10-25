"""Planner: proposes an interest-free plan for whatever is still owed and, only after the patient
approves on their phone, writes it to Nessie as one scheduled bill per payment, so the plan ends: N payments,
the last one smaller so they add up to exactly the balance (Nessie bills have no end date or payment count)."""
import asyncio
from datetime import date

from .. import finance
from ..events import bus
from ..harness import Tool, ToolRegistry


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
    if plan["months"] == 0:
        return {"plan": plan, "created": None}

    reg = ToolRegistry(cid, "planner")

    async def create_payment_plan(monthly: float, months: int, payee: str, last: float | None = None,
                                  text: str = "") -> dict:
        acct = case["bill"]["account_number"]
        dates = schedule(case["today"], months, monthly, monthly if last is None else last)
        sem = asyncio.Semaphore(4)

        async def one(k: int, when: str, amount: float) -> dict:
            async with sem:
                return await bank.create_bill(account_id, payee=payee, amount=amount, payment_date=when,
                                              nickname=f"{payee} plan {k}/{months} ${amount:,.2f} ({acct})")
        bills = await asyncio.gather(*(one(k, when, amt) for k, (when, amt) in enumerate(dates, 1)))
        return {"_id": bills[0].get("_id"), "bill_ids": [b.get("_id") for b in bills], "payments": months,
                "first_date": dates[0][0], "last_date": dates[-1][0], "last_amount": dates[-1][1],
                "total": round(sum(a for _, a in dates), 2)}

    reg.add(Tool("create_payment_plan", "Schedule each payment of the plan in the patient's bank", {
        "type": "object", "properties": {"monthly": {"type": "number"}, "months": {"type": "integer"},
                                         "payee": {"type": "string"}, "last": {"type": "number"},
                                         "text": {"type": "string"}},
        "required": ["monthly", "months", "payee"]},
        create_payment_plan, tier="consequential",
        approval_title=lambda a: f"Set up {a.get('text') or finance.plan_text(a)}, interest-free, with {a['payee']}?"))

    created = await reg.call("create_payment_plan", {"monthly": plan["monthly"], "months": plan["months"],
                                                     "last": plan["last"], "text": plan["text"],
                                                     "payee": case["hospital"]["name"]})
    return {"plan": plan, "created": created}

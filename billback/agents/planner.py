"""Planner: proposes an interest-free plan for whatever is still owed and, only after the patient
approves on their phone, writes it to Nessie as a recurring bill."""
from datetime import date

from .. import finance
from ..events import bus
from ..harness import Tool, ToolRegistry


async def run(case: dict, bank, account_id: str, balance: float, monthly_income: float) -> dict:
    cid = case["id"]
    plan = finance.payment_plan(balance, monthly_income)
    bus.emit(cid, "plan_proposed", "planner", balance=balance, **plan)
    if plan["months"] == 0:
        return {"plan": plan, "created": None}

    reg = ToolRegistry(cid, "planner")

    async def create_payment_plan(monthly: float, months: int, payee: str) -> dict:
        start = date.fromisoformat(case["today"]).replace(day=1)
        first = date(start.year + (start.month // 12), start.month % 12 + 1, 15).isoformat()
        return await bank.create_bill(account_id, payee=payee,
                                      nickname=f"{payee} plan {months}x${monthly:.0f} ({case['bill']['account_number']})",
                                      amount=monthly, payment_date=first, status="recurring", recurring_date=15)

    reg.add(Tool("create_payment_plan", "Create a recurring monthly payment in the patient's bank", {
        "type": "object", "properties": {"monthly": {"type": "number"}, "months": {"type": "integer"},
                                         "payee": {"type": "string"}}, "required": ["monthly", "months", "payee"]},
        create_payment_plan, tier="consequential"))

    created = await reg.call("create_payment_plan", {"monthly": plan["monthly"], "months": plan["months"],
                                                     "payee": case["hospital"]["name"]})
    return {"plan": plan, "created": created}

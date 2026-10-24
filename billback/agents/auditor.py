"""Auditor: works out what is actually owed, using the bill, the EOB and Nessie transactions."""
from .. import finance
from ..events import bus


async def run(case: dict, bank, account_id: str, bank_label: str) -> dict:
    cid = case["id"]
    bus.emit(cid, "agent_started", "auditor", text=f"Pulling transactions from {bank_label}")
    txns = await bank.transactions(account_id)
    bus.emit(cid, "evidence", "auditor", label="Transactions loaded", value=f"{len(txns)} records", source=bank_label)

    rec = finance.reconcile(case["bill"], case["eob"], txns)
    for p in rec["payments"]:
        bus.emit(cid, "evidence", "auditor", label="Payment hospital may not see",
                 value=f"${p['amount']:,.2f} on {p['date'][:10]}", source=f"Nessie txn {p['id']}")
    for d in rec["duplicates"]:
        bus.emit(cid, "evidence", "auditor", label="Duplicate charge",
                 value=f"{d['duplicate']['merchant']} ${d['amount']:,.2f} charged twice", source=d["source"])
    bus.emit(cid, "reconciliation", "auditor", **{k: rec[k] for k in ("lines", "owed", "billed_due", "overbilled_by")})
    bus.emit(cid, "balance", "auditor", amount=rec["owed"], label="Actually owed")

    inc = finance.income(txns, today=finance._d(case["today"]))
    bus.emit(cid, "evidence", "auditor", label="Income from payroll deposits",
             value=f"${inc['annual']:,.0f}/yr (${inc['monthly']:,.0f}/mo)", source=inc["source"])
    bus.emit(cid, "agent_finished", "auditor", summary=f"Owes ${rec['owed']:,.2f}, not ${rec['billed_due']:,.2f}")
    return {"reconciliation": rec, "income": inc, "transactions": txns}

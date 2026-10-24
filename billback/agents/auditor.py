"""Auditor: works out what is actually owed, using the bill, the EOB and Nessie transactions.

Publishes the transactions and the balance on futures so the Policy agent (running in parallel)
can use them as soon as they exist."""
import asyncio

from .. import finance
from ..events import bus, step


def _dup_note(d: dict) -> str:
    return f"also: possible double charge at {d['duplicate']['merchant']} (${d['amount']:,.2f}), separate from this bill"


async def run(case: dict, bank, account_id: str, bank_label: str,
              txns_ready: asyncio.Future, owed_ready: asyncio.Future) -> dict:
    cid = case["id"]
    bill, eob = case["bill"], case["eob"]
    bus.emit(cid, "agent_started", "auditor", text="Finding what you actually owe")

    step(cid, "auditor", "read", "Read bill & EOB", "running")
    await asyncio.sleep(0.6)
    step(cid, "auditor", "read", "Read bill & EOB", "ok",
         f"${bill['amount_due']:,.0f} billed; your insurance says you owe ${eob['patient_responsibility']:,.0f}")

    step(cid, "auditor", "payments", "Find past payments", "running")
    txns = await bank.transactions(account_id)
    txns_ready.set_result(txns)
    rec = finance.reconcile(bill, eob, txns)
    for p in rec["payments"]:
        bus.emit(cid, "evidence", "auditor", label="Payment hospital may not see",
                 value=f"${p['amount']:,.2f} on {p['date'][:10]}", source=f"{bank_label} · txn {p['id']}")
    if rec["payments"]:
        p = rec["payments"][0]
        step(cid, "auditor", "payments", "Find past payments", "ok",
             f"You already paid ${rec['paid']:,.0f} ({p['date'][:10]}, {bank_label.split(' (')[0]})")
    else:
        step(cid, "auditor", "payments", "Find past payments", "clear", "Nothing found, no payments yet")

    step(cid, "auditor", "dupes", "Check for duplicates", "running")
    await asyncio.sleep(0.5)
    if rec["duplicates"]:
        d = rec["duplicates"][0]
        step(cid, "auditor", "dupes", "Check for duplicates", "flag",
             f"{d['duplicate']['merchant']} charged ${d['amount']:,.2f} twice (separate from this bill, dispute with your bank)")
        bus.emit(cid, "evidence", "auditor", label="Duplicate charge",
                 value=f"{d['duplicate']['merchant']} ${d['amount']:,.2f}", source=d["source"])
    else:
        step(cid, "auditor", "dupes", "Check for duplicates", "clear", "Nothing found")

    step(cid, "auditor", "balance", "Calculate balance", "running")
    await asyncio.sleep(0.4)
    paid = f" − ${rec['paid']:,.0f}" if rec["paid"] else ""
    adj = rec["billed_due"] - rec["patient_responsibility"]
    step(cid, "auditor", "balance", "Calculate balance", "ok",
         f"${rec['billed_due']:,.0f} − ${adj:,.0f} insurance{paid} = ${rec['owed']:,.0f}" if adj > 0 or rec["paid"]
         else f"${rec['owed']:,.0f} — the bill matches your insurance statement")
    bus.emit(cid, "reconciliation", "auditor", **{k: rec[k] for k in ("lines", "owed", "billed_due", "overbilled_by")})
    bus.emit(cid, "balance", "auditor", amount=rec["owed"], label="Actually owed")
    owed_ready.set_result({"owed": rec["owed"], "responsibility": rec["patient_responsibility"], "paid": rec["paid"]})

    note = (_dup_note(rec["duplicates"][0]) if rec["duplicates"]
            else "no errors on the bill")
    bus.emit(cid, "agent_finished", "auditor", summary=f"${rec['owed']:,.0f} owed", note=note)
    return {"reconciliation": rec, "transactions": txns}

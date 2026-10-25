"""Nessie agent (the Auditor): works out what is actually owed from the patient's own records: the bill,
the EOB and their bank transactions (Capital One Nessie). Reports in the shared format (report.py).

Publishes the transactions and the balance on futures so the Policy agent (running in parallel)
can use them as soon as they exist."""
import asyncio

from .. import finance
from ..events import bus
from .report import Report


def _dup_note(d: dict) -> str:
    return f"also: possible double charge at {d['duplicate']['merchant']} (${d['amount']:,.2f}), separate from this bill"


def report(case_id: str, bank_label: str) -> Report:
    return Report(case_id, "auditor", "Nessie agent",
                  f"Your own records: bank ({bank_label.split(' (')[0]}), the bill, your insurance statement")


async def run(case: dict, bank, account_id: str, bank_label: str,
              txns_ready: asyncio.Future, owed_ready: asyncio.Future) -> dict:
    cid = case["id"]
    bill, eob = case["bill"], case["eob"]
    bank_name = bank_label.split(" (")[0]
    r = report(cid, bank_label)
    r.start("Finding what you actually owe")

    acct = f"bill · acct {bill.get('account_number', '?')}"
    claim = f"{eob.get('insurer', 'insurance statement')} · claim {eob.get('claim_number', '?')}"
    billed, resp = float(bill["amount_due"]), float(eob["patient_responsibility"])
    r.step("read", "Read the bill and insurance statement", "running")
    r.step("read", "Read the bill and insurance statement", "ok",
           f"{bill['provider']} bills ${billed:,.0f}; your insurer says your share is ${resp:,.0f}",
           [(f"Bill: ${billed:,.2f} due by {bill.get('due_date', '?')}", acct),
            (f"Insurance statement: plan paid ${float(eob.get('plan_paid') or 0):,.2f}, "
             f"you owe ${resp:,.2f}", claim)])

    r.step("insurance", "Compare the bill with the insurance statement", "running")
    if billed > resp:
        r.step("insurance", "Compare the bill with the insurance statement", "flag",
               f"The bill ignores your insurance: ${billed - resp:,.0f} too high",
               [(f"Bill asks for ${billed:,.2f}", acct), (f"Insurance statement says ${resp:,.2f}", claim)])
        r.finding("insurance", "Insurance wasn’t applied", billed - resp,
                  [f"The bill asks for ${billed:,.2f}, but your insurance statement says your share is ${resp:,.2f}."], claim)
    else:
        r.step("insurance", "Compare the bill with the insurance statement", "clear", "The bill matches your insurance statement")

    r.step("bank", f"Pull your bank history ({bank_name})", "running")
    txns = await bank.transactions(account_id)
    txns_ready.set_result(txns)
    dates = sorted(t["date"][:10] for t in txns if t.get("date"))
    r.step("bank", f"Pull your bank history ({bank_name})", "ok",
           f"{len(txns)} transactions" + (f", {dates[0]} to {dates[-1]}" if dates else ""))

    rec = finance.reconcile(bill, eob, txns)
    r.step("payments", f"Look for payments to {bill['provider']}", "running")
    for p in rec["payments"]:
        bus.emit(cid, "evidence", "auditor", label="Payment hospital may not see",
                 value=f"${p['amount']:,.2f} on {p['date'][:10]}", source=f"{bank_label} · txn {p['id']}")
    if rec["payments"]:
        proof = [(f"${p['amount']:,.2f} on {p['date'][:10]}: {p.get('merchant') or p.get('description') or 'payment'}",
                  f"{bank_name} · txn {p['id']}") for p in rec["payments"]]
        r.step("payments", f"Look for payments to {bill['provider']}", "flag",
               f"You already paid ${rec['paid']:,.0f} and the bill doesn’t show it", proof)
        for p in rec["payments"]:
            r.finding(f"paid-{p['id']}", "A payment you made was missed", p["amount"],
                      [f"Your bank shows ${p['amount']:,.2f} paid on {p['date'][:10]}; the bill still asks for the full amount."],
                      f"{bank_name} · txn {p['id']}")
    else:
        r.step("payments", f"Look for payments to {bill['provider']}", "clear", "No payments to the hospital yet")

    r.step("dupes", "Check your bank for duplicate charges", "running")
    if rec["duplicates"]:
        d = rec["duplicates"][0]
        r.step("dupes", "Check your bank for duplicate charges", "flag",
               f"{d['duplicate']['merchant']} charged ${d['amount']:,.2f} twice (separate from this bill, dispute with your bank)",
               [(f"${d['amount']:,.2f} on {t['date'][:10]}", f"{bank_name} · txn {t['id']}") for t in (d["first"], d["duplicate"])])
        bus.emit(cid, "evidence", "auditor", label="Duplicate charge",
                 value=f"{d['duplicate']['merchant']} ${d['amount']:,.2f}", source=d["source"])
        r.finding("dupe", f"Double charge at {d['duplicate']['merchant']}", 0,
                  [f"Charged ${d['amount']:,.2f} twice within days. Not part of this bill: dispute it with your bank."],
                  d["source"], value=f"${d['amount']:,.2f} · bank")
    else:
        r.step("dupes", "Check your bank for duplicate charges", "clear", "No double charges")

    r.step("balance", "Work out what you actually owe", "running")
    paid = f" − ${rec['paid']:,.0f} paid" if rec["paid"] else ""
    adj = rec["billed_due"] - rec["patient_responsibility"]
    r.step("balance", "Work out what you actually owe", "ok",
           f"${rec['billed_due']:,.0f} − ${adj:,.0f} insurance{paid} = ${rec['owed']:,.0f}" if adj > 0 or rec["paid"]
           else f"${rec['owed']:,.0f}: the bill matches your insurance statement")
    bus.emit(cid, "reconciliation", "auditor", **{k: rec[k] for k in ("lines", "owed", "billed_due", "overbilled_by")})
    bus.emit(cid, "balance", "auditor", amount=rec["owed"], label="Actually owed")
    owed_ready.set_result({"owed": rec["owed"], "responsibility": rec["patient_responsibility"], "paid": rec["paid"]})

    note = (_dup_note(rec["duplicates"][0]) if rec["duplicates"]
            else "no errors on the bill")
    r.finish(f"${rec['owed']:,.0f} owed", note)
    return {"reconciliation": rec, "transactions": txns}

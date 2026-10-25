"""Nessie agent (the Auditor): works out what is actually owed from the patient's own records: the bill,
the EOB and their bank transactions (Capital One Nessie). Reports in the shared format (report.py).

Publishes the transactions and the balance on futures so the Policy agent (running in parallel)
can use them as soon as they exist."""
import asyncio

from .. import billcheck, finance
from ..events import bus
from .report import Report


def _dup_note(d: dict) -> str:
    return f"also: possible double charge at {d['duplicate']['merchant']} (${d['amount']:,.2f}), separate from this bill"


def mistakes(rec: dict) -> int:
    """Mistakes found on the bill: each line error, insurance not applied, each payment it doesn't show."""
    ins = any(ln["label"].startswith("Insurance") and ln["amount"] < 0 for ln in rec["lines"])
    return len(rec.get("line_errors") or []) + ins + len(rec["uncredited_payments"])


def summary(rec: dict) -> dict:
    """The headline (agent_finished and review_done): the balance, then what was actually found ('4 mistakes
    found'; 'no errors on the bill' only when there are none), plus a bank double charge when there is one."""
    n = mistakes(rec)
    note = f"{n} mistake{'s' if n != 1 else ''} found" if n else "no errors on the bill"
    if rec["duplicates"]:
        note += f" · {_dup_note(rec['duplicates'][0])}"
    return {"result": f"${rec['owed']:,.0f} owed", "note": note}


def report(case_id: str, bank_label: str) -> Report:
    return Report(case_id, "auditor", "Nessie agent",
                  f"Your own records: bank ({bank_label.split(' (')[0]}), the bill, your insurance statement")


async def run(case: dict, bank, account_id: str, bank_label: str,
              txns_ready: asyncio.Future, owed_ready: asyncio.Future) -> dict:
    cid = case["id"]
    bill = case["bill"]
    insured = billcheck.insured(case)
    eob = (case.get("eob") or {}) if insured else {}
    bank_name = bank_label.split(" (")[0]
    r = report(cid, bank_label)
    r.start("Finding what you actually owe")

    acct = f"bill · acct {bill.get('account_number', '?')}"
    billed = float(bill["amount_due"])
    credits = billcheck.bill_credits(bill)
    # Every bill gets the line checks. Insured: the share comes from the insurance statement, so the balance
    # doesn't change, but a line billed twice or above the hospital's own posted price is its own mistake (the
    # claim was built on it) and is reported apart from the insurance gap. Drug lines are compared only when the
    # price file's unit converts to the HCPCS unit (billcheck.posted_unit_price); the rest are listed as skipped.
    review = billcheck.line_review(bill, case.get("hospital", {}).get("name"))
    corrections = review["corrections"]
    line_total = round(sum(c["amount"] for c in corrections), 2)
    if insured:
        claim = f"{eob.get('insurer', 'insurance statement')} · claim {eob.get('claim_number', '?')}"
        resp = float(eob["patient_responsibility"])
        expect = round(resp - credits, 2)  # what the bill should ask for once insurance and its own credits apply
        r.step("read", "Read the bill and insurance statement", "running")
        r.step("read", "Read the bill and insurance statement", "ok",
               f"{bill['provider']} bills ${billed:,.0f}; your insurer says your share is ${resp:,.0f}",
               [(f"Bill: ${billed:,.2f} due by {bill.get('due_date', '?')}", acct),
                (f"Insurance statement: plan paid ${float(eob.get('plan_paid') or 0):,.2f}, "
                 f"you owe ${resp:,.2f}", claim)])

        r.step("insurance", "Compare the bill with the insurance statement", "running")
        ins_gap = round(billed - expect - line_total, 2)
        if ins_gap > 0:
            also = f" (plus ${line_total:,.0f} in line errors, below)" if line_total else ""
            r.step("insurance", "Compare the bill with the insurance statement", "flag",
                   f"The bill ignores your insurance: ${ins_gap:,.0f} too high{also}",
                   [(f"Bill asks for ${billed:,.2f}", acct), (f"Insurance statement says ${resp:,.2f}", claim)])
            proof = f"The bill asks for ${billed:,.2f}, but your insurance statement says your share is ${resp:,.2f}."
            if line_total:
                proof += f" ${line_total:,.2f} of the difference is line errors, reported separately."
            r.finding("insurance", "Insurance wasn’t applied", ins_gap, [proof], claim)
        else:
            r.step("insurance", "Compare the bill with the insurance statement", "clear", "The bill matches your insurance statement")
    else:
        r.step("read", "Read the bill", "running")
        r.step("read", "Read the bill", "ok",
               f"{bill['provider']} bills ${billed:,.0f}; no insurance statement, so you're self-pay",
               [(f"Bill: ${billed:,.2f} due by {bill.get('due_date', '?')}", acct)])
        r.step("insurance", "Check for insurance", "clear",
               "Uninsured: your share is the bill's own charges, so each line is checked instead")

    r.step("lines", "Check the bill's lines", "running")
    checked = ([(f"{review['compared']} line{'s' if review['compared'] != 1 else ''} compared with the hospital's "
                 f"posted prices", f"{review['table']} price file")] if review["table"] else [])
    checked += [(f"Line {s['line']} ({s['code']}) not compared: {s['why']}", "unit check") for s in review["skipped"]]
    if corrections:
        r.step("lines", "Check the bill's lines", "flag",
               f"{len(corrections)} line error{'s' if len(corrections) > 1 else ''}: ${line_total:,.0f} too high",
               [(c["detail"], c["source"]) for c in corrections] + checked)
        for c in corrections:
            title = (f"{c['code']} billed twice" if c["kind"] == "duplicate_line"
                     else f"{c['code']} charged above the hospital's posted price")
            r.finding(f"{c['kind']}-{c['line']}", title, c["amount"], [c["detail"]], c.get("url") or c["source"])
    else:
        r.step("lines", "Check the bill's lines", "clear", "No repeated lines or charges above posted prices", checked)

    r.step("bank", f"Pull your bank history ({bank_name})", "running")
    txns = await bank.transactions(account_id)
    txns_ready.set_result(txns)
    dates = sorted(t["date"][:10] for t in txns if t.get("date"))
    notice = getattr(bank, "notice", "")
    if notice:  # no Capital One account for this patient, or Nessie didn't answer: sample data, said plainly
        bank_label = getattr(bank, "label", bank_label)
        bank_name = bank_label.split(" (")[0]
        bus.emit(cid, "warning", "auditor", text=notice)
    r.step("bank", f"Pull your bank history ({bank_name})", "ok",
           (f"{notice} · " if notice else "")
           + f"{len(txns)} transactions" + (f", {dates[0]} to {dates[-1]}" if dates else ""))

    rec = finance.reconcile(bill, eob, txns, corrections=corrections)
    missed = rec["uncredited_payments"]
    r.step("payments", f"Look for payments to {bill['provider']}", "running")
    for p in missed:
        bus.emit(cid, "evidence", "auditor", label="Payment hospital may not see",
                 value=f"${p['not_on_bill']:,.2f} on {p['date'][:10]}", source=f"{bank_label} · txn {p['id']}")
    credited = [(f"${p['amount']:,.2f} on {p['date'][:10]}: already credited on the bill",
                 f"{bank_name} · txn {p['id']}") for p in rec["credited_payments"]]
    if missed:
        proof = [(f"${p['amount']:,.2f} on {p['date'][:10]}: {p.get('merchant') or p.get('description') or 'payment'}",
                  f"{bank_name} · txn {p['id']}") for p in missed]
        r.step("payments", f"Look for payments to {bill['provider']}", "flag",
               f"You already paid ${rec['uncredited_paid']:,.0f} and the bill doesn’t show it", proof + credited)
        for p in missed:
            part = "" if p["not_on_bill"] == p["amount"] else f" (${p['not_on_bill']:,.2f} of it isn't credited)"
            r.finding(f"paid-{p['id']}", "A payment you made was missed", p["not_on_bill"],
                      [f"Your bank shows ${p['amount']:,.2f} paid on {p['date'][:10]}{part}; the bill doesn’t show it."],
                      f"{bank_name} · txn {p['id']}")
    elif rec["credited_payments"]:
        r.step("payments", f"Look for payments to {bill['provider']}", "clear",
               f"The bill already credits your ${rec['paid']:,.0f} in payments", credited)
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

    med = medical_step(r, case, txns, eob, bank_name)

    r.step("balance", "Work out what you actually owe", "running")
    r.step("balance", "Work out what you actually owe", "ok", _balance_text(rec))
    bus.emit(cid, "reconciliation", "auditor", **{k: rec[k] for k in ("lines", "owed", "billed_due", "overbilled_by")})
    bus.emit(cid, "balance", "auditor", amount=rec["owed"], label="Actually owed")
    owed_ready.set_result({"owed": rec["owed"], "responsibility": rec["patient_responsibility"], "paid": rec["paid"],
                           "insured": insured})

    done = summary(rec)
    r.finish(done["result"], done["note"])
    return {"reconciliation": rec, "transactions": txns, "medical": med}


MEDICAL_LABEL = "Add up your medical spending (last 12 months)"


def medical_step(r: Report, case: dict, txns: list[dict], eob: dict, bank_name: str) -> dict:
    """The bank's last 12 months of medical purchases (pharmacies, clinics, doctors), apart from payments toward
    this bill. Hospitals' high-medical-cost rules count them (the Bright Data agent applies the rule)."""
    med = finance.medical_spending(txns, case["bill"], eob, today=finance._d(case["today"]) if case.get("today") else None)
    r.step("medical", MEDICAL_LABEL, "running")
    if not med["txns"]:
        r.step("medical", MEDICAL_LABEL, "clear", "No other medical spending in your bank history")
        return med
    n = len(med["txns"])
    proof = [(f"${t['amount']:,.2f} on {t['date'][:10]}: {t.get('merchant') or t.get('description') or 'purchase'}",
              f"{bank_name} · txn {t['id']}") for t in med["txns"]]
    r.step("medical", MEDICAL_LABEL, "ok",
           f"${med['total']:,.2f} in other medical bills since {med['since']} ({n} payment{'s' if n != 1 else ''})", proof)
    r.finding("medical", "Other medical bills in the last 12 months", 0,
              [f"Your bank shows ${med['total']:,.2f} paid to pharmacies, clinics and doctors since {med['since']}, "
               "apart from this bill. Hospitals' high-medical-cost rules count these toward your out-of-pocket costs.",
               *[f"{t} ({s})" for t, s in proof]],
              f"{bank_name} · {n} txn{'s' if n != 1 else ''}", value=f"${med['total']:,.0f} · 12 months")
    return med


def _balance_text(rec: dict) -> str:
    """The reconciliation lines as one sum: bill − each correction = owed."""
    parts, total = [], None
    for ln in rec["lines"]:
        if ln.get("total"):
            total = ln
        elif not parts:
            parts.append(f"${ln['amount']:,.0f}")
        elif round(ln["amount"], 2):
            what = ("insurance" if ln["label"].startswith("Insurance") else "paid" if ln["label"].startswith("Payment")
                    else "bill errors")
            parts.append(f"{'−' if ln['amount'] < 0 else '+'} ${abs(ln['amount']):,.0f} {what}")
    if len(parts) == 1:
        return (f"${rec['owed']:,.0f}: the bill matches your insurance statement" if rec["insured"]
                else f"${rec['owed']:,.0f}: the bill's charges check out")
    return " ".join(parts) + f" = ${total['amount']:,.0f}"

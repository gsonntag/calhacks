"""Decide agent: step 3, the plan, and step 5, what's next. Turns what the Review found (saved once as
case["facts"]), the patient's corrections ("This is wrong"), their strategy preferences (planner chat) and,
after each call, that call's scorecard (agents/scorecard.py) into a versioned plan: the money line, who to
call, every ask the findings back up that isn't done yet, and how it decided. Format: docs/plan-format.md.

The decision is plain code and recalculates from scratch on every change (milliseconds, no refetching).
Claude only writes the "How I decided" paragraph, turns chat requests into preference changes (which the
rules here check), and answers questions; it never produces a number.
"""
import json
import re
import time

from .. import config, finance, followups, llm
from ..events import bus
from . import scorecard
from .report import Report

AGENTS = {"auditor": "Nessie agent", "policy": "Bright Data agent", "decide": "Decide agent"}
KINDS = ("insurance", "bill_line", "payment", "discount", "refund", "plan", "supervisor", "hold", "case_number")
SKIPPABLE = ("plan", "hold", "supervisor")
CORRECTABLE_UNTIL = ("awaiting_call_approval", "done")  # no call is running (done: the plan for the next call)


def _fast() -> dict:
    """The fast model for short answers (Haiku takes no effort setting; see orchestrator.answer_status)."""
    model = config.MODEL_FAST
    opts = {"thinking": {"type": "disabled"}, "effort": None} if "haiku-5-5" in model else {"effort": "low"}
    return {"model": model, **opts}


def money(x: float) -> str:
    return f"${x:,.0f}"


# ---------------------------------------------------------------- saved facts

def save_facts(case: dict, audit: dict, pol: dict) -> dict:
    """Keep everything the Review fetched and found, so every later change recalculates without refetching."""
    hist = bus.history(case["id"])
    findings = {e["key"]: {k: e.get(k) for k in ("key", "agent", "title", "saved", "value", "proof", "source")}
                for e in hist if e["kind"] == "finding" and e.get("agent") in ("auditor", "policy")}
    steps = {e["key"]: {k: e.get(k) for k in ("key", "agent", "label", "status", "result", "proof")}
             for e in hist if e["kind"] == "step" and e.get("agent") in ("auditor", "policy") and e["status"] != "running"}
    rec = {k: v for k, v in audit["reconciliation"].items() if k != "lines"}
    keep = ("eligibility", "income", "plan_terms", "source", "origin", "target", "refund", "plan")
    p = {k: pol.get(k) for k in keep}
    p["policy"] = {k: pol["policy"].get(k) for k in ("tiers", "collections_quote", "apply_how") if k in pol["policy"]}
    case["facts"] = {"rec": rec, "pol": p, "findings": findings, "steps": steps}
    case.setdefault("corrections", [])
    case.setdefault("plan", {"prefs": {}, "versions": [], "approved_v": None, "chat": []})
    return case["facts"]


def wrong(case: dict) -> set[str]:
    return {c["finding"] for c in case.get("corrections") or [] if c.get("active")}


def latest(case: dict) -> dict | None:
    vs = (case.get("plan") or {}).get("versions") or []
    return vs[-1] if vs else None


def approved(case: dict) -> dict | None:
    """The version "Call now" approved, else the latest (approved from the phone link)."""
    p = case.get("plan") or {}
    v = p.get("approved_v")
    return next((x for x in p.get("versions", []) if x["v"] == v), None) or latest(case)


def round_of(case: dict) -> int:
    """Which call the plan is for: 1 before the first call, 2 after it, and so on."""
    return len(case.get("calls") or []) + 1


def call_results(case: dict) -> dict[str, dict]:
    """Each ask's latest mark across the calls so far: {ask id: {status, evidence, call, date, rep}}."""
    out: dict[str, dict] = {}
    for c in case.get("calls") or []:
        for x in c.get("scorecard") or []:
            out[x["id"]] = {**x, "call": c["n"], "date": c.get("date"), "rep": c.get("rep")}
    return out


def _after_call(asks: list[dict], results: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Round 2+: drop what's done, and reword what's left so the caller knows what happened last time.
    Returns (asks still to make, asks already done)."""
    left, done = [], []
    for a in asks:
        r = results.get(a["id"])
        if a["kind"] == "case_number":
            a.update(text="Get a reference number for this call and the rep's name",
                     goal="Get a reference number for this call and the rep's name; repeat it back to confirm.")
        elif r and r["status"] == "got":
            done.append({"id": a["id"], "kind": a["kind"], "text": a["text"], "call": r["call"], "evidence": r["evidence"]})
            continue
        elif r and r["status"] in ("later", "refused", "unclear"):
            when = f"the call on {followups.fmt(r['date'])}" + (f" with {r['rep']}" if r.get("rep") else "") if r.get("date") else "the last call"
            if r["status"] == "later":
                a.update(text=f"Check it was done: {a['text']}",
                         goal=f"On {when} billing promised this ({r['evidence']}). Ask whether it's done now; if not, "
                              f"get a date. {a['goal']}")
            elif r["status"] == "refused":
                a.update(text=f"Ask again, with a supervisor if needed: {a['text']}",
                         goal=f"On {when} the rep said no to this (“{r['evidence']}”). Restate the evidence once, then "
                              f"ask for a supervisor or a written review. {a['goal']}")
            else:
                a.update(text=f"Get a clear answer: {a['text']}", goal=f"{when[0].upper() + when[1:]} didn't settle this. {a['goal']}")
        if r and a["kind"] != "case_number":
            a["last"] = {k: r.get(k) for k in ("status", "label", "evidence", "call")}
        left.append(a)
    if all(a["kind"] in ("case_number", "supervisor") for a in left):  # nothing left to argue about
        left = [a for a in left if a["kind"] != "supervisor"]
    return left, done


def next_step(case: dict, asks: list[dict], results: dict[str, dict]) -> dict | None:
    """Round 2+: what happens next and when. None before the first call."""
    calls = case.get("calls") or []
    if not calls:
        return None
    last, pol = calls[-1], case["facts"]["pol"]
    open_asks = [a for a in asks if a["kind"] not in ("case_number", "supervisor")]
    status = {a["id"]: (a.get("last") or {}).get("status") for a in open_asks}
    options = []
    for a in open_asks:
        if status[a["id"]] != "refused":
            continue
        if a["kind"] == "discount" and pol["policy"].get("apply_how"):
            options.append(f"Apply for financial assistance yourself: {pol['policy']['apply_how']}")
        elif a["kind"] in ("insurance", "bill_line", "payment", "refund"):
            options.append("Send billing a written dispute with the insurance statement and bank record attached")
        elif a["kind"] == "hold":
            options.append("Ask in writing for collections to be paused while this is reviewed")
    later = [a for a in open_asks if status[a["id"]] == "later"]
    unsettled = [a for a in open_asks if status[a["id"]] in ("refused", "unclear", None)]
    due = last.get("follow_up_date")
    if not open_asks:
        return {"kind": "done", "when": due, "options": [],
                "text": "Everything on the plan was done on the call. Nothing to call about"
                        + (f"; I'll check by {followups.fmt(due)} that the corrected bill arrived." if due else ".")}
    if later and not unsettled:
        return {"kind": "wait", "when": due, "options": options,
                "text": f"Billing promised {len(later)} of these for later. Best to call after {followups.fmt(due)}, "
                        "when they said it'd be done, to check it was." if due else
                        f"Billing promised {len(later)} of these for later. Call back to check they were done."}
    n = len(unsettled)
    return {"kind": "call", "when": None, "options": options,
            "text": f"{n} ask{'s' if n != 1 else ''} still need{'s' if n == 1 else ''} an answer: call again now."
                    + (f" {len(later)} more {'were' if len(later) != 1 else 'was'} promised for later; I'll check those on the same call."
                       if later else "")}


# ---------------------------------------------------------------- the rules

def compute(case: dict) -> dict:
    """One plan from the saved facts, corrections and preferences. Pure: no events, no I/O."""
    f, prefs, bad = case["facts"], case["plan"]["prefs"], wrong(case)
    rec, pol, finds = f["rec"], f["pol"], f["findings"]
    bill, eob = case["bill"], case.get("eob") or {}
    acct = f"bill · acct {bill.get('account_number', '?')}"
    claim = f"{eob.get('insurer', 'insurance statement')} · claim {eob.get('claim_number', '?')}"

    billed, eob_share = float(rec["billed_due"]), float(rec["patient_responsibility"])
    ins_wrong = "auditor:insurance" in bad
    line_errors = [c for c in rec.get("line_errors") or [] if f"auditor:{c['kind']}-{c['line']}" not in bad]
    line_total = round(sum(c["amount"] for c in line_errors), 2)
    share = billed if ins_wrong else eob_share
    if not rec.get("insured", True):  # self-pay: a line marked wrong goes back into the share
        share = round(billed - line_total, 2)
    elif ins_wrong:
        share = round(billed - line_total, 2)
    ins_gap = round(billed - share - line_total, 2)  # what's left once the line errors are taken out
    payments = [p for p in rec["payments"] if f"auditor:paid-{p['id']}" not in bad]
    paid = round(sum(p["amount"] for p in payments), 2)
    elig, inc = pol["eligibility"], pol["income"]
    d = 0 if "policy:discount" in bad else elig["discount_pct"]
    owed = round(max(0.0, share - paid), 2)
    st = finance.settle(share, paid, d)
    target, refund = st["balance"], st["refund"]

    math_lines = [{"label": "Hospital says you owe", "amount": billed, "source": acct}]
    for c in line_errors:
        math_lines.append({"label": c.get("label", "Bill error"), "amount": -c["amount"], "source": c.get("source", acct),
                           "finding": f"auditor:{c['kind']}-{c['line']}"})
    if ins_gap > 0:
        math_lines.append({"label": "Insurance not applied", "amount": -ins_gap, "source": claim,
                           "finding": "auditor:insurance"})
    for p in payments:
        math_lines.append({"label": f"Payment you already made {p['date'][:10]}", "amount": -p["amount"],
                           "source": f"bank · txn {p['id']}", "finding": f"auditor:paid-{p['id']}"})
    math_lines.append({"label": "You owe", "amount": owed, "total": True})
    if d:
        math_lines.append({"label": "Free care under their policy" if d == 100 else f"{d:.0f}% discount under their policy",
                           "amount": -round(owed - target + refund, 2), "source": pol.get("source") or "",
                           "finding": "policy:discount"})
        math_lines.append({"label": "They owe you back" if refund else "After the discount",
                           "amount": refund if refund else target, "total": True})

    asks, skipped = [], []

    def ask(kind: str, text: str, goal: str, amount: float = 0, value: str = "", findings: list[str] | None = None,
            basis: str = "finding", **extra) -> None:
        asks.append({"id": kind, "kind": kind, "money": round(amount, 2), "value": value or (f"−{money(amount)}" if amount else ""),
                     "text": text, "goal": goal, "findings": findings or [], "basis": basis, **extra})

    for c in line_errors:
        what = (f"line {c['line']} bills {c['code']} a second time" if c["kind"] == "duplicate_line"
                else f"line {c['line']} charges {c['code']} above the hospital's posted price")
        ask("bill_line", f"Remove the error on your bill: {what} ({money(c['amount'])})",
            f"Get the bill corrected: {c['detail']} Source: {c['source']}.",
            c["amount"], findings=[f"auditor:{c['kind']}-{c['line']}"], id=f"bill_line-{c['line']}")
    if ins_gap > 0:
        ask("insurance", f"Apply your insurance: the statement says you owe {money(eob_share)}, not {money(billed)}",
            f"Get the insurance adjustment applied: the EOB says the patient owes {money(eob_share)}, not {money(billed)}.",
            ins_gap, findings=["auditor:insurance"])
    if paid:
        ask("payment", f"Show the {money(paid)} you already paid, bringing it to {money(owed)}",
            f"Get the {money(paid)} already paid applied (give date and reference when asked).",
            paid, findings=[f"auditor:paid-{p['id']}" for p in payments])
    if d:
        what = "free care" if d == 100 else f"the {d:.0f}% charity-care discount"
        ask("discount", f"Ask for {what} under their own policy, bringing the bill to {money(target)}",
            f"Request a financial-assistance review under the hospital's own policy: the patient qualifies for "
            f"{'free care' if d == 100 else f'a {d:.0f}% discount'}, which brings the balance to {money(target)}. Quote the policy.",
            owed - target, findings=["policy:discount"])
        if refund:
            ask("refund", f"Get the {money(refund)} you overpaid refunded",
                f"Ask for the {money(refund)} the patient already paid to be refunded once the assistance is applied.",
                refund, findings=["policy:discount", *[f"auditor:paid-{p['id']}" for p in payments]])

    plan = None
    if not d and target > 0:
        terms = pol.get("plan_terms") or {}
        if "policy:plan" not in finds:
            skipped.append({"kind": "plan", "text": "Payment plan",
                            "why": "The Bright Data agent didn't find payment-plan terms to back it up"})
        elif "policy:plan" in bad:
            skipped.append({"kind": "plan", "text": "Payment plan", "why": "You said the payment-plan finding is wrong"})
        else:
            plan = finance.payment_plan(target, inc["monthly"], policy_cap_pct=terms.get("max_pct_monthly_income"),
                                        max_monthly=prefs.get("max_monthly"), monthly_bills=inc.get("bills_monthly"))
            why = " Their own billing policy says payment plans are interest-free." if terms.get("interest_free") else ""
            ask("plan", f"Set up an interest-free payment plan for the {money(target)}: "
                        f"{plan['text']}, as their own billing policy allows",
                f"Once the balance is corrected to {money(target)}, set up an interest-free payment plan: "
                f"{finance.plan_said(plan)}.{why} Don't give card details; "
                f"the patient sets up the payments.",
                value=plan["text"], findings=["policy:plan"], plan=plan)
    results = call_results(case)
    refused_before = any(r["status"] == "refused" for r in results.values())
    if prefs.get("if_refused") == "supervisor" or refused_before:
        ask("supervisor", "If they say no to anything, ask for a supervisor",
            "If the rep refuses any request, restate the evidence once, then politely ask to speak with a supervisor.",
            basis="you asked" if prefs.get("if_refused") == "supervisor" else "they said no last time")
    ask("hold", "Pause collections while it's corrected",
        "Ask for the account to be placed on hold so it isn't sent to collections while this is reviewed.",
        basis="standard")
    ask("case_number", "Get a case number and the rep's name",
        "Get a case or reference number and the rep's name; repeat the case number back to confirm.", basis="standard")
    done: list[dict] = []
    if case.get("calls"):
        asks, done = _after_call(asks, results)

    # biggest money first; then the patient's "X first"; then what they asked to leave off
    asks.sort(key=lambda a: (-a["money"], KINDS.index(a["kind"])))
    first = [k for k in prefs.get("first") or [] if k in KINDS]
    asks.sort(key=lambda a: first.index(a["kind"]) if a["kind"] in first else len(first))
    for a in [a for a in asks if a["kind"] in (prefs.get("skip") or []) and a["kind"] in SKIPPABLE]:
        asks.remove(a)
        skipped.append({"kind": a["kind"], "text": a["text"], "why": "You asked me to leave this off the call"})

    nxt = next_step(case, asks, results)
    out = {"round": round_of(case), "money": {"billed": billed, "owed": owed, "target": target, "refund": refund,
                                              "saved": round(billed - target + refund, 2)},
           "math": math_lines, "call": who_to_call(case, d), "asks": asks, "skipped": skipped,
           "decided": decided(case, share, paid, d, owed, target, refund, plan, bad),
           "prefs": json.loads(json.dumps(prefs)), "corrections": [c["id"] for c in case["corrections"] if c.get("active")]}
    if nxt:  # after a call: what's done, what that call got, and what happens next
        last = case["calls"][-1]
        out.update(done=done, next=nxt, after_call={"n": last["n"], "date": last.get("date"), "rep": last.get("rep"),
                                                    "case_number": last.get("case_number"),
                                                    "marks": last.get("scorecard") or []})
        out["decided"].update(path=nxt["text"], last_call=[f"{x['label']}: {x['text']}" + (f" (“{x['evidence']}”)" if x["source"] == "rep said" else f" ({x['evidence']})")
                                                           for x in last.get("scorecard") or []])
        if nxt["options"]:
            out["decided"]["fallback"] += ". Other routes: " + "; ".join(nxt["options"])
        out["decided"]["explanation"] = f"{nxt['text']} {out['decided']['call']}."
    return out


def who_to_call(case: dict, discount: float) -> dict:
    """Collections if the bill names an agency, financial assistance if a discount applies, else billing.
    The number comes from the bill (intake reads it); never guessed."""
    bill, hosp = case["bill"], case["hospital"]
    if bill.get("collection_agency"):
        return {"who": "collections", "department": bill["collection_agency"], "phone": bill.get("collection_phone") or None,
                "why": "The bill says the account is with a collection agency, so they hold it now"}
    dept = hosp.get("billing_phone_label") or "billing"
    if discount:
        return {"who": "financial_assistance", "department": f"{hosp['name']} financial assistance (through {dept})",
                "phone": bill.get("billing_phone") or None,
                "why": "You qualify for a discount, which their financial-assistance team applies"}
    return {"who": "billing", "department": f"{hosp['name']} {dept}", "phone": bill.get("billing_phone") or None,
            "why": "No discount applies, so billing can correct the balance and set up the plan"}


def decided(case: dict, share: float, paid: float, d: float, owed: float, target: float, refund: float,
            plan: dict | None, bad: set[str]) -> dict:
    f = case["facts"]
    rec, pol, finds = f["rec"], f["pol"], f["findings"]
    elig, inc = pol["eligibility"], pol["income"]
    nessie, bright = [], []
    for k, x in finds.items():
        line = f"{x['title']} ({x['value']})" + (" — you said this is wrong, so I left it out" if k in bad else "")
        (nessie if x["agent"] == "auditor" else bright).append(line)
    if not rec["payments"]:
        nessie.append("No payments to the hospital in your bank history")
    annual = inc.get("annual") or inc.get("monthly", 0) * 12
    hh = elig.get("household_size") or case["patient"].get("household_size", 1)
    if inc.get("check"):  # the patient told us their income: the checked figure, then what the bank said about it
        bright.insert(0, f"Your income is about {money(elig.get('income_annual') or annual)}/yr before taxes, "
                         f"{elig.get('fpl_pct', 0):.0f}% of the poverty line (household of {hh})")
        bright.insert(1, inc["check"]["line"])
    else:
        bright.insert(0, f"Your income is about {money(annual)}/yr, {elig.get('fpl_pct', 0):.0f}% of the poverty line "
                         f"(household of {hh})")
    if not elig["discount_pct"]:
        bright.append("No discount tier in their policy fits your income and bills")
    math = [f"{money(rec['billed_due'])} billed" + (f" − {money(rec['billed_due'] - share)} insurance" if rec["billed_due"] > share else "")
            + (f" − {money(paid)} paid" if paid else "") + f" = {money(owed)} owed"]
    if d:
        math.append(f"{money(share)} share × {100 - d:.0f}% = {money(share * (1 - d / 100))}"
                    + (f" − {money(paid)} paid" if paid else "")
                    + (f" → they owe you {money(refund)}" if refund else f" = {money(target)}"))
    if plan:
        math.append(f"{money(target)} as {plan['text']}: {plan['reason']}")
    if d:
        path = f"Fix the balance, then {'free care' if d == 100 else f'a {d:.0f}% discount'} under their own policy"
    elif plan:
        path = "Fix the balance, then an interest-free payment plan under their billing policy"
    elif target > 0:
        path = "Fix the balance; no discount or payment plan is backed by a finding"
    else:
        path = "Fix the balance: nothing is left to pay"
    call = who_to_call(case, d)
    fallback = ("If billing says no: restate the evidence once, ask for a supervisor, then ask for a written review "
                "and a case number" + (f". You can also apply directly: {pol['policy']['apply_how']}"
                                      if pol["policy"].get("apply_how") else ""))
    return {"nessie": nessie, "brightdata": bright, "math": math, "path": path,
            "call": f"{call['department']}: {call['why']}", "fallback": fallback,
            "explanation": f"{path}. {call['why']}."}


def diff(old: dict | None, new: dict) -> tuple[list[str], list[str]]:
    """What changed since the previous version, in plain words, and which ask ids to highlight."""
    if not old:
        return [], []
    out = []
    om, nm = old["money"], new["money"]
    if om["target"] != nm["target"]:
        out.append(f"You owe {money(om['target'])} → {money(nm['target'])}")
    if om["refund"] != nm["refund"]:
        out.append(f"Refund {money(om['refund'])} → {money(nm['refund'])}")
    oa = {a["id"]: a for a in old["asks"]}
    na = {a["id"]: a for a in new["asks"]}
    changed = []
    for i, a in na.items():
        if i not in oa:
            out.append(f"Added: {a['text']}")
            changed.append(i)
        elif (oa[i]["money"], oa[i]["value"], oa[i]["text"]) != (a["money"], a["value"], a["text"]):
            out.append(f"{a['text']}" if not oa[i]["value"] or not a["value"] else f"{i.replace('_', ' ').capitalize()}: {oa[i]['value']} → {a['value']}")
            changed.append(i)
    for i, a in oa.items():
        if i not in na:
            out.append(f"Dropped: {a['text']}")
    order_o = [i for i in oa if i in na]
    order_n = [i for i in na if i in oa]
    if order_o != order_n:
        out.append(f"New order: {na[order_n[0]]['text'].split(':')[0]} first")
        changed.append(order_n[0])
    if old["call"]["who"] != new["call"]["who"]:
        out.append(f"Calling {new['call']['department']} instead")
    return out, list(dict.fromkeys(changed))


# ---------------------------------------------------------------- versions

EXPLAIN_SYSTEM = """You are BillBack's Decide agent. In 2-4 short plain sentences, explain to the patient why
their plan is what it is: which findings it uses (and any they said were wrong), the path chosen, and who
we'll call. If the PLAN has "after_call", start with what that call got and what happens next ("next").
Use ONLY the numbers in the PLAN JSON, copied exactly. No new facts, no legal advice, no lists."""


async def explain(case: dict, version: dict) -> str:
    """Claude's plain-language "How I decided"; the template stays when Claude is off or fails."""
    if not llm.available():
        return version["decided"]["explanation"]
    view = {k: version[k] for k in ("money", "asks", "skipped", "decided", "call", "done", "next", "after_call") if k in version}
    try:
        resp = await llm.create(system=EXPLAIN_SYSTEM, max_tokens=1500, **_fast(),
                                messages=[{"role": "user", "content": json.dumps(view, default=str)}])
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or version["decided"]["explanation"]
    except Exception as e:
        print("decide explanation LLM error, using template:", type(e).__name__)
        return version["decided"]["explanation"]


async def new_version(case: dict, kind: str, text: str) -> dict:
    """Recalculate everything, number it, say what changed, emit it. A new version resets an approval the
    call hasn't used yet."""
    p = case["plan"]
    ver = compute(case)
    # changes are against this call's previous version; a plan for a new call starts fresh
    prev = next((x for x in reversed(p["versions"]) if x.get("round", 1) == ver["round"]), None)
    ver["decided"]["explanation"] = await explain(case, ver)
    changes, changed = diff(prev, ver)
    ver = {"v": len(p["versions"]) + 1, "ts": time.time(), "reason": {"kind": kind, "text": text}, **ver,
           "changes": changes, "changed": changed}
    p["versions"].append(ver)
    if p.get("approved_v") is not None and case["status"] == "awaiting_call_approval":
        bus.emit(case["id"], "plan_approval_reset", "decide", v=p["approved_v"], reason=text)
        p["approved_v"] = None
    case["result"].update({"target": ver["money"]["target"], "refund": ver["money"]["refund"]})
    bus.emit(case["id"], "plan_version", "decide", plan=ver)
    return ver


def report(case_id: str, rnd: int = 1) -> Report:
    """Round 1 is "decide" (step 3); after call n it's "decide:<n+1>" (step 5), so each run keeps its own panel."""
    if rnd == 1:
        return Report(case_id, "decide", AGENTS["decide"], "Both agents’ findings → the plan and who to call")
    return Report(case_id, f"decide:{rnd}", AGENTS["decide"], "What the call got → what’s left and what’s next")


async def next_round(case: dict) -> dict:
    """After a call (its scorecard is on case["calls"][-1]): the same rules, run again with the call's result,
    make the plan for what's left. Step 5 shows it; "Call again" uses it."""
    rnd, call = round_of(case), case["calls"][-1]
    r = report(case["id"], rnd)
    r.start("Reading the call")
    marks = call.get("scorecard") or []
    r.step("call", f"Read call {call['n']}", "running")
    r.step("call", f"Read call {call['n']}", "ok", scorecard.summary(marks),
           [(f"{x['label']}: {x['text']}", x["evidence"]) for x in marks])
    ver = compute(case)
    r.step("left", "Work out what’s left", "running")
    r.step("left", "Work out what’s left", "ok",
           f"{len(ver['done'])} done · {sum(1 for a in ver['asks'] if a['kind'] != 'case_number')} still open",
           [(a["text"], (a.get("last") or {}).get("label") or "new") for a in ver["asks"]])
    r.step("next", "Choose what’s next", "running")
    r.step("next", "Choose what’s next", "ok", ver["next"]["text"], [(o, "other route") for o in ver["next"]["options"]])
    r.step("explain", "Write how I decided", "running")
    who = f" with {call['rep']}" if call.get("rep") else ""
    out = await new_version(case, "call_result", f"After call {call['n']}{who}: {scorecard.summary(marks)}")
    r.step("explain", "Write how I decided", "ok", "Done")
    r.finish(scorecard.summary(marks), f"plan v{out['v']} for call {rnd}")
    return out


async def run(case: dict) -> dict:
    """v1, at the end of the Review. The steps are the rules in order, so the panel shows real work."""
    r = report(case["id"])
    r.start("Building your plan")
    f = case["facts"]
    r.step("gather", "Gather both agents’ findings", "running")
    r.step("gather", "Gather both agents’ findings", "ok", f"{len(f['findings'])} findings with proof",
           [(x["title"], AGENTS.get(x["agent"], x["agent"])) for x in f["findings"].values()])
    ver = compute(case)
    m = ver["money"]
    r.step("math", "Work out what you owe", "running")
    r.step("math", "Work out what you owe", "ok", " · ".join(ver["decided"]["math"]))
    r.step("path", "Choose the path", "running")
    r.step("path", "Choose the path", "ok", ver["decided"]["path"])
    r.step("asks", "List every ask the findings back up", "running")
    r.step("asks", "List every ask the findings back up", "ok", f"{len(ver['asks'])} asks, biggest money first",
           [(a["text"], ", ".join(f["findings"][k]["title"] for k in a["findings"] if k in f["findings"]) or a["basis"])
            for a in ver["asks"]])
    r.step("call", "Pick who to call", "running")
    r.step("call", "Pick who to call", "ok", ver["call"]["department"], [(ver["call"]["why"], "rules")])
    r.step("explain", "Write how I decided", "running")
    out = await new_version(case, "initial", "First plan from the Review")
    r.step("explain", "Write how I decided", "ok", "Done")
    r.finish(f"{money(m['billed'])} → {money(m['target'])}", f"plan v{out['v']}")
    return out


# ---------------------------------------------------------------- corrections ("This is wrong")

class Refused(Exception):
    pass


def _editable(case: dict) -> None:
    if not case.get("facts") or not latest(case):
        raise Refused("The plan isn't ready yet")
    if case["status"] not in CORRECTABLE_UNTIL:
        raise Refused("The call has already started, so the plan can't change now")


async def correct(case: dict, finding: str, note: str = "") -> dict:
    _editable(case)
    x = case["facts"]["findings"].get(finding)
    if not x:
        raise Refused("No such finding")
    if finding in wrong(case):
        raise Refused("Already marked wrong")
    c = {"id": f"c{len(case['corrections']) + 1}", "finding": finding, "agent": x["agent"], "title": x["title"],
         "note": note.strip()[:500], "ts": time.time(), "active": True}
    case["corrections"].append(c)
    ver = await new_version(case, "correction", f"You said “{x['title']}” is wrong" + (f": {c['note']}" if c["note"] else ""))
    bus.emit(case["id"], "correction", "decide", correction=c, v=ver["v"])
    return {"correction": c, "plan": ver}


async def undo_correction(case: dict, cid: str) -> dict:
    _editable(case)
    c = next((c for c in case["corrections"] if c["id"] == cid and c["active"]), None)
    if not c:
        raise Refused("No such correction")
    c["active"] = False
    ver = await new_version(case, "correction", f"You undid “{c['title']}” is wrong")
    bus.emit(case["id"], "correction_undone", "decide", id=cid, finding=c["finding"], v=ver["v"])
    return {"plan": ver}


async def restore(case: dict, v: int) -> dict:
    """Go back to an older version's strategy; facts (and corrections) stay as they are now."""
    _editable(case)
    old = next((x for x in case["plan"]["versions"] if x["v"] == v), None)
    if not old:
        raise Refused("No such version")
    case["plan"]["prefs"] = json.loads(json.dumps(old["prefs"]))
    return await new_version(case, "restore", f"Back to v{v}’s choices")


def approve(case: dict, v: int) -> None:
    if case["status"] != "awaiting_call_approval":
        raise Refused(f"Nothing to approve while the case is {case['status']}")
    cur = latest(case)
    if not cur or cur["v"] != v:
        raise Refused("That isn't the latest plan: go back to it first")
    case["plan"]["approved_v"] = v
    bus.emit(case["id"], "plan_approved", "decide", v=v)


# ---------------------------------------------------------------- the planner chat

def check_prefs(case: dict, patch: dict) -> tuple[dict, list[str]]:
    """The rules every strategy change passes: returns the new prefs and what was refused, and why."""
    prefs = json.loads(json.dumps(case["plan"]["prefs"]))
    kinds = {a["kind"] for a in latest(case)["asks"]} | {s["kind"] for s in latest(case)["skipped"]}
    refused = []
    if patch.get("reset"):
        prefs = {}
    if patch.get("first"):
        first = [k for k in patch["first"] if k in kinds]
        refused += [f"There's no “{k}” ask on this plan" for k in patch["first"] if k not in kinds]
        if first:
            prefs["first"] = first
    for k in patch.get("skip") or []:
        if k in SKIPPABLE:
            prefs["skip"] = list(dict.fromkeys([*(prefs.get("skip") or []), k]))
        elif k == "case_number":
            refused.append("I always get a case number: the follow-ups depend on it")
        elif k in KINDS:
            refused.append(f"I can't leave off the {k.replace('_', ' ')} ask: it's money the findings show you don't owe. "
                           "If the finding is wrong, tap “This is wrong” on it in step 2")
    for k in patch.get("include") or []:
        prefs["skip"] = [s for s in prefs.get("skip") or [] if s != k]
    if "max_monthly" in patch and patch["max_monthly"] is not None:
        try:
            m = float(patch["max_monthly"])
        except (TypeError, ValueError):
            m = 0
        if 10 <= m <= 100_000:
            prefs["max_monthly"] = m
        else:
            refused.append("The monthly amount has to be between $10 and $100,000")
    if "if_refused" in patch:
        prefs["if_refused"] = "supervisor" if patch["if_refused"] == "supervisor" else None
    return {k: v for k, v in prefs.items() if v not in (None, [], "")}, refused


PLANNER_SYSTEM = """You are BillBack's planner (the Decide agent) talking with the patient about their call plan.
You get the plan (every ask with the findings behind it), all findings from both agents with their proof, the
math and the rules. Talk like a calm, clear friend: 1-4 short sentences, no lists, no markdown.

Questions ("why X?", "what does this mean?"): answer only from the data given, pointing at the exact finding,
number and source. If the data doesn't say, say you don't know.

Strategy change requests: call change_strategy in the same reply, right away; never ask the patient to
confirm first (every version is kept and they can go back). You can only change: which asks go first ("first"), leaving
the plan / collections-hold / supervisor asks off ("skip"; "include" puts one back), what the patient can pay
per month ("max_monthly"), asking for a supervisor if billing says no ("if_refused": "supervisor"), or
"reset" to the default strategy. Then say in one sentence what you changed; the plan updates on its own.
The plan in CASE DATA already includes every earlier change: only call change_strategy for a new change asked
for in the patient's latest message, never to repeat an earlier one.

Refuse, briefly and kindly, and explain why:
- changing facts (what they paid, owe, earn, household size, insurance): numbers come from the documents and
  bank; if a finding is wrong they tap "This is wrong" on it in step 2.
- any ask no finding backs (e.g. "waive the whole bill"): say which finding would be needed.
- agreeing to pay, giving card or bank details on the call, or hiding that the caller is an automated assistant.
- dropping the case-number ask; leaving off money the findings show they don't owe.
- anything unrelated to this bill.
Never write numbers that aren't in the data. Never claim you changed something you didn't."""

STRATEGY_TOOL = {"name": "change_strategy", "description": "Change the call strategy; the plan is recalculated by the rules",
                 "input_schema": {"type": "object", "properties": {
                     "first": {"type": "array", "items": {"type": "string", "enum": list(KINDS)}},
                     "skip": {"type": "array", "items": {"type": "string", "enum": list(KINDS)}},
                     "include": {"type": "array", "items": {"type": "string", "enum": list(SKIPPABLE)}},
                     "max_monthly": {"type": "number"},
                     "if_refused": {"type": "string", "enum": ["supervisor", "none"]},
                     "reset": {"type": "boolean"}}}}


def _context(case: dict) -> str:
    f, ver = case["facts"], latest(case)
    return json.dumps({"plan": {k: ver[k] for k in ("v", "round", "money", "math", "call", "asks", "skipped", "decided", "prefs",
                                                     "done", "next", "after_call") if k in ver},
                       "findings": f["findings"], "agent_checks": f["steps"],
                       "corrections": [c for c in case["corrections"] if c["active"]],
                       "rules": ["numbers come from code (finance.py), never from chat",
                                 "every money ask needs a finding that isn't marked wrong",
                                 "the caller never agrees to pay and always says it's an automated assistant",
                                 "payment plan only when no discount applies, under 4% of monthly income unless the "
                                 "patient sets what they can pay (our choice, not the hospital's)"]}, default=str)


FACT = re.compile(r"\b(i (only )?(owe|paid|earn|make)|my (income|household|insurance)|household size|i'?m (not )?insured|"
                  r"actually (owe|paid))\b", re.I)
PAY = re.compile(r"\b(agree to pay|pay (it|them|the bill) (now|today)|(give|use|charge) (them )?my (card|bank|account)|"
                 r"card (number|details)|bank details)\b", re.I)
HIDE = re.compile(r"\b(don'?t|do not|never) (say|tell|mention).{0,30}(automated|ai|robot|assistant)|pretend to be\b", re.I)
KIND_WORDS = {"refund": "refund", "insurance": "insurance", "payment": "payment", "discount": "discount",
              "free care": "discount", "plan": "plan", "hold": "hold", "collections": "hold", "supervisor": "supervisor",
              "case number": "case_number"}


def _offline_intent(msg: str) -> tuple[str, dict | str]:
    """Without Claude: a few patterns for the common requests; everything else is a question."""
    low = msg.lower()
    if PAY.search(msg) or HIDE.search(msg):
        return "refused", ("I can't do that: the caller never agrees to pay or gives card details, and always says "
                           "it's an automated assistant.")
    if FACT.search(msg):
        return "refused", ("I can't change facts in the chat: the numbers come from your documents and bank. If a "
                           "finding is wrong, tap “This is wrong” on it in step 2 and I'll redo the plan.")
    patch: dict = {}
    m = re.search(r"\$?\s?(\d[\d,]*)\s*(?:/|a|per|each)\s*(?:mo|month)", low)
    if m:
        patch["max_monthly"] = float(m.group(1).replace(",", ""))
    if re.search(r"(don'?t|do not|no|skip|leave off|without).{0,20}\b(payment )?plan\b", low):
        patch["skip"] = ["plan"]
    else:
        skip = re.search(r"(don'?t|do not|skip|leave off|drop|remove).{0,25}\b(hold|collections|insurance|payment|"
                         r"discount|free care|refund|supervisor|case number)\b", low)
        if skip:
            patch["skip"] = [KIND_WORDS.get(skip.group(2), skip.group(2).replace(" ", "_"))]
    first = re.search(r"\b(refund|insurance|payment|discount|free care|plan|hold|collections)\b\s+first|"
                      r"\bfirst\b.{0,15}\b(refund|insurance|payment|discount|free care|plan|hold|collections)\b", low)
    if first:
        patch["first"] = [KIND_WORDS[first.group(1) or first.group(2)]]
    if patch.get("skip") == ["supervisor"]:
        patch = {**patch, "skip": [], "if_refused": None}
    elif "supervisor" in low:
        patch["if_refused"] = "supervisor"
    if re.search(r"\b(reset|start the plan over|default plan)\b", low):
        patch["reset"] = True
    return ("change", patch) if patch else ("question", "")


def _offline_answer(case: dict, msg: str) -> str:
    ver = latest(case)
    d = ver["decided"]
    low = msg.lower()
    for a in ver["asks"]:
        if a["kind"] in low or a["kind"].replace("_", " ") in low:
            src = [case["facts"]["findings"][k] for k in a["findings"] if k in case["facts"]["findings"]]
            if src:
                x = src[0]
                return f"“{a['text']}” comes from the {AGENTS[x['agent']]}'s finding “{x['title']}”: {' '.join(x['proof'])} (source: {x['source']})."
            return f"“{a['text']}” is a standard ask on every call ({a['basis']})."
    return f"{d['path']}. The math: {' '.join(d['math'])}. {d['call']}."


async def chat(case: dict, message: str, via: str = "") -> dict:
    """One planner-chat turn. Questions get an answer; change requests become a new version via the rules.
    via="billback": BillBack handed the patient's message over from the agent chat."""
    if not latest(case):
        raise Refused("The plan isn't ready yet")
    msg = message.strip()[:1000]
    p = case["plan"]
    rnd = round_of(case)
    p["chat"].append({"role": "user", "text": msg, "ts": time.time(), "round": rnd, **({"via": via} if via else {})})
    bus.emit(case["id"], "planner_message", "decide", role="user", text=msg, round=rnd, **({"via": via} if via else {}))
    kind, reply, patch = "question", "", None
    if llm.available():
        history = [{"role": "assistant" if m["role"] == "planner" else "user",
                    "content": m["text"] + (f" [this made plan v{m['v']}]" if m.get("v") else "")} for m in p["chat"][-9:-1]]
        try:
            resp = await llm.create(system=PLANNER_SYSTEM, max_tokens=2000, **_fast(),
                                    tools=[STRATEGY_TOOL], messages=[
                                        {"role": "user", "content": f"CASE DATA (JSON):\n{_context(case)}"},
                                        {"role": "assistant", "content": "Got it. I'll answer only from this data."},
                                        *history, {"role": "user", "content": msg}])
            reply = " ".join(b.text for b in resp.content if b.type == "text").strip()
            use = next((b for b in resp.content if b.type == "tool_use"), None)
            if use:
                kind, patch = "change", dict(use.input) if isinstance(use.input, dict) else {}
                if patch.get("if_refused") == "none":
                    patch["if_refused"] = None
        except Exception as e:
            print("planner chat LLM error, using rules:", type(e).__name__)
            reply = ""
    if not reply and patch is None:
        kind, got = _offline_intent(msg)
        if kind == "change":
            patch = got
        else:
            reply = got or _offline_answer(case, msg)
    ver = None
    if kind == "change":
        try:
            _editable(case)
        except Refused as e:
            kind, reply, patch = "refused", str(e), None
    if kind == "change":
        prefs, refused = check_prefs(case, patch or {})
        if prefs != p["prefs"]:
            p["prefs"] = prefs
            ver = await new_version(case, "strategy", f"You said: “{msg[:120]}”")
            # the rules' own account of what changed, never the model's (it writes before the change is checked)
            reply = f"Done: plan v{ver['v']}. " + ("; ".join(ver["changes"]) + "." if ver["changes"] else "The plan is updated.")
        elif not refused:
            kind, reply = "question", reply or "That's already how the plan works, so nothing changed."
        if refused:
            reply = (reply + " " if ver else "") + " ".join(r.rstrip(".") + "." for r in refused)
            kind = "change" if ver else "refused"
    out = {"role": "planner", "text": reply, "kind": kind, "ts": time.time(), "round": rnd, **({"v": ver["v"]} if ver else {})}
    p["chat"].append(out)
    bus.emit(case["id"], "planner_message", "decide", role="planner", text=reply, type=kind, round=rnd,
             **({"v": ver["v"]} if ver else {}))
    return {"reply": reply, "kind": kind, **({"v": ver["v"], "plan": ver} if ver else {})}


# ---------------------------------------------------------------- "Ask" under a finding

ASK_SYSTEM = """You are the BillBack app agent that made this finding, answering the patient's question about
it. Answer ONLY from the EVIDENCE JSON (your finding, your own checks with their proof, and the records behind
them). Answer the actual question and explain the numbers; don't just restate the finding, which the patient
can already see. 2-4 short plain sentences, and name the source you used. If the evidence doesn't answer it, say "I don't know from what I
checked" and what would be needed. Never invent numbers. Asking never changes anything."""


def _records(case: dict, agent: str) -> dict:
    """An app agent's own checks plus the full records behind them, so it can explain the numbers (not just
    repeat a finding)."""
    f = case["facts"]
    evidence = {"my_checks": [s for s in f["steps"].values() if s["agent"] == agent]}
    if agent == "auditor":
        rec = f["rec"]
        evidence.update(bill={k: case["bill"].get(k) for k in ("provider", "account_number", "service_date",
                                                              "amount_due", "due_date")},
                        insurance_statement=case.get("eob") or "none: the patient is uninsured",
                        payments_found_in_bank=rec.get("payments"), duplicate_bank_charges=rec.get("duplicates"),
                        math={k: rec.get(k) for k in ("billed_due", "patient_responsibility", "insurance_adjustment",
                                                      "paid", "owed")})
    if agent == "policy":
        pol = f["pol"]
        evidence.update(policy_quotes=[t.get("quote") for t in pol["policy"].get("tiers") or [] if t.get("quote")],
                        eligibility=pol.get("eligibility"), plan_terms=pol.get("plan_terms"),
                        income={k: (pol.get("income") or {}).get(k)
                                for k in ("annual", "monthly", "source", "stated", "check")})
    return evidence


async def _answer(system: str, evidence: dict, question: str, what: str) -> tuple[str, str]:
    if llm.available():
        try:
            resp = await llm.create(system=system, max_tokens=1500, **_fast(),
                                    messages=[{"role": "user", "content": f"EVIDENCE (JSON):\n{json.dumps(evidence, default=str)}"
                                                                          f"\n\nPatient asks: {question}"}])
            answer = "".join(b.text for b in resp.content if b.type == "text").strip()
            if answer:
                return answer, "claude"
        except Exception as e:
            print(f"{what} LLM error, using evidence:", type(e).__name__)
    return "", "evidence"


async def answer_finding(case: dict, key: str, question: str) -> dict:
    f = case.get("facts")
    if not f or key not in f["findings"]:
        raise Refused("No such finding")
    x = f["findings"][key]
    agent = x["agent"]
    answer, src = await _answer(ASK_SYSTEM, {"finding": x, **_records(case, agent)}, question, "finding ask")
    if not answer:
        answer = f"What I found: {' '.join(x['proof'])} Source: {x['source']}."
    sources = [x["source"]] if x.get("source") else []
    bus.emit(case["id"], "finding_qa", agent, finding=key, question=question, answer=answer, sources=sources)
    return {"answer": answer, "agent": agent, "agent_name": AGENTS.get(agent, agent), "sources": sources, "source": src}


# ---------------------------------------------------------------- the agent chat: one Review agent, all it checked

AGENT_ASK_SYSTEM = """You are one of BillBack's app agents, answering the patient's question about what you
checked on their hospital bill. Answer ONLY from the EVIDENCE JSON (all your findings, your own checks with their
proof, and the records behind them). Answer the actual question and explain the numbers. 2-4 short plain
sentences, and name the source you used. If the evidence doesn't answer it, say "I don't know from what I
checked" and which other agent might (BillBack knows the whole case; the planner owns the call plan). Never
invent numbers. Asking never changes anything."""


async def answer_agent(case: dict, agent: str, question: str) -> dict:
    """The Nessie or Bright Data agent answers from everything it checked (not one finding)."""
    if agent not in ("auditor", "policy"):
        raise Refused("No such agent")
    f = case.get("facts")
    if not f:
        raise Refused(f"The {AGENTS[agent]} is still checking. Ask again once the plan is ready.")
    mine = [x for x in f["findings"].values() if x["agent"] == agent]
    answer, src = await _answer(AGENT_ASK_SYSTEM, {"my_findings": mine, **_records(case, agent)}, question, "agent ask")
    if not answer:
        answer = ("What I found: " + " ".join(f"{x['title']} ({x['value']}): {' '.join(x['proof'])}" for x in mine)
                  if mine else "I found nothing to correct in what I checked.")
    sources = list(dict.fromkeys(x["source"] for x in mine if x.get("source")))
    bus.emit(case["id"], "agent_qa", agent, question=question, answer=answer, sources=sources)
    return {"answer": answer, "agent": agent, "agent_name": AGENTS[agent], "sources": sources, "source": src}

"""The Cases list at a glance: which of the 5 steps each case is on, whether it needs the patient, and
archive / restore / delete with a guard for cases that are still running.

Steps: 1 Read bill · 2 Review · 3 Plan · 4 Call · 5 Resolved.
Attention (how the list sorts): needs_you > running > waiting (on the hospital) > done.
"""
from datetime import date

from . import events, orchestrator
from .events import bus
from .harness import gate

STEPS = ("Read bill", "Review", "Plan", "Call", "Resolved")
ATTENTION = ("needs_you", "running", "waiting", "done")
LABEL = {"needs_you": "Needs you", "running": "Running", "waiting": "Waiting on hospital", "done": "Done"}
# an agent or the call is working right now (awaiting_call_approval is not: it waits on the patient)
RUNNING = {"created": 1, "reviewing": 2, "deciding": 3, "calling": 4, "planning": 5}


class Blocked(RuntimeError):
    """The action isn't allowed right now; the message is what the patient sees."""


class NeedsConfirm(RuntimeError):
    """Allowed, but only with an explicit confirm; the message is the warning to show."""


def _fmt_day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%a %b} {d.day}"


def _reached(case: dict) -> int:
    """How far a stopped case got (for interrupted / error)."""
    if case.get("calls"):
        return 5
    if case["result"].get("brief"):
        return 4
    if case.get("plan") or case["result"].get("target") is not None:
        return 3
    return 2 if case["bill"].get("amount_due") else 1


def overview(case: dict) -> dict:
    """Step, step state, attention and a one-line headline for one case (pure: reads, changes nothing)."""
    st, hosp = case["status"], case["hospital"].get("name") or case["bill"].get("provider") or "billing"
    pending = {a["agent"] for a in gate.pending_for(case["id"])}
    nxt = orchestrator.next_action(case)
    if "planner" in pending:
        step, state, att, line = 5, "waiting", "needs_you", "Payment plan ready: approve it"
    elif st == "awaiting_call_approval" or "caller" in pending:
        call_again = bool(case.get("calls"))
        step, state, att = (5 if call_again else 3), "waiting", "needs_you"
        line = "Plan ready: tap Call now" if not call_again else "Approve the follow-up call"
    elif st in RUNNING:
        step, state, att = RUNNING[st], "active", "running"
        line = {"created": "Reading the bill…", "reviewing": "Checking the bill and the hospital's rules…",
                "deciding": "Building your call plan…", "calling": f"Calling {hosp} billing…",
                "planning": "Working out a plan for what's left…"}[st]
    elif st == "error":
        step, state, att, line = _reached(case), "failed", "needs_you", "Something went wrong: open the case"
    elif st == "interrupted":
        step, state, att = _reached(case), "failed", "needs_you"
        line = "Stopped by a restart: call again to pick it up" if orchestrator.can_call_again(case) else "Stopped by a restart"
    elif nxt.get("due") and nxt.get("overdue"):
        step, state, att, line = 5, "waiting", "needs_you", f"Follow-up overdue (was due {_fmt_day(nxt['due'])}): call again"
    elif nxt.get("due"):
        step, state, att = 5, "waiting", "waiting"
        line = f"Billing promised to act · check back {'today' if nxt.get('due_today') else _fmt_day(nxt['due'])}"
    else:
        step, state, att, line = 5, "done", "done", "Resolved: nothing pending"
    return {"step": step, "step_label": STEPS[step - 1], "step_state": state, "attention": att,
            "attention_label": LABEL[att], "headline": line, "archived": bool(case.get("archived")),
            "archived_at": case.get("archived_at")}


def rank(row: dict) -> tuple:
    """Sort key for a dashboard row (summary + overview): needs you, running, waiting, done; newest first within."""
    return ATTENTION.index(row["attention"]), -(row.get("created_at") or 0)


def guard_warning(case: dict) -> str | None:
    """The warning shown before archiving or deleting a case that is still running or waiting on the patient."""
    st, hosp = case["status"], case["hospital"].get("name") or "billing"
    if st == "calling":
        return f"This case is still running: BillBack is on a call with {hosp} billing."
    if st in RUNNING:
        return "This case is still running: BillBack is working on it right now."
    o = overview(case)
    if o["attention"] == "needs_you" and st != "error":
        return f"This case still needs you: {o['headline']}."
    return None


def archive(case: dict, confirm: bool = False) -> dict:
    if not case.get("archived"):
        warn = guard_warning(case)
        if warn and not confirm:
            raise NeedsConfirm(warn + " Archive it anyway? It keeps running in the background.")
        o = overview(case)
        # what needed the patient when they archived it: that one they've seen, anything new brings it back
        case.update(archived=True, archived_at=orchestrator.case_now(case).isoformat(),
                    archived_need=o["headline"] if o["attention"] == "needs_you" else None)
        orchestrator.save(case)
    return overview(case)


def restore(case: dict) -> dict:
    for k in ("archived_at", "archived_need"):
        case.pop(k, None)
    case["archived"] = False
    orchestrator.save(case)
    return overview(case)


def resurface(case: dict) -> bool:
    """An archived case that newly needs the patient (plan ready, a failed step, a follow-up overdue) goes back
    to the Active list, logged in its history. One they archived while it already needed them (with the
    confirm) stays archived until something else needs them."""
    if not case.get("archived") or case["id"] not in orchestrator.cases:
        return False
    o = overview(case)
    if o["attention"] != "needs_you" or o["headline"] == case.get("archived_need"):
        return False
    restore(case)
    bus.emit(case["id"], "unarchived", text=f"Unarchived: needs you ({o['headline']})")
    return True


def resurface_all() -> None:
    for case in list(orchestrator.cases.values()):
        resurface(case)


def delete(case: dict, task=None, confirm: bool = False) -> None:
    """Delete a case for good. Blocked mid-call (end the call first, so billing isn't left on a dead line);
    a case that's otherwise running or waiting on the patient needs confirm, and its run is stopped first.
    The append-only audit log (data/<id>.audit.jsonl) stays, as it does for /api/reset."""
    if case["status"] == "calling":
        raise Blocked(f"BillBack is on a call with {case['hospital'].get('name') or 'billing'}. "
                      "End the call on the case page first, then delete.")
    warn = guard_warning(case)
    if warn and not confirm:
        raise NeedsConfirm(warn + " Delete it anyway? BillBack stops working on it.")
    if task is not None and not task.done():
        task.cancel()
    gate.forget(case["id"])
    orchestrator.cases.pop(case["id"], None)
    bus.forget(case["id"])
    events.delete_case(case["id"])

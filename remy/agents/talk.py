"""Talk to Remy: the mic on each case on the Cases page. Hold it, ask where things stand, change the plan, or
tell it to act ("call them", "set up the plan"), and hear the answer.

Anything with consequences takes two turns, and this module enforces it, not the model: Remy first says
exactly what it will do (the words come from code, from the case's current plan) and stores that as pending;
only a yes in a later turn runs it, and only if the case still matches what was said. Plan changes go to the
planner like the agent chat (they change what a call will ask, not what happens).
"""
import json
import re
import time
from typing import Awaitable, Callable

from .. import config, followups, llm
from ..events import bus
from ..harness import gate
from . import decide

PENDING_FOR = 180  # seconds a proposed action waits for a yes
HISTORY = 8  # turns of the conversation kept for context

YES = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok(ay)?|do it|go ahead|sounds good|please do|correct|confirm(ed)?|"
                 r"let'?s do it|go for it)\b", re.I)
NO = re.compile(r"^\W*(no|nope|don'?t|do not|cancel|stop|wait|hold on|not yet|never ?mind)\b", re.I)


def _spoken(text: str) -> str:
    """A plan line for the ear: the part before the colon ("Apply your insurance")."""
    return text.split(":")[0].strip().rstrip(".")


def _join(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def options(case: dict) -> dict[str, dict]:
    """What the patient can have Remy do right now, each with the exact words it says before doing it."""
    from .. import orchestrator  # the orchestrator imports the agents
    out, prov = {}, case["hospital"]["name"]
    pend = {a["agent"]: a for a in gate.pending_for(case["id"])}
    ver = decide.latest(case)

    def call_words(v: dict) -> str:
        asks = [_spoken(a["text"]) for a in v["asks"]]
        more = f", plus {len(asks) - 3} more" if len(asks) > 3 else ""
        return f"with plan v{v['v']}. I'll ask them to {_join([a[0].lower() + a[1:] for a in asks[:3]])}{more}"

    if "caller" in pend and ver:
        out["start_call"] = {"v": ver["v"], "say": f"I'll call {prov} billing now {call_words(ver)}. "
                                                   "I won't agree to pay anything."}
        out["not_now"] = {"say": f"I won't call {prov} for now. Just tell me when you want me to."}
    if orchestrator.can_call_again(case):
        nxt = ver if ver and ver.get("round", 1) == decide.round_of(case) else None  # step 5's plan, if any
        again = " again" if case.get("calls") else ""  # after "Not now" there has been no call yet
        out["call_again"] = {"v": nxt["v"] if nxt else None,
                             "say": f"I'll call {prov} billing{again} {call_words(nxt)}." if nxt
                             else f"I'll get a call to {prov} billing ready, then check the plan with you."}
    if "planner" in pend:
        a = pend["planner"]
        d = a["detail"]
        out["approve_payment_plan"] = {"approval_id": a["approval_id"], "say":
            f"I'll set up the interest-free plan: {d['months']} payments of {decide.money(d['monthly'])} a month "
            f"to {d['payee']}, saved as a recurring payment in your bank."}
        out["decline_payment_plan"] = {"approval_id": a["approval_id"], "say": "I won't set up the payment plan."}
    return out


DONE = {"start_call": "Calling now. You can listen in on the case page.",
        "not_now": "Okay, I won't call for now.",
        "call_again": "Done. The follow-up call is starting.",
        "approve_payment_plan": "Done. The payment plan is set up.",
        "decline_payment_plan": "Okay, no payment plan."}

SYSTEM = """You are Remy, the AI agent fighting this patient's hospital bill, talking with them out loud through
the mic on their Cases page. Your words are spoken by a voice, so: at most 3 short sentences (under 50 words), plain
words, no lists, symbols or markdown; say amounts as they'd be said ("six hundred dollars" is fine as "$600").
Answer only from the CASE JSON. Never invent facts, amounts or dates. "balance_after_corrections" is what the bill
should come down to once billing applies everything, not what it is now: only call it confirmed if a call has
balance_confirmed_by_rep.

You can do things, but never in one step. CAN_DO lists what can be done right now. When the patient wants one of
them, call propose with its kind: your exact words describing it are added after your text, so keep your own text
to one short sentence or none. When the patient clearly agrees to the PENDING action ("yes", "do it", "sounds
good"), call confirm. If they say no or change their mind, call cancel. If they ask to change the call plan (what
to ask first, what they can pay a month, leaving an ask off, asking for a supervisor), call hand_to_planner.
If they ask what's happening and something in CAN_DO is waiting on them, say so and offer it."""


def _tools(kinds: list[str]) -> list[dict]:
    tools = [{"name": "confirm", "description": "The patient agreed to the pending action: do it.",
              "input_schema": {"type": "object", "properties": {}}},
             {"name": "cancel", "description": "The patient doesn't want the pending action.",
              "input_schema": {"type": "object", "properties": {}}},
             {"name": "hand_to_planner", "description": "Pass a request to change the call plan to the planner.",
              "input_schema": {"type": "object", "properties": {}}}]
    if kinds:
        tools.insert(0, {"name": "propose", "description": "Offer one of CAN_DO; it runs only after the patient says yes.",
                         "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": kinds}},
                                          "required": ["kind"]}})
    return tools


def _offline(text: str, can: dict, pending: dict | None) -> tuple[str, str]:
    """No Claude: (tool, kind) from plain rules."""
    if pending and YES.search(text):
        return "confirm", ""
    if pending and NO.search(text):
        return "cancel", ""
    t = text.lower()
    if "payment plan" in t and re.search(r"\b(set|approve|accept|yes|go)\b", t) and "approve_payment_plan" in can:
        return "propose", "approve_payment_plan"
    if re.search(r"\b(not now|hold off|don'?t call)\b", t) and "not_now" in can:
        return "propose", "not_now"
    if re.search(r"\b(call|ring|phone)\b", t):
        for k in ("start_call", "call_again"):
            if k in can:
                return "propose", k
    if decide._offline_intent(text)[0] == "change":
        return "hand_to_planner", ""
    if YES.search(text):
        return "confirm", ""
    return "", ""


async def turn(case: dict, text: str, run: Callable[[str, dict], Awaitable[str | None]]) -> dict:
    """One voice turn. run(kind, option) carries out a confirmed action and returns an error, or None."""
    from .. import orchestrator
    v = case.setdefault("voice", {"history": [], "pending": None})
    can = options(case)
    pending = v["pending"]
    if pending and time.time() - pending["ts"] > PENDING_FOR:
        pending = v["pending"] = None

    tool, kind, said = "", "", ""
    if llm.available():
        ctx = {**orchestrator._whole_case(case), "CAN_DO": {k: o["say"] for k, o in can.items()},
               "PENDING": pending and {"kind": pending["kind"], "you_said": pending["say"]}}
        history = [{"role": r, "content": m} for t in v["history"][-HISTORY:] for r, m in (("user", t["heard"]),
                                                                                         ("assistant", t["reply"]))]
        try:
            resp = await llm.create(system=SYSTEM, model=config.MODEL_FAST, max_tokens=1500, effort="low",
                                    tools=_tools(list(can)), messages=[
                                        {"role": "user", "content": f"CASE (JSON):\n{json.dumps(ctx, default=str)}"},
                                        {"role": "assistant", "content": "Got it."}, *history,
                                        {"role": "user", "content": text}])
            said = " ".join(b.text for b in resp.content if b.type == "text").strip()
            use = next((b for b in resp.content if b.type == "tool_use"), None)
            if use:
                tool, kind = use.name, (use.input or {}).get("kind", "") if isinstance(use.input, dict) else ""
        except Exception as e:
            print("talk LLM error, using rules:", type(e).__name__)
    if not said and not tool:
        tool, kind = _offline(text, can, pending)

    did, reply = None, said
    if tool == "confirm":
        if not pending:
            reply = "There's nothing waiting for a yes right now. " + (said or _status(case, can))
        elif pending["kind"] not in can or can[pending["kind"]]["say"] != pending["say"]:
            # the case changed since Remy said it: never run what it didn't say
            if pending["kind"] in can:
                pending = v["pending"] = {"kind": pending["kind"], "say": can[pending["kind"]]["say"], "ts": time.time()}
                reply = f"Something changed since I asked. {pending['say']} Should I go ahead?"
            else:
                pending = v["pending"] = None
                reply = "Something changed since I asked, so I didn't do it. " + _status(case, can)
        else:
            err = await run(pending["kind"], can[pending["kind"]])
            did, v["pending"] = (pending["kind"], None) if not err else (None, v["pending"])
            reply = DONE[pending["kind"]] if not err else f"I couldn't do that: {err}"
    elif tool == "cancel":
        v["pending"] = None
        reply = said or "Okay, I won't."
    elif tool == "propose" and kind in can:
        v["pending"] = {"kind": kind, "say": can[kind]["say"], "ts": time.time()}
        reply = (said + " " if said else "") + can[kind]["say"] + " Should I go ahead?"
    elif tool == "hand_to_planner":
        if decide.latest(case):
            try:
                out = await decide.chat(case, text, via="voice")
                did, reply = ("plan_change" if out.get("v") else None), out["reply"]
            except decide.Refused as e:
                reply = str(e)
        else:
            reply = "Your plan isn't ready yet. Once the review is done, I can change it."
    if not reply:
        reply = _status(case, can)

    v["history"] = (v["history"] + [{"heard": text, "reply": reply, "ts": time.time()}])[-HISTORY:]
    bus.emit(case["id"], "agent_qa", "remy", question=text, answer=reply, sources=[], voice=True)
    return {"reply": reply, "did": did, "pending": (v["pending"] or {}).get("kind")}


def _status(case: dict, can: dict) -> str:
    """Where things stand, and what's waiting on the patient (offline, or when the model said nothing)."""
    from .. import orchestrator
    nxt = orchestrator.next_action(case)
    ask = ("start_call" in can and "Want me to call them?") or ("approve_payment_plan" in can and
                                                               "Want me to set up the payment plan?") \
        or ("call_again" in can and "Want me to call them again?") or ""
    return " ".join(x for x in (followups.fallback_status(orchestrator.summary(case)), ask) if x) or nxt["action"]

"""Caller: the brain behind the phone call to hospital billing.

The brain is transport-agnostic: `CallSession.turn(rep_said)` returns what BillBack should say next.
Transports plug in on top:
  - desk mode (web/rep.html): the rep types/speaks on a page, BillBack's lines are spoken with TTS
  - Twilio phone calls (billback/voice): Cartesia streaming STT -> turn() -> Cartesia streaming TTS
  - Cartesia Managed Agent (scripts/setup_cartesia.py) reuses SYSTEM as its instructions
"""
import json
import re
import uuid

from .. import config, llm
from ..events import bus
from ..harness import ToolRegistry, Tool

SYSTEM = """You are BillBack, an AI assistant on a live phone call with a hospital billing representative,
calling on behalf of a patient who authorized you. You speak out loud, so:
- Say 1-2 short sentences per turn (under 30 words; this is spoken aloud) and make ONE request per turn, then stop
  and let the rep answer. Give an ID or claim number once, only when it is needed or asked for.
  No lists, no markdown, never several paragraphs.
- Open by saying you are an automated assistant calling on behalf of the patient with their authorization
  and that the call is transcribed, give the patient's name and account number, and state the purpose in
  one sentence.

Work through the "goals" list in the CASE BRIEF, in order, one at a time. Skip any goal the rep has already
granted. Only bring up evidence when it supports the current goal.

If the rep needs to verify identity, give the patient's date of birth and insurance member ID from the brief.
If they insist the patient must call personally, ask them to note the account (disputed / assistance review
requested) and for a reference number, and ask what the patient should say when they call.

If the rep asks whether the patient authorized this, answer from "patient_authorization" in the brief: say
how and when the patient agreed. If it says the patient is on the line, ask the patient to confirm to the rep.

If "call_type" is "follow_up", this is a follow-up: "previous_calls" lists the earlier calls (date, rep,
case_number, what they promised, outcome). In your opening, name the last call (date, rep, case number) so
the rep can find it, then check whether each promise was kept, in goal order. Don't re-argue what was
already granted; move on to what is still open.

Rules:
- Use only the facts in the CASE BRIEF. Never invent numbers, policies, or laws.
- Be polite, calm, and persistent. If the rep says no, restate the evidence once more, then ask for a
  supervisor or a written review instead of arguing in circles.
- NEVER pay anything or give card or bank details on the call. If the rep asks for payment today, say the
  patient will set up any payments themselves.
- Payment plans: only if "proposed_payment_plan" is in the brief, ask for that interest-free plan (months x
  monthly amount). If the rep offers it or something no costlier, thank them and ask them to note it on the
  account; the patient confirms it and sets up the payments. Never accept interest or a higher monthly
  amount. Without "proposed_payment_plan" in the brief, don't agree to any plan.
- Refer to the patient by name or as "the patient"; don't guess their pronouns.
- When the rep gives a case/reference number, call record_outcome. When the goals are done or the rep
  ends it, call end_call and say a short goodbye. If the rep says goodbye or wants to end the call, never push
  another request: record what you have, thank them, say goodbye and call end_call.
- When you use a tool you may say a brief sentence first; never include XML tags in what you say.
"""


def call_goals(rec: dict, elig: dict, policy: dict) -> list[str]:
    goals = []
    if rec["billed_due"] > rec["patient_responsibility"]:
        goals.append(f"Get the insurance adjustment applied: the EOB says the patient owes "
                     f"${rec['patient_responsibility']:,.0f}, not ${rec['billed_due']:,.0f}.")
    if rec["paid"]:
        goals.append(f"Get the ${rec['paid']:,.0f} already paid applied (give date and reference when asked).")
    if elig["discount_pct"]:
        what = "free care" if elig["discount_pct"] == 100 else f"a {elig['discount_pct']:.0f}% discount"
        goals.append(f"Request a financial-assistance review under the hospital's own policy: the patient qualifies "
                     f"for {what}, which brings the balance to ${policy['target']:,.0f}. Quote the policy.")
        if policy.get("refund"):
            goals.append(f"Ask for the ${policy['refund']:,.0f} the patient already paid to be refunded once "
                         f"the assistance is applied.")
    plan = policy.get("plan") or {}
    if not elig["discount_pct"] and plan.get("months") and policy["target"] > 0:
        terms = policy.get("plan_terms") or {}
        why = " Their own billing policy says payment plans are interest-free." if terms.get("interest_free") else ""
        goals.append(f"Once the balance is corrected to ${policy['target']:,.0f}, set up an interest-free payment plan: "
                     f"{plan['months']} monthly payments of ${plan['monthly']:,.0f}.{why} Don't give card details; "
                     f"the patient sets up the payments.")
    goals.append("Ask for the account to be placed on hold so it isn't sent to collections while this is reviewed.")
    goals.append("Get a case or reference number and the rep's name; repeat the case number back to confirm.")
    return goals


def build_brief(case: dict, audit: dict, policy: dict, plan: dict | None = None) -> dict:
    """plan: the approved plan version (agents/decide.py). Its asks are the goals, in its order, and its money
    replaces the Review's (corrections and strategy changes are already applied)."""
    rec, elig = audit["reconciliation"], policy["eligibility"]
    brief = {
        "goals": call_goals(rec, elig, policy),
        "patient": f"{case['patient']['first_name']} {case['patient']['last_name']}",
        "hospital": case["hospital"]["name"],
        "account_number": case["bill"]["account_number"],
        "service_date": case["bill"]["service_date"],
        "hospital_says_due": rec["billed_due"],
        "insurer": case["eob"]["insurer"],
        "claim_number": case["eob"]["claim_number"],
        "eob_patient_responsibility": rec["patient_responsibility"],
        # short reference: a full transaction UUID is unreadable on a phone call
        "payments_made": [{"date": p["date"][:10], "amount": p["amount"], "reference": str(p["id"])[:8].upper()}
                          for p in rec["payments"]],
        "correct_balance": rec["owed"],
        "income_pct_fpl": elig["fpl_pct"],
        "policy_tier": elig["tier"],
        "policy_quotes": [t.get("quote") for t in policy["policy"].get("tiers", [])],
        "collections_quote": policy["policy"].get("collections_quote"),
        "payment_plan_quotes": [q for q in ((policy.get("plan_terms") or {}).get(k) for k in ("quote", "cap_quote")) if q],
        "proposed_payment_plan": policy.get("plan") if not elig["discount_pct"] else None,
        "policy_source": policy["source"],
        "balance_after_assistance": policy["target"],
        "refund_due_after_assistance": policy.get("refund", 0),
        "patient_date_of_birth": case["patient"].get("dob"),
        "insurance_member_id": case["eob"].get("member_id"),
        "deadline_on_notice": case["bill"]["due_date"],
        **({"voice_id": case["voice_id"]} if case.get("voice_id") else {}),
    }
    if plan:
        m, plan_ask = plan["money"], next((a for a in plan["asks"] if a["kind"] == "plan"), None)
        kept = {k.removeprefix("auditor:paid-") for a in plan["asks"] for k in a["findings"] if k.startswith("auditor:paid-")}
        brief.update({"goals": [a["goal"] for a in plan["asks"]], "plan_version": plan["v"], "correct_balance": m["owed"],
                      "balance_after_assistance": m["target"], "refund_due_after_assistance": m["refund"],
                      "proposed_payment_plan": plan_ask["plan"] if plan_ask else None,
                      "payments_made": [p for p, raw in zip(brief["payments_made"], rec["payments"]) if str(raw["id"]) in kept]})
    return brief


def _case_number(said: str) -> str:
    """Lenient: '58213', 'case number is 58213', 'BB 4471' (speech-to-text spacing), 'fa-2291'."""
    m = re.search(r"(?:\b([A-Za-z]{1,4})([ -]?))?(\d[\d-]{2,})", said)
    if not m:
        return said.strip()[:40]
    prefix, sep, digits = m.groups()
    if prefix and (sep != " " or prefix.isupper()):  # "is 58213" is not a prefix; "BB 4471" is
        return f"{prefix.upper()}{sep.strip()}{digits}"
    return digits


def _last_call(brief: dict) -> dict:
    """Most recent entry of brief["previous_calls"] ({date, rep, case_number, promised, outcome}); {} on a first call."""
    prev = (brief.get("previous_calls") or [{}])[-1]
    return prev if isinstance(prev, dict) else {"outcome": str(prev)}


def _follow_up(brief: dict) -> str:
    """Opening clause naming the earlier call, so the rep can find it."""
    prev = _last_call(brief)
    if not prev:
        return ""
    when = f" on {prev['date']}" if prev.get("date") else ""
    rep = f" with {prev['rep']}" if prev.get("rep") else ""
    case_no = f", case number {prev['case_number']}" if prev.get("case_number") else ""
    return f"I'm following up on our call{when}{rep}{case_no}. "


class CallSession:
    def __init__(self, case_id: str, brief: dict) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.case_id, self.brief = case_id, brief
        self.messages: list[dict] = []
        self.outcome: dict = {}
        self.ended = False
        self.reg = ToolRegistry(case_id, "caller")
        self.reg.add(Tool("record_outcome", "Record what the hospital agreed to and the case/reference number", {
            "type": "object", "properties": {
                "case_number": {"type": "string"}, "rep_name": {"type": "string"},
                "account_on_hold": {"type": "boolean"}, "assistance_review_opened": {"type": "boolean"},
                "adjustment_applied": {"type": "boolean"}, "payment_applied": {"type": "boolean"},
                "next_step": {"type": "string"}},
            "required": ["next_step", "account_on_hold", "assistance_review_opened"]}, self._record, tier="act"))
        self.reg.add(Tool("end_call", "End the call after saying goodbye", {
            "type": "object", "properties": {"reason": {"type": "string"}}}, self._end, tier="act"))

    async def _record(self, **kw) -> dict:
        self.outcome.update({k: v for k, v in kw.items() if v not in (None, "")})
        bus.emit(self.case_id, "call_outcome", "caller", outcome=self.outcome)
        return {"ok": True}

    async def _end(self, reason: str = "") -> dict:
        self.ended = True
        bus.emit(self.case_id, "call_ended", "caller", reason=reason, outcome=self.outcome)
        return {"ok": True}

    def _system(self) -> str:
        return SYSTEM + "\nCASE BRIEF (JSON):\n" + json.dumps(self.brief, indent=1)

    def _scripted(self, rep_said: str | None) -> list[str]:
        """Offline fallback (no Claude key): walks the goals in order with lines built from the brief."""
        b = self.brief
        if b.get("call_type") == "follow_up":
            return self._scripted_follow_up()
        pay = b["payments_made"][0] if b["payments_made"] else None
        return [
            f"Hi, this is an automated assistant calling on behalf of {b['patient']}, with their authorization, "
            f"about account {b['account_number']}. {_follow_up(b)}"
            "The balance looks incorrect and I'd like to get it corrected.",
            f"Your statement shows ${b['hospital_says_due']:,.0f} due, but {b['insurer']}'s EOB for claim "
            f"{b['claim_number']} lists patient responsibility of ${b['eob_patient_responsibility']:,.0f}. "
            "Could you apply the insurance adjustment?",
            (f"There's also a ${pay['amount']:,.0f} payment made on {pay['date']} that should be on the account. "
             "With both applied the balance is "
             f"${b['correct_balance']:,.0f}." if pay else "Could you confirm the corrected balance?"),
            f"The patient's income is about {b['income_pct_fpl']:.0f} percent of the poverty line, which qualifies "
            "under your own financial assistance policy. Can you open a financial assistance review and hold the "
            "account from collections while it's pending?",
            "Thank you. Could I get a case or reference number for this?",
            "Thanks so much for your help today. Goodbye.",
        ]

    async def turn(self, rep_said: str | None) -> str:
        """rep_said=None means the call just connected and BillBack speaks first."""
        if rep_said:
            bus.emit(self.case_id, "transcript", "caller", speaker="rep", text=rep_said)
        if not llm.available():
            return await self._scripted_turn(rep_said)
        n = len(self.messages)
        heard = rep_said or "[call connected — the representative has picked up and said hello]"
        last = self.messages[-1] if self.messages else None
        if last and last["role"] == "user" and isinstance(last["content"], list):
            last["content"].append({"type": "text", "text": heard})  # turn ended on tool results; keep roles alternating
        else:
            self.messages.append({"role": "user", "content": heard})
        model = config.MODEL_FAST
        # Lowest latency for a voice turn: no thinking on Haiku 5.5 (allowed at its default effort), low effort elsewhere.
        opts = {"thinking": {"type": "disabled"}, "effort": None} if "haiku-5-5" in model else {"effort": "low"}
        parts: list[str] = []
        try:
            for _ in range(4):  # allow tool calls, then a spoken reply
                resp = await llm.create(system=self._system(), messages=self.messages, tools=self.reg.specs(),
                                        model=model, max_tokens=1024, timeout=20, **opts)
                if resp.stop_reason in ("max_tokens", "refusal"):
                    break  # never run a cut-off tool call, and keep it out of the history
                self.messages.append({"role": "assistant", "content": resp.content})
                t = " ".join(b.text for b in resp.content if b.type == "text").strip()
                if t:
                    parts.append(t)
                calls = [b for b in resp.content if b.type == "tool_use"]
                if not calls:
                    break
                results = [{"type": "tool_result", "tool_use_id": c.id,
                            "content": json.dumps(await self.reg.call(c.name, c.input), default=str)} for c in calls]
                self.messages.append({"role": "user", "content": results})
                if parts and (self.ended or all(c.name == "record_outcome" for c in calls)):
                    break  # already said something this turn: a second model call only repeats it
        except Exception as e:
            del self.messages[n:]  # keep the history valid for the next turn
            bus.emit(self.case_id, "warning", "caller", text=f"Caller LLM error: {type(e).__name__}")
            parts = []
        say = " ".join(parts) or "Sorry, could you say that again?"
        if not self.ended and re.search(r"\b(goodbye|bye)\b", say, re.I):
            await self.reg.call("end_call", {"reason": "done"})  # model said goodbye but forgot end_call
        bus.emit(self.case_id, "transcript", "caller", speaker="billback", text=say)
        return say

    def _scripted_follow_up(self) -> list[str]:
        b, prev = self.brief, _last_call(self.brief)
        promised = " and ".join(p[0].lower() + p[1:] for p in prev.get("promised") or [] if p)
        lines = [f"Hi, this is an automated assistant calling on behalf of {b['patient']}, with their authorization, "
                 f"about account {b['account_number']}. {_follow_up(b)}",
                 (f"Last time we were told you'd {promised}. " if promised else "")
                 + f"Can you confirm the balance is now ${b.get('balance_after_assistance', b['correct_balance']):,.0f}?"]
        if b.get("refund_due_after_assistance"):
            lines.append(f"Thank you. When will the ${b['refund_due_after_assistance']:,.0f} refund be issued?")
        return lines + ["Could you keep the account on hold until the corrected statement goes out?",
                        "Thanks so much for your help today. Goodbye."]

    async def _scripted_turn(self, rep_said: str | None) -> str:
        """Offline: the outcome follows the script step, so it never depends on how the rep phrases things."""
        lines = self._scripted(rep_said)
        step = sum(1 for m in self.messages if m["role"] == "assistant")
        if self.brief.get("call_type") == "follow_up":
            if rep_said and step == len(lines) - 1:  # replying to the hold ask
                await self.reg.call("record_outcome", {
                    "account_on_hold": True, "assistance_review_opened": True,
                    "case_number": _last_call(self.brief).get("case_number"),
                    "next_step": "Corrected statement pending"})
        elif rep_said and step >= 4:  # replying to the assistance ask (4) or the case-number ask (5)
            fields = {"account_on_hold": True, "assistance_review_opened": True,
                      "next_step": "Hospital reviewing financial assistance"}
            if step == 5:
                fields["case_number"] = _case_number(rep_said)
            await self.reg.call("record_outcome", fields)
        say = lines[min(step, len(lines) - 1)]
        self.messages += [{"role": "user", "content": rep_said or ""}, {"role": "assistant", "content": say}]
        if step >= len(lines) - 1:
            if not self.outcome.get("assistance_review_opened"):
                await self.reg.call("record_outcome", {"account_on_hold": True, "assistance_review_opened": True,
                                                       "next_step": "Hospital reviewing financial assistance"})
            await self.reg.call("end_call", {"reason": "done"})
        bus.emit(self.case_id, "transcript", "caller", speaker="billback", text=say)
        return say


sessions: dict[str, CallSession] = {}

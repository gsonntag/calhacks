"""Caller: the brain behind the phone call to hospital billing.

The brain is transport-agnostic: `CallSession.turn(rep_said)` returns what BillBack should say next.
Transports plug in on top:
  - desk mode (web/rep.html): the rep types/speaks on a page, BillBack's lines are spoken with TTS
  - Cartesia Line (telephony): the Line agent forwards each rep utterance to /api/calls/{id}/turn
"""
import json
import uuid

from .. import config, llm
from ..events import bus
from ..harness import ToolRegistry, Tool

SYSTEM = """You are BillBack, an AI assistant on a live phone call with a hospital billing representative,
calling on behalf of a patient who authorized you. You speak out loud, so:
- Say 1-3 short sentences per turn. No lists, no markdown, no reading out long numbers digit by digit
  unless asked (account numbers are fine when asked).
- Open by saying you are an automated assistant calling on behalf of the patient with their authorization,
  give the patient's name and account number, and state the purpose in one sentence.

Goals, in order:
1. Get the insurance adjustment applied: the EOB says patient responsibility is lower than the bill.
2. Get the payment already made applied (give date, amount, and reference when asked).
3. Ask for a financial-assistance (charity care) review under the hospital's OWN policy, citing it.
4. Ask for the account to be placed on hold / not sent to collections while the review is pending.
5. Get a case or reference number and the representative's name. Repeat the case number back to confirm.

Rules:
- Use only the facts in the CASE BRIEF. Never invent numbers, policies, or laws.
- Be polite, calm, and persistent. If the rep says no, restate the evidence once more, then ask for a
  supervisor or a written review instead of arguing in circles.
- NEVER agree to pay anything, set up a payment plan, or give card or bank details on the call. If the rep
  asks for payment, say the patient will review any payment arrangement and you'll follow up.
- When the rep gives a case/reference number, call record_outcome. When the goals are done or the rep
  ends it, call end_call and say a short goodbye.
"""


def build_brief(case: dict, audit: dict, policy: dict) -> dict:
    rec, elig = audit["reconciliation"], policy["eligibility"]
    discounted = round(rec["owed"] * (1 - elig["discount_pct"] / 100), 2)
    return {
        "patient": f"{case['patient']['first_name']} {case['patient']['last_name']}",
        "hospital": case["hospital"]["name"],
        "account_number": case["bill"]["account_number"],
        "service_date": case["bill"]["service_date"],
        "hospital_says_due": rec["billed_due"],
        "insurer": case["eob"]["insurer"],
        "claim_number": case["eob"]["claim_number"],
        "eob_patient_responsibility": rec["patient_responsibility"],
        "payments_made": [{"date": p["date"][:10], "amount": p["amount"], "reference": p["id"]} for p in rec["payments"]],
        "correct_balance": rec["owed"],
        "income_pct_fpl": elig["fpl_pct"],
        "policy_tier": elig["tier"],
        "policy_quotes": [t.get("quote") for t in policy["policy"].get("tiers", [])],
        "collections_quote": policy["policy"].get("collections_quote"),
        "policy_source": policy["source"],
        "balance_after_assistance": discounted,
        "deadline_on_notice": case["bill"]["due_date"],
    }


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
                "next_step": {"type": "string"}}, "required": ["next_step"]}, self._record, tier="act"))
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
        pay = b["payments_made"][0] if b["payments_made"] else None
        return [
            f"Hi, this is an automated assistant calling on behalf of {b['patient']}, with their authorization, "
            f"about account {b['account_number']}. The balance looks incorrect and I'd like to get it corrected.",
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
            import re
            if rep_said and (m := re.search(r"\b([A-Z]{1,4}-?\d{3,}[A-Z0-9-]*)\b", rep_said)):
                await self.reg.call("record_outcome", {"case_number": m.group(1), "account_on_hold": True,
                                                       "assistance_review_opened": True,
                                                       "next_step": "Hospital reviewing financial assistance"})
            lines = self._scripted(rep_said)
            step = sum(1 for m in self.messages if m["role"] == "assistant")
            say = lines[min(step, len(lines) - 1)]
            self.messages += [{"role": "user", "content": rep_said or ""}, {"role": "assistant", "content": say}]
            if step >= len(lines) - 1:
                await self.reg.call("end_call", {"reason": "done"})
            bus.emit(self.case_id, "transcript", "caller", speaker="billback", text=say)
            return say
        self.messages.append({"role": "user", "content": rep_said or "[call connected — the representative has picked up and said hello]"})
        say = ""
        for _ in range(4):  # allow tool calls, then a spoken reply
            resp = await llm.create(system=self._system(), messages=self.messages, tools=self.reg.specs(),
                                    model=config.MODEL_FAST, max_tokens=400)
            self.messages.append({"role": "assistant", "content": resp.content})
            say = " ".join(b.text for b in resp.content if b.type == "text").strip() or say
            calls = [b for b in resp.content if b.type == "tool_use"]
            if not calls:
                break
            results = [{"type": "tool_result", "tool_use_id": c.id,
                        "content": json.dumps(await self.reg.call(c.name, c.input))} for c in calls]
            self.messages.append({"role": "user", "content": results})
            if say and self.ended:
                break
        if say:
            bus.emit(self.case_id, "transcript", "caller", speaker="billback", text=say)
        return say


sessions: dict[str, CallSession] = {}

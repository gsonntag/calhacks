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

from .. import config, finance, llm
from ..events import bus
from ..harness import ToolRegistry, Tool
from . import mandate

INTERRUPTED = "—[interrupted]"  # ends a remembered reply the rep talked over (voice/heard.py)

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
- Payment plans: only if "proposed_payment_plan" is in the brief, ask for that interest-free plan (its "text":
  the monthly payments and the smaller last one, adding up to the balance). If the rep offers it or something
  no costlier, thank them and ask them to note it on the account; the patient confirms it and sets up the
  payments. Never accept interest or a higher monthly amount. Without "proposed_payment_plan" in the brief, don't agree to any plan.
- Refer to the patient by name or as "the patient"; don't guess their pronouns.
- When the rep gives a case/reference number, read it back first: say only "Let me read that back: <the
  number exactly as you heard it>. Is that right?" Call record_outcome with case_number after the rep says
  yes; if they correct it, read back the corrected one. When the goals are done or the rep
  ends it, call end_call and say a short goodbye. If the rep says goodbye or wants to end the call, never push
  another request: record what you have, thank them, say goodbye and call end_call.
- When you use a tool, say your spoken sentence first, then call the tool; never include XML tags in what you say.
- An automated phone menu ("press 1 for billing") is not a person: don't talk to it. Call press_keys with the
  option for billing / patient accounts (or the operator), with no spoken text.
- A line of yours ending in "—[interrupted]" was cut off: the rep only heard the part before it.
- Before you agree to ANYTHING the rep offers or asks for (a payment plan, a balance, a settlement, paying
  today) or give any piece of information (date of birth, member ID, SSN, card, anything), call check_offer
  FIRST, with no spoken text, and follow its decision. If it's allowed, go ahead. If it's refused, the
  refusal is spoken for you; then continue with the next goal.
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
                     f"{finance.plan_said(plan)}.{why} Don't give card details; "
                     f"the patient sets up the payments.")
    goals.append("Ask for the account to be placed on hold so it isn't sent to collections while this is reviewed.")
    goals.append("Get a case or reference number and the rep's name; repeat the case number back to confirm.")
    return goals


def _ask_kinds(rec: dict, elig: dict, policy: dict) -> list[str]:
    """The ask kinds call_goals() covers, for a brief built without an approved plan (decide.KINDS names)."""
    kinds = ["insurance"] * (rec["billed_due"] > rec["patient_responsibility"]) + ["payment"] * bool(rec["paid"])
    if elig["discount_pct"]:
        kinds += ["discount"] + ["refund"] * bool(policy.get("refund"))
    elif (policy.get("plan") or {}).get("months") and policy["target"] > 0:
        kinds.append("plan")
    return kinds + ["hold", "case_number"]


def build_brief(case: dict, audit: dict, policy: dict, plan: dict | None = None) -> dict:
    """plan: the approved plan version (agents/decide.py). Its asks are the goals, in its order, and its money
    replaces the Review's (corrections and strategy changes are already applied)."""
    rec, elig = audit["reconciliation"], policy["eligibility"]
    eob = case.get("eob") or {}  # empty for uninsured patients
    brief = {
        "goals": call_goals(rec, elig, policy),
        "ask_kinds": _ask_kinds(rec, elig, policy),
        "patient": f"{case['patient']['first_name']} {case['patient']['last_name']}",
        "hospital": case["hospital"]["name"],
        "account_number": case["bill"]["account_number"],
        "service_date": case["bill"]["service_date"],
        "hospital_says_due": rec["billed_due"],
        "insurer": eob.get("insurer"),
        "claim_number": eob.get("claim_number"),
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
        "insurance_member_id": eob.get("member_id"),
        "deadline_on_notice": case["bill"]["due_date"],
        **({"voice_id": case["voice_id"]} if case.get("voice_id") else {}),
    }
    if plan:
        m, plan_ask = plan["money"], next((a for a in plan["asks"] if a["kind"] == "plan"), None)
        kept = {k.removeprefix("auditor:paid-") for a in plan["asks"] for k in a["findings"] if k.startswith("auditor:paid-")}
        brief.update({"goals": [a["goal"] for a in plan["asks"]], "ask_kinds": [a["kind"] for a in plan["asks"]],
                      "plan_version": plan["v"], "correct_balance": m["owed"],
                      "balance_after_assistance": m["target"], "refund_due_after_assistance": m["refund"],
                      "proposed_payment_plan": plan_ask["plan"] if plan_ask else None,
                      "payments_made": [p for p, raw in zip(brief["payments_made"], rec["payments"]) if str(raw["id"]) in kept]})
    if "discount" not in brief["ask_kinds"]:  # no discount ask: nothing the caller could read as "the patient qualifies"
        brief.update(income_pct_fpl=None, policy_tier=None, policy_quotes=[])
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


def _id_key(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


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
        self.last_say = ""
        self.last_heard: str | None = None  # the part of the last reply the rep heard, if they cut it off
        self._turn_base = 0                 # index in self.messages where the current/last turn starts
        self._replying = False
        self._cut: str | None = None
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
        self.reg.add(Tool("press_keys", "Press phone keypad keys to answer an automated phone menu", {
            "type": "object", "properties": {"digits": {"type": "string", "description": "e.g. \"2\" or \"0\""}},
            "required": ["digits"]}, self._press, tier="act"))
        self.reg.add(Tool("check_offer", "Check an offer or request from the rep against what the patient approved, "
                          "before agreeing to it or giving information", mandate.TOOL_SPEC, self._check, tier="act"))
        self.keys = ""  # keypad presses the phone transport hasn't sent yet (take_keys)
        self.refused: list[dict] = []  # check_offer refusals this call (agents/mandate.py)

    async def _record(self, **kw) -> dict:
        self.outcome.update({k: v for k, v in kw.items() if v not in (None, "")})
        note = None
        if kw.get("case_number"):
            # Read-back rule: a number is confirmed only if BillBack said it back in an earlier turn (so the rep
            # has answered since) or we already had it. Otherwise it's kept, flagged, and the model is told to read it back.
            ok = self._read_back(kw["case_number"])
            self.outcome["case_number_confirmed"] = ok
            if not ok:
                note = ('Saved as unconfirmed. Read the case number back ("Let me read that back: ... Is that right?") '
                        "and call record_outcome again once the rep confirms.")
        bus.emit(self.case_id, "call_outcome", "caller", outcome=self.outcome)
        return {"ok": True, **({"note": note} if note else {})}

    async def _check(self, kind: str = "", **kw) -> dict:
        """The mandate decides, not the model (agents/mandate.py). Every decision goes to the case page and
        the audit log; a refusal is spoken in the code's own words (_reply)."""
        d = mandate.evaluate(self.brief, kind, **{k: v for k, v in kw.items()
                                                  if k in ("monthly", "months", "total", "interest_pct", "info")})
        out = {"offer": kind, **{k: v for k, v in kw.items() if v not in (None, "")}, **d.as_dict()}
        if not d.allowed:
            self.refused.append(out)
        bus.emit(self.case_id, "guardrail", "caller", **out)
        return out

    async def _press(self, digits: str = "") -> dict:
        keys = re.sub(r"[^0-9*#]", "", digits)
        self.keys += keys
        bus.emit(self.case_id, "call_keys", "caller", digits=keys)
        return {"ok": bool(keys)}

    def take_keys(self) -> str:
        keys, self.keys = self.keys, ""
        return keys

    def _read_back(self, number: str) -> bool:
        key = _id_key(number)
        if not key:
            return False
        known = (self.brief.get("account_number"), self.brief.get("claim_number"), _last_call(self.brief).get("case_number"))
        if any(k and _id_key(k) == key for k in known):
            return True
        for m in self.messages[:self._turn_base]:  # earlier turns only: the rep must have answered the read-back
            if m["role"] == "assistant":
                blocks = [None] if isinstance(m["content"], str) else m["content"]
                said = " ".join(_text_of(m, b) for b in blocks if _is_text(b))
                if key in _id_key(said.split(INTERRUPTED)[0]):
                    return True
        return False

    async def _end(self, reason: str = "") -> dict:
        self.ended = True
        bus.emit(self.case_id, "call_ended", "caller", reason=reason, outcome=self.outcome)
        return {"ok": True}

    def _system(self) -> str:
        return SYSTEM + "\nCASE BRIEF (JSON):\n" + json.dumps(self.brief, indent=1)

    def _kinds(self) -> list[str]:
        """The approved plan's ask kinds; a hand-made brief without them gets only what its numbers show."""
        b = self.brief
        if b.get("ask_kinds") is not None:
            return b["ask_kinds"]
        ins = b.get("insurer") and b.get("hospital_says_due", 0) > b.get("eob_patient_responsibility", 0)
        return ["insurance"] * bool(ins) + ["payment"] * bool(b.get("payments_made")) + ["hold", "case_number"]

    def _scripted(self, rep_said: str | None) -> list[str]:
        """Offline fallback (no Claude key): one line per ask in the approved plan, built from the brief. Never
        says anything the plan doesn't ask for. The last three lines are always: the hold ask (with the discount
        or plan ask when there is one), the case-number ask, goodbye."""
        b, kinds = self.brief, self._kinds()
        if b.get("call_type") == "follow_up":
            return self._scripted_follow_up()
        pay = b["payments_made"][0] if b["payments_made"] and "payment" in kinds else None
        why = ("The balance looks incorrect and I'd like to get it corrected." if {"insurance", "payment"} & set(kinds)
               else "I'd like to request a financial assistance review." if "discount" in kinds
               else "I'd like to set up a payment plan." if "plan" in kinds else "I'd like to go over the balance.")
        lines = [f"Hi, this is an automated assistant calling on behalf of {b['patient']}, with their authorization, "
                 f"about account {b['account_number']}. {_follow_up(b)}{why}",
                 (f"Your statement shows ${b['hospital_says_due']:,.0f} due, but {b['insurer']}'s EOB for claim "
                  f"{b['claim_number']} lists patient responsibility of ${b['eob_patient_responsibility']:,.0f}. "
                  "Could you apply the insurance adjustment?" if "insurance" in kinds else
                  f"Your statement shows ${b['hospital_says_due']:,.0f} due."
                  + ("" if b.get("insurer") else " The patient is uninsured."))]
        if pay:
            lines.append(f"There's also a ${pay['amount']:,.0f} payment made on {pay['date']} that should be on the "
                         f"account. With {'both' if 'insurance' in kinds else 'it'} applied the balance is "
                         f"${b['correct_balance']:,.0f}.")
        elif "insurance" in kinds:
            lines.append("Could you confirm the corrected balance?")
        hold = "hold the account from collections while it's pending?"
        plan = b.get("proposed_payment_plan") if "plan" in kinds else None
        if "discount" in kinds:
            fpl = (f"The patient's income is about {b['income_pct_fpl']:.0f} percent of the poverty line, which "
                   if b.get("income_pct_fpl") is not None else "The patient ")
            refund = (f" and refund the ${b['refund_due_after_assistance']:,.0f} already paid once it's applied,"
                      if "refund" in kinds and b.get("refund_due_after_assistance") else "")
            lines.append(f"{fpl}qualifies under your own financial assistance policy. Can you open a financial "
                         f"assistance review{refund} and {hold}")
        elif plan:
            lines.append(f"Once it's corrected to ${b['balance_after_assistance']:,.0f}, could you set up an "
                         f"interest-free payment plan of {plan['months']} monthly payments of ${plan['monthly']:,.0f}, "
                         f"and {hold.replace('pending', 'corrected')}")
        else:
            lines.append("Could you place the account on hold so it isn't sent to collections while this is corrected?")
        return lines + ["Thank you. Could I get a case or reference number for this?",
                        f"Let me read that back: {_case_number(rep_said or '')}. Is that right?",
                        "Thanks so much for your help today. Goodbye."]

    async def turn(self, rep_said: str | None) -> str:
        """rep_said=None means the call just connected and BillBack speaks first."""
        async for _ in self.reply(rep_said):
            pass
        return self.last_say

    # Voice turns stream: reply() yields the spoken reply sentence by sentence while Claude is still writing,
    # and draft() starts Claude before the rep's turn is final (Ink's turn.eager_end) without touching the
    # session, so a draft the rep talks over is simply cancelled.

    def _heard(self, rep_said: str | None) -> list[dict]:
        """self.messages plus the rep's words, as a new list (self.messages is not changed)."""
        heard = rep_said or "[call connected — the representative has picked up and said hello]"
        msgs = list(self.messages)
        if msgs and msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list):
            # turn ended on tool results; keep roles alternating
            msgs[-1] = {**msgs[-1], "content": [*msgs[-1]["content"], {"type": "text", "text": heard}]}
        else:
            msgs.append({"role": "user", "content": heard})
        return msgs

    def _llm(self, messages: list) -> "llm.Stream":
        model = config.MODEL_FAST
        # Lowest latency for a voice turn: no thinking on Haiku 5.5 (allowed at its default effort), low effort elsewhere.
        opts = ({"thinking": {"type": "disabled"}} if "haiku-5-5" in model else {"output_config": {"effort": "low"}})
        return llm.Stream(system=llm.cached(self._system()), messages=messages, tools=self.reg.specs(), model=model,
                          max_tokens=1024, timeout=20, cache_control={"type": "ephemeral"}, **opts)

    def draft(self, rep_said: str | None) -> dict | None:
        """Start the first model call for this reply now; pass it to reply() to use it, or cancel_draft() it."""
        if not llm.available():
            return None
        msgs = self._heard(rep_said)
        return {"said": rep_said, "base": len(self.messages), "messages": msgs, "stream": self._llm(msgs)}

    @staticmethod
    def cancel_draft(d: dict | None) -> None:
        if d:
            d["stream"].cancel()

    async def reply(self, rep_said: str | None, draft: dict | None = None):
        """Like turn(), but yields the reply as it's written, one sentence (with its trailing space) at a time.
        Tool calls run after the text that came with them has been yielded, so speech never waits on
        record_outcome. Consume it to the end: history, tools and transcript events finish with it."""
        self._turn_base, self._replying, self._cut, self.last_heard = len(self.messages), True, None, None
        try:
            async for s in self._reply(rep_said, draft):
                yield s
        finally:
            self._replying = False
            if self._cut is not None:
                self._apply_cut(self._cut)

    async def _reply(self, rep_said: str | None, draft: dict | None):
        if rep_said:
            bus.emit(self.case_id, "transcript", "caller", speaker="rep", text=rep_said)
        if not llm.available():
            self.last_say = await self._scripted_turn(rep_said)
            yield self.last_say
            return
        if draft and (draft["said"] != rep_said or draft["base"] != len(self.messages)):
            self.cancel_draft(draft)
            draft = None
        before = list(self.messages)
        self.messages = draft["messages"] if draft else self._heard(rep_said)
        parts: list[str] = []
        pressed = False  # answered a phone menu with keypad tones: no words needed this turn
        try:
            for i in range(4):  # allow tool calls, then a spoken reply
                stream = draft["stream"] if draft and i == 0 else self._llm(self.messages)
                cut, said = llm.Sentences(), []
                async for delta in stream.deltas():
                    for s in cut.feed(delta):
                        said.append(s)
                        yield s
                for s in cut.flush():
                    said.append(s)
                    yield s
                if "".join(said).strip():
                    parts.append("".join(said).strip())
                resp = stream.final
                if resp.stop_reason in ("max_tokens", "refusal"):
                    break  # never run a cut-off tool call, and keep it out of the history
                self.messages.append({"role": "assistant", "content": resp.content})
                calls = [b for b in resp.content if b.type == "tool_use"]
                if not calls:
                    break
                results = [{"type": "tool_result", "tool_use_id": c.id,
                            "content": json.dumps(await self.reg.call(c.name, c.input), default=str)} for c in calls]
                self.messages.append({"role": "user", "content": results})
                if any(c.name == "press_keys" for c in calls):
                    pressed = True
                    break  # the menu answers the tones, not us
                no = [json.loads(r["content"]) for c, r in zip(calls, results) if c.name == "check_offer"]
                no = [d for d in no if d.get("decision") == "refused"]
                if no:  # a refusal is said in the mandate's words, never left to the model
                    line = " ".join(dict.fromkeys(d["say"] for d in no if d.get("say")))
                    yield line
                    parts.append(line)
                    self.messages.append({"role": "assistant", "content": line})
                    break
                if parts and (self.ended or all(c.name == "record_outcome" for c in calls)):
                    break  # already said something this turn: a second model call only repeats it
        except Exception as e:
            self.messages = before  # keep the history valid for the next turn
            bus.emit(self.case_id, "warning", "caller", text=f"Caller LLM error: {type(e).__name__}")
            if not parts:
                parts = ["Sorry, could you say that again?"]
                yield parts[0]
        if not parts and not pressed:
            parts = ["Sorry, could you say that again?"]
            yield parts[0]
        say = " ".join(parts)
        if not self.ended and re.search(r"\b(goodbye|bye)\b", say, re.I):
            await self.reg.call("end_call", {"reason": "done"})  # model said goodbye but forgot end_call
        if say:
            bus.emit(self.case_id, "transcript", "caller", speaker="billback", text=say)
        self.last_say = say

    # Barge-in: the transport reports how much of the last reply was actually played (voice/heard.py).

    def interrupted(self, heard: str) -> None:
        """The rep talked over the reply and heard only `heard` (a word prefix of it). The history is cut to
        that, ending in "—[interrupted]", so the next turn doesn't assume they heard the rest (tool calls
        stay). Works while the reply is still being written (applied when it finishes) or after."""
        if self._replying:
            self._cut = heard
        else:
            self._apply_cut(heard)

    def _apply_cut(self, heard: str) -> None:
        self._cut = None
        msgs = [m for m in self.messages[self._turn_base:] if m["role"] == "assistant"]
        texts = [(m, b) for m in msgs for b in ([None] if isinstance(m["content"], str) else m["content"]) if _is_text(b)]
        budget, total = len(heard.split()), sum(len(_text_of(m, b).split()) for m, b in texts)
        if budget >= total:
            return  # all of it was heard
        self.last_heard = heard
        for m, b in texts:
            text = _text_of(m, b)
            if budget >= len(text.split()):
                budget -= len(text.split())
                continue
            words = list(re.finditer(r"\S+", text))
            _set_text(m, b, text[:words[budget - 1].end()] + INTERRUPTED if budget else "")
            budget = 0
        for m in msgs:  # the API rejects an assistant message with no content
            if not m["content"]:
                m["content"] = [{"type": "text", "text": INTERRUPTED}]
        bus.emit(self.case_id, "transcript_cut", "caller", heard=heard, said=self.last_say)

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

    def _held(self) -> dict:
        """The offline outcome: the hold is always asked; an assistance review only when the plan asks for one."""
        if "discount" in self._kinds():
            return {"account_on_hold": True, "assistance_review_opened": True,
                    "next_step": "Hospital reviewing financial assistance"}
        return {"account_on_hold": True, "next_step": "Hospital correcting the balance"}

    async def _scripted_turn(self, rep_said: str | None) -> str:
        """Offline: the outcome follows the script step, so it never depends on how the rep phrases things."""
        lines = self._scripted(rep_said)
        step = sum(1 for m in self.messages if m["role"] == "assistant")
        if self.brief.get("call_type") == "follow_up":
            if rep_said and step == len(lines) - 1:  # replying to the hold ask
                await self.reg.call("record_outcome", {
                    **self._held(), "case_number": _last_call(self.brief).get("case_number"),
                    "next_step": "Corrected statement pending"})
        elif rep_said and len(lines) >= 5 and step >= len(lines) - 3:  # replying to the hold ask, case-number ask or read-back
            fields = self._held()
            if step == len(lines) - 1:  # the rep confirmed the read-back, or corrected it ("no, it's FA-20931")
                said_back = _case_number(str(self.messages[-1]["content"]).removeprefix("Let me read that back: "))
                fields["case_number"] = _case_number(rep_said) if re.search(r"\d", rep_said) else said_back
            await self.reg.call("record_outcome", fields)
        say = lines[min(step, len(lines) - 1)]
        self.messages += [{"role": "user", "content": rep_said or ""}, {"role": "assistant", "content": say}]
        if step >= len(lines) - 1:
            if not self.outcome.get("account_on_hold"):
                await self.reg.call("record_outcome", self._held())
            await self.reg.call("end_call", {"reason": "done"})
        bus.emit(self.case_id, "transcript", "caller", speaker="billback", text=say)
        return say


def _is_text(b) -> bool:
    """A text block of an assistant message (None stands for a plain-string content)."""
    return b is None or getattr(b, "type", None) == "text" or (isinstance(b, dict) and b.get("type") == "text")


def _text_of(m: dict, b) -> str:
    if b is None:
        return m["content"]
    return b.get("text", "") if isinstance(b, dict) else b.text


def _set_text(m: dict, b, text: str) -> None:
    """Replace one text block of an assistant message ("" drops it); SDK blocks are kept as they are."""
    if b is None:
        m["content"] = text
        return
    m["content"] = [({"type": "text", "text": text} if x is b else x) for x in m["content"] if x is not b or text]


sessions: dict[str, CallSession] = {}

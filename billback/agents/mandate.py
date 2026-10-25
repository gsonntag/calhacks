"""What the caller may agree to on the patient's behalf: the mandate, enforced in code.

The patient approved one plan (agents/decide.py) before the call. Its brief says what the call may accept:
the corrected balance, and the interest-free payment plan when the plan has one. Anything a billing rep
offers or asks for goes through `evaluate` before the caller answers it (the caller's check_offer tool).
It fails closed: an offer kind or a piece of information this module doesn't know is refused.

Refusals are spoken with their reason, sent to the case page as a `guardrail` event and kept in the audit
log, so the patient sees every decision the call made for them.
"""
from dataclasses import dataclass

KINDS = ("payment_plan", "balance", "pay_now", "settlement", "info_request")
# what the caller may give to verify identity: only fields the brief carries (the patient's own account)
SHAREABLE = {"account_number": "account_number", "claim_number": "claim_number",
             "date_of_birth": "patient_date_of_birth", "member_id": "insurance_member_id", "name": "patient"}
NEVER_SHARED = {"ssn": "a Social Security number", "card": "card details", "bank": "bank account details",
                "password": "passwords or PINs", "other_patient": "another patient's information"}
SLACK = 0.5  # rounding on spoken amounts


@dataclass
class Decision:
    allowed: bool
    reason: str
    say: str

    def as_dict(self) -> dict:
        return {"decision": "allowed" if self.allowed else "refused", "reason": self.reason, "say": self.say}


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _num(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def evaluate(brief: dict, kind: str, monthly=None, months=None, total=None, interest_pct=None,
             info: str = "") -> Decision:
    """One offer or request from the rep, against the brief the patient approved."""
    target = _num(brief.get("balance_after_assistance"))
    plan = brief.get("proposed_payment_plan") or {}
    monthly, months, total, interest = _num(monthly), _num(months), _num(total), _num(interest_pct)

    if kind == "pay_now":
        return Decision(False, "BillBack never pays on a call",
                        "I can't make any payment on this call. The patient will set up any payments themselves.")

    if kind == "info_request":
        what = (info or "").strip().lower()
        if what in NEVER_SHARED:
            return Decision(False, f"Never shares {NEVER_SHARED[what]}",
                            f"I'm not able to give out {NEVER_SHARED[what]}. I can give you the account number instead.")
        field = SHAREABLE.get(what)
        if field and brief.get(field):
            return Decision(True, f"The patient's {what.replace('_', ' ')} is in the approved brief for verification", "")
        return Decision(False, f"“{info}” isn't something the patient approved sharing",
                        "I don't have that to share. I can give you the account number and the claim number.")

    if kind in ("balance", "settlement"):
        amount = total if total is not None else monthly
        if amount is None:
            return Decision(False, "No amount was given", "What amount would that be?")
        if target is not None and amount <= target + SLACK:
            return Decision(True, f"{_money(amount)} is at or below the corrected balance of {_money(target)}", "")
        what = "settle" if kind == "settlement" else "agree to a balance"
        return Decision(False, f"{_money(amount)} is more than the corrected balance of {_money(target or 0)}",
                        f"I can't {what} of {_money(amount)}. Based on the insurance statement and the hospital's own "
                        f"policy, the balance should be {_money(target or 0)}.")

    if kind == "payment_plan":
        if not plan or not plan.get("months"):
            return Decision(False, "The patient's plan has no payment plan to agree to",
                            "I'm not able to agree to a payment plan today. The patient will decide on that once the "
                            "balance is corrected.")
        if interest and interest > 0:
            return Decision(False, f"The plan has {interest:g}% interest; the patient approved interest-free only",
                            "The patient can only agree to an interest-free plan, as your billing policy allows.")
        cap = float(plan.get("monthly") or 0)
        if monthly is not None and monthly > cap + SLACK:
            return Decision(False, f"{_money(monthly)}/month is more than the {_money(cap)}/month the patient approved",
                            f"That's more than the patient can pay. They can do {plan.get('text') or _money(cap) + ' a month'}.")
        if total is not None and target is not None and total > target + SLACK:
            return Decision(False, f"A plan for {_money(total)} would accept more than the corrected {_money(target)}",
                            f"A plan has to be for the corrected balance of {_money(target)}, not {_money(total)}. "
                            "Can we correct the balance first?")
        if monthly is None and total is None:
            return Decision(False, "No monthly amount or total was given", "What would the monthly payment be?")
        return Decision(True, "Interest-free and within the plan the patient approved", "")

    return Decision(False, f"Unknown kind of offer: {kind}", "I'll need to check with the patient before agreeing to that.")


TOOL_SPEC = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS),
                 "description": "payment_plan: rep offers a plan · balance: rep wants you to agree an amount is owed · "
                                "settlement: a lump-sum deal · pay_now: rep asks for payment today · "
                                "info_request: rep asks for a piece of information"},
        "monthly": {"type": "number", "description": "monthly payment the rep offered"},
        "months": {"type": "number"},
        "total": {"type": "number", "description": "total or balance amount"},
        "interest_pct": {"type": "number"},
        "info": {"type": "string", "description": "for info_request: ssn, date_of_birth, member_id, account_number, "
                                                  "claim_number, name, card, bank, password, other_patient, or what was asked"},
    },
    "required": ["kind"],
}

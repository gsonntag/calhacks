"""The call's mandate (agents/mandate.py): what the caller may agree to or share, decided by code.
The caller tests use a fake Claude (tests/test_latency.py), so they run offline."""
import json

from billback.agents import caller, mandate
from billback.events import bus

from test_latency import claude, respond, tool_block  # noqa: F401  (claude is a fixture)

BRIEF = {"patient": "Rosa Martinez", "account_number": "CPMC-48213977", "claim_number": "C-2026-0815-44917",
         "patient_date_of_birth": "1991-05-22", "insurance_member_id": "GSH-7712093",
         "balance_after_assistance": 600, "goals": [],
         "proposed_payment_plan": {"months": 6, "monthly": 100.0, "total": 600.0, "text": "6 payments of $100"}}


def ok(**kw) -> bool:
    return mandate.evaluate(BRIEF, **kw).allowed


def test_payment_plans_only_within_what_the_patient_approved():
    assert ok(kind="payment_plan", monthly=100, months=6)
    assert ok(kind="payment_plan", monthly=75, total=600)            # cheaper is fine
    assert not ok(kind="payment_plan", monthly=300, months=3)        # more per month than approved
    assert not ok(kind="payment_plan", monthly=100, total=8700)      # a plan on the uncorrected balance
    assert not ok(kind="payment_plan", monthly=100, interest_pct=12)
    assert not ok(kind="payment_plan")                               # no amount: ask, don't agree
    assert not ok(kind="payment_plan", monthly=100, months=87)       # same monthly, longer: a bigger total
    assert not ok(kind="payment_plan", monthly=100, months=7)
    assert ok(kind="payment_plan", monthly=100, months=5)            # shorter and cheaper is fine
    assert not ok(kind="payment_plan", monthly=90, months=12)        # $1,080 is more than the corrected $600
    last = {**BRIEF, "proposed_payment_plan": {"months": 6, "monthly": 100.0, "last": 50.0, "total": 550.0},
            "balance_after_assistance": 550}
    assert mandate.evaluate(last, "payment_plan", monthly=100, months=6).allowed  # the approved plan, smaller last one
    no_plan = {**BRIEF, "proposed_payment_plan": None}
    assert not mandate.evaluate(no_plan, "payment_plan", monthly=50).allowed


def test_balances_and_payments():
    assert ok(kind="balance", total=600) and ok(kind="balance", total=0)
    d = mandate.evaluate(BRIEF, "balance", total=1100)
    assert not d.allowed and "$600" in d.say and "$1,100" in d.reason
    assert not ok(kind="settlement", total=900)
    assert not ok(kind="pay_now", total=50)                          # never pays, whatever the amount


def test_information_fails_closed():
    assert ok(kind="info_request", info="date_of_birth") and ok(kind="info_request", info="member_id")
    for what in ("ssn", "card", "bank", "password", "other_patient", "mother's maiden name"):
        assert not ok(kind="info_request", info=what), what
    assert not mandate.evaluate({**BRIEF, "patient_date_of_birth": None}, "info_request", info="date_of_birth").allowed
    assert not ok(kind="something_new")


def test_information_names_are_normalised():
    for what in ("DOB", "birthdate", "Date of Birth", "birth-date", "member number", "Insurance ID", "policy_number",
                 "account", "Account Number", "claim", "name"):
        assert ok(kind="info_request", info=what), what
    for what in ("social", "Social Security Number", "SSN", "credit card", "card_number", "debit-card",
                 "bank account", "routing", "favourite colour"):
        assert not ok(kind="info_request", info=what), what
    assert not mandate.evaluate({**BRIEF, "insurance_member_id": None}, "info_request", info="policy number").allowed


async def test_a_refusal_is_spoken_in_the_mandates_words(claude):
    """The rep pushes a $300/month plan; Claude checks it; the code says no, not the model."""
    claude.script = [respond("", tool_block("check_offer", {"kind": "payment_plan", "monthly": 300, "months": 3}))]
    s = caller.CallSession("m1", BRIEF)
    said = [x async for x in s.reply("I can do three hundred a month starting today. Should I enrol you?")]
    want = mandate.evaluate(BRIEF, "payment_plan", monthly=300, months=3).say
    assert said == [want + " "] and s.last_say == want                # trailing space for TTS, like every sentence
    assert len(claude.created) == 1                                  # no second model call to talk past it
    g = [e for e in bus.history("m1") if e["kind"] == "guardrail"]
    assert g[0]["decision"] == "refused" and g[0]["offer"] == "payment_plan" and g[0]["monthly"] == 300
    assert [r["offer"] for r in s.refused] == ["payment_plan"]
    assert s.messages[-1] == {"role": "assistant", "content": want}  # the history matches what was said
    tr = [(e["speaker"], e["text"]) for e in bus.history("m1") if e["kind"] == "transcript"]
    assert tr[-1] == ("billback", want)


async def test_an_allowed_check_lets_claude_answer(claude):
    claude.script = [respond("", tool_block("check_offer", {"kind": "info_request", "info": "date_of_birth"})),
                     respond("Sure, her date of birth is May 22, 1991.")]
    s = caller.CallSession("m2", BRIEF)
    said = "".join([x async for x in s.reply("Can you verify the date of birth?")])
    assert said == "Sure, her date of birth is May 22, 1991." and not s.refused
    result = json.loads(s.messages[2]["content"][0]["content"])
    assert result["decision"] == "allowed"

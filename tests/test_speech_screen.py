"""The speech screen (mandate.screen): the last check on every sentence BillBack speaks, whether or not the
model called check_offer. The caller tests use a fake Claude (tests/test_latency.py), so they run offline."""
import pytest

from billback.agents import caller, mandate
from billback.events import bus

from test_latency import claude, respond, tool_block  # noqa: F401  (claude is a fixture)
from test_mandate import BRIEF


def rules(text, allowed=(), heard=()):
    return [b["rule"] for b in mandate.screen(text, BRIEF, allowed, heard)[1]]


@pytest.mark.parametrize("text", [
    "Could you apply the insurance adjustment?",
    "The patient will set up any payments themselves.",
    "I can't make any payment on this call.",
    "Let me read that back: FA20931. Is that right?",
    "I can't agree to pay that.",
    "I'm not able to agree to a payment plan today.",
    "We won't pay the $8,700 on the statement.",
    "How much would I need to pay?",
    "Please don't charge the card.",
    "Yes, go ahead.",
    "Could you enroll her in financial assistance?",
    "I agree that the balance should be $600.",
    "I'm an automated assistant calling on behalf of Rosa Martinez, with her authorization.",
    "Your statement shows $8,700, but the EOB for claim C-2026-0815-44917 lists $1,100.50.",
    "The account number is CPMC-48213977 and her member ID is GSH-7712093.",
    "Her date of birth is 1991-05-22.",
    "Call (415) 555-1212 or 415-555-1212.",
    "That's account 48213977 6 months ago.",
    "Once it's corrected to $600, could you set up an interest-free payment plan of 6 monthly payments of $100?",
    # confirming what the patient approved, asking it back, and words that only look like promises
    "So the patient would pay $100 a month for six months, interest-free?",
    "Just to confirm, the patient will pay $600 total once corrected.",
    "Once it's corrected, the patient can pay the remaining balance.",
    "Sure, the patient will accept the corrected balance of $600.",
    "I'd like to make a payment plan request.",
    "We will pay attention to that.",
])
def test_normal_lines_pass(text):
    assert mandate.screen(text, BRIEF) == (text, [])


@pytest.mark.parametrize("text, rule", [
    ("Her SSN is 123-45-6789.", "pii"),
    ("It's 123456789.", "pii"),
    ("It's one two three, four five, six seven eight nine.", "pii"),
    ("The card is 4111 1111 1111 1111.", "pii"),
    ("Card number 4111111111111111.", "pii"),
    ("Sure, I agree to pay $300 a month.", "commit"),
    ("We'll pay.", "commit"),
    ("I'd be happy to pay that.", "commit"),
    ("I can't, but we'll pay $300.", "commit"),        # the negation is in another clause
    ("I accept the payment plan.", "commit"),
    ("We'll take that plan.", "commit"),
    ("Please go ahead and enroll her.", "commit"),
    ("Yes, set it up.", "commit"),
    ("Deal.", "commit"),
    ("Once it's corrected, we'll pay the $8,700.", "commit"),             # more than the corrected balance
    ("Once it's corrected, the patient will pay $300 a month.", "commit"),  # more than the approved monthly
    ("Once it's corrected, we'll pay three hundred.", "commit"),           # an amount in words can't be checked
    ("I agree to pay $300, okay?", "commit"),                             # a tag question is still a promise
    ("Absolutely, put her down for $300 a month.", "commit"),
    ("You can bill the card on file.", "pay_now"),
    ("Her SSN ends in 6789.", "pii"),
    ("Her social security number ends with 6789.", "pii"),
    ("I can pay $100 today.", "pay_now"),
    ("Go ahead and charge the card.", "pay_now"),
    ("I'm a real person.", "human"),
    ("No, I'm not a robot.", "human"),
    ("I'm not an AI.", "human"),
])
def test_blocked_lines(text, rule):
    safe, blocked = mandate.screen(text + " ", BRIEF)
    assert [b["rule"] for b in blocked] == [rule]
    assert safe == mandate.SAY[rule] + " "                  # the code's words, trailing space kept for TTS
    assert not any(c.isdigit() for c in blocked[0]["reason"] + safe)  # never echoes the number


def test_an_allowed_check_offer_lets_the_caller_agree_but_never_pay_now():
    assert rules("Great, we'll take that plan.", allowed={"payment_plan"}) == []
    assert rules("I agree to pay $100 a month.", allowed={"balance"}) == []
    assert rules("I agree to pay $100 a month.", allowed={"info_request"}) == ["commit"]
    assert rules("Go ahead and charge the card.", allowed={"payment_plan"}) == ["pay_now"]
    assert rules("Her SSN is 123-45-6789.", allowed={"payment_plan"}) == ["pii"]


def test_cleared_promises_stay_within_the_allowed_amounts():
    allowed = {"payment_plan": {"total": 600, "monthly": 100}}
    assert rules("Great, we'll pay $100 a month.", allowed=allowed) == []
    assert rules("Great, we'll pay $300 a month.", allowed=allowed) == ["commit"]
    assert rules("Great, we'll pay $8,700.", allowed=allowed) == ["commit"]
    assert rules("Go ahead and charge the card.", allowed=allowed) == ["pay_now"]


def test_policy_numbers_pass_only_when_they_are_the_patients():
    line = "She has insurance through Blue Shield, policy 123 456 789."
    assert rules(line) == ["pii"]                                              # 9 digits nobody gave us
    assert mandate.screen(line, {**BRIEF, "insurance_member_id": "123456789"})[1] == []


def test_every_number_in_the_brief_may_be_said():
    brief = {**BRIEF, "previous_calls": [{"date": "2026-10-01", "rep": "Dana", "case_number": "482913377"}],
             "payments_made": [{"date": "2026-03-03", "amount": 250, "reference": "482913378"}]}
    assert mandate.screen("I'm following up on case number 482913377.", brief)[1] == []
    assert mandate.screen("The payment reference is 4829 13378.", brief)[1] == []
    assert mandate.screen("The payment reference is 482913379.", brief)[1]     # not in the brief


def test_brief_values_and_read_backs_of_long_reference_numbers():
    assert rules("The claim number is 2026 0815 44917.") == []         # in the brief, grouped differently
    assert rules("The claim number is 2026 0815 44917.") == []
    assert rules("Let me read that back: 2026-1234-56789.") == ["pii"]  # 13 digits nobody gave us
    assert rules("Let me read that back: 2026-1234-56789.", heard=["It's 2026-1234-56789."]) == []
    assert rules("Is it 123-45-6789?", heard=["Is her SSN 123-45-6789?"]) == ["pii"]  # never confirms an SSN
    assert rules("Let me read that back: 482913377.", heard=["Your reference is 482913377."]) == []  # 9 digits too
    assert rules("Let me read that back: 482913377.") == ["pii"]
    no_claim = {**BRIEF, "claim_number": None}
    assert mandate.screen("Claim C-2026-0815-44917.", no_claim)[1]     # not in this brief: fails closed


async def test_the_model_agreeing_without_check_offer_is_screened(claude):
    """The model skips check_offer and agrees; the screen speaks the code's line, not the model's."""
    claude.script = [respond("Sure, I agree to pay $300 a month.")]
    s = caller.CallSession("sp1", BRIEF)
    said = [x async for x in s.reply("I can do three hundred a month. Want me to enrol you?")]
    safe = mandate.SAY["commit"]
    assert said == [safe] and s.last_say == safe
    g = [e for e in bus.history("sp1") if e["kind"] == "guardrail"]
    assert len(g) == 1 and g[0]["offer"] == "speech" and g[0]["decision"] == "refused" and g[0]["say"] == safe
    assert s.messages[-1] == {"role": "assistant", "content": [{"type": "text", "text": safe}]}
    tr = [(e["speaker"], e["text"]) for e in bus.history("sp1") if e["kind"] == "transcript"]
    assert tr[-1] == ("billback", safe)


async def test_screen_keeps_streaming_and_tools(claude):
    """Clean sentences go out as written, a blocked one is replaced in place, a repeat of the safe line is
    dropped, and tool calls in the same response still run."""
    claude.script = [respond("Thank you. I agree to pay that. We'll pay it monthly. Is the account on hold?",
                             tool_block("record_outcome", {"next_step": "review", "account_on_hold": True,
                                                           "assistance_review_opened": False}))]
    s = caller.CallSession("sp2", BRIEF)
    said = [x async for x in s.reply("We can do a plan.")]
    assert said == ["Thank you. ", mandate.SAY["commit"] + " ", "Is the account on hold?"]
    assert s.outcome["account_on_hold"] is True
    blocks = s.messages[1]["content"]
    assert blocks[0] == {"type": "text", "text": "".join(said).strip()} and blocks[1].type == "tool_use"


async def test_after_an_allowed_check_offer_the_agreement_is_spoken(claude):
    claude.script = [respond("", tool_block("check_offer", {"kind": "payment_plan", "monthly": 100, "months": 6})),
                     respond("Great, we'll take that plan at $100 a month.")]
    s = caller.CallSession("sp3", BRIEF)
    said = "".join([x async for x in s.reply("We can do six payments of $100, interest-free.")])
    assert said == "Great, we'll take that plan at $100 a month."
    assert [e["offer"] for e in bus.history("sp3") if e["kind"] == "guardrail"] == ["payment_plan"]


async def test_scripted_path_is_screened(monkeypatch):
    """Offline (no Claude key) lines pass through the same screen."""
    monkeypatch.setattr(caller.llm, "available", lambda: False)
    monkeypatch.setattr(caller.CallSession, "_scripted", lambda self, rep: ["Her SSN is 123-45-6789."])
    s = caller.CallSession("sp4", {**BRIEF, "call_type": "first"})
    assert await s.turn(None) == mandate.SAY["pii"]
    assert s.messages[-1]["content"] == mandate.SAY["pii"]
    assert [e["offer"] for e in bus.history("sp4") if e["kind"] == "guardrail"] == ["speech"]


def test_zip_plus_four_is_not_an_ssn():
    brief = {"account_number": "CPMC-48213977"}
    for line in ("The mailing address is 2210 Sacramento St, Berkeley, CA 94702-1234.",
                 "Her ZIP code is 94702-1234."):
        assert mandate.screen(line, brief)[1] == [], line
    assert mandate.screen("Her number is 123-45-6789.", brief)[1]           # SSN shape still blocked
    assert mandate.screen("It's 947021234.", brief)[1]                      # bare 9 digits: can't tell, blocked


async def test_an_allowed_check_offer_clears_promises_only_briefly(claude):
    """An allowed plan clears the agreement right after it and one more model call, not the rest of the call."""
    claude.script = [respond("", tool_block("check_offer", {"kind": "payment_plan", "monthly": 100, "months": 6})),
                     respond("Great, we'll take that plan at $100 a month."),
                     respond("Yes, we'll pay $100 a month."),
                     respond("We'll pay $100 a month.")]
    s = caller.CallSession("sp5", BRIEF)
    assert await s.turn("We can do six payments of $100, interest-free.") == "Great, we'll take that plan at $100 a month."
    assert await s.turn("So that's $100 a month?") == "Yes, we'll pay $100 a month."
    assert await s.turn("Anything else?") == mandate.SAY["commit"]               # expired


async def test_an_allowed_check_offer_clears_only_its_amounts(claude):
    claude.script = [respond("", tool_block("check_offer", {"kind": "payment_plan", "monthly": 100, "months": 6})),
                     respond("Great, we'll pay $300 a month.")]
    s = caller.CallSession("sp6", BRIEF)
    assert await s.turn("We can do six payments of $100, interest-free.") == mandate.SAY["commit"]


FOLLOW_UP = {**BRIEF, "call_type": "follow_up", "correct_balance": 600, "hospital_says_due": 8700,
             "previous_calls": [{"date": "2026-10-01", "rep": "Dana", "case_number": "482913377",
                                 "promised": ["Apply the insurance adjustment"]}]}


async def test_a_follow_up_opener_keeps_a_nine_digit_case_number(monkeypatch):
    monkeypatch.setattr(caller.llm, "available", lambda: False)
    s = caller.CallSession("sp7", FOLLOW_UP)
    said = await s.turn(None)
    assert said.startswith("Hi, this is an automated assistant") and "case number 482913377" in said
    assert not [e for e in bus.history("sp7") if e["kind"] == "guardrail"]


async def test_a_blocked_sentence_doesnt_take_the_disclosure_with_it(monkeypatch):
    monkeypatch.setattr(caller.llm, "available", lambda: False)
    monkeypatch.setattr(caller.CallSession, "_scripted",
                        lambda self, rep: ["Hi, this is an automated assistant for Rosa. Her SSN is 123-45-6789."])
    s = caller.CallSession("sp8", {**BRIEF, "call_type": "first"})
    assert await s.turn(None) == "Hi, this is an automated assistant for Rosa. " + mandate.SAY["pii"]


OFFLINE = {"patient": "Rosa Martinez", "account_number": "CPMC-48213977", "claim_number": "C-2026-0815-44917",
           "insurer": "Golden State", "hospital_says_due": 8700, "eob_patient_responsibility": 1100, "payments_made": [],
           "correct_balance": 1100, "balance_after_assistance": 1100, "ask_kinds": ["insurance", "hold", "case_number"],
           "goals": []}


async def offline_call(case_id, reference):
    s = caller.CallSession(case_id, OFFLINE)
    said = []
    for rep in [None, "Hello", "ok", "ok", "Done, it's on hold", reference, "Yes that's right", "bye"]:
        if s.ended:
            break
        said.append(await s.turn(rep))
    return s, said


async def test_offline_read_back_of_a_nine_digit_reference(monkeypatch):
    monkeypatch.setattr(caller.llm, "available", lambda: False)
    s, said = await offline_call("sp9", "Your reference is 482913377")
    assert "Let me read that back: 482913377. Is that right?" in said
    assert s.outcome["case_number"] == "482913377" and s.outcome["case_number_confirmed"] is True


async def test_offline_blocked_read_back_is_not_recorded(monkeypatch):
    monkeypatch.setattr(caller.llm, "available", lambda: False)
    s, said = await offline_call("sp10", "Your reference is 482913377, same as her social")
    assert said[-2] == mandate.SAY["pii"] + " Is that right?"              # the rep's number with "social": not read back
    assert "case_number" not in s.outcome and s.outcome["account_on_hold"] is True

"""Cartesia track upgrades, offline: remembering only what the rep heard (Sonic word timestamps + played
audio, phone and desk), desk mode listening through Cartesia Ink with barge-in and the browser fallback,
and reading case numbers back before recording them."""
import asyncio
import base64
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from remy import config
from remy.agents import caller
from remy.events import bus
from remy.voice import calls, desk, heard, media, pronounce, voices
from remy.voice.cartesia_ws import PCM_24K, STT, TTS
from remy.voice.routes import router
from tests.test_latency import FakeSonicWS, claude, respond, tool_block, wait_for  # noqa: F401  (claude is a fixture)
from tests.test_voice import FakeSTT, FakeTTS, FakeTwilio, fakes  # noqa: F401  (fakes is a fixture)

# ---------------------------------------------------------------- heard.Spoken: cutting a reply at what played


def spoken_two_sentences() -> heard.Spoken:
    s = heard.Spoken(8000)
    s.begin("Thank you, Diane. ")
    s.audio(8000)                      # 1.0 s
    s.begin("Your statement shows $8,700, but the EOB says $1,100. ")
    s.audio(16000)                     # 2.0 s
    return s


def test_spoken_without_timestamps_cuts_by_played_fraction():
    s = spoken_two_sentences()
    assert s.heard(0) == "" and s.heard(0.5) == "Thank"
    assert s.heard(1.0) == "Thank you, Diane."
    assert s.heard(1.5) == "Thank you, Diane. Your statement shows"  # "$8,700" is 5 spoken words: not reached
    assert s.heard(2.0) == "Thank you, Diane. Your statement shows $8,700,"
    assert s.heard(3.0) == s.heard(99) == s.text().strip()
    assert s.boundaries() == [1.0] and s.seconds == 3.0


def test_spoken_with_sonic_word_timestamps():
    s = spoken_two_sentences()
    words = "Your statement shows eight thousand seven hundred dollars but the EOB says one thousand one hundred dollars"
    s.timestamps(words.split(), [0.1 * (i + 1) for i in range(len(words.split()))])
    assert s.heard(1.65) == "Thank you, Diane. Your statement shows"   # "eight thousand seven" said, not the amount
    assert s.heard(1.8) == "Thank you, Diane. Your statement shows $8,700,"


def test_spoken_drops_a_retried_request_without_audio():
    s = heard.Spoken(8000)
    s.begin("Hello there. ")
    s.begin("Hello there. ")  # concurrency retry: the first try produced nothing
    s.audio(8000)
    assert len(s.segments) == 1 and s.heard(5) == "Hello there."


def test_is_echo():
    said = "Your statement shows $8,700, but the EOB says $1,100."
    assert heard.is_echo("statement shows eight thousand seven hundred", said)
    assert not heard.is_echo("wait, that's not right", said) and not heard.is_echo("shows", said)


# ---------------------------------------------------------------- CallSession.interrupted


def text(t):
    return SimpleNamespace(type="text", text=t)


async def test_interrupted_after_the_reply_cuts_history_keeps_tools(claude):
    claude.script = [respond("Thank you, Diane. Could you apply the insurance adjustment to the account?",
                             tool_block("record_outcome", {"next_step": "x", "account_on_hold": True,
                                                           "assistance_review_opened": False}))]
    s = caller.CallSession("h1", {"goals": []})
    await s.turn("Hi, this is Diane.")
    s.interrupted("Thank you, Diane. Could you")
    blocks = s.messages[1]["content"]
    assert blocks[0] == {"type": "text", "text": "Thank you, Diane. Could you—[interrupted]"}
    assert blocks[1].type == "tool_use" and s.messages[2]["content"][0]["type"] == "tool_result"
    assert s.last_heard == "Thank you, Diane. Could you"
    assert any(e["kind"] == "transcript_cut" for e in bus.history("h1"))
    # nothing heard at all: the API still gets a non-empty assistant message
    claude.script = [respond("Sure. One more thing.")]
    await s.turn("Hold on.")
    s.interrupted("")
    assert s.messages[-1]["content"] == [{"type": "text", "text": caller.INTERRUPTED}]


async def test_interrupted_while_replying_is_applied_when_the_reply_ends(claude):
    claude.script = [respond("First part here. Second part here.")]
    s = caller.CallSession("h2", {"goals": []})
    gen = s.reply("Hello?")
    assert await gen.__anext__() == "First part here. "
    s.interrupted("First part")
    assert s.messages[-1]["role"] == "user"  # not applied yet: the reply is still being written
    rest = [x async for x in gen]
    assert rest == ["Second part here."]
    assert s.messages[-1]["content"][0]["text"] == "First part—[interrupted]"
    s.interrupted("First part here. Second part here.")  # everything heard: no change
    assert s.messages[-1]["content"][0]["text"] == "First part—[interrupted]"


async def test_interrupted_scripted_turn():
    s = caller.CallSession("h3", {"goals": [], "patient": "Rosa", "account_number": "BCH-1"})
    s._scripted = lambda said: ["Hi, this is an automated assistant calling about the account.", "Bye."]
    await s.turn("Hello?")
    s.interrupted("Hi, this is an")
    assert s.messages[-1] == {"role": "assistant", "content": "Hi, this is an—[interrupted]"}


# ---------------------------------------------------------------- Sonic: timestamps recorded per request


async def test_stream_many_records_text_audio_and_timestamps():
    tts = TTS(voices.default())

    class WS(FakeSonicWS):
        async def send(self, data):
            msg = json.loads(data)
            assert msg["add_timestamps"] is True
            q = self.tts._queues[msg["context_id"]]
            q.put_nowait({"type": "timestamps", "context_id": msg["context_id"],
                          "word_timestamps": {"words": ["Hi", "Diane"], "start": [0, 0.2], "end": [0.2, 0.5]}})
            await super().send(data)

    tts.ws = WS(tts)
    sp = heard.Spoken(tts.bytes_per_s)

    async def texts():
        yield "Hi Diane. "

    audio = [c async for c in tts.stream_many(texts(), "say-9", spoken=sp)]
    assert audio == [b"a", b"b"] and sp.total == 2
    assert [(g.text, g.nbytes, g.words, g.ends) for g in sp.segments] == [("Hi Diane. ", 2, ["Hi", "Diane"], [0.2, 0.5])]
    assert TTS(voices.default(), PCM_24K).bytes_per_s == 48000 and tts.bytes_per_s == 8000


def test_stt_url_for_the_browser_mic():
    q = parse_qs(urlsplit(STT(encoding="pcm_s16le", sample_rate=16000).url()).query)
    assert q["encoding"] == ["pcm_s16le"] and q["sample_rate"] == ["16000"]


# ---------------------------------------------------------------- phone: Twilio marks -> what was heard


class CutBrain:
    def __init__(self):
        self.ended, self.cut = False, []

    async def reply(self, said, draft=None):
        yield "Thank you, Diane. "
        yield "Your statement shows the wrong balance for the visit."

    def interrupted(self, heard_text):
        self.cut.append(heard_text)


async def test_phone_barge_in_remembers_only_what_twilio_played(fakes):
    brain = CutBrain()
    leg = calls.register(media.Leg("p1", voices.default(), brain=brain, greet_after=None))
    tw = FakeTwilio(leg.token, [], echo_marks=False)
    run = asyncio.create_task(media.MediaSession(tw, calls.legs.get).run())
    await wait_for(lambda: FakeSTT.instances)
    stt, s = FakeSTT.instances[0], leg.session
    stt.push({"type": "turn.end", "transcript": "Hi, this is Diane."})
    await wait_for(lambda: any(m["event"] == "mark" and m["mark"]["name"] == "say-1" for m in tw.sent))
    marks = [m["mark"]["name"] for m in tw.sent if m["event"] == "mark"]
    assert marks == ["say-1.2", "say-1"]  # a progress mark where the second Sonic request starts
    # FakeTTS: 3 x 400 bytes = 0.15 s per sentence. Nothing echoed yet: we can't be past the unechoed mark.
    assert s.played_s() <= 0.15
    tw.inbox.put_nowait({"event": "mark", "streamSid": "MZ1", "mark": {"name": "say-1.2"}})  # Twilio played sentence 1
    await wait_for(lambda: s._echo is not None)
    assert 0.15 <= s.played_s() < 0.2
    stt.push({"type": "turn.update", "transcript": "sorry, which balance"})
    await wait_for(lambda: brain.cut)
    assert tw.sent[-1]["event"] == "clear" and brain.cut == ["Thank you, Diane."]
    await tw.close()
    await run


# ---------------------------------------------------------------- desk: streamed turn + interrupt


class RecordingSonic:
    """stream_many that records into `spoken` like TTS does: each sentence is 1 s of 24 kHz audio."""

    def __init__(self, gate: asyncio.Event | None = None):
        self.cancelled, self.gate = [], gate

    async def ensure(self):
        pass

    async def stream_many(self, texts, prefix, spoken=None):
        async for t in texts:
            if prefix in self.cancelled:
                continue
            spoken.begin(t)
            spoken.audio(48000)
            yield b"\x00" * 48000
            if self.gate:
                await self.gate.wait()

    async def cancel(self, prefix):
        self.cancelled.append(prefix)


async def test_desk_interrupt_after_the_turn_cuts_to_the_played_seconds(claude, monkeypatch):
    monkeypatch.setattr(desk, "_tts_for", lambda v: RecordingSonic())
    claude.script = [respond("Thank you, Diane. Your statement shows the wrong balance.")]
    s = caller.CallSession("d1", {"goals": []})
    caller.sessions[s.id] = s
    try:
        lines = [json.loads(b) async for b in desk.events(s, "Hi, this is Diane.")]
        assert lines[-1]["interrupted"] is False
        out = await desk.interrupt(s.id, 1.5)  # the browser had played 1.5 s
        assert out == {"ok": True, "heard": "Thank you, Diane. Your statement shows"}
        assert s.messages[-1]["content"][0]["text"] == "Thank you, Diane. Your statement shows—[interrupted]"
        assert (await desk.interrupt("nope", 1.0)).status_code == 404
    finally:
        caller.sessions.pop(s.id, None)


async def test_desk_interrupt_mid_turn_stops_sonic(claude, monkeypatch):
    gate = asyncio.Event()
    sonic = RecordingSonic(gate)
    monkeypatch.setattr(desk, "_tts_for", lambda v: sonic)
    claude.script = [respond("Thank you, Diane. Your statement shows the wrong balance.")]
    s = caller.CallSession("d2", {"goals": []})
    caller.sessions[s.id] = s
    try:
        stream = desk.events(s, "Hi.")
        got = []
        async for b in stream:
            got.append(json.loads(b))
            if got[-1]["type"] == "audio":
                break
        out = await desk.interrupt(s.id, 0.7)
        assert out["heard"] == "Thank you," and sonic.cancelled  # Sonic stopped for this reply
        gate.set()
        got += [json.loads(b) async for b in stream]
        assert got[-1]["type"] == "done" and got[-1]["interrupted"] is True
        assert sum(e["type"] == "audio" for e in got) == 1  # nothing generated after the cut
        assert s.messages[-1]["content"][0]["text"] == "Thank you,—[interrupted]"
    finally:
        caller.sessions.pop(s.id, None)


async def test_interrupt_route():
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/api/calls/nope/interrupt", json={"played_s": 1})).status_code == 404


# ---------------------------------------------------------------- desk: listening through Ink


class FakeWS:
    def __init__(self):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent, self.closed = [], None

    async def accept(self):
        pass

    async def receive(self):
        return await self.inbox.get()

    async def send_text(self, data):
        self.sent.append(json.loads(data))

    async def close(self, code=1000):
        self.closed = code


class FakeInk(FakeSTT):
    fail: Exception | None = None

    def __init__(self, keyterms=(), **kw):
        super().__init__(keyterms)
        self.kw, self.audio = kw, []

    async def connect(self):
        if FakeInk.fail:
            raise FakeInk.fail

    async def send(self, data):
        self.audio.append(data)


@pytest.fixture
def ink(monkeypatch):
    monkeypatch.setattr(config, "CARTESIA_API_KEY", "k")
    monkeypatch.setattr(desk, "STT", FakeInk)
    FakeSTT.instances, FakeInk.fail = [], None
    s = caller.CallSession("l1", {"goals": [], "patient": "Rosa Martinez", "hospital": "Bayview", "insurer": "GSHP"})
    caller.sessions[s.id] = s
    yield s
    caller.sessions.pop(s.id, None)
    desk._turns.pop(s.id, None)


async def test_listen_relays_mic_to_ink_and_turn_events_back(ink):
    ws = FakeWS()
    task = asyncio.create_task(desk.listen(ws, ink.id))
    await wait_for(lambda: ws.sent)
    assert ws.sent == [{"type": "ready"}]
    stt = FakeSTT.instances[0]
    assert stt.kw == {"encoding": "pcm_s16le", "sample_rate": 16000} and "Rosa Martinez" in stt.keyterms
    ws.inbox.put_nowait({"type": "websocket.receive", "bytes": b"\x01" * 5000})
    ws.inbox.put_nowait({"type": "websocket.receive", "bytes": b"\x01" * 2000})
    await wait_for(lambda: len(stt.audio) == 2)
    assert [len(a) for a in stt.audio] == [3200, 3200]  # 100 ms frames of 16 kHz pcm_s16le
    desk._turns[ink.id] = desk.DeskTurn(heard.Spoken(48000), None, "p", said=["Your statement shows $8,700."])
    for ev in ({"type": "turn.start"}, {"type": "turn.update", "transcript": "mm-hmm"},
               {"type": "turn.update", "transcript": "statement shows eight thousand"},
               {"type": "turn.end", "transcript": "Wait, which statement?"}):
        stt.push(ev)
    await wait_for(lambda: len(ws.sent) == 5)
    assert ws.sent[1:] == [
        {"type": "start"},
        {"type": "partial", "text": "mm-hmm", "barge": False, "echo": False},
        {"type": "partial", "text": "statement shows eight thousand", "barge": True, "echo": True},  # our own voice
        {"type": "end", "text": "Wait, which statement?", "barge": True, "echo": False}]
    ws.inbox.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(task, 1)


@pytest.mark.parametrize("why", ["no key", "402", "ink error"])
async def test_listen_says_unavailable_so_the_page_falls_back(ink, monkeypatch, why):
    ws = FakeWS()
    if why == "no key":
        monkeypatch.setattr(config, "CARTESIA_API_KEY", "")
    elif why == "402":
        FakeInk.fail = RuntimeError("rejected")
        FakeInk.fail.response = SimpleNamespace(status_code=402)
    task = asyncio.create_task(desk.listen(ws, ink.id))
    if why == "ink error":
        await wait_for(lambda: FakeSTT.instances)
        FakeSTT.instances[0].push({"type": "error", "message": "Model credits limit reached"})
    await asyncio.wait_for(task, 1)
    last = ws.sent[-1]
    assert last["type"] == "unavailable" and ws.closed is not None
    assert {"no key": "no Cartesia key", "402": "Cartesia Ink unavailable (HTTP 402)",
            "ink error": "Cartesia Ink error: Model credits limit reached"}[why] == last["reason"]


async def test_listen_unknown_call():
    ws = FakeWS()
    await desk.listen(ws, "nope")
    assert ws.sent == [{"type": "unavailable", "reason": "no such call"}] and ws.closed == 1008


# ---------------------------------------------------------------- reading IDs back


@pytest.mark.parametrize("line,spoken", [
    ("Let me read that back: FA209-31. Is that right?",
     "Let me read that back: <spell>F A 2 0 9</spell> dash <spell>3 1</spell>. Is that right?"),
    ("Let me read that back: FA 20931. Is that right?",
     "Let me read that back: <spell>F A</spell>, <spell>2 0 9 3 1</spell>. Is that right?"),
    ("Let me read it back, case number 58213.", "Let me read it back, case number <spell>5 8 2 1 3</spell>."),
    ("Let me read that back: Diane.", "Let me read that back: Diane."),
])
def test_read_back_is_spelled_one_character_at_a_time(line, spoken):
    assert pronounce.normalize(line) == spoken and pronounce.normalize(spoken) == spoken
    assert pronounce.normalize("Case FA209-31 is open.") == "Case <spell>FA 209 31</spell> is open."  # elsewhere: unchanged


def test_prompt_asks_for_a_read_back():
    assert "Let me read that back:" in caller.SYSTEM and "—[interrupted]" in caller.SYSTEM


async def test_case_number_counts_as_confirmed_only_after_a_read_back(claude):
    rec = lambda n, i: tool_block("record_outcome", {"case_number": n, "next_step": "review",  # noqa: E731
                                                     "account_on_hold": True, "assistance_review_opened": True}, i)
    claude.script = [respond("Thank you, noted.", rec("FA209-31", "t1"))]
    s = caller.CallSession("r1", {"goals": [], "claim_number": "C-2026-0815-44917"})
    await s.turn("Your case number is FA209-31.")
    assert s.outcome["case_number"] == "FA209-31" and s.outcome["case_number_confirmed"] is False
    assert "Read the case number back" in json.loads(s.messages[-1]["content"][0]["content"])["note"]
    claude.script = [respond("Let me read that back: FA209-31. Is that right?")]
    await s.turn("Okay.")
    claude.script = [respond("Thank you.", rec("FA-20981", "t2"))]
    await s.turn("No, it's FA-20981.")  # the corrected number hasn't been read back yet
    assert s.outcome["case_number"] == "FA-20981" and s.outcome["case_number_confirmed"] is False
    claude.script = [respond("Let me read that back: FA-20981. Is that right?")]
    await s.turn("Okay.")
    claude.script = [respond("Great, thank you.", rec("FA-20981", "t3"))]
    await s.turn("Yes, that's right.")
    assert s.outcome["case_number_confirmed"] is True
    claude.script = [respond("Thanks.", rec("C-2026-0815-44917", "t4"))]
    await s.turn("And the claim?")
    assert s.outcome["case_number_confirmed"] is True  # a number from the brief needs no read-back


async def test_case_number_read_inside_the_same_turn_is_not_confirmed(claude):
    claude.script = [respond("Let me read that back: FA-20931. Is that right?",
                             tool_block("record_outcome", {"case_number": "FA-20931", "next_step": "x",
                                                           "account_on_hold": True, "assistance_review_opened": True}))]
    s = caller.CallSession("r2", {"goals": []})
    await s.turn("It's FA-20931.")
    assert s.outcome["case_number_confirmed"] is False  # the rep hasn't answered the read-back yet


@pytest.mark.parametrize("answer,number", [("Yes, that's right.", "FA20931"), ("No, it's FA-20981.", "FA-20981")])
async def test_scripted_call_reads_the_case_number_back(answer, number):
    from tests.test_voice import _brief
    from remy import orchestrator
    case = orchestrator.new_case()
    s = caller.CallSession(case["id"], _brief(case))
    for line in ["Billing, this is Diane.", "Okay.", "I see it.", "Okay.", "Yes, it's on hold."]:
        await s.turn(line)
    assert await s.turn("Your case number is FA 20931.") == "Let me read that back: FA20931. Is that right?"
    assert "case_number" not in s.outcome
    assert "Goodbye" in await s.turn(answer) and s.ended
    assert s.outcome["case_number"] == number
    assert s.outcome["case_number_confirmed"] is (number == "FA20931")


# ---------------------------------------------------------------- phone menus (keypad tones) and hold


def _strongest(pcm: list[int], freqs) -> list[int]:
    """Goertzel power at each frequency; returns the two strongest."""
    import math
    power = {}
    for f in freqs:
        c, s1, s2 = 2 * math.cos(2 * math.pi * f / 8000), 0.0, 0.0
        for x in pcm:
            s1, s2 = x + c * s1 - s2, s1
        power[f] = s1 * s1 + s2 * s2 - c * s1 * s2
    return sorted(sorted(power, key=power.get)[-2:])


@pytest.mark.parametrize("key", list("1590#*"))
def test_dtmf_tones_decode_to_the_right_key(key):
    from remy.voice import audio
    tone = audio.dtmf(key)
    assert len(tone) == 8000 * (120 + 100) // 1000  # 120 ms tone + 100 ms gap
    pcm = list(memoryview(audio.ulaw_to_pcm16(tone[:960])).cast("h"))
    assert _strongest(pcm, (697, 770, 852, 941, 1209, 1336, 1477, 1633)) == list(audio.DTMF[key])
    assert len(audio.dtmf("1w2")) == 2 * 1760 + 4000 and audio.dtmf("x") == b""


@pytest.mark.parametrize("line,hold", [
    ("Your call is important to us. Please continue to hold.", True),
    ("Thank you for your patience. The next available representative will be with you shortly.", True),
    ("Thank you for holding, this is Diane, how can I help?", False),
    ("Please hold while I pull that up.", False),
    ("This call may be recorded. For billing, press 2.", False),
    ("Okay, I see the account.", False)])
def test_is_hold(line, hold):
    assert media.is_hold(line) is hold


class MenuBrain:
    def __init__(self):
        self.ended, self.heard, self.keys = False, [], ""

    async def reply(self, said, draft=None):
        self.heard.append(said)
        if "press 2" in said:
            self.keys = "2"
            return
        yield "Hi, I'm calling about an account."

    def take_keys(self):
        keys, self.keys = self.keys, ""
        return keys


async def test_phone_menu_gets_keypad_tones_and_hold_messages_get_silence(fakes):
    from remy.voice import audio
    brain = MenuBrain()
    leg = calls.register(media.Leg("m1", voices.default(), brain=brain, greet_after=None))
    tw = FakeTwilio(leg.token, [])
    run = asyncio.create_task(media.MediaSession(tw, calls.legs.get).run())
    await wait_for(lambda: FakeSTT.instances)
    stt = FakeSTT.instances[0]
    stt.push({"type": "turn.end", "transcript": "Thanks for calling Bayview. For billing, press 2."})
    await wait_for(lambda: any(m["event"] == "mark" for m in tw.sent))
    tones = b"".join(base64.b64decode(m["media"]["payload"]) for m in tw.sent if m["event"] == "media")
    assert tones == audio.dtmf("2") and tw.sent[-1]["mark"]["name"].startswith("keys-") and not FakeTTS.spoken
    stt.push({"type": "turn.end", "transcript": "Your call is important to us. Please continue to hold."})
    stt.push({"type": "turn.end", "transcript": "Billing, this is Diane."})
    await wait_for(lambda: len(brain.heard) == 2)
    assert brain.heard[1] == "Billing, this is Diane."  # the hold message never reached the brain
    assert any(e["kind"] == "call_hold" for e in bus.history("m1"))
    await tw.close()
    await run


async def test_press_keys_turn_is_silent_and_takes_one_model_call(claude):
    claude.script = [respond("", tool_block("press_keys", {"digits": "2"}))]
    s = caller.CallSession("k1", {"goals": []})
    out = [x async for x in s.reply("For billing, press 2. For pharmacy, press 3.")]
    assert out == [] and s.last_say == "" and len(claude.created) == 1
    assert s.take_keys() == "2" and s.take_keys() == ""
    assert not any(e["kind"] == "transcript" and e["speaker"] == "remy" for e in bus.history("k1"))
    assert "press_keys" in {t["name"] for t in s.reg.specs()} and "phone menu" in caller.SYSTEM

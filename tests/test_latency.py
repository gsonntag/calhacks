"""Voice latency work, offline: sentence chunking of streamed Claude text, the streaming caller turn
(speech before record_outcome, one model call), speculative drafts on Ink's turn.eager_end (used on
turn.end, discarded on turn.resume), Sonic stream_many, and the streamed desk-mode turn."""
import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from billback import llm
from billback.agents import caller
from billback.events import bus
from billback.voice import calls, desk, media, pronounce, voices
from billback.voice.cartesia_ws import TTS
from tests.test_voice import FakeSTT, FakeTTS, FakeTwilio, fakes  # noqa: F401  (fakes is a fixture)

# ---------------------------------------------------------------- sentence chunking


def chunk(text: str, step: int = 3) -> list[str]:
    cut, out = llm.Sentences(), []
    for i in range(0, len(text), step):  # token-sized deltas
        out += cut.feed(text[i:i + step])
    return out + cut.flush()


def test_sentences_split_and_rejoin():
    text = ("Thank you, Diane. The EOB for claim C-2026-0815-44917 shows $1,100.50, not $8,700! "
            "Dr. Lee at St. Mary's agreed, e.g. on the call. Could you apply it?")
    parts = chunk(text)
    assert "".join(parts) == text  # nothing lost or duplicated; trailing spaces kept for TTS
    assert [p.strip() for p in parts] == [
        "Thank you, Diane.",
        "The EOB for claim C-2026-0815-44917 shows $1,100.50, not $8,700!",
        "Dr. Lee at St. Mary's agreed, e.g. on the call.",
        "Could you apply it?"]


def test_sentences_keep_ids_initials_and_short_bits_whole():
    assert [p.strip() for p in chunk("Case F. A. 2 0 9. Okay. Your case number is FA209-31. Thanks!")] == [
        "Case F. A. 2 0 9.", "Okay. Your case number is FA209-31.", "Thanks!"]
    assert chunk("") == [] and chunk("No period at the end") == ["No period at the end"]


@pytest.mark.parametrize("step", [1, 2, 5, 40])
def test_normalizer_on_streamed_chunks_matches_whole_text(step):
    """The pronunciation normalizer runs per chunk (TTS.request); chunking must not split what it rewrites."""
    text = ("Your statement shows $8,700, but the EOB for claim C-2026-0815-44917 lists $1,100. "
            "The $500 payment on 2026-09-14, reference A1B2C3D4, should be applied. Call (415) 555-1212.")
    parts = chunk(text, step)
    assert len(parts) == 3
    assert "".join(pronounce.normalize(p) for p in parts) == pronounce.normalize(text)


# ---------------------------------------------------------------- streaming caller turn (fake Claude)


def text_block(t):
    return SimpleNamespace(type="text", text=t)


def tool_block(name, inp, id_="tu1"):
    return SimpleNamespace(type="tool_use", name=name, input=inp, id=id_)


class FakeStream:
    """Stands in for llm.Stream: replays one scripted response as text deltas, then .final."""
    script: list = []          # [(deltas, content_blocks, stop_reason)], one per model call
    created: list = []
    gate: asyncio.Event | None = None  # if set, pause once the first sentence is out, until released

    def __init__(self, **kwargs):
        self.kwargs, self.cancelled = kwargs, False
        self.deltas_, self.content, self.stop = FakeStream.script.pop(0)
        self.final = None
        FakeStream.created.append(self)

    async def deltas(self):
        paused = False
        for i, d in enumerate(self.deltas_):
            yield d
            if FakeStream.gate and not paused and ". " in "".join(self.deltas_[:i + 1]):
                paused = True
                await FakeStream.gate.wait()
        self.final = SimpleNamespace(content=self.content, stop_reason=self.stop)

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "Stream", FakeStream)
    FakeStream.script, FakeStream.created, FakeStream.gate = [], [], None
    yield FakeStream


def respond(text, *tools, stop=None):
    deltas = [text[i:i + 4] for i in range(0, len(text), 4)]
    return deltas, ([text_block(text)] if text else []) + list(tools), stop or ("tool_use" if tools else "end_turn")


async def test_reply_yields_first_sentence_before_claude_finishes(claude):
    claude.script = [respond("Thank you, Diane. Could you apply the adjustment?")]
    claude.gate = asyncio.Event()
    s = caller.CallSession("c1", {"goals": []})
    gen = s.reply("Hi, this is Diane.")
    first = await asyncio.wait_for(gen.__anext__(), 1)  # Claude is still "writing" (gate closed)
    assert first == "Thank you, Diane. " and not s.last_say
    claude.gate.set()
    rest = [x async for x in gen]
    assert rest == ["Could you apply the adjustment?"]
    assert s.last_say == "Thank you, Diane. Could you apply the adjustment?"
    tr = [(e["speaker"], e["text"]) for e in bus.history("c1") if e["kind"] == "transcript"]
    assert tr == [("rep", "Hi, this is Diane."), ("billback", s.last_say)]  # same events the UI had
    assert claude.created[0].kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}  # prompt caching


async def test_record_outcome_runs_after_the_spoken_text_with_one_model_call(claude):
    claude.script = [respond("Thank you, I've noted case FA209-31. Is the account on hold?",
                             tool_block("record_outcome", {"case_number": "FA209-31", "next_step": "review",
                                                           "account_on_hold": True, "assistance_review_opened": True}))]
    s = caller.CallSession("c2", {"goals": []})
    seen_outcome_at_first_sentence = None
    async for sentence in s.reply("Your case number is FA209-31."):
        if seen_outcome_at_first_sentence is None:
            seen_outcome_at_first_sentence = dict(s.outcome)
    assert seen_outcome_at_first_sentence == {}  # speech went out before the tool ran
    assert s.outcome["case_number"] == "FA209-31" and len(claude.created) == 1  # no second round trip
    assert [m["role"] for m in s.messages] == ["user", "assistant", "user"]  # ends on the tool result
    assert any(e["kind"] == "call_outcome" for e in bus.history("c2"))
    # next turn appends the rep's words to the tool-result message (roles keep alternating)
    claude.script = [respond("Thanks, goodbye.", tool_block("end_call", {"reason": "done"}, "tu2"))]
    assert await s.turn("Yes. Bye now.") == "Thanks, goodbye." and s.ended
    assert s.messages[2]["content"][-1] == {"type": "text", "text": "Yes. Bye now."}


async def test_tool_only_response_then_spoken_reply(claude):
    claude.script = [respond("", tool_block("record_outcome", {"next_step": "x", "account_on_hold": True,
                                                               "assistance_review_opened": False})),
                     respond("Thank you. Could I get your name?")]
    s = caller.CallSession("c3", {"goals": []})
    assert await s.turn("The case is 58213.") == "Thank you. Could I get your name?" and len(claude.created) == 2


async def test_stream_error_keeps_history_and_apologizes(claude, monkeypatch):
    class Boom(FakeStream):
        async def deltas(self):
            raise RuntimeError("overloaded")
            yield  # pragma: no cover
    monkeypatch.setattr(llm, "Stream", Boom)
    claude.script = [respond("never")]
    s = caller.CallSession("c4", {"goals": []})
    assert await s.turn("hello?") == "Sorry, could you say that again?" and s.messages == []


async def test_draft_is_side_effect_free_until_used(claude):
    claude.script = [respond("Hello, I'm an automated assistant calling for Rosa.")]
    s = caller.CallSession("c5", {"goals": []})
    d = s.draft("Billing, this is Diane.")
    assert s.messages == [] and not bus.history("c5") and len(claude.created) == 1
    out = [x async for x in s.reply("Billing, this is Diane.", d)]
    assert out == ["Hello, I'm an automated assistant calling for Rosa."] and len(claude.created) == 1  # reused
    assert s.messages[0] == {"role": "user", "content": "Billing, this is Diane."}


async def test_draft_for_other_words_is_cancelled(claude):
    claude.script = [respond("stale"), respond("Sure, it's account BCH-48213977.")]
    s = caller.CallSession("c6", {"goals": []})
    d = s.draft("Billing, this is")
    out = [x async for x in s.reply("Billing, this is Diane, what's the account?", d)]
    assert claude.created[0].cancelled and out == ["Sure, it's account BCH-48213977."]
    assert s.messages[0]["content"] == "Billing, this is Diane, what's the account?"


# ---------------------------------------------------------------- media session: speculation on eager_end


class DraftBrain:
    """reply()/draft() brain that records what happened to each draft."""

    def __init__(self):
        self.ended, self.drafts, self.cancelled, self.used = False, [], [], []

    def draft(self, said):
        self.drafts.append(said)
        return {"said": said}

    def cancel_draft(self, d):
        self.cancelled.append(d["said"])

    async def reply(self, said, draft=None):
        self.used.append((said, draft and draft["said"]))
        yield f"You said: {said} "
        yield "Anything else?"

    async def turn(self, said):  # pragma: no cover - reply() is preferred
        raise AssertionError


async def wait_for(cond, n=200):
    for _ in range(n):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


async def test_eager_end_draft_discarded_on_resume_and_used_on_end(fakes):
    brain = DraftBrain()
    leg = calls.register(media.Leg("c7", voices.default(), brain=brain, greet_after=None))
    tw = FakeTwilio(leg.token, [])
    run = asyncio.create_task(media.MediaSession(tw, calls.legs.get).run())
    await wait_for(lambda: FakeSTT.instances)
    stt = FakeSTT.instances[0]
    stt.push({"type": "turn.start"})
    stt.push({"type": "turn.eager_end", "transcript": "Okay, I have the account up."})
    await wait_for(lambda: brain.drafts)
    stt.push({"type": "turn.resume"})  # the rep kept talking: throw the draft away
    await wait_for(lambda: brain.cancelled)
    stt.push({"type": "turn.eager_end", "transcript": "Okay, I have the account up. It shows $8,700."})
    stt.push({"type": "turn.end", "transcript": "Okay, I have the account up. It shows $8,700."})
    await wait_for(lambda: any(m["event"] == "mark" for m in tw.sent))
    assert brain.drafts == ["Okay, I have the account up.", "Okay, I have the account up. It shows $8,700."]
    assert brain.cancelled == ["Okay, I have the account up."]
    assert brain.used == [("Okay, I have the account up. It shows $8,700.",) * 2]
    assert FakeTTS.spoken == ["You said: Okay, I have the account up. It shows eight thousand seven hundred dollars. ",
                              "Anything else?"]  # streamed sentence by sentence, normalizer on each
    assert leg.stats["drafts"] == {"used": 1, "discarded": 1} and len(leg.stats["latency_ms"]) == 1
    await tw.close()
    await run


async def test_draft_with_different_final_words_is_not_used(fakes):
    brain = DraftBrain()
    leg = calls.register(media.Leg("c8", voices.default(), brain=brain, greet_after=None))
    tw = FakeTwilio(leg.token, [])
    run = asyncio.create_task(media.MediaSession(tw, calls.legs.get).run())
    await wait_for(lambda: FakeSTT.instances)
    stt = FakeSTT.instances[0]
    stt.push({"type": "turn.eager_end", "transcript": "Your case number is"})
    await wait_for(lambda: brain.drafts)
    stt.push({"type": "turn.end", "transcript": "Your case number is FA209-31."})
    await wait_for(lambda: any(m["event"] == "mark" for m in tw.sent))
    assert brain.cancelled == ["Your case number is"] and brain.used == [("Your case number is FA209-31.", None)]
    assert leg.stats["drafts"] == {"used": 0, "discarded": 1}
    await tw.close()
    await run


async def test_barge_in_mid_stream_still_finishes_the_brain_turn(fakes):
    gate = asyncio.Event()

    class Slow(DraftBrain):
        async def reply(self, said, draft=None):
            yield "First sentence here. "
            await gate.wait()
            yield "Second sentence."
            self.finished = True

    brain = Slow()
    leg = calls.register(media.Leg("c9", voices.default(), brain=brain, greet_after=None))
    tw = FakeTwilio(leg.token, [], echo_marks=False)
    run = asyncio.create_task(media.MediaSession(tw, calls.legs.get).run())
    await wait_for(lambda: FakeSTT.instances)
    stt = FakeSTT.instances[0]
    stt.push({"type": "turn.end", "transcript": "Hello there."})
    await wait_for(lambda: any(m["event"] == "media" for m in tw.sent))
    stt.push({"type": "turn.update", "transcript": "wait, hold on"})
    await wait_for(lambda: any(m["event"] == "clear" for m in tw.sent))
    gate.set()
    await wait_for(lambda: getattr(brain, "finished", False))
    n = len(tw.sent)
    await asyncio.sleep(0.05)
    assert tw.sent[-1]["event"] == "clear" and len(tw.sent) == n  # nothing more after the barge-in
    assert FakeTTS.spoken == ["First sentence here. "] and leg.stats["barge_ins"] == 1
    await tw.close()
    await run


# ---------------------------------------------------------------- Sonic: several sentences, one context each


class FakeSonicWS:
    """Answers every request with two chunks and done, after `delay`; records requests."""

    def __init__(self, tts):
        from websockets.protocol import State
        self.state, self.tts, self.sent = State.OPEN, tts, []

    async def send(self, data):
        msg = json.loads(data)
        self.sent.append(msg)
        if msg.get("cancel"):
            return

        async def answer():
            await asyncio.sleep(0.01)
            for part in (b"a", b"b"):
                q = self.tts._queues.get(msg["context_id"])
                if q:
                    q.put_nowait({"type": "chunk", "context_id": msg["context_id"], "data": base64.b64encode(part).decode()})
            q = self.tts._queues.get(msg["context_id"])
            if q:
                q.put_nowait({"type": "done", "context_id": msg["context_id"]})
        asyncio.create_task(answer())


async def test_stream_many_one_generation_at_a_time_and_batches_waiting_text():
    tts = TTS(voices.default())
    tts.ws = FakeSonicWS(tts)
    live = []

    async def texts():
        yield "Thank you, Diane. "
        await asyncio.sleep(0.001)
        yield "The EOB shows $1,100. "  # both arrive while the first is generating: sent together
        yield "Could you apply it?"
        await asyncio.sleep(0.05)
        yield "One more."

    async def watch():
        while True:
            live.append(len(tts._queues))
            await asyncio.sleep(0.002)

    w = asyncio.create_task(watch())
    audio = [c async for c in tts.stream_many(texts(), "say-1")]
    w.cancel()
    sent = [m["transcript"] for m in tts.ws.sent]
    assert sent[0] == "Thank you, Diane. "
    assert sent[1] == pronounce.normalize("The EOB shows $1,100. Could you apply it?")
    assert sent[2] == "One more." and [m["context_id"] for m in tts.ws.sent] == ["say-1_1", "say-1_2", "say-1_3"]
    assert audio == [b"a", b"b"] * 3 and max(live) <= 1  # never two generations at once (plan limit is 2)


async def test_stream_many_cancel_by_prefix_drains_text_but_sends_nothing_more():
    tts = TTS(voices.default())
    tts.ws = FakeSonicWS(tts)
    drained = []

    async def texts():
        for t in ("One. ", "Two. ", "Three."):
            drained.append(t)
            yield t
            await asyncio.sleep(0.03)

    got = []
    async for c in tts.stream_many(texts(), "say-2"):
        got.append(c)
        if len(got) == 1:
            await tts.cancel("say-2")
    assert drained == ["One. ", "Two. ", "Three."]  # the brain still finished its turn
    assert [m["transcript"] for m in tts.ws.sent if "transcript" in m] == ["One. "]
    assert {"context_id": "say-2_1", "cancel": True} in tts.ws.sent and got == [b"a"]


# ---------------------------------------------------------------- desk mode, streamed


async def test_desk_events_stream_text_then_audio_then_done(claude, monkeypatch):
    class Sonic:
        async def ensure(self):
            pass

        async def stream_many(self, texts, prefix):
            async for t in texts:
                yield t.encode()  # "audio" = the sentence bytes

    monkeypatch.setattr(desk, "_tts_for", lambda v: Sonic())
    claude.script = [respond("Hello Diane, this is BillBack. I'm an automated assistant.")]
    s = caller.CallSession("c10", {"goals": [], "voice_id": "priya"})
    lines = [json.loads(b) async for b in desk.events(s, "Billing, this is Diane.")]
    kinds = [e["type"] for e in lines]
    assert kinds[0] == "text" and kinds[-1] == "done" and "audio" in kinds
    assert [base64.b64decode(e["data"]).decode() for e in lines if e["type"] == "audio"] == [
        "Hello Diane, this is BillBack. ", "I'm an automated assistant."]
    assert lines[-1] == {"type": "done", "say": "Hello Diane, this is BillBack. I'm an automated assistant.",
                         "ended": False, "outcome": {}, "audio": True}


async def test_desk_route_without_cartesia_streams_text_only(monkeypatch):
    import httpx
    from fastapi import FastAPI

    from billback.voice.routes import router
    app = FastAPI()
    app.include_router(router)
    s = caller.CallSession("c11", {"goals": [], "call_type": "first"})
    s.brief = {**s.brief}
    caller.sessions[s.id] = s
    monkeypatch.setattr(caller.CallSession, "_scripted", lambda self, said: ["Hi, this is an automated assistant.",
                                                                              "Thanks, goodbye."])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/api/calls/{s.id}/turn/stream", json={"rep_said": "Hello?"})
        lines = [json.loads(x) for x in r.text.splitlines() if x.strip()]
        assert [e["type"] for e in lines] == ["text", "done"] and lines[-1]["audio"] is False
        assert lines[-1]["say"] == "Hi, this is an automated assistant."
        assert (await c.post("/api/calls/nope/turn/stream", json={})).status_code == 404
    caller.sessions.pop(s.id)


async def test_desk_tts_failure_still_returns_text_for_the_browser_voice(claude, monkeypatch):
    """E.g. Cartesia quota exceeded (HTTP 402): the turn still completes; rep.html falls back to speech."""
    class Broken:
        async def ensure(self):
            raise RuntimeError("HTTP 402")

        async def stream_many(self, texts, prefix):
            await self.ensure()
            yield b""  # pragma: no cover

    monkeypatch.setattr(desk, "_tts_for", lambda v: Broken())
    claude.script = [respond("Thank you. Could you apply the adjustment?")]
    s = caller.CallSession("c12", {"goals": []})
    lines = [json.loads(b) async for b in desk.events(s, "Okay.")]
    assert [e["type"] for e in lines] == ["text", "text", "done"]
    assert lines[-1]["audio"] is False and lines[-1]["say"] == "Thank you. Could you apply the adjustment?"
    assert any("Desk text-to-speech failed" in e.get("text", "") for e in bus.history("c12"))

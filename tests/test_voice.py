"""Voice pipeline, offline: pronunciation, μ-law, Twilio protocol + signatures, barge-in, and full calls
through run_twilio_call with a fake Twilio (REST + media stream) and fake Cartesia STT/TTS."""
import asyncio
import base64
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from remy import config, orchestrator
from remy.agents import caller
from remy.events import bus
from remy.integrations import twilio
from remy.voice import audio, calls, media, pronounce, voices
from remy.voice.authorization import AuthorizationBrain, classify
from remy.voice.cartesia_ws import STT, TTS, keyterm_params
from remy.voice.routes import router

# ---------------------------------------------------------------- pronunciation


@pytest.mark.parametrize("text,spoken", [
    ("$8,700", "eight thousand seven hundred dollars"),
    ("$2,160.50", "two thousand one hundred sixty dollars and fifty cents"),
    ("$1", "one dollar"),
    ("$0.75", "seventy-five cents"),
    ("$10-15", "ten to fifteen dollars"),
    ("2026-08-02", "August second, twenty twenty-six"),
    ("08/14/2026", "August fourteenth, twenty twenty-six"),
    ("1991-05-22", "May twenty-second, nineteen ninety-one"),
    ("claim C-2026-0815-44917", "claim <spell>C 2026 0815 44917</spell>"),
    ("account BCH-48213977", "account <spell>BCH 4821 3977</spell>"),
    ("case FA-20931", "case <spell>FA 20931</spell>"),
    ("ref A1B2C3D4", "ref <spell>A1B2C3D4</spell>"),
    ("CPT 80053", "<spell>CPT</spell> <spell>80053</spell>"),
    ("HCPCS J2405", "<spell>HCPCS</spell> <spell>J 2405</spell>"),
    ("+14155551212", "(415) 555-1212"),
    ("415.555.1212", "(415) 555-1212"),
    ("the EOB says", "the <spell>EOB</spell> says"),
    ("216% of FPL", "216% of federal poverty level"),
    ("Ondansetron injection", "<<ɑ|n|ˈ|d|æ|n|s|ə|ˌ|t|ɹ|ɑ|n>> injection"),
    ("comprehensive metabolic panel", "comprehensive <<ˌ|m|ɛ|t|ə|ˈ|b|ɑ|l|ɪ|k>> panel"),
])
def test_normalize(text, spoken):
    assert pronounce.normalize(text) == spoken


@pytest.mark.parametrize("text", [
    "On the 3rd, 2 COVID-19 tests at 9am in 2026 for 1,234 people.",  # ordinals, words, years, counts untouched
    "Your ER visit", "**Thanks!**"])
def test_normalize_leaves_ordinary_text(text):
    out = pronounce.normalize(text)
    assert "<spell>" not in out.replace("<spell>ER</spell>", "") and "*" not in out


def test_normalize_is_idempotent_and_keeps_markup():
    line = "Claim C-2026-0815-44917 for $2,600 on 2026-08-02, EOB attached. <spell>X1</spell> <<ˈ|k|æ|t>>"
    once = pronounce.normalize(line)
    assert pronounce.normalize(once) == once
    assert "<spell>X1</spell>" in once and "<<ˈ|k|æ|t>>" in once


def test_normalize_spanish_keeps_money_for_cartesia_but_spells_ids():
    out = pronounce.normalize("El saldo es $8,700, reclamo C-2026-0815-44917", "es")
    assert "$8,700" in out and "<spell>C 2026 0815 44917</spell>" in out


def test_number_words():
    assert pronounce.number_words(1_234_567) == "one million two hundred thirty-four thousand five hundred sixty-seven"
    assert [pronounce.ordinal_words(n) for n in (1, 12, 21, 30)] == ["first", "twelfth", "twenty-first", "thirtieth"]
    assert [pronounce.year_words(y) for y in (2000, 2005, 2026, 1905, 1900)] == [
        "two thousand", "two thousand five", "twenty twenty-six", "nineteen oh five", "nineteen hundred"]


# ---------------------------------------------------------------- voices + Cartesia request shapes


def test_voice_catalog_has_accents():
    accents = {v["accent"] for v in voices.catalog()}
    assert {"American English", "British English", "Indian English", "Mexican Spanish"} <= accents
    priya = voices.resolve("priya")
    assert priya.tts_fields() == {"voice": priya.id, "language": "en-IN", "normalization": "en-US"}
    assert voices.resolve(priya.id) is priya and voices.resolve(None).id == config.CARTESIA_VOICE_ID
    assert voices.resolve("some-other-id").id == "some-other-id"


def test_cartesia_ws_requests():
    url = STT(keyterms=["Bayview Community Hospital", "EOB", "EOB", ""], end_timeout_ms=3000).url()
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("wss://api.cartesia.ai/stt/turns/websocket?")
    assert q["encoding"] == ["pcm_mulaw"] and q["sample_rate"] == ["8000"] and q["model"] == ["ink-2"]
    assert q["cartesia_version"] == ["2026-08-14"] and q["turn_end_timeout_ms"] == ["3000"]
    assert q["keyterm"] == ["Bayview Community Hospital", "EOB"]
    assert len(keyterm_params(f"term{i}" for i in range(500))) == 100
    req = TTS(voices.resolve("priya")).request("Claim C-2026-0815-44917 is $2,600.", "ctx1")
    assert req["output_format"] == {"container": "raw", "encoding": "pcm_mulaw", "sample_rate": 8000}
    assert req["language"] == "en-IN" and req["model_id"] == "sonic-3.6" and req["continue"] is False
    assert req["transcript"] == "Claim <spell>C 2026 0815 44917</spell> is two thousand six hundred dollars."


# ---------------------------------------------------------------- μ-law


def test_mulaw():
    assert audio.ulaw_to_pcm16(bytes([0xFF, 0x7F])) == b"\x00\x00\x00\x00"
    assert audio.ulaw_to_pcm16(bytes([0x00])) == (-32124).to_bytes(2, "little", signed=True)
    assert audio.ulaw_to_pcm16(bytes([0x80])) == (32124).to_bytes(2, "little", signed=True)
    assert [len(f) for f in audio.frames(audio.silence(50))] == [160, 160, 80]
    w = audio.wav(audio.silence(20))
    assert w[:4] == b"RIFF" and w[8:12] == b"WAVE" and len(w) == 44 + 320


# ---------------------------------------------------------------- Twilio REST, TwiML, signatures


def test_stream_twiml(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://abc.trycloudflare.com")
    xml = twilio.stream_twiml(twilio.ws_url("/voice/twilio/stream"), {"token": 'a"b<c'})
    assert xml == ('<Response><Connect><Stream url="wss://abc.trycloudflare.com/voice/twilio/stream">'
                   '<Parameter name="token" value=\'a"b&lt;c\'/></Stream></Connect></Response>')  # valid XML quoting


def test_signature(monkeypatch):
    url, params = "https://x.example/voice/twilio/status?token=t", {"CallStatus": "ringing", "CallSid": "CA1"}
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "")
    assert not twilio.valid_signature(url, params, twilio.signature(url, params, ""))  # unset token rejects all
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "secret")
    sig = twilio.signature(url, params, "secret")
    assert twilio.valid_signature(url, params, sig)
    assert not twilio.valid_signature(url, {**params, "CallStatus": "completed"}, sig)
    assert not twilio.valid_signature(url + "x", params, sig) and not twilio.valid_signature(url, params, "")


async def test_twilio_rest_requests(monkeypatch):
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(req)
        return httpx.Response(201, json={"sid": "CA123", "call_sid": "CA9", "conference_sid": "CF1"})

    real = httpx.AsyncClient
    monkeypatch.setattr(twilio.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(config, "TWILIO_FROM_NUMBER", "+15550001111")
    assert await twilio.create_call("+15552223333", "https://x/voice/twilio/agent?token=t", "https://x/status") == "CA123"
    await twilio.add_participant("remy-c1", "app:AP1?token=t", label="remy", status_callback="https://x/s",
                                 end_conference_on_exit=True)
    call, part = sent
    assert str(call.url) == "https://api.twilio.com/2010-04-01/Accounts/AC1/Calls.json"
    assert call.headers["authorization"] == "Basic " + base64.b64encode(b"AC1:tok").decode()
    form = parse_qs(call.content.decode())
    assert form["To"] == ["+15552223333"] and form["From"] == ["+15550001111"]
    assert form["Url"] == ["https://x/voice/twilio/agent?token=t"]
    assert not {"Twiml", "Timeout", "StatusCallbackMethod"} & set(form)  # refused on Twilio's free trial
    assert form["StatusCallbackEvent"] == ["initiated", "ringing", "answered", "completed"]
    pform = parse_qs(part.content.decode())
    assert str(part.url).endswith("/Conferences/remy-c1/Participants.json")
    assert pform["To"] == ["app:AP1?token=t"] and pform["EndConferenceOnExit"] == ["true"] and pform["Beep"] == ["false"]


@pytest.fixture
async def api(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://pub.example")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "secret")
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as c:
        yield c
    calls.legs.clear()


async def test_status_webhook_checks_signature(api):
    leg = calls.register(media.Leg("c1", voices.default(), label="hospital"))
    params = {"CallSid": "CA77", "CallStatus": "in-progress"}
    path = f"/voice/twilio/status?token={leg.token}"
    bad = await api.post(path, data=params, headers={"X-Twilio-Signature": "nope"})
    assert bad.status_code == 403 and not leg.answered.is_set()
    sig = twilio.signature("https://pub.example" + path, params, "secret")  # Twilio signs the public URL
    ok = await api.post(path, data=params, headers={"X-Twilio-Signature": sig})
    assert ok.status_code == 204 and leg.answered.is_set() and leg.call_sid == "CA77"
    params = {"CallSid": "CA77", "CallStatus": "no-answer"}
    await api.post(path, data=params, headers={"X-Twilio-Signature": twilio.signature("https://pub.example" + path, params, "secret")})
    assert leg.done.is_set()


async def test_twiml_app_voice_url(api):
    leg = calls.register(media.Leg("c1", voices.default()))
    params = {"CallSid": "CA1", "token": leg.token}
    sig = twilio.signature("https://pub.example/voice/twilio/agent", params, "secret")
    r = await api.post("/voice/twilio/agent", data=params, headers={"X-Twilio-Signature": sig})
    assert r.headers["content-type"].startswith("application/xml")
    assert 'url="wss://pub.example/voice/twilio/stream"' in r.text and f'value="{leg.token}"' in r.text


async def test_unsigned_twiml_fetch_needs_token_and_call_sid(api):
    leg = calls.register(media.Leg("c1", voices.default()))
    leg.call_sid = "CA42"  # what Twilio returned when we placed the call
    path = f"/voice/twilio/agent?token={leg.token}"
    assert (await api.post(path, data={"CallSid": "CA99"})).status_code == 403                    # another call
    assert (await api.post("/voice/twilio/agent?token=guess", data={"CallSid": "CA42"})).status_code == 403
    assert (await api.post(path, data={"CallSid": "CA42"}, headers={"X-Twilio-Signature": "forged"})).status_code == 403
    r = await api.post(path, data={"CallSid": "CA42"})                                             # unsigned, both match
    assert r.status_code == 200 and f'value="{leg.token}"' in r.text


async def test_voices_routes(api):
    r = (await api.get("/api/voices")).json()
    assert any(v["accent"] == "Indian English" for v in r["voices"]) and r["default"] == config.CARTESIA_VOICE_ID
    n = (await api.post("/api/voices/normalize", json={"text": "Claim C-2026-0815-44917", "voice_id": "gemma"})).json()
    assert n["spoken"] == "Claim <spell>C 2026 0815 44917</spell>" and n["voice"] == "Gemma"
    assert (await api.post("/api/voices/sample", json={"text": "hi"})).status_code == 404  # no key offline


# ---------------------------------------------------------------- fakes: Twilio media stream + Cartesia


def said(text: str) -> bytes:
    """Fake 'audio': the words, padded to whole 100 ms STT chunks. FakeSTT 'transcribes' them back."""
    raw = text.encode()
    return raw + bytes([0xFF]) * (-len(raw) % media.STT_CHUNK or media.STT_CHUNK)


class FakeSTT:
    instances: list["FakeSTT"] = []

    def __init__(self, keyterms=(), **turn):
        self.keyterms, self.q = list(keyterms), asyncio.Queue()
        FakeSTT.instances.append(self)

    async def connect(self):
        pass

    async def send(self, audio_bytes: bytes):
        text = audio_bytes.replace(b"\xff", b"").decode()
        if text:
            for ev in ({"type": "turn.start"}, {"type": "turn.update", "transcript": text},
                       {"type": "turn.end", "transcript": text}):
                self.q.put_nowait(ev)

    def push(self, ev: dict):
        self.q.put_nowait(ev)

    async def events(self):
        while True:
            yield await self.q.get()

    async def close(self):
        pass


class FakeTTS:
    spoken: list[str] = []
    cancelled: list[str] = []

    def __init__(self, voice):
        self.voice = voice

    async def connect(self):
        pass

    async def stream(self, text, context_id):
        FakeTTS.spoken.append(pronounce.normalize(text, self.voice.language))
        for _ in range(3):
            yield bytes([0xFF]) * 400
            await asyncio.sleep(0)

    async def cancel(self, context_id):
        FakeTTS.cancelled.append(context_id)

    async def close(self):
        pass


class FakeTwilio:
    """Plays Twilio's side of a Media Stream: connected/start, then one caller line per turn as media
    frames; echoes each mark (= playback finished) and only then says the next line."""

    def __init__(self, token: str, lines: list[str], echo_marks: bool = True):
        self.token, self.lines, self.echo_marks = token, list(lines), echo_marks
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False
        self.inbox.put_nowait({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        self.inbox.put_nowait({"event": "start", "sequenceNumber": "1", "streamSid": "MZ1",
                               "start": {"streamSid": "MZ1", "callSid": "CA1", "accountSid": "AC1",
                                         "tracks": ["inbound"], "customParameters": {"token": token},
                                         "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}})
        self.say_next()

    def say_next(self):
        if self.lines:
            for frame in audio.frames(said(self.lines.pop(0))):
                self.inbox.put_nowait({"event": "media", "streamSid": "MZ1",
                                       "media": {"track": "inbound", "payload": base64.b64encode(frame).decode()}})

    async def receive_text(self) -> str:
        msg = await self.inbox.get()
        if msg is None:
            raise RuntimeError("closed")
        return json.dumps(msg)

    async def send_text(self, data: str):
        msg = json.loads(data)
        self.sent.append(msg)
        if msg["event"] == "mark" and self.echo_marks:
            self.inbox.put_nowait({"event": "mark", "streamSid": "MZ1", "mark": msg["mark"]})
            if "." not in msg["mark"]["name"]:  # "say-3" ends an utterance; "say-3.2" is a progress mark
                self.say_next()

    async def close(self, code: int = 1000):
        self.closed = True
        self.inbox.put_nowait(None)


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(media, "STT", FakeSTT)
    monkeypatch.setattr(media, "TTS", FakeTTS)
    FakeSTT.instances, FakeTTS.spoken, FakeTTS.cancelled = [], [], []
    yield
    calls.legs.clear()


# ---------------------------------------------------------------- media session


def test_backchannel():
    assert media.is_backchannel("Mm-hmm.") and media.is_backchannel("okay, yeah") and not media.is_backchannel("no")
    assert media.should_barge_in("wait, hold on") and media.should_barge_in("Wait") and media.should_barge_in("I don't see")
    assert not media.should_barge_in("uh-huh") and not media.should_barge_in("") and not media.should_barge_in("you?")


async def test_unknown_token_is_rejected(fakes):
    tw = FakeTwilio("not-a-real-token", [])
    await media.MediaSession(tw, calls.legs.get).run()
    assert tw.closed and not FakeSTT.instances


class Echo:
    def __init__(self):
        self.ended, self.heard = False, []

    async def turn(self, said):
        self.heard.append(said)
        return f"You said {said}" if said else "Hello, this is Remy."


async def test_barge_in_sends_clear_and_cancels_tts(fakes):
    leg = calls.register(media.Leg("c1", voices.default(), brain=Echo(), greet_after=None))
    tw = FakeTwilio(leg.token, ["Billing, this is Diane."], echo_marks=False)  # never finishes playing
    run = asyncio.create_task(media.MediaSession(tw, calls.legs.get).run())
    for _ in range(100):
        if any(m["event"] == "mark" for m in tw.sent):
            break
        await asyncio.sleep(0.01)
    s = leg.session
    assert s.speaking and [m["event"] for m in tw.sent] == ["media"] * 3 + ["mark"]
    assert all(m["streamSid"] == "MZ1" for m in tw.sent)
    stt = FakeSTT.instances[0]
    stt.push({"type": "turn.update", "transcript": "mm-hmm"})  # backchannel: keep talking
    await asyncio.sleep(0.02)
    assert tw.sent[-1]["event"] == "mark"
    stt.push({"type": "turn.update", "transcript": "wait, hold on"})
    await asyncio.sleep(0.02)
    assert tw.sent[-1] == {"event": "clear", "streamSid": "MZ1"} and FakeTTS.cancelled and not s.speaking
    assert leg.stats["barge_ins"] == 1
    stt.push({"type": "turn.end", "transcript": "wait, hold on"})  # the agent answers the interruption
    for _ in range(100):
        if tw.sent[-1]["event"] == "mark" and len(tw.sent) > 5:
            break
        await asyncio.sleep(0.01)
    assert FakeTTS.spoken[-1] == "You said wait, hold on" and s.speaking
    await tw.close()
    await run
    assert leg.done.is_set()


async def test_speaks_first_when_line_is_silent(fakes):
    brain = Echo()
    leg = calls.register(media.Leg("c1", voices.default(), brain=brain, greet_after=0.05))
    tw = FakeTwilio(leg.token, [])
    run = asyncio.create_task(media.MediaSession(tw, calls.legs.get).run())
    await asyncio.sleep(0.2)
    assert brain.heard == [None] and FakeTTS.spoken == ["Hello, this is Remy."]
    await tw.close()
    await run


# ---------------------------------------------------------------- full calls through run_twilio_call

REP_LINES = ["Patient Financial Services, this is Diane, how can I help you?", "Okay, let me pull that up.",
             "I see it.", "Okay.", "Yes, I've opened a financial assistance review and put the account on hold.",
             "Your case number is 58213.", "Yes, that's right."]


@pytest.fixture
def phone(monkeypatch, fakes):
    for k, v in {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "tok", "TWILIO_FROM_NUMBER": "+15550001111",
                 "REP_PHONE_NUMBER": "+15552223333", "PATIENT_PHONE_NUMBER": "+15554445555",
                 "CARTESIA_API_KEY": "k", "PUBLIC_BASE_URL": "https://pub.example", "TWILIO_TWIML_APP_SID": ""}.items():
        monkeypatch.setattr(config, k, v)
    dialed, hung_up, streams = [], [], []
    scripts = {"+15554445555": ["Hello?", "Yes, I authorize that."], "+15552223333": REP_LINES}

    async def create_call(to, url, status_callback):
        assert status_callback.startswith("https://pub.example/voice/twilio/status?token=")
        assert url.startswith("https://pub.example/voice/twilio/agent?token=")
        token = url.split("token=", 1)[1]
        twiml = calls.stream_twiml(calls.legs[token])  # what /voice/twilio/agent serves when Twilio fetches it
        assert 'url="wss://pub.example/voice/twilio/stream"' in twiml
        dialed.append(to)
        tw = FakeTwilio(token, scripts[to])
        streams.append(tw)
        asyncio.get_running_loop().create_task(media.MediaSession(tw, calls.legs.get).run())  # callee answers
        return f"CA{len(dialed)}"

    async def hangup(sid):
        hung_up.append(sid)

    monkeypatch.setattr(twilio, "create_call", create_call)
    monkeypatch.setattr(twilio, "hangup", hangup)
    yield {"dialed": dialed, "streams": streams, "scripts": scripts}
    caller.sessions.clear()


def _case(**patient) -> dict:
    case = orchestrator.new_case()
    case["patient"].update(patient)
    return case


def _brief(case: dict) -> dict:
    return {"goals": ["Get a case number"], "patient": "Rosa Martinez", "hospital": case["hospital"]["name"],
            "account_number": "BCH-48213977", "hospital_says_due": 8700, "insurer": "Golden State Health Plan",
            "claim_number": "C-2026-0815-44917", "eob_patient_responsibility": 2600,
            "payments_made": [{"date": "2026-08-14", "amount": 500, "reference": "A1B2C3D4"}],
            "correct_balance": 2100, "income_pct_fpl": 216, "voice_id": "priya"}


async def test_direct_call_end_to_end(phone):
    case = _case()
    brief = _brief(case)
    outcome = await asyncio.wait_for(calls.run_twilio_call(case, brief), 10)
    assert phone["dialed"] == ["+15554445555", "+15552223333"]  # patient's OK first, then billing
    hist = bus.history(case["id"])
    auth = next(e for e in hist if e["kind"] == "authorization")
    assert auth["granted"] is True and auth["statement"] == "Yes, I authorize that." and auth["call_sid"] == "CA1"
    assert "164.510(b)" in auth["basis"] and "transcribed" in auth["question"]
    ready = next(e for e in hist if e["kind"] == "call_ready")
    assert ready["mode"] == "phone" and ready["transport"] == "twilio" and ready["voice"] == "Priya"
    rep = [e["text"] for e in hist if e["kind"] == "transcript" and e["speaker"] == "rep"]
    assert rep == REP_LINES  # consent-call audio never lands in the billing transcript
    assert sum(1 for e in hist if e["kind"] == "transcript" and e["speaker"] == "remy") == len(REP_LINES)
    assert outcome["case_number"] == "58213" and outcome["account_on_hold"] is True
    assert case["call_done"].is_set() and any(e["kind"] == "call_ended" for e in hist)
    assert caller.sessions[case["call_id"]].brief["patient_authorization"]["given"] is True
    hospital = phone["streams"][1]
    assert hospital.closed and not any(m["event"] == "clear" for m in hospital.sent)
    # every TTS line went through the normalizer
    assert any("<spell>BCH 4821 3977</spell>" in t for t in FakeTTS.spoken)
    m = next(e for e in hist if e["kind"] == "voice_metrics")
    assert m["turns"] == 2 + len(REP_LINES) and m["voice"] == "Priya" and m["median_response_ms"] is not None
    assert not calls.legs


async def test_patient_declines_so_no_hospital_call(phone):
    phone["scripts"]["+15554445555"] = ["Hello?", "No, don't do that."]
    case = _case()
    outcome = await asyncio.wait_for(calls.run_twilio_call(case, _brief(case)), 10)
    assert phone["dialed"] == ["+15554445555"] and outcome == {}
    hist = bus.history(case["id"])
    assert next(e for e in hist if e["kind"] == "authorization")["granted"] is False
    assert case["call_done"].is_set() and not any(e["kind"] == "call_ready" for e in hist)


async def test_dial_failure_raises_for_desk_fallback(phone, monkeypatch):
    monkeypatch.setattr(config, "PATIENT_PHONE_NUMBER", "")

    async def broken(*a, **k):
        raise RuntimeError("Twilio 401")

    monkeypatch.setattr(twilio, "create_call", broken)
    case = _case()
    with pytest.raises(calls.DialError):
        await calls.run_twilio_call(case, _brief(case))
    assert not case["call_done"].is_set() and "call_id" not in case and not caller.sessions


def test_twilio_ready(monkeypatch):
    assert not calls.twilio_ready()
    for k, v in {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t", "TWILIO_FROM_NUMBER": "+15550001111",
                 "CARTESIA_API_KEY": "k", "REP_PHONE_NUMBER": "+15552223333"}.items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://localhost:8000")
    assert not calls.twilio_ready()  # Twilio can't reach localhost
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://pub.example")
    assert calls.twilio_ready()


# ---------------------------------------------------------------- authorization + caller brief


@pytest.mark.parametrize("said,verdict", [("Yes, I authorize that.", True), ("yeah go ahead", True), ("Sure.", True),
                                          ("No.", False), ("I'm not okay with that", False), ("Who is this?", None)])
def test_classify_consent(said, verdict):
    assert classify(said) is verdict


async def test_authorization_reasks_once_then_records():
    b = AuthorizationBrain("c9", "Rosa", "Bayview Community Hospital")
    assert "permission" in await b.turn("Hello?")
    assert "Yes or no" in await b.turn("Who is this?")
    await b.turn("hmm")
    assert b.ended and b.granted is False  # still unclear: never assume consent
    assert next(e for e in bus.history("c9") if e["kind"] == "authorization")["granted"] is False


async def test_follow_up_scripted_call_references_previous_call():
    case = orchestrator.new_case()
    prev = [{"date": "2026-10-24", "rep": "Diane", "case_number": "FA-20931",
             "promised": ["Apply the charity-care write-off"], "outcome": "review opened"}]
    brief = _brief(case) | {"call_type": "follow_up", "previous_calls": prev, "balance_after_assistance": 0,
                            "refund_due_after_assistance": 500}
    s = caller.CallSession(case["id"], brief)
    first = await s.turn(None)
    assert "following up on our call on 2026-10-24 with Diane, case number FA-20931" in first
    second = await s.turn("Sure, what's this about?")
    assert "you'd apply the charity-care write-off" in second and "$0" in second
    assert "$500 refund" in await s.turn("Yes, it's zero now.")
    assert "on hold" in await s.turn("Within two weeks.")
    assert "Goodbye" in await s.turn("Sure, it's on hold.")
    assert s.ended and s.outcome["case_number"] == "FA-20931" and s.outcome["account_on_hold"] is True
    assert "previous_calls" in caller.SYSTEM and "patient_authorization" in caller.SYSTEM


async def test_first_call_opening_has_no_follow_up():
    case = orchestrator.new_case()
    s = caller.CallSession(case["id"], _brief(case) | {"previous_calls": []})
    assert "following up" not in await s.turn(None)


async def test_tts_cancel_ends_the_stream():
    """Regression (found in a live run): cancelling a context must end stream(), or speak() hangs forever."""
    from websockets.protocol import State

    class WS:
        state = State.OPEN
        sent: list = []

        async def send(self, data):
            self.sent.append(json.loads(data))

    tts = TTS(voices.default())
    tts.ws = WS()
    got = []

    async def consume():
        async for chunk in tts.stream("hello", "c1"):
            got.append(chunk)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    tts._queues["c1"].put_nowait({"type": "chunk", "context_id": "c1", "data": base64.b64encode(b"ab").decode()})
    await asyncio.sleep(0)
    await tts.cancel("c1")
    await asyncio.wait_for(task, 1)
    assert got == [b"ab"] and tts.ws.sent[-1] == {"context_id": "c1", "cancel": True}

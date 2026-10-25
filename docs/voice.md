# BillBack voice pipeline (Twilio + Cartesia + Claude)

BillBack's phone calls run on our own real-time pipeline: **Twilio** carries the call, **Cartesia Ink**
transcribes the billing rep as they speak and detects when they've finished a turn, **Claude** decides what
to say (the same call brain as desk mode, `agents/caller.py`), and **Cartesia Sonic** speaks it in the
voice and accent picked for the case. Before every TTS request, a pronunciation normalizer rewrites claim
numbers, CPT codes, drug names, money and dates into the spoken forms a billing rep expects.

The earlier path, a Cartesia Managed Agent (`integrations/cartesia.py`, `scripts/setup_cartesia.py`), still
works as an alternative. It is billed per agent minute and our free quota ran out, while TTS/STT credits
are plentiful. Running our own pipeline also gives us control over turn-taking, barge-in, pronunciation and
the patient-authorization handoff.

## Files

| File | What it does |
|---|---|
| `billback/voice/calls.py` | Entry point `run_twilio_call(case, brief)`, `twilio_ready()`. Places calls (direct or conference), registers per-call stream tokens, handles status callbacks, cleans up. |
| `billback/voice/media.py` | `MediaSession`: one Twilio Media Stream. Handles the Twilio stream protocol, feeds audio to STT, runs the turn loop, streams TTS back, does barge-in. `Leg` = one expected stream. |
| `billback/voice/cartesia_ws.py` | Streaming clients: Ink STT (`/stt/turns/websocket`) and Sonic TTS (`/tts/websocket`; `stream_many` speaks sentences as they arrive). |
| `billback/voice/desk.py` | Desk mode, streamed: `POST /api/calls/{id}/turn/stream` returns the reply as NDJSON text + audio for `web/rep.html`. |
| `billback/voice/pronounce.py` | Text normalizer applied before every TTS call (phone pipeline, desk mode `/api/tts`, voice previews). |
| `billback/voice/voices.py` | Voice catalog (American, British, Indian English, Mexican Spanish), resolved per case. |
| `billback/voice/authorization.py` | Captures the patient's verbal consent, the HIPAA 164.510(b) step. |
| `billback/voice/audio.py` | G.711 μ-law helpers (framing, decoding for recordings). |
| `billback/voice/routes.py` | FastAPI `router`: Twilio webhooks + media WebSocket, `/api/voices*`. |
| `billback/integrations/twilio.py` | Twilio REST over httpx (calls, conference participants), TwiML, `X-Twilio-Signature` validation. |
| `scripts/sim_twilio_stream.py` | Plays Twilio against a local server to test the whole loop without a phone. |
| `scripts/bench_desk.py` | Desk-mode latency bench over real HTTP (`--old` measures the original turn + `/api/tts` path). |
| `web/voice.html` | Voice picker + pronunciation before/after demo. |
| `tests/test_voice.py` | Offline tests (fake Twilio, fake STT/TTS): protocol, barge-in, signatures, normalizer, full calls. |
| `tests/test_latency.py` | Offline tests for the latency work: sentence chunking, streamed turns, eager-end drafts, desk stream. |

## Architecture

```
 orchestrator ──run_twilio_call(case, brief)──► voice/calls.py ──REST──► Twilio (Calls / Conference Participants)
                                                     │                        │ dials patient, hospital
                                                     │ registers Leg(token)   ▼
                                                     │               phone network ◄──► billing rep (and patient)
                                                     │                        │
                                                     │          <Connect><Stream url=wss://…/voice/twilio/stream>
                                                     ▼                        ▼   <Parameter name="token" …/>
                       voice/routes.py  WS /voice/twilio/stream  ◄── Twilio Media Stream (μ-law 8 kHz, 20 ms frames)
                                                     │
                                         voice/media.py MediaSession
               ┌─────────────────────────────────────┼──────────────────────────────────────┐
               ▼                                     ▼                                      ▼
   Cartesia Ink STT (ink-2)              brain.turn(text) -> reply              Cartesia Sonic TTS (sonic-3.6)
   wss /stt/turns/websocket              CallSession (Claude Haiku,             wss /tts/websocket
   pcm_mulaw 8 kHz in, turn events        tools record_outcome/end_call)        pronounce.normalize(text) in,
   keyterms: names, insurer, "EOB"…      or AuthorizationBrain (scripted)       pcm_mulaw 8 kHz chunks out
               │                                     │                                      │
               └── turn.start/update/end ────────────┘── transcript + call_outcome ─────────┘── Twilio "media" + "mark"
                                                         events on the bus -> web/case.html (unchanged)
```

No audio is transcoded on the live path. Twilio sends μ-law 8 kHz, Ink accepts `pcm_mulaw` at 8000 Hz
directly, and Sonic can output `pcm_mulaw` at 8000 Hz, so its chunks are base64'd straight into Twilio
`media` messages.

## Message flow for one turn

```
Twilio → us   {"event":"start","start":{"streamSid","callSid","customParameters":{"token":…}}}
              token must match a Leg we registered before dialing; otherwise the socket is closed (1008)
Twilio → us   {"event":"media","media":{"payload":<base64 μ-law>}}   every 20 ms, continuously
us → Ink      binary 100 ms chunks (800 bytes)
Ink → us      turn.start … turn.update {"transcript"} … turn.eager_end … turn.end {"transcript":"Your case number is FA-20931"}
us            on eager_end: CallSession.draft(text) starts Claude early (no side effects); turn.resume cancels it
us            on turn.end: CallSession.reply(text, draft) streams Claude's reply sentence by sentence
              ("Thank you, Diane. " first); record_outcome / end_call run after the text that came with them
              (CallSession emits the rep and BillBack "transcript" events, the same as in desk mode)
us → Sonic    per sentence (one generation at a time; sentences that arrived meanwhile are joined):
              {"context_id":"<sid>-3_1","transcript":normalize(sentence),"voice","language":"en-IN","normalization":"en-US",
               "output_format":{"container":"raw","encoding":"pcm_mulaw","sample_rate":8000}}
Sonic → us    {"type":"chunk","data":<base64>} … {"type":"done"}
us → Twilio   {"event":"media","streamSid","media":{"payload":…}} per chunk, then {"event":"mark","mark":{"name":"say-3"}}
Twilio → us   {"event":"mark","mark":{"name":"say-3"}}  when playback actually finished
```

**Speaking state.** We count ourselves as speaking from the first TTS chunk until Twilio echoes the mark
(sent once the whole streamed reply has been generated).
Sonic generates faster than real time, so most of an utterance is still sitting in Twilio's buffer after
generation finishes.

**Barge-in.** While we are speaking, an Ink `turn.update` with real speech triggers barge-in: we send Twilio
`{"event":"clear"}` (which flushes its playback buffer), cancel the reply's Sonic contexts, and drop chunks
still in flight. The brain still finishes its turn (history, tools, transcript), but nothing more is sent. "Real speech" means two or more words that aren't backchannel ("mm-hmm", "okay"), or a one-word
interjection ("wait", "sorry", "no"). A single stray word is usually the tail of a turn that Ink closed a
beat early. We found this in a live run: the rep's "…how can I help" ended a turn, the trailing "you?"
arrived as a new turn while the reply was starting, and that should not interrupt. Backchannel heard while
we talk is ignored entirely rather than answered.

**Greeting.** On an outbound call the person who answers speaks first. If the line stays silent (4 s for
the hospital, 2–2.5 s for the patient), the brain speaks first with `turn(None)`.

**Hang-up.** When the brain calls `end_call`, we wait for the goodbye's mark and then close the stream. With
`<Connect><Stream>`, Twilio then runs out of TwiML and hangs up. In a conference we end the conference
through REST instead. The dashboard's hang-up button (`orchestrator.end_call`) ends the `CallSession` and
sets `call_done`. `run_twilio_call` sees that and hangs up through REST.

## Latency

The number that matters is the time from the rep finishing a sentence to BillBack starting to talk.
Both paths were measured live (Claude Haiku 5.5, Cartesia Ink + Sonic) the same way before and after the
changes, on 2026-10-25.

| Path, metric | Before | After |
|---|---|---|
| **Phone** (`scripts/sim_twilio_stream.py`): Ink `turn.end` → first agent audio sent to Twilio | 1044, 1119, 1152, 1250, 2715 ms (earlier run: 1419, 1936, 2733) | 439, 457, 464, 614, 614, 627, 634, 666, 697, 781, 1122 ms |
| **Desk** (`scripts/bench_desk.py`, what `web/rep.html` waits for): rep's send → first playable audio | 1198, 2360, 2720, 3293, 3456, 3569 ms (median 3.0 s) | 664–1684 ms over 12 turns, median 0.93 s |

On the phone path 10 of 11 turns now start speaking in under 0.8 s. The outlier is a slow Claude first
token. Sonic's first chunk takes 110–130 ms after it gets a sentence.

What each change bought:

1. **Streaming Claude into Sonic, sentence by sentence** (`llm.Stream`, `llm.Sentences`, `CallSession.reply`,
   `TTS.stream_many`). This is the biggest win, about 0.5–2 s per turn: speech starts once Claude has written
   its first sentence (Claude's first token ≈ 0.4–0.6 s, first sentence ≈ 0.1 s later) instead of
   after the whole reply. Each sentence goes through `pronounce.normalize` on its own. The chunker only
   breaks at `.`/`!`/`?` followed by whitespace, and not after abbreviations or initials, so money, IDs and
   dates are never split (tested). Each request gets its own Sonic context (`<ctx>_<n>`), one generation at a
   time. The Cartesia plan allows only 2 concurrent generations, and a context expires 1 s after its last
   audio, so continuations would break on a slow gap between sentences.
2. **Speculation on `turn.eager_end`** (`CallSession.draft`, `MediaSession._speculate`). Claude starts
   with the words so far. `turn.resume`, or a `turn.end` with different words, cancels the draft. A draft
   has no side effects (no history, events or tools) until `reply()` uses it. Ink's eager_end comes
   10–570 ms before `turn.end` (typically 130–340 ms), and that time comes straight off the latency. In the
   sim, drafts were used on most turns.
3. **`record_outcome` off the critical path.** Tools run after the text that came with them has been
   yielded to TTS, so a reply that also records the outcome speaks as fast as any other (the 2.6–2.7 s
   turns before; 440 ms in the run above). The system prompt now asks for the spoken sentence before the
   tool call, because a reply that is only a tool call still needs a second model call.
4. **Prompt caching.** `cache_control` on the system prompt + case brief (tools render first, so they are
   cached too) and on the conversation history. Haiku 5.5 caches prefixes of 512 tokens and up, and every
   turn after the first reads about 2.5 k cached tokens.
5. **Desk mode** (`voice/desk.py`, `web/rep.html`). Before, it waited for Claude's whole reply, then for
   Sonic's whole 44.1 kHz WAV from `/api/tts`, before playing anything. Now one request streams NDJSON
   (`text` sentences, `audio` pcm_s16le 24 kHz chunks, `done`) over a Sonic WebSocket that stays open
   between turns, and the page schedules the chunks back to back with Web Audio. If streaming isn't
   available it falls back to the old `turn` + `/api/tts`, and if Sonic fails it falls back to the browser
   voice.

Model choice is unchanged: Haiku 5.5 with thinking disabled.

**Repeatable timing log.** Every phone turn logs one line (logger `billback.latency`, printed even under
uvicorn), and every desk turn logs `desk turn: first_text=… first_audio=…`:

```
billback.latency turn=4 draft=used eager_lead=347ms first_text=307ms first_audio=439ms (from rep turn.end)
```

Set `BILLBACK_LATENCY_DEBUG=1` to also log every Ink event and Claude's first-token time, total time and
cache hits per call. The simulator prints `leg.stats` (`latency_ms`, time to first sentence, Sonic first
chunk, drafts used and discarded).

Twilio adds network and jitter-buffer delay on a real call (typically 100–200 ms each way), which the
simulator doesn't include.

Turn-taking caveat: because we now answer within about 0.5 s, a rep who pauses mid-sentence ("…this is
Diane. [pause] How can I help you?") is more likely to have Ink end the turn early and then barge in on
the start of our reply. The barge-in rules are unchanged; the old pipeline did the same thing, just later.

## Voices and accents

A case picks its caller voice with `voice_id` (catalog key or Cartesia voice id, on the case or the brief).
The default is `CARTESIA_VOICE_ID` (Tessa). `GET /api/voices` lists the catalog for the UI picker. Ids come
from Cartesia's `GET /voices`.

| key | Voice | Accent | locale | id |
|---|---|---|---|---|
| tessa | Tessa | American English (default) | en-US | 6ccbfb76-1fc6-48f7-b71d-91ac6298247b |
| katie | Katie | American English | en-US | f786b574-daa5-4673-aa0c-cbe3e8534c02 |
| gemma | Gemma | British English | en-GB | 62ae83ad-4f6a-430b-af41-a9bede9286ca |
| harrison | Harrison | British English | en-GB | df89f42f-f285-4613-adbf-14eedcec4c9e |
| priya | Priya | Indian English | en-IN | f6141af3-5f94-418c-80ed-a45d450e7e2e |
| sameer | Sameer | Indian English | en-IN | 638efaaa-4d0c-442e-b701-3fae16aad012 |
| fernanda | Fernanda | Mexican Spanish | es-MX | b4b8e2af-6139-466e-a93a-30c20d2e1fc5 |

Each request sends the voice's locale as `language`, so Sonic keeps the native accent. English voices also
send `normalization: "en-US"`. The call is to a US hospital, so dates and phone numbers should follow US
reading conventions even when the voice has an Indian or British accent: 03/04 is March 4th, and amounts
are never read in lakh.

## Pronunciation (`voice/pronounce.py`)

Every TTS request goes through `normalize(text, language)`: the phone pipeline (`cartesia_ws.TTS`), desk
mode's `/api/tts` (`integrations/cartesia.tts`) and the voice previews. The transcript shown on the
dashboard keeps the original text. Rules, all unit-tested:

| Written | Spoken (sent to Sonic) | How |
|---|---|---|
| `$8,700`, `$2,160.50`, `$10-15` | eight thousand seven hundred dollars, … and fifty cents, ten to fifteen dollars | our number-to-words (English) |
| `2026-08-02`, `08/14/2026` | August second, twenty twenty-six | our date words (English) |
| `C-2026-0815-44917` | `<spell>C 2026 0815 44917</spell>` | Sonic `<spell>` tag: character by character; spaces become pauses between groups; no punctuation inside |
| `BCH-48213977`, `FA-20931`, `A1B2C3D4` | `<spell>BCH 4821 3977</spell>`, … | same; long digit runs grouped 4/3 |
| `CPT 80053`, `J2405` | `<spell>CPT</spell> <spell>80053</spell>`, `<spell>J 2405</spell>` | any 5+ digit run or letter+digit code is an ID |
| `+14155551212`, `415.555.1212` | `(415) 555-1212` | canonical form; Sonic's normalizer groups it as a phone number |
| `EOB`, `CT`, `ICU`, `HCPCS` | `<spell>EOB</spell>` … | case-sensitive acronym list |
| `FPL`, `DOB`, `501(r)` | federal poverty level, date of birth, five oh one R | expansions |
| ondansetron, metabolic, acetaminophen, ketorolac, ceftriaxone, … | `<<ɑ\|n\|ˈ\|d\|æ\|n\|s\|ə\|ˌ\|t\|ɹ\|ɑ\|n>>` … | Sonic 3.x inline IPA phonemes |

Ordinals, counts, years, `COVID-19`, `9am` and `3rd` are left alone, existing markup is kept, and
`normalize` is idempotent. A Cartesia pronunciation dictionary (`pronunciation_dict_id`) does the same
search-and-replace on the server. We keep the table in code so it is versioned, reviewed and tested, and so
it works for every request without dictionary setup. For non-English voices only IDs, codes and phones are
rewritten; money and dates are left to Sonic's own normalizer for that language.

On the listening side, the Ink STT socket gets **keyterms** (patient, hospital and insurer names, "EOB",
"charity care", "financial assistance", and so on) to improve recall of the words that matter on these calls.

`web/voice.html` lets you pick a voice and play the same billing line raw and normalized, and shows exactly
what Sonic receives.

## Patient authorization (HIPAA) and the conference design

**Legal basis.** 45 CFR 164.510(b) lets a covered entity disclose information to a person the patient
identifies as involved in payment for their care, if the patient agrees. The agreement can be verbal. The
billing office decides whether to accept it, so the cleanest design is the patient saying it on the call.
Every call also discloses that BillBack is an automated assistant and that the call is transcribed
(California requires all-party consent to recording). We don't record call audio; we transcribe it.
BillBack only places an AI-voice call to the patient after the patient approved the call in the app, which
is relevant under the FCC's 2024 TCPA ruling on AI voices.

**What gets recorded.** `AuthorizationBrain` asks one fixed question (no LLM: the wording is auditable),
classifies the answer (a refusal wins, an unclear answer gets re-asked once and then counts as no), and
emits an `authorization` event to the append-only audit log. The event holds the exact question, the
patient's transcribed words, a UTC timestamp, the Twilio CallSid, the method and the legal basis. The
hospital call's brief then carries `patient_authorization`, and the system prompt tells the agent to cite
it when the rep asks.

**The Twilio constraint.** Bidirectional audio (we hear the line *and* speak into it) requires
`<Connect><Stream>`. That verb holds the call: no further TwiML runs while the stream is open, and the
call can't also sit in a `<Conference>`. `<Start><Stream>` forks audio without blocking, but it is
listen-only, so our speech would have to go through `<Play>`/`<Say>` or conference announcements, adding
seconds per turn and making barge-in impossible. Dialing a second PSTN number back into our own stream
works but costs an extra call leg and loses context.

**Our choice: the AI as a TwiML App participant in a conference.** Twilio's Conference Participants API
accepts `To=app:<TwiML App SID>?token=…`, which dials one of our TwiML Apps into the conference as an
ordinary participant, with no PSTN leg ([Twilio: Connect your Voice AI Agent to a Twilio Conference](https://www.twilio.com/en-us/blog/developers/tutorials/product/connect-twiml-app-twilio-conference)).
The app's Voice URL (`POST /voice/twilio/agent`) returns `<Connect><Stream>`, so the AI leg gets a normal
bidirectional media stream that hears the conference mix. When `TWILIO_TWIML_APP_SID` is set and the
patient has a phone number:

1. Dial the patient into conference `billback-<case>` (participant label `patient`).
2. When they answer (status callback `in-progress`), add BillBack (`app:AP…?token=…`, label `billback`).
   The stream starts with `AuthorizationBrain`: "Hi Rosa, this is BillBack… This call is transcribed…
   do I have your permission to discuss your account with the hospital's billing office?" The answer goes to the audit log.
3. On "yes", BillBack tells the patient to stay on the line, then dials the hospital into the same
   conference (`EndConferenceOnExit=true`). When billing answers, the same media session switches brains
   (`MediaSession.set_brain`) to the Claude `CallSession`. If the rep wants to hear it from the patient,
   the patient is right there and confirms in their own words, which is the warm three-way handoff
   described in `docs/feasibility.md`.
4. When the call brain ends the call, we end the conference through REST.

**Fallback: direct mode** (no TwiML App configured). The pipeline places two plain `<Connect><Stream>` calls:
a short consent call to the patient (the same `AuthorizationBrain`, then hang up), then the hospital call,
whose brief cites the recorded consent with its timestamp. If the patient can't be reached, the in-app
approval, which is already timestamped in the audit log, is the recorded agreement, and the brief says so.
If the patient says no, the hospital is not called.

**Known limits.** In conference mode Ink hears a single mixed track, so the patient's words during the
billing phase are labelled as the rep in the transcript. Per-participant transcription (Twilio's
participant labels, or a second `<Start><Stream>` fork on the patient's leg) is the fix. The patient's
leg isn't muted, by design: they need to be able to speak up.

## Security

- **Twilio webhooks** (`/voice/twilio/status`, `/voice/twilio/agent`) check `X-Twilio-Signature`:
  HMAC-SHA1 with the auth token over the full public URL plus the POST params sorted by name. The URL is
  rebuilt from `PUBLIC_BASE_URL`, because Twilio signs the tunnel URL, not the `localhost` URL uvicorn
  sees. If `TWILIO_AUTH_TOKEN` is unset, everything is rejected.
- **The media WebSocket** accepts a stream only if its `start` message carries a token we registered
  just before dialing (`secrets.token_urlsafe`, passed as a TwiML `<Parameter>`) and that token has no
  session yet. Tokens are removed when the call ends.
- The Cartesia API key never leaves the server. Both Cartesia sockets authenticate with `X-API-Key`.

## Configure Twilio

Environment (`.env`):

```
TWILIO_ACCOUNT_SID=AC…               # console.twilio.com → Account info
TWILIO_AUTH_TOKEN=…                  # also used to validate webhook signatures
TWILIO_FROM_NUMBER=+1…               # a Twilio voice number you bought
TWILIO_TWIML_APP_SID=AP…             # optional: enables the conference warm handoff
PATIENT_PHONE_NUMBER=+1…             # optional: demo patient's phone (verbal consent); or case.patient.phone
REP_PHONE_NUMBER=+1…                 # the "hospital billing" phone (teammate), as before
PUBLIC_BASE_URL=https://….trycloudflare.com
CARTESIA_API_KEY=…                   # as before; CARTESIA_STT_MODEL=ink-2, CARTESIA_TTS_MODEL=sonic-3.6 by default
```

1. **Buy a number:** Console → Phone Numbers → Buy a number (Voice capable). Put it in `TWILIO_FROM_NUMBER`.
2. **Trial accounts** can only call **verified** numbers (Console → Phone Numbers → Verified Caller IDs):
   verify the teammate's phone and the patient phone. Trial calls start with a Twilio message and ask the
   callee to press a key before our TwiML runs, so upgrade before demoing.
3. **Expose the server** so Twilio can reach the webhooks and the WebSocket:
   `cloudflared tunnel --url http://localhost:8000`, then set `PUBLIC_BASE_URL` to the printed https URL and
   restart. The stream URL is derived from it (`wss://<host>/voice/twilio/stream`).
4. **For the conference handoff:** Console → Voice → TwiML Apps → create one with Voice Request URL
   `https://<public host>/voice/twilio/agent` (HTTP POST). Put its SID in `TWILIO_TWIML_APP_SID`.
5. No per-number webhook setup is needed: every call is outbound with inline TwiML and per-call status
   callbacks.

`voice.twilio_ready()` is true when the Twilio credentials, `CARTESIA_API_KEY`, an https `PUBLIC_BASE_URL`
and an E.164 `REP_PHONE_NUMBER` are all set.

## Wiring (app + orchestrator)

```python
# app.py
from . import voice
app.include_router(voice.router)

# orchestrator._phone_call(case, brief), before the Managed Agent path
if voice.twilio_ready():
    try:
        await voice.run_twilio_call(case, brief)   # sets call_done; outcome in case["call_outcome"]
    except voice.DialError as e:                   # nothing rang: safe to fall back
        bus.emit(case["id"], "warning", "caller", text=f"Phone call failed ({e}); switching to desk mode")
        return await _desk_call(case, brief)
    await _fill_outcome(case, "phone")
    return case["call_outcome"]
```

The `run_case` choice `_phone_call(...) if cartesia.phone_ready() else _desk_call(...)` also needs
`or voice.twilio_ready()`.

## Testing without phone calls

- `uv run pytest -q tests/test_voice.py`, fully offline. A fake Twilio plays the Media Streams protocol,
  fake Ink/Sonic replace the Cartesia sockets, and the brain is the real `CallSession` (scripted when no
  Claude key) and the real `AuthorizationBrain`. The tests cover:
  - Twilio message parsing, μ-law framing, `clear` on barge-in (and not on "mm-hmm")
  - signature validation and the REST request shapes
  - the normalizer
  - full `run_twilio_call` runs: consent then hospital, consent refused, dial failure → `DialError`
  - follow-up briefs
- `uv run python scripts/sim_twilio_stream.py [--voice priya] [--rep-voice tessa] [--short] [--barge-in]`
  starts the app with the voice router on `127.0.0.1:8022` and acts as Twilio over a real WebSocket:
  - streams the rep's lines (synthesized once by Sonic, cached in `data/sim/cache/`) as real-time 20 ms
    μ-law frames, with line silence in between
  - plays the agent's audio back at real-time speed and echoes marks
  - writes `line_inbound.wav`, `agent_outbound.wav` and `transcript.json` to `data/sim/<case>/`, and prints
    latency and audio-seconds used

  Everything downstream (Ink, Claude, Sonic) is live. No phone call and no Cartesia agent minutes.

Sample live run (`--short`, Priya / Indian English):

```
rep      Patient financial services? This is Diane. How can I help
billback Hi Diane, I'm an automated assistant calling on behalf of Rosa Martinez, who has authorized this
         call. This call is transcribed. Rosa's account number is CPMC-48213977, …
         -> Sonic got: … account number is <spell>CPMC 4821 3977</spell>, …
rep      Okay, I've paused collections on the account while we look at this.
billback … the explanation of benefits on claim C-2026-0815-44917 shows Rosa's patient responsibility as $1,100, …
         -> Sonic got: … claim <spell>C 2026 0815 44917</spell> shows … one thousand one hundred dollars, not the
            eight thousand seven hundred dollars …
rep      Your case number is FA209-31.
outcome: case_number FA209-31, account_on_hold (recorded by Claude's record_outcome tool)
latency (turn.end → first audio): 1419, 1936, 2733 ms; Sonic first chunk 125–148 ms   (before streaming; now ~0.45–0.8 s, see Latency)
```

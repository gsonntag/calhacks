# Remy

**An AI agent that fights your hospital bill.** You upload the bill and your insurance statement. Remy
checks them against your bank records (Capital One Nessie) and the hospital's own published financial-assistance
policy and prices (crawled with Bright Data). It works out what you really owe, makes a plan, and, with your
OK, phones the hospital's billing office to get it fixed (Twilio + Cartesia Ink/Sonic + Claude). Then it
follows up until the corrected bill arrives. Code does all the money math; Claude reads documents and talks,
and every number it uses has a source. Nothing is said or paid on your behalf without your approval.

Demo case (synthetic patient, real hospital policy): Marcus Lee owes **$5,600** to UCSF Medical Center. His
income from bank deposits is 170% of the poverty line and the bill is over 10% of his income, so UCSF's own
policy says the hospital writes off his share: **$0**. The same bill under each of the 12 hospital policies
in our database: $0 at 7 of them, $5,600 at the other 5 (`/hospitals.html`, numbers below).

**Before you go live.** Live mode places real phone calls and spends paid API credits (Claude, Cartesia,
Twilio, Bright Data, Nessie). Remy dials only the numbers in your `.env`: `REP_PHONE_NUMBER` plays hospital
billing and `PATIENT_PHONE_NUMBER` the patient. It never dials a hospital's real number (a billing number
read off a bill is only shown). Use only numbers you own or have consent to call, and start with
`REMY_OFFLINE=1` (below). This is a hackathon prototype; read the [Disclaimer](#disclaimer).

## Quick setup

Needs Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
uv run uvicorn remy.app:app --port 8000
# open http://localhost:8000   (Cases; the chat intake when there are no cases yet; also /hospitals.html)
uv run pytest -q                # offline, no keys needed
```

**Offline (no keys).** Leave `.env` blank. Every integration runs in a labelled offline mode: the sample
case, the bank mirror in `demo/`, the policy DB committed in `policydb/`, a scripted caller instead of
Claude, the browser's voice instead of Cartesia. `GET /api/mode` shows which integrations are live.

**No-wifi demo.** `REMY_OFFLINE=1 uv run uvicorn remy.app:app --port 8000` runs the whole flow on one laptop
with no network, even with a full `.env` (every key is ignored). On top of the offline mode above, mocks in
`remy/offline.py` cover what needs a network or a second person:

| Feature | Online | No-wifi demo |
|---|---|---|
| Fonts and icons | Google Fonts | `web/fonts/` (copied by `scripts/vendor_fonts.py`) |
| The call | a second person plays billing on the rep console | billing ("Diane") is played automatically; watch it on the case page |
| Mic (Ask Remy) | Cartesia Ink or Chrome's speech service | a demo voice says the next sensible thing ("Call them for me", "Yes, go ahead", "Where do things stand?") |
| Get started bank prefill | Nessie | the sample patients' bank histories in `demo/` (Rosa Martinez, Marcus Lee, ...) |
| Income by month | Tiger Data | computed from the same transactions |
| Recheck now (case page, Hospitals) | Bright Data | compared with the stored copy, nothing fetched |
| Voice samples | Cartesia Sonic | the browser's voice |

Every mock says so where it shows, and an **Offline demo** chip sits in the top bar. The demo keeps its cases
and profiles in `data/offline/`, so its reset never touches live data.

**Live.** Fill in what you have:

| Variable | For |
|---|---|
| `ANTHROPIC_API_KEY` | Reading the documents, policy extraction, the call brain, answers |
| `NESSIE_API_KEY` | The patient's bank (then `uv run python scripts/seed_nessie.py` and `... seed_nessie.py freecare`) |
| `BRIGHTDATA_API_KEY`, `BRIGHTDATA_SERP_ZONE`, `BRIGHTDATA_UNLOCKER_ZONE` | Live policy crawls and "Recheck now" |
| `CARTESIA_API_KEY` (+ optional `CARTESIA_STT_MODEL`, default `ink-2`; `CARTESIA_TTS_MODEL`, default `sonic-3.6`) | Ink speech-to-text and Sonic speech |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `REP_PHONE_NUMBER`, `PUBLIC_BASE_URL` | Real phone calls (optional; see below). On a Twilio free trial, `TWILIO_FROM_NUMBER` must be the trial number Twilio assigned for your verified recipient, and the call can't stream audio (see Known limitations) |
| `TIGER_DATABASE_URL` | Optional: Tiger Data (Timescale Postgres) for household profiles and bank history over time. Unset: onboarding answers are kept in a small local file per profile |
| `PATIENT_PHONE_NUMBER`, `TWILIO_TWIML_APP_SID` | Optional: the patient's verbal authorization on a 3-way call |
| `REMY_ADMIN_TOKEN` | Shared secret (16+ characters) for every action that places a call, spends API credits or changes data, and for viewing a case built from uploaded documents (the built-in sample cases are open). Open any page once with `?key=<token>`; the browser keeps it and removes it from the address bar. Unset: those actions only work from `localhost` on the same machine, never through a tunnel |
| `REMY_REQUIRE_LIVE=1` | Strict live mode: the server refuses to start unless the Claude, Bright Data and Nessie keys and the admin token are set (format check only, no API calls), and the sample policies are never used |

**Ports.** The app serves everything on one port (8000 above; any port works). Real phone calls need Twilio
to reach `/voice/twilio/*` over HTTPS: `cloudflared tunnel --url http://localhost:8000`, then set
`PUBLIC_BASE_URL` (details in [`docs/voice.md`](docs/voice.md)). Without Twilio, calls run in **desk mode**:
a second person plays billing on the rep console (`rep.html?call=<id>`; once the call starts, the case's **Under the
hood** page links it as "rep console"), and Remy listens through Cartesia Ink and answers out loud through
Sonic.

One-time data scripts (they call paid APIs; the results are already committed):

```bash
uv run python scripts/seed_nessie.py [freecare|<scenario>]   # synthetic patient's bank in Nessie
uv run python scripts/crawl_policies.py crawl                # (re)build the hospital policy DB via Bright Data
uv run python scripts/crawl_policies.py recheck              # detect policy changes
uv run python scripts/crawl_policies.py hcai <id>            # add a hospital's HCAI-filed policy (no Bright Data request)
uv run python scripts/build_price_tables.py [--fetch]        # rebuild policydb/prices/ from the hospitals' CMS price files
```

## How it works

0. **Onboarding (optional, `/onboarding.html`).** Asks only the questions that could still change the
   answer: hospital, household size, **household income before taxes**, insurance, medical bills. The answers
   carry into the patient's next case (Tiger Data when it's on, else `data/profiles/`). The intake chat also
   asks household size and, optionally, income.
1. **Read the bill.** Claude reads the bill and the insurance statement (PDFs or photos) into fields:
   patient, hospital, account, lines, amounts, the insurer's patient share.
2. **Review.** Two agents run side by side, and the case page shows each check as it happens:
   - **Nessie agent** (the patient's own records): payments the bill doesn't show, a line billed twice,
     duplicate bank charges, the bill against the insurance statement, lines above the hospital's posted
     price, income from payroll deposits, 12 months of medical spending. **It double-checks the income the
     patient stated** against the bank (`finance.stated_check`): take-home pay is converted to a before-tax
     range. If the stated figure fits, it's used. If it's lower than the bank shows, the bank's lower estimate is
     used and flagged ("have pay stubs ready"), so Remy never claims a discount the hospital would reject on
     pay stubs. If it's higher, the stated figure is used and noted.
   - **Bright Data agent** (public rules): the hospital's financial-assistance policy from our policy DB
     (refreshed live when it's stale, crawled live for a new hospital), the discount the patient qualifies
     for, the payment-plan terms.
3. **The plan.** Rules-based code (`remy/agents/decide.py`) turns the findings into a list of asks, biggest
   money first, each linked to its proof. Mark a finding "This is wrong", or open **Ask Remy** (the round
   button bottom-right of the case page) and pick who answers: Remy (the whole case), the Nessie agent, the
   Bright Data agent, or the Planner ("I can do $150/mo"). Every plan change makes a new version; "Call now"
   approves the version on screen. In the Ask Remy chat panel, a hold-to-talk mic: ask where things
   stand or say "call them"; anything with consequences is said back first and runs only after a spoken yes.
4. **The call.** Remy phones billing (or the desk console), says it's an automated assistant, works
   through the asks, reads the case number back, and records what billing agreed. **Guardrails are enforced
   in code, not just the prompt** (`remy/agents/mandate.py`):
   - Before agreeing to anything (a payment plan, a balance, a settlement, paying today) or giving out any
     information, the caller calls `check_offer`.
   - Code decides from the plan the patient approved, and fails closed. Plans are allowed only if
     interest-free, within the approved monthly amount and length, and on the corrected balance. SSN, card and
     bank details are never shared.
   - A refusal is spoken in the code's own words.
   - **Every sentence is screened before it's spoken** (`mandate.screen`). It blocks SSN- or card-like numbers
     (unless they're in the brief or the rep just said them), unapproved agreements to pay or enrol, paying
     now, and claiming to be human, and replaces the sentence with a safe line.
   - Each decision shows as a note in the call transcript and goes to the audit log.
5. **Resolved and follow-up.** A scorecard marks each ask got / refused / promised later. The balance only
   drops when billing confirms it ("expected $0 once they apply the corrections" until then); once it does,
   the hospital bill in Nessie is marked corrected. If money is still owed, the planner proposes an
   interest-free plan ("6 × $100/mo"); the patient decides and sets up the payments, and nothing is written
   to the bank for it. A follow-up is scheduled from what billing promised, and "Call again" reuses the plan
   with only the open asks.

```
 chat / drop-and-go ──► Orchestrator ──► audit log + Cases page (web/case.html: case rail + live case)
                              │
   ┌──────────────┬───────────┼────────────────┬────────────────────┬──────────────────┐
   ▼              ▼           ▼                ▼                    ▼                  ▼
 Intake        Nessie agent Bright Data      Decide (plan)        Caller             Follow-up / Planner
 Claude vision bank txns,   agent: policy DB rules-based asks,    Twilio or desk ↔   scorecard, next round,
 bill + EOB    payments,    ◄── SERP → Unlocker  versions,        Cartesia Ink/Sonic plan proposal,
               duplicates,  → PDFs → Claude  Ask Remy chat    ↔ Claude call brain bill corrected → Nessie
               income       → verified quotes                      ▲
                                                 APPROVAL GATE ────┘ (before every call)
```

- **Orchestration** (`remy/orchestrator.py`): a fixed pipeline on purpose, so it's predictable.
- **Harness** (`remy/harness.py`): every tool has a permission tier (`read` runs freely, `act` is
  announced, `consequential` waits for the patient), and every call waits on the approval gate ("Call now",
  "Call again", the phone approval page or a spoken yes). Every tool call, result and approval is an event
  streamed to the UI and appended to the audit log.
- **Money is computed, not generated** (`remy/finance.py`): reconciliation, duplicates, income from
  deposits, % of the 2026 federal poverty line, discount tiers, payment plans (written as "17 × $88/mo, then
  $78.98" so the total is exact). Claude extracts policy rules with verbatim quotes, and code checks each
  quote against the fetched document before any number is used.

## Sponsors: what each one does here

### Cartesia (voice)
Our own voice pipeline, not a hosted agent: Twilio Media Streams ↔ Cartesia Ink (streaming STT) ↔ Claude call
brain ↔ Cartesia Sonic (streaming TTS).
- **Ink** (`/stt/turns/websocket`, `ink-2`): turn detection (`turn.end`), and Claude starts drafting on
  `turn.eager_end`. Keyterms (patient, hospital and insurer names, "charity care", "EOB") improve recall.
  Desk mode streams the browser mic to Ink too (16 kHz PCM, `WS /api/calls/{id}/listen?key=…`).
- **Cases-page mic**: one recorded turn goes to Ink's batch STT (`POST /api/cases/{id}/talk`), the browser's
  own transcript as fallback; the answer is spoken with Sonic (`/api/tts`), or the browser voice without
  credits.
- **Sonic** (`/tts/websocket`, `sonic-3.6`): Claude's reply is spoken sentence by sentence as it streams.
  Word timestamps (`add_timestamps`) tell us what billing actually heard when they interrupt.
- **Barge-in**: real words stop playback ("mm-hmm" doesn't); the call brain's memory is cut to the last word
  played, so Remy never assumes billing heard the rest.
- **Pronunciation** (`remy/voice/pronounce.py`): money, dates, phone numbers, IDs, CPT codes (`<spell>`)
  and drug names (inline IPA phonemes). Case numbers are read back one character at a time, dashes said.
- **Voices**: American, British and Indian English, and Mexican Spanish (`remy/voice/voices.py`).
- **Phone menus and hold**: keypad tones as in-band DTMF on the same stream; recorded hold messages are
  ignored without calling Claude.
- **Latency** (measured live before credits ran out): first audio 0.44–1.1 s after the rep stops on the phone
  path (10 of 11 turns under 0.8 s); desk mode median 0.93 s (was 3.0 s).

Code: `remy/voice/` (`media.py`, `cartesia_ws.py`, `desk.py`, `heard.py`, `pronounce.py`),
`remy/integrations/cartesia.py`, `web/rep.html`, `scripts/sim_twilio_stream.py`. Docs:
[`docs/voice.md`](docs/voice.md). Voice and pronunciation demo: `/voice.html`.

### Bright Data (hospital policies and prices)
- **Policy database** (`policydb/`, committed as crawl evidence): 27 hospitals: the 12 Bay Area hospitals with verified tiers, plus 15 major US hospitals crawled on 2026-10-25 (verified tiers at Mayo Clinic, Cleveland Clinic, Northwestern Memorial, Ronald Reagan UCLA and Michigan Medicine; the other 10 are stored with their sources but state no fixed-percent tier, so they give no discount). SERP API finds each
  hospital's financial-assistance policy, Web Unlocker fetches the page and its PDFs, Claude extracts the
  discount tiers and payment-plan rules with quotes, code verifies each quote against the fetched text, and
  SQLite keeps versions. Rechecks compare content hashes and re-extract only on change. All 12 have verified
  tiers: where a hospital's own site doesn't give the real policy (Stanford, SF General, Saint Francis), the
  copy it filed with California HCAI is fetched over plain HTTPS (Bright Data refuses .gov sites) and checked
  the same way (`remy/policies/hcai.py`).
- **Live on the case page**: a case uses the stored policy unless it is stale (a "no tiers" result older than
  6 hours, or any policy older than 30 days) or the hospital is new; then it crawls live. **"Recheck now"**
  under the Bright Data panel does the same on demand. Each request streams as its own line: tool, URL, and
  found / empty / blocked / timed out / rate-limited / not found / server error, "request n of at most 10".
  Retries and fallbacks (next page, `site:` search, URLs on record, skip a failed PDF, stored copy) each show
  as a line. Hard cap: 10 requests per crawl (`remy/policies/crawl.py: HARD_CAP`).
- **Real prices**: each hospital's CMS price-transparency file, found through its `cms-hpt.txt`; about 67
  common ER and outpatient codes kept (`policydb/prices/`). The bill check flags lines above the hospital's
  posted price, for insured and self-pay bills (drug lines only when the units convert).
- **Same bill, every hospital** (`/hospitals.html`, `remy/policies/compare.py`): Marcus's $5,600 UCSF bill
  run through each stored policy. $0 at CPMC, UCSF, Stanford, Alta Bates Summit, El Camino, John Muir, Kaiser
  Oakland and MarinHealth (their high-medical-cost or income tiers cover him); $5,600 at Chinese Hospital,
  Highland and Saint Francis (their tiers are for uninsured patients) and at SF General (its charity care
  stops at 138% FPL).

Code: `remy/integrations/brightdata.py`, `remy/policies/` (HCAI: `hcai.py`), `remy/agents/policy.py`,
`remy/prices.py`, `remy/price_sources.py`, `remy/billcheck.py`, `scripts/crawl_policies.py`.
Docs: [`docs/brightdata.md`](docs/brightdata.md).

### Capital One Nessie (the patient's bank)
- **Reads**: the customer and account found by the patient's name, then purchases, deposits, withdrawals and
  bills (`remy/integrations/nessie.py`).
- **Income**: payroll deposits annualised (pay varies per check), plus gig and payment-app deposits (Uber,
  DoorDash, Venmo, Zelle…) from the last 90 days. Labelled take-home pay.
- **Payments the bill missed**: a payment to the hospital's health system ("SUTTER HEALTH PYMT") matched by
  name and date, never on a premium or a non-payment.
- **Duplicate charges** on the card, reported apart from the bill.
- **12 months of medical spending** (pharmacies, clinics, dental…) added to this bill for the hospitals'
  "medical costs over 10% of income" rule. Each transaction is listed as proof.
- **Plan sized on the bank**: regular monthly bills found in the history lower what the patient can pay.
- **Writes**: only after billing confirms a new balance on a call: the hospital bill in Nessie is updated to
  it and marked "corrected by billing" (`PUT /bills/{id}`; cancelled at $0). The proposed payment plan is
  never written to the bank.
- **Fallback**: if Nessie has no account for the patient or doesn't answer, the case uses sample bank data
  and says so on the case page.

Code: `remy/integrations/nessie.py`, `remy/agents/auditor.py`, `remy/finance.py`,
`remy/agents/planner.py`, `scripts/seed_nessie.py`.

### Tiger Data (household history)
Optional (`TIGER_DATABASE_URL`). Every household Remy helps is stored in Tiger Cloud (Timescale Postgres):
- a `profiles` row per household (same person = same row across cases)
- every Nessie transaction in a `transactions` hypertable, tagged income or not
- a real-time `monthly_cashflow` continuous aggregate

The case page shows "Income by month (Tiger Data)" for the current household. Tiger only stores and rolls up
data; eligibility math stays in `finance.py`. A Tiger failure never blocks a case. Code: `remy/tiger.py`, `remy/onboarding.py`.

### Anthropic Claude
Reads the bill and insurance statement (vision), extracts policy rules with verbatim quotes, runs the phone
call (a tool-use loop with `record_outcome`, `press_keys`, `check_offer`, `end_call`;
`remy/agents/caller.py`), reads the call transcript into outcomes and marks each ask (a mark only counts if
its quote is really in the rep's words, `remy/agents/scorecard.py`), answers in the Ask Remy chat (as Remy, the Nessie agent, the Bright
Data agent or the Planner) and on the Cases-page mic. Models:
`claude-sonnet-5-5` and `claude-haiku-5-5` (`remy/config.py`). It never produces a dollar figure that code
didn't compute.

## Real vs simulated
- **Real:** Bright Data crawls of real hospital policies, the policies hospitals filed with California HCAI, and
  real CMS price files (`policydb/`), Nessie API
  records, Claude, Cartesia speech, the approval gate and the audit log.
- **Simulated:** the patients, bills, insurance statements, insurer and bank histories are synthetic (PDFs are
  watermarked SAMPLE). "Hospital billing" is a second person on the rep console (or their phone with Twilio).
- **Live mode builds the case only from the uploaded documents.** "Use a sample bill" sends the sample PDFs
  through the same reading; the sample case data only feeds the offline mode.
- **Replay** (`case.html?replay=1`, `&scenario=freecare`, `&jump=call`) plays back a recorded live desk-mode
  run. Only live runs are recorded.

## Known limitations
- **Real phone calls need a paid Twilio account.** On a Twilio free trial:
  - We place calls with a TwiML `Url`, because the trial refuses inline `Twiml`, `Timeout` and
    `StatusCallbackMethod`.
  - The trial fetches that URL unsigned. Remy accepts the unsigned fetch only when it carries both the
    call's secret token and the CallSid Twilio returned when we placed the call; a wrong signature is still
    refused (`remy/voice/routes.py`).
  - The trial **strips `<Stream>`** ("The Stream verb is not available on trial accounts"), so the call can't
    reach our voice pipeline.

  We confirmed this end to end on 2026-10-25: the call rings and our server answers, but there's no audio
  stream. Upgrading Twilio unlocks it.
  [Photon](https://photon.codes) (calls from an iMessage line over SIP/RTP) is a possible alternative. It
  needs a SIP and RTP client in the server, so it isn't built.
- **Cartesia credits.** Live voice needs Cartesia credits; without them the voice paths below fall back as
  described. No real Twilio phone call has been recorded yet;
  the phone path is tested against simulated Twilio and Cartesia sockets. Desk mode on Ink, barge-in with
  word timestamps, ID read-back and keypad tones were built and tested offline (fakes, plus a headless Chrome
  run of `rep.html`) and need one live check. The Cases-page mic's Ink batch STT was written while the account
  had no credits and has never run live. Without credits, desk mode and the mic fall back to Chrome's speech
  recognition and voice, and say so.
- **Desk mode** doesn't yet start Claude early on Ink's `turn.eager_end` (the phone path does). In
  conference mode Ink hears one mixed track.
- **The live Bright Data path** (streamed crawl, retries, "Recheck now", stale re-crawl) is tested with replies
  in the real shapes but has not run against Bright Data since it was built. Stanford, Saint Francis and SF
  General get their tiers from the HCAI filing, not their own sites. MarinHealth's and El Camino's tiers
  were read as "all patients" and need a manual check. A policy that changes on "Recheck now" is shown but
  not applied to the current plan; a new case applies it. The `crawl_policies.py recheck` CLI doesn't re-crawl
  "no tiers" hospitals from Google (the app does).
- **Price tables** are a curated subset (about 67 codes, one representative line per code). Drug lines priced
  in other units (e.g. per mL vs per mg) are listed as "not compared".
- **Synthetic data.** Patients, insurer and bank histories are made up. The live Nessie sandbox still has the
  older, cleaner histories until it is reseeded. Medical merchants are matched by keyword, not MCC codes.
  Nessie's account balance isn't read.
- **Income.** When the patient states their income before taxes (onboarding or chat), Remy uses it after
  the bank double-check (see "How it works"). Without a stated income, it falls back to take-home pay from
  bank deposits. The poverty-line test should use gross income, so Remy labels the figure, estimates gross
  as take-home ÷ 0.80, and warns on the case page when that would change the discount ("have pay stubs ready"),
  but eligibility still uses take-home. Using gross would flip one test
  patient (dana-altabates-refund, who shows the warning). Marcus stays eligible either way: his bill plus
  12 months of bank medical spending is about 14% of take-home and 11.5% of estimated gross, so he gets no
  warning.
- **Recorded replays** predate the latest changes: they still end on "$8,700 → $600" and use older wording.
  A live run shows the current behaviour (balance stays at billing's figure until confirmed).
- **Not built:** archiving or deleting a single case, Gmail intake (the "Demo inbox" chip loads the sample
  bill, marked simulated), "Start over" (re-fetching data for a case), the California collections-law check,
  notifications when a case needs you.
  The `/flows/` pages are early intake mockups, not linked from the app (except `/flows/drop-go.html`, which
  works).
- **Admin key:** the browser side (`web/auth.js`) has only been tested server-side.
- **The speech screen is pattern-based.** It errs toward blocking: a bare 9-digit number is treated as an SSN
  unless it's in the brief or the rep said it. Some loose agreements ("sure, that works for us") aren't
  caught by the screen. The `check_offer` decision and the approved plan still bound what the caller can
  commit to. The onboarding "Do you have insurance?" answer is stored, but the insurance statement still
  decides insured or not.

## Privacy and responsible use
- **Says what it is.** Every call opens with "an automated assistant calling on behalf of <patient>, with their
  authorization" and says the call is transcribed (`remy/agents/caller.py`). The Planner in Ask Remy
  refuses to hide this (`remy/agents/decide.py`).
- **Never agrees to pay, enforced in code.**
  - The call brain never pays, agrees to pay, or gives card or bank details, and the Planner refuses any
    strategy that would.
  - On the call, `check_offer` (`remy/agents/mandate.py`) decides every offer or request against the
    approved plan.
  - Every sentence passes `mandate.screen` before it's spoken, so even if the model skips the check it can't
    say an SSN or card number, agree to something unapproved, or claim to be human.
  - Refusals are logged and shown in the transcript.
- **Nothing happens without approval.** Every call waits for the patient's OK on the approval gate
  (`remy/orchestrator.py`, `remy/harness.py`); on the Cases-page mic it takes two turns, and the words
  come from code (`remy/agents/talk.py`). A change to the plan after approval resets the approval. The
  planner only proposes a payment plan and writes nothing to the bank.
- **Patient authorization (HIPAA 45 CFR 164.510(b)).** A hospital may discuss a bill with someone the patient
  involves in payment, with the patient's verbal agreement. With `PATIENT_PHONE_NUMBER` set, Remy first
  calls the patient, asks one scripted question, and logs the question, the transcribed answer, the time and
  the Twilio CallSid (`remy/voice/authorization.py`). Without it, the in-app approval is the recorded
  agreement.
- **No audio is recorded.** Calls are transcribed only; the app saves no call audio (the
  `scripts/sim_twilio_stream.py` test tool saves its own simulated audio to `data/sim/<case>/`).
- **Claims are checked.** Every policy figure needs a verbatim quote found in the fetched document; every
  finding shows its proof and source. The balance never drops before billing confirms it. Research notes on the legal
  claims (not legal advice) are in [`docs/legal-check.md`](docs/legal-check.md).
- **Accessibility.** The patient never fills in a form: drop the documents (PDF or photo) or chat. Remy
  makes the phone call, so a patient who can't spend an hour on hold, or isn't comfortable arguing on the
  phone, doesn't have to. Every step, finding and call line is also plain text on the case page.
- **Where patient data goes:** Anthropic (documents, transcripts), Nessie (lookup by name), Cartesia and
  Twilio (call audio, live only). Bright Data requests carry only hospital names and URLs, never patient data.
- **Where patient data is stored (all under `data/`, gitignored):**
  - `data/cases/<case_id>.json`: the case (bill, insurance statement, findings, plan, call transcripts).
  - `data/<case_id>.audit.jsonl`: the append-only audit log (every event, approval and transcript line).
  - `data/replay_<scenario>.json`: the last live run, for replay.
  - `data/nessie_ids*.json`: Nessie ids of the seeded demo accounts.
  - `data/profiles/<profile_id>.json`: onboarding answers only (no name or date of birth), when Tiger Data is
    off. With `TIGER_DATABASE_URL` set, profiles and bank history go to Tiger Cloud instead.
  - Uploaded PDFs are sent to Claude and not saved to disk.
- **Deleting it:** `POST /api/reset` (admin key) removes all case snapshots and the locally saved onboarding
  profiles, and keeps the audit logs on purpose. There is no per-case delete in the app. To erase everything, stop the server and delete `data/`
  (or the files above for one case).

## Tests
`uv run pytest -q`: fully offline, no API keys. The scorecard runs
against the fixture calls in `demo/outcomes/` (all won, partly won, all refused, promised later). Live checks that need paid
credits are listed in each PR.

## Docs
- [`docs/DEMO.md`](docs/DEMO.md): the 2-minute demo script and fallbacks.
- [`docs/brightdata.md`](docs/brightdata.md): the policy DB, crawl log, live crawl, price tables.
- [`docs/voice.md`](docs/voice.md): the voice pipeline, latency, pronunciation, authorization.
- [`docs/plan-format.md`](docs/plan-format.md): the plan's data format.
- [`docs/legal-check.md`](docs/legal-check.md): research notes on the legal claims (not legal advice).
- [`docs/research/`](docs/research/): competitors and market research.
- [`docs/feasibility.md`](docs/feasibility.md), [`docs/competitors.md`](docs/competitors.md).

## Layout
```
remy/  app.py orchestrator.py harness.py events.py finance.py billcheck.py followups.py llm.py config.py auth.py prices.py contacts.py
           onboarding.py tiger.py
           agents/        intake auditor (Nessie agent) policy (Bright Data agent) decide scorecard mandate caller planner talk report
           policies/      registry discover fetch extract store monitor pipeline crawl compare routes
           voice/         calls media cartesia_ws desk heard pronounce voices authorization audio routes
           integrations/  nessie brightdata cartesia twilio
web/       case.html (Cases, at /: rail.js case rail + live case)  new.html (chat intake)  ops.html (Under the hood)  hospitals.html
           onboarding.html  rep.html (desk billing)  approve.html (phone approval)  voice.html  pitch.html
           auth.js  flows/drop-go.html
demo/      case.json, case_freecare.json (synthetic patients), sample PDFs, scenarios/, outcomes/, replays
policydb/  policies.sqlite3, policies.json, raw/ (Bright Data crawl artifacts), prices/ (real price tables)
scripts/   seed_nessie crawl_policies build_price_tables build_contacts make_scenario setup_cartesia sim_twilio_stream bench_desk make_demo_docs
```

## Disclaimer
- **A hackathon prototype**, not legal, medical, financial or tax advice. Eligibility estimates come from
  hospitals' published policies, which can be misread, out of date or changed since they were fetched; the
  hospital decides what you owe. Check anything that matters with the hospital or a qualified advisor.
- **Calls and consent.** Remy opens every call by saying it's an automated assistant calling with the
  patient's authorization and that the call is transcribed. California and some other states require every
  party's consent to record a call, and other laws may apply where you are. Saying so is not a guarantee of
  compliance: you are responsible for following the law wherever you place calls.
- **Case access.** A case built from uploaded documents needs the admin key (`REMY_ADMIN_TOKEN`) to view; the
  built-in sample cases are open to anyone who can reach the server.

## License
MIT, see [`LICENSE`](LICENSE). The vendored fonts in `web/fonts/` keep their own licenses (OFL 1.1, Apache 2.0).

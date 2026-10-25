# BillBack

**An AI agent that fights your hospital bill.** You upload the bill and your insurance statement. BillBack
checks them against your bank records (Capital One Nessie) and the hospital's own published financial-assistance
policy and prices (crawled with Bright Data). It works out what you really owe, makes a plan, and, with your
OK, phones the hospital's billing office to get it fixed (Twilio + Cartesia Ink/Sonic + Claude). Then it
follows up until the corrected bill arrives. Code does all the money math; Claude reads documents and talks,
and every number it uses has a source. Nothing is said or paid on your behalf without your approval.

Demo case (synthetic patient, real hospital policy): Marcus Lee owes **$5,600** to UCSF Medical Center. His
income from bank deposits is 170% of the poverty line and the bill is over 10% of his income, so UCSF's own
policy says the hospital writes off his share: **$0**. The same bill under each of the 12 hospital policies
in our database: $0 at 7 of them, $5,600 at the other 5 (`/hospitals.html`, numbers below).

Judges: the 2-minute demo script is [`docs/DEMO.md`](docs/DEMO.md).

## Quick setup

Needs Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
uv run uvicorn billback.app:app --port 8000
# open http://localhost:8000   (Cases; the chat intake when there are no cases yet; also /hospitals.html)
uv run pytest -q                # offline, no keys needed
```

**Offline (no keys).** Leave `.env` blank. Every integration runs in a labelled offline mode: the sample
case, the bank mirror in `demo/`, the policy DB committed in `policydb/`, a scripted caller instead of
Claude, the browser's voice instead of Cartesia. `GET /api/mode` shows which integrations are live.

**Live.** Fill in what you have:

| Variable | For |
|---|---|
| `ANTHROPIC_API_KEY` | Reading the documents, policy extraction, the call brain, answers |
| `NESSIE_API_KEY` | The patient's bank (then `uv run python scripts/seed_nessie.py` and `... seed_nessie.py freecare`) |
| `BRIGHTDATA_API_KEY`, `BRIGHTDATA_SERP_ZONE`, `BRIGHTDATA_UNLOCKER_ZONE` | Live policy crawls and "Recheck now" |
| `CARTESIA_API_KEY` (+ optional `CARTESIA_STT_MODEL`, default `ink-2`; `CARTESIA_TTS_MODEL`, default `sonic-3.6`) | Ink speech-to-text and Sonic speech |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `REP_PHONE_NUMBER`, `PUBLIC_BASE_URL` | Real phone calls (optional; see below) |
| `PATIENT_PHONE_NUMBER`, `TWILIO_TWIML_APP_SID` | Optional: the patient's verbal authorization on a 3-way call |
| `BILLBACK_ADMIN_TOKEN` | Shared secret (16+ characters) for every action that places a call, spends API credits or changes data. Open any page once with `?key=<token>`; the browser keeps it and removes it from the address bar. Unset: those actions only work from `localhost` on the same machine, never through a tunnel |
| `BILLBACK_REQUIRE_LIVE=1` | Judging mode: the server refuses to start unless the Claude, Bright Data and Nessie keys and the admin token are set (format check only, no API calls), and the sample policies are never used |

**Ports.** The app serves everything on one port (8000 above; any port works). Real phone calls need Twilio
to reach `/voice/twilio/*` over HTTPS: `cloudflared tunnel --url http://localhost:8000`, then set
`PUBLIC_BASE_URL` (details in [`docs/voice.md`](docs/voice.md)). Without Twilio, calls run in **desk mode**:
the case page links a billing-rep console (`rep.html`) where a teammate plays billing, and BillBack listens
through Cartesia Ink and answers out loud through Sonic.

One-time data scripts (they call paid APIs; the results are already committed):

```bash
uv run python scripts/seed_nessie.py [freecare|<scenario>]   # synthetic patient's bank in Nessie
uv run python scripts/crawl_policies.py crawl                # (re)build the hospital policy DB via Bright Data
uv run python scripts/crawl_policies.py recheck              # detect policy changes
uv run python scripts/build_price_tables.py [--fetch]        # rebuild policydb/prices/ from the hospitals' CMS price files
```

## How it works

1. **Read the bill.** Claude reads the bill and the insurance statement (PDFs or photos) into fields:
   patient, hospital, account, lines, amounts, the insurer's patient share.
2. **Review.** Two agents run side by side, and the case page shows each check as it happens:
   - **Nessie agent** (the patient's own records): payments the bill doesn't show, a line billed twice,
     duplicate bank charges, the bill against the insurance statement, lines above the hospital's posted
     price, income from payroll deposits, 12 months of medical spending.
   - **Bright Data agent** (public rules): the hospital's financial-assistance policy from our policy DB
     (refreshed live when it's stale, crawled live for a new hospital), the discount the patient qualifies
     for, the payment-plan terms.
3. **The plan.** Rules-based code (`billback/agents/decide.py`) turns the findings into a list of asks, biggest
   money first, each linked to its proof. You can ask about any finding, mark one wrong, or change the
   strategy in a chat; every change makes a new plan version. "Call now" approves the version on screen.
4. **The call.** BillBack phones billing (or the desk console), says it's an automated assistant, works
   through the asks, refuses to pay on the call, reads the case number back, and records what billing agreed.
5. **Resolved and follow-up.** A scorecard marks each ask got / refused / promised later. The balance only
   drops when billing confirms it. A payment plan, if one is needed, is written to the bank as scheduled
   bills. A follow-up is scheduled from what billing promised, and "Call again" reuses the plan with only the
   open asks.

```
 chat / drop-and-go ──► Orchestrator ──► audit log + live case page (web/case.html), Cases list (web/dashboard.html)
                              │
   ┌──────────────┬───────────┼────────────────┬────────────────────┬──────────────────┐
   ▼              ▼           ▼                ▼                    ▼                  ▼
 Intake        Nessie agent Bright Data      Decide (plan)        Caller             Follow-up / Planner
 Claude vision bank txns,   agent: policy DB rules-based asks,    Twilio or desk ↔   scorecard, next round,
 bill + EOB    payments,    ◄── SERP → Unlocker  versions,        Cartesia Ink/Sonic payment plan → Nessie
               duplicates,  → PDFs → Claude  planner chat         ↔ Claude call brain ── APPROVAL GATE ──
               income       → verified quotes
```

- **Orchestration** (`billback/orchestrator.py`): a fixed pipeline on purpose, so it's predictable on stage.
- **Harness** (`billback/harness.py`): every tool has a permission tier. `read` runs freely, `act` is
  announced, `consequential` waits for the patient's approval (placing a call, saving a payment plan). Every
  tool call, result and approval is an event streamed to the UI and appended to the audit log.
- **Money is computed, not generated** (`billback/finance.py`): reconciliation, duplicates, income from
  deposits, % of the 2026 federal poverty line, discount tiers, payment plans (written as "17 × $88/mo, then
  $78.98" so the total is exact). Claude extracts policy rules with verbatim quotes, and code checks each
  quote against the fetched document before any number is used.

## Sponsors: what each one does here

### Cartesia (voice)
Our own voice pipeline, not a hosted agent: Twilio Media Streams ↔ Cartesia Ink (streaming STT) ↔ Claude call
brain ↔ Cartesia Sonic (streaming TTS).
- **Ink** (`/stt/turns/websocket`, `ink-2`): turn detection (`turn.end`), and Claude starts drafting on
  `turn.eager_end`. Keyterms (patient, hospital and insurer names, "charity care", "EOB") improve recall.
  Desk mode streams the browser mic to Ink too (16 kHz PCM, `WS /api/calls/{id}/listen`).
- **Sonic** (`/tts/websocket`, `sonic-3.6`): Claude's reply is spoken sentence by sentence as it streams.
  Word timestamps (`add_timestamps`) tell us what billing actually heard when they interrupt.
- **Barge-in**: real words stop playback ("mm-hmm" doesn't); the call brain's memory is cut to the last word
  played, so BillBack never assumes billing heard the rest.
- **Pronunciation** (`billback/voice/pronounce.py`): money, dates, phone numbers, IDs, CPT codes (`<spell>`)
  and drug names (inline IPA phonemes). Case numbers are read back one character at a time, dashes said.
- **Voices**: American, British and Indian English, and Mexican Spanish (`billback/voice/voices.py`).
- **Phone menus and hold**: keypad tones as in-band DTMF on the same stream; recorded hold messages are
  ignored without calling Claude.
- **Latency** (measured live before credits ran out): first audio 0.44–1.1 s after the rep stops on the phone
  path (10 of 11 turns under 0.8 s); desk mode median 0.93 s (was 3.0 s).

Code: `billback/voice/` (`media.py`, `cartesia_ws.py`, `desk.py`, `heard.py`, `pronounce.py`),
`billback/integrations/cartesia.py`, `web/rep.html`, `scripts/sim_twilio_stream.py`. Docs:
[`docs/voice.md`](docs/voice.md). Voice and pronunciation demo: `/voice.html`.

### Bright Data (hospital policies and prices)
- **Policy database** (`policydb/`, committed as crawl evidence): 12 Bay Area hospitals. SERP API finds each
  hospital's financial-assistance policy, Web Unlocker fetches the page and its PDFs, Claude extracts the
  discount tiers and payment-plan rules with quotes, code verifies each quote against the fetched text, and
  SQLite keeps versions. Rechecks compare content hashes and re-extract only on change.
- **Live on the case page**: a case uses the stored policy unless it is stale (a "no tiers" result older than
  6 hours, or any policy older than 30 days) or the hospital is new; then it crawls live. **"Recheck now"**
  under the Bright Data panel does the same on demand. Each request streams as its own line: tool, URL, and
  found / empty / blocked / timed out / rate-limited / not found / server error, "request n of at most 10".
  Retries and fallbacks (next page, `site:` search, URLs on record, skip a failed PDF, stored copy) each show
  as a line. Hard cap: 10 requests per crawl (`billback/policies/crawl.py: HARD_CAP`).
- **Real prices**: each hospital's CMS price-transparency file, found through its `cms-hpt.txt`; about 67
  common ER and outpatient codes kept (`policydb/prices/`). The bill check flags lines above the hospital's
  posted price, for insured and self-pay bills (drug lines only when the units convert).
- **Same bill, every hospital** (`/hospitals.html`, `billback/policies/compare.py`): Marcus's $5,600 UCSF bill
  run through each stored policy. $0 at CPMC, UCSF, Alta Bates Summit, El Camino, John Muir, Kaiser Oakland
  and MarinHealth (their high-medical-cost or income tiers cover him); $5,600 at Chinese Hospital and Highland
  (their tiers are for uninsured patients) and at Stanford, Saint Francis and SF General (no verified income
  tiers on record).

Code: `billback/integrations/brightdata.py`, `billback/policies/`, `billback/agents/policy.py`,
`billback/prices.py`, `billback/price_sources.py`, `billback/billcheck.py`, `scripts/crawl_policies.py`.
Docs: [`docs/brightdata.md`](docs/brightdata.md).

### Capital One Nessie (the patient's bank)
- **Reads**: the customer and account found by the patient's name, then purchases, deposits, withdrawals and
  bills (`billback/integrations/nessie.py`).
- **Income**: payroll deposits annualised (pay varies per check), plus gig and payment-app deposits (Uber,
  DoorDash, Venmo, Zelle…) from the last 90 days. Labelled take-home pay.
- **Payments the bill missed**: a payment to the hospital's health system ("SUTTER HEALTH PYMT") matched by
  name and date, never on a premium or a non-payment.
- **Duplicate charges** on the card, reported apart from the bill.
- **12 months of medical spending** (pharmacies, clinics, dental…) added to this bill for the hospitals'
  "medical costs over 10% of income" rule. Each transaction is listed as proof.
- **Plan sized on the bank**: regular monthly bills found in the history lower what the patient can pay.
- **Writes**: a payment plan as one scheduled Nessie bill per payment (so it ends); after billing confirms a
  new balance, the hospital bill in Nessie is updated (`PUT /bills/{id}`).
- **Fallback**: if Nessie has no account for the patient or doesn't answer, the case uses sample bank data
  and says so on the case page.

Code: `billback/integrations/nessie.py`, `billback/agents/auditor.py`, `billback/finance.py`,
`billback/agents/planner.py`, `scripts/seed_nessie.py`.

### Anthropic Claude
Reads the bill and insurance statement (vision), extracts policy rules with verbatim quotes, runs the phone
call (a tool-use loop with `record_outcome`, `press_keys`, `end_call`; `billback/agents/caller.py`), reads the
call transcript into outcomes, answers "Ask" on a finding, the planner chat and "what's the status?". Models:
`claude-sonnet-5-5` and `claude-haiku-5-5` (`billback/config.py`). It never produces a dollar figure that code
didn't compute.

## Real vs simulated
- **Real:** Bright Data crawls of real hospital policies and real CMS price files (`policydb/`), Nessie API
  records, Claude, Cartesia speech, the approval gate and the audit log.
- **Simulated:** the patients, bills, insurance statements, insurer and bank histories are synthetic (PDFs are
  watermarked SAMPLE). "Hospital billing" is a teammate on the rep console (or their phone with Twilio).
- **Live mode builds the case only from the uploaded documents.** "Use a sample bill" sends the sample PDFs
  through the same reading; the sample case data only feeds the offline mode.
- **Replay** (`case.html?replay=1`, `&scenario=freecare`, `&jump=call`) plays back a recorded live desk-mode
  run. Only live runs are recorded.

## Known limitations
- **Cartesia is out of credits** (HTTP 402 since 2026-10-25). No real Twilio phone call has been recorded yet;
  the phone path is tested against simulated Twilio and Cartesia sockets. Desk mode on Ink, barge-in with
  word timestamps, ID read-back and keypad tones were built and tested offline (fakes, plus a headless Chrome
  run of `rep.html`) and need one live check. Without credits, desk mode falls back to Chrome's speech
  recognition and voice, and says so.
- **Desk mode** doesn't yet start Claude early on Ink's `turn.eager_end` (the phone path does). In
  conference mode Ink hears one mixed track.
- **The live Bright Data path** (streamed crawl, retries, "Recheck now", stale re-crawl) is tested with replies
  in the real shapes but has not run against Bright Data since it was built. Three hospitals (Stanford, Saint
  Francis, SF General) are stored as "no tiers", so they give no discount. MarinHealth's and El Camino's tiers
  were read as "all patients" and need a manual check. A policy that changes on "Recheck now" is shown but
  not applied to the current plan; a new case applies it. The `crawl_policies.py recheck` CLI doesn't re-crawl
  "no tiers" hospitals from Google (the app does).
- **Price tables** are a curated subset (about 67 codes, one representative line per code). Drug lines priced
  in other units (e.g. per mL vs per mg) are listed as "not compared".
- **Synthetic data.** Patients, insurer and bank histories are made up. The live Nessie sandbox still has the
  older, cleaner histories until it is reseeded. Medical merchants are matched by keyword, not MCC codes.
  Nessie's account balance isn't read.
- **Income is take-home pay** (bank deposits). The poverty-line test should use gross income. BillBack labels
  the figure, estimates gross as take-home ÷ 0.80, and warns on the case page when that would change the
  discount ("have pay stubs ready"), but eligibility still uses take-home. Using gross would flip two test
  patients (marcus-real, dana-altabates-refund) from free care to none.
- **Recorded replays** predate the latest changes: they still end on "$8,700 → $600" and use older wording.
  A live run shows the current behaviour (balance stays at billing's figure until confirmed).
- **Not built:** Gmail intake (the "Demo inbox" chip loads the sample bill, marked simulated), "Start over"
  (re-fetching data for a case), the California collections-law check, notifications when a case needs you.
  The `/flows/` pages are early intake mockups, not linked from the app (except `/flows/drop-go.html`, which
  works).
- **Admin key:** the browser side (`web/auth.js`) has only been tested server-side.

## Privacy and responsible use
- **Says what it is.** Every call opens with "an automated assistant calling on behalf of <patient>, with their
  authorization" and says the call is transcribed (`billback/agents/caller.py`). The planner chat refuses to
  hide this (`billback/agents/decide.py`).
- **Never agrees to pay.** The call brain never pays, agrees to pay, or gives card or bank details; the planner
  chat refuses any strategy that would.
- **Nothing happens without approval.** Placing a call and saving a payment plan are `consequential` tools
  that wait for the patient (`billback/harness.py`). A change to the plan after approval resets the approval.
- **Patient authorization (HIPAA 45 CFR 164.510(b)).** A hospital may discuss a bill with someone the patient
  involves in payment, with the patient's verbal agreement. With `PATIENT_PHONE_NUMBER` set, BillBack first
  calls the patient, asks one scripted question, and logs the question, the transcribed answer, the time and
  the Twilio CallSid (`billback/voice/authorization.py`). Without it, the in-app approval is the recorded
  agreement.
- **No audio is recorded.** Calls are transcribed only; the app saves no call audio (the
  `scripts/sim_twilio_stream.py` test tool saves its own simulated audio to `data/sim/<case>/`).
- **Claims are checked.** Every policy figure needs a verbatim quote found in the fetched document; every
  finding shows its proof and source. The balance never drops before billing confirms it. Legal claims are
  reviewed in [`docs/legal-check.md`](docs/legal-check.md).
- **Accessibility.** The patient never fills in a form: drop the documents (PDF or photo) or chat. BillBack
  makes the phone call, so a patient who can't spend an hour on hold, or isn't comfortable arguing on the
  phone, doesn't have to. Every step, finding and call line is also plain text on the case page.
- **Where patient data goes:** Anthropic (documents, transcripts), Nessie (lookup by name), Cartesia and
  Twilio (call audio, live only). Bright Data requests carry only hospital names and URLs, never patient data.
- **Where patient data is stored (all under `data/`, gitignored):**
  - `data/cases/<case_id>.json`: the case (bill, insurance statement, findings, plan, call transcripts).
  - `data/<case_id>.audit.jsonl`: the append-only audit log (every event, approval and transcript line).
  - `data/replay_<scenario>.json`: the last live run, for replay.
  - `data/nessie_ids*.json`: Nessie ids of the seeded demo accounts.
  - Uploaded PDFs are sent to Claude and not saved to disk.
- **Deleting it:** delete a case from the Cases list (removes its snapshot; refused mid-call), or `POST
  /api/reset` (all case snapshots). Both keep the audit log on purpose. To erase everything, stop the server
  and delete `data/` (or the files above for one case).

## Tests
`uv run pytest -q`: fully offline, no API keys. 281 tests on master before PRs #1–#8; those PRs add about 140
more (300+ in total). Live checks that need paid credits are listed in each PR and in
[`docs/PLAN.md`](docs/PLAN.md).

## Docs
- [`docs/DEMO.md`](docs/DEMO.md): the 2-minute demo script and fallbacks.
- [`docs/brightdata.md`](docs/brightdata.md): the policy DB, crawl log, live crawl, price tables.
- [`docs/voice.md`](docs/voice.md): the voice pipeline, latency, pronunciation, authorization.
- [`docs/plan-format.md`](docs/plan-format.md): the plan's data format.
- [`docs/legal-check.md`](docs/legal-check.md): legal claims checked against sources.
- [`docs/feasibility.md`](docs/feasibility.md), [`docs/competitors.md`](docs/competitors.md),
  [`docs/judge-qa.md`](docs/judge-qa.md), [`docs/PLAN.md`](docs/PLAN.md).

## Layout
```
billback/  app.py orchestrator.py harness.py events.py finance.py billcheck.py followups.py llm.py config.py auth.py caselist.py prices.py
           agents/        intake auditor (Nessie agent) policy (Bright Data agent) decide scorecard caller planner
           policies/      registry discover fetch extract store monitor pipeline crawl compare routes
           voice/         calls media cartesia_ws desk heard pronounce voices authorization audio routes
           integrations/  nessie brightdata cartesia twilio
web/       dashboard.html (Cases, at /)  new.html (chat intake)  case.html  hospitals.html  rep.html (desk billing)
           approve.html (phone approval)  voice.html  pitch.html  flows/drop-go.html
demo/      case.json, case_freecare.json (synthetic patients), sample PDFs, scenarios/, outcomes/, replays
policydb/  policies.sqlite3, policies.json, raw/ (Bright Data crawl artifacts), prices/ (real price tables)
scripts/   seed_nessie crawl_policies build_price_tables make_scenario setup_cartesia sim_twilio_stream make_demo_docs
```

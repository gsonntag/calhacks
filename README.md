# Remy

**An AI agent that fights your hospital bill.** Upload the bill and your insurance statement. Remy
works out what you actually owe from your bank records, finds the discount the hospital's own published
policy requires it to give you, calls billing to get it fixed, and follows up until the corrected bill
arrives. Nothing is said or paid on your behalf without your approval.

Demo arc (synthetic patient, real hospital policy): **$8,700 final notice from California Pacific Medical
Center → $1,100 after the insurance the hospital never applied → $600 after the $500 she already paid →
an interest-free plan (6 × $100/month) under Sutter Health's own billing policy, collections paused, case
number, follow-up scheduled.**

## How it works

```
 chat / drop-and-go ──► Orchestrator (our harness) ──► audit log + live timeline (web/case.html)
                              │                         dashboard + follow-ups (web/dashboard.html)
   ┌──────────────┬───────────┼────────────────┬────────────────────┬──────────────────┐
   ▼              ▼           ▼                ▼                    ▼                  ▼
 Intake        Auditor      Policy           Caller               Follow-up          Planner
 Claude vision Nessie txns  policy DB ◄──    Twilio ↔ Cartesia    remembers calls,   Nessie recurring
 bill + EOB    payments,    Bright Data      STT/TTS ↔ Claude     dates from what    bill (only if
               duplicates,  pipeline:        call brain; patient  billing promised,  something is owed)
               income       SERP → Unlocker  authorization        calls back         ── APPROVAL GATE ──
                            → PDFs → verified handoff
                            quotes
```

**Orchestration** (`remy/orchestrator.py`): a fixed pipeline, on purpose (predictable on stage):
intake → Auditor ∥ Policy (in parallel, sharing results) → approval → call → plan → follow-ups. Claude is
used where judgement is needed: reading the documents, extracting policy rules, the phone call (a Claude
tool-use loop with `record_outcome` / `end_call`, `remy/agents/caller.py`), reading the call outcome and
answering "what's the status?". The Auditor, Policy step and Planner are deterministic code.

**Harness** (`remy/harness.py`): tools have permission tiers: `read` runs freely, `act` is announced,
`consequential` blocks on the patient's approval (placing the call, saving a payment plan). Every tool
call/result/approval is an event streamed to the UI and appended to `data/<case>.audit.jsonl`.

**Money is computed, not generated** (`remy/finance.py`): reconciliation, duplicate detection, income
annualisation from payroll deposits, % of the federal poverty line, discount tiers (including the insured
high-medical-cost rule), payment plan within the policy's income cap. Claude extracts policy text with verbatim quotes, which the
code verifies against the fetched document, and does the talking; it never invents a number.

## Sponsor map (where to look)
| Sponsor | What it does here | Code | Docs |
|---|---|---|---|
| **Bright Data** | Hospital policy database: SERP API discovers each hospital's financial-assistance policy, Web Unlocker fetches the page and its policy PDFs, Claude extracts discount tiers, collections rules and payment-plan terms (interest-free, income cap) whose quotes are verified against the source, versioned SQLite store, hash-based change detection. Seeded live with 12 Bay Area hospitals, all with verified discount tiers; same bill → different answers. When a hospital's own site doesn't give the real policy (Stanford, ZSFG, Saint Francis), the copy it filed with California HCAI is fetched over plain HTTPS (Bright Data refuses .gov sites) and checked the same way. Real price tables: each hospital's CMS price file found via its `cms-hpt.txt`, ~67 common ER/outpatient codes kept (gross, cash, negotiated min/max, named payer rates). | `remy/policies/` (HCAI: `remy/policies/hcai.py`), `remy/integrations/brightdata.py`, `scripts/crawl_policies.py`, `remy/prices.py`, `remy/price_sources.py`, `scripts/build_price_tables.py` | [`docs/brightdata.md`](docs/brightdata.md); crawl evidence in `policydb/` |
| **Cartesia** | Our own voice pipeline: Twilio Media Streams ↔ Cartesia Ink streaming STT + Sonic TTS ↔ Claude call brain, barge-in, voice/accent picker (American, British, Indian English, Spanish), pronunciation normalizer for money, IDs, CPT codes and medical terms. Managed Agents path kept as an alternative. | `remy/voice/`, `remy/integrations/cartesia.py`, `scripts/sim_twilio_stream.py` | [`docs/voice.md`](docs/voice.md); voice demo at `/voice.html` |
| **Capital One Nessie** | The patient's bank: payroll deposits → income, the $500 payment the hospital missed, a duplicate charge, and a payment plan written back as a recurring bill when something is still owed. | `remy/integrations/nessie.py`, `remy/agents/auditor.py`, `scripts/seed_nessie.py` | [`docs/feasibility.md`](docs/feasibility.md) (how real bank data is obtained) |
| **Anthropic Claude** | Intake (vision on bill/EOB), policy extraction with quotes, the call brain, outcome/follow-up extraction, status answers. | `remy/agents/`, `remy/followups.py` | |

Also: [`docs/competitors.md`](docs/competitors.md), [`docs/judge-qa.md`](docs/judge-qa.md),
[`docs/DEMO.md`](docs/DEMO.md), pitch deck at `/pitch.html`.

## Run

```bash
uv sync
cp .env.example .env            # fill what you have; blanks run in a labelled offline mode
uv run uvicorn remy.app:app --port 8000
# open http://localhost:8000   (Cases; the chat intake when there are none yet. Also /flows/, /hospitals.html)
uv run pytest -q                # 139 tests, fully offline
```

Setup scripts (once):
```bash
uv run python scripts/seed_nessie.py [freecare]    # synthetic patient's bank in Nessie
uv run python scripts/crawl_policies.py crawl      # (re)build the hospital policy DB via Bright Data
uv run python scripts/crawl_policies.py recheck    # detect policy changes
uv run python scripts/crawl_policies.py hcai <id>  # add a hospital's HCAI-filed policy (no Bright Data request)
uv run python scripts/build_price_tables.py        # rebuild policydb/prices/ from data/price_files/ (--fetch to download)
```

Real phone calls need Twilio credentials and a public HTTPS URL (Twilio must reach `/voice/twilio/*`):
`cloudflared tunnel --url http://localhost:8000`, then set `PUBLIC_BASE_URL` (see `docs/voice.md`).
Without them, calls run in **desk mode**: the case page links a rep console (`rep.html`) where a teammate
plays billing, and Remy answers out loud through Cartesia TTS. With no Anthropic key it falls back to a
scripted caller so the whole flow still runs offline. `case.html?replay=1` replays the last finished run.

## Status: what's done, what's partial

| Piece | Status | Evidence |
|---|---|---|
| Intake (Claude reads the bill + EOB PDFs) | Done, run live | live cases start empty and are filled from the documents (`orchestrator.new_case`, `app._apply_uploads`) |
| Money math | Done | `remy/finance.py`, `tests/test_finance.py` |
| Bank (Capital One Nessie): income, missed payment, duplicate, plan written back | Done, run live | `integrations/nessie.py`, `agents/planner.py`, `scripts/seed_nessie.py` |
| Hospital policy DB (Bright Data SERP → Unlocker → PDFs → Claude → quote verification → SQLite) | Done, run live | `remy/policies/`, crawl artifacts in `policydb/raw/`, request log in `policydb/policies.json` |
| Policy change detection (recheck) | Done; tested, no real policy change seen yet | `policies/monitor.py`, `tests/test_policies.py::test_recheck_detects_change_only_when_content_changes` |
| Call agent (Claude tool-use loop) | Done, run live in desk mode | `agents/caller.py`; replays in `demo/replay_*.json` |
| Real phone call (Twilio Media Streams ↔ Cartesia STT/TTS ↔ Claude, barge-in) | **Partial: built and tested against simulated Twilio/Cartesia sockets; no live phone call recorded yet** | `remy/voice/`, `tests/test_voice.py`, `scripts/sim_twilio_stream.py` |
| Cartesia TTS in desk mode, voice picker, pronunciation | Done, run live | `integrations/cartesia.py`, `voice/voices.py`, `voice/pronounce.py` |
| Fast replies: Claude streamed into Sonic sentence by sentence, speculative start on Ink eager_end, prompt caching | Done, measured live: first audio 0.44–0.8 s after the rep stops (phone simulator), median 0.93 s (desk mode, was 3.0 s) | `voice/media.py`, `voice/desk.py`, `llm.py`, `docs/voice.md` (Latency), `tests/test_latency.py` |
| Approval gate, audit log, follow-ups, "call again", status answers | Done | `harness.py`, `followups.py`, `tests/test_followups.py` |
| Gmail intake | **Not built: the "Demo inbox" chip loads the sample bill** (marked `simulated` by the API and in the UI) | `app.gmail_scan` |
| 10 of the 12 intake designs in `/flows/` | Clickable mockups (labelled Mock); Drop & Go is the live one | `web/flows/index.html` |

## Real vs simulated (say this to judges)
- **Real:** Bright Data crawls of real hospital policies, plus the policies hospitals filed with California HCAI
  (both committed in `policydb/`), real prices from the
  hospitals' public CMS price-transparency files (`policydb/prices/`), Nessie API records,
  Claude reasoning, Cartesia STT/TTS, the approval gate and audit log.
- **Simulated:** the patient, bill, EOB and bank history are synthetic (PDFs are watermarked SAMPLE).
  The numbers were chosen for the story: Rosa's insurance share ($1,100) and pay ($1,400 biweekly) make her
  an honest "not eligible for the write-off, but owed a lot less" case; Marcus is the free-care case.
  "Hospital billing" is a teammate (the rep console, or their phone once Twilio is live). The "Demo inbox"
  chip loads the sample bill instead of real Gmail.
- **Replay** (`case.html?replay=1`) plays back a recorded live run (desk mode) with long pauses shortened;
  only live runs are ever recorded.
- **Live mode builds the case only from the uploaded documents:** Claude reads the patient's name, DOB,
  bill and EOB; the bank account is found in Nessie by the patient's name; the policy comes from the Bright
  Data policy DB. "Use the sample bill" sends the sample PDFs through the same reading. The sample case data
  is only used by the offline mock (no API keys).
- Remy says it's an automated assistant and that the call is transcribed, never agrees to pay on a
  call, and gets the patient's verbal authorization (HIPAA 45 CFR 164.510(b)); see `docs/feasibility.md`.

## Layout
```
remy/  app.py orchestrator.py harness.py events.py finance.py followups.py llm.py config.py prices.py price_sources.py
           agents/        intake auditor policy caller planner
           policies/      registry discover fetch extract store monitor pipeline crawl compare routes
           voice/         calls media cartesia_ws pronounce voices authorization audio routes
           integrations/  nessie brightdata cartesia twilio
web/       dashboard.html (Cases, served at /)  new.html (chat intake)  flows/ (12 intake designs)  case.html (timeline)
           hospitals.html (policy DB)  voice.html  approve.html (phone)  rep.html (desk-mode billing)  pitch.html
demo/      case.json, case_freecare.json (synthetic patients), sample PDFs, offline test fixtures, replays
policydb/  policies.sqlite3, policies.json, raw/ (real Bright Data crawl artifacts), prices/ (real price tables)
scripts/   seed_nessie  crawl_policies  build_price_tables  setup_cartesia  sim_twilio_stream  make_demo_docs
```

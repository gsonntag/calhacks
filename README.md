# BillBack

**An AI agent that fights your hospital bill.** Upload the bill and your insurance statement. BillBack
works out what you actually owe from your bank records, finds the discount the hospital's own policy
requires it to give you, calls billing to get it fixed, and sets up an affordable plan — nothing is paid
without your approval.

Demo arc: **$8,700 final notice → $600 actually owed → $150 under the hospital's own charity-care policy
→ account on hold, case number, interest-free plan approved on your phone.**

## How it works

```
 chat (web/index.html) ──► Orchestrator (our harness) ──► audit log + live dashboard (web/case.html)
                              │
   ┌──────────────┬───────────┼──────────────┬──────────────────┐
   ▼              ▼           ▼              ▼                  ▼
 Intake        Auditor      Policy         Caller             Planner
 Claude vision Nessie txns  Bright Data    Cartesia agent     Nessie recurring bill
 bill + EOB    payments,    hospital's     live phone call    ── APPROVAL GATE ──
               duplicates,  financial-     argues with the    (patient's phone,
               income       assistance     evidence           web/approve.html)
                            policy + Claude
                            quote extraction
```

**Harness** (`billback/harness.py`): tools have permission tiers — `read` runs freely, `act` is announced,
`consequential` blocks on the patient's approval. Every tool call/result/approval is an event streamed to
the dashboard and appended to `data/<case>.audit.jsonl`.

**Money is computed, not generated** (`billback/finance.py`): reconciliation, duplicate detection, income
annualisation, % of the federal poverty line, discount tier, payment plan. Claude extracts policy text with
verbatim quotes and does the talking; it never invents a number.

## Sponsors
| Sponsor | Where |
|---|---|
| **Cartesia** | Managed Agent places the real outbound call (Claude Haiku on Cartesia), webhook tool reports outcome, call events stream the transcript. Sonic TTS for desk mode. |
| **Capital One Nessie** | Patient's account: payroll deposits (income), the $500 payment, duplicate pharmacy charge, the hospital bill, and the approved recurring payment plan. |
| **Bright Data** | SERP API finds the hospital's financial-assistance policy; Web Unlocker fetches it live. |
| **Anthropic Claude** | Chat intake, bill/EOB vision extraction, policy extraction with quotes, the call brain (desk mode). |

## Run

```bash
uv sync
cp .env.example .env            # fill what you have; blanks run in demo mode
uv run uvicorn billback.app:app --reload --port 8000
# open http://localhost:8000
```

Phone calls need a public HTTPS URL for Cartesia webhooks:
```bash
cloudflared tunnel --url http://localhost:8000     # copy the https URL into PUBLIC_BASE_URL
uv run python scripts/seed_nessie.py               # once, after NESSIE_API_KEY is set
uv run python scripts/setup_cartesia.py            # once; paste printed ids into .env
```

Demo screens: laptop = `case.html` (dashboard), patient's phone = `approve.html?case=<id>` (link on the
dashboard), teammate's phone = receives the Cartesia call as "hospital billing".

**Backup call mode:** if `CARTESIA_AGENT_ID`/`REP_PHONE_NUMBER` aren't set, the call runs in *desk mode*:
the dashboard links a rep console (`rep.html`) where a teammate plays billing by voice (Chrome) or typing,
and BillBack answers through Cartesia TTS (or the browser voice). With no Anthropic key it falls back to a
scripted caller so the whole flow still runs offline.

## Real vs simulated (say this to judges)
- **Real:** Nessie API records, Bright Data live fetch of a real hospital policy page, Cartesia phone call,
  Claude reasoning, the approval gate and audit log.
- **Simulated:** the patient, bill, EOB and bank history are synthetic; "hospital billing" is a teammate;
  the Gmail button loads a demo inbox. The dashboard header shows live/demo per integration.
- BillBack discloses it's an automated assistant on every call and never agrees to pay on a call.

## Layout
```
billback/  app.py  orchestrator.py  harness.py  events.py  finance.py  llm.py  config.py
           agents/  intake auditor policy caller planner
           integrations/  nessie brightdata cartesia
web/       index.html (chat)  case.html (dashboard)  approve.html (phone)  rep.html (desk-mode call)
demo/      case.json (synthetic patient)  policy_sample.md (offline policy)
scripts/   seed_nessie.py  setup_cartesia.py
```

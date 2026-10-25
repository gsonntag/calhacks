# Remy — Devpost draft

## Inspiration
An $8,700 "final notice" for an ER visit insurance was supposed to cover. Most people in that spot either
pay it or ignore it until collections. The fixes exist — insurance adjustments that never got posted,
payments the hospital "can't see", charity-care discounts nonprofit hospitals are required to offer — but
getting them takes hours on hold and knowing exactly what to ask for.

## What it does
Drop in your hospital bill and insurance statement. Remy:
1. **Proves what you actually owe**: an Auditor agent reconciles the bill against your insurance EOB and
   your bank records (Capital One Nessie): it finds payments the hospital missed and double charges.
2. **Finds the discount the hospital owes you**: a Policy agent looks the hospital up in our policy
   database, built by crawling each hospital's own published financial-assistance policy with Bright Data
   (pages and policy PDFs, every quoted rule verified against the source, re-checked for changes), works
   out your income from payroll deposits, and checks you against its tiers and its payment-plan terms.
3. **Calls billing for you**: after you approve, Remy phones the billing office through our own voice
   pipeline (Twilio audio, Cartesia speech-to-text and Sonic text-to-speech, Claude as the brain), with the
   patient's verbal authorization at the start, in the voice and accent you pick, pronouncing claim
   numbers and medical terms correctly. It cites the evidence, asks for the corrections, the discount and a
   hold on collections, and gets a case number. It never agrees to pay anything.
4. **Follows up**: it remembers every call, turns "we'll send a corrected bill in 5 business days" into a
   follow-up date, tells you the status when you ask, and calls back when a promise is overdue. If anything
   is still owed, it sets up an interest-free plan in your bank, only after you approve.

Demo: **$8,700 → $1,100 (insurance the hospital never applied) → $600 (the $500 she already paid), on a
6 × $100 interest-free plan** under Sutter's own billing policy, collections paused, case number,
follow-up scheduled. She isn't eligible for Sutter's write-off, and Remy says so. Same bill at four San
Francisco hospitals → different answers (our second case, Marcus: $0 at CPMC and UCSF, $5,600 at the
other two).

## How we built it
- **Our own agent harness** (Python): tools carry permission tiers (read / act / consequential);
  consequential tools block on a human approval gate; every tool call and result is streamed to the UI and
  appended to an audit log.
- **Pipeline:** intake (Claude vision reads PDFs/photos) → auditor and policy check (deterministic code,
  run in parallel, the policy step waiting on the auditor's results) → approval → caller (a Claude tool-use
  loop that runs the phone call) → planner. A fixed order on purpose; Claude where judgement is needed.
- **Deterministic money math** — the model never invents a number; every figure cites its source.
- **Capital One Nessie** for the patient's account; **Bright Data** SERP API + Web Unlocker feeding a
  versioned hospital-policy database with quote verification and change detection; **Cartesia** Ink STT +
  Sonic TTS in our own Twilio voice pipeline (with a voice/accent picker and a medical pronunciation
  normalizer); **Claude** (Sonnet 5.5 / Haiku 5.5) for reading, extraction, the call brain and follow-ups.
- FastAPI + WebSockets; a vanilla-JS timeline UI (Upload → Review → Approval → Call → Resolved).

## Challenges
- Nessie's rewritten API (bills must be created with `recurring_date` or listing breaks; whole-dollar amounts).
- Cartesia's managed agent minutes are scarce, so we built our own streaming pipeline on Twilio Media
  Streams with Cartesia STT/TTS: turn-taking, barge-in, and a conference design for patient authorization.
- Bright Data returns 200 even when the target fails (status is in headers); Google sometimes returns only
  redirect links; government sites are blocked, so we crawl hospitals' own sites and PDFs instead.
- Making a voice agent negotiate without monologuing — tested against simulated "difficult" billing reps
  over Cartesia's WebSocket before ever ringing a real phone.

## Accomplishments
A real phone negotiation driven by real bank data, with every number traceable — and a human in the loop
for anything that matters.

## What we learned
Hospitals' own policies are the strongest argument; the hard part is getting the right facts in front of
the right person on the phone.

## Why it's feasible
Banks: Remy only needs transactions and payroll deposits, which consumer-consent aggregators (Plaid, MX,
Finicity) provide today; Nessie is the sandbox stand-in. Hospitals: nonprofit hospitals must publish these
policies (IRS 501(r)) and California's Hospital Fair Pricing Act sets the tiers. Calls: HIPAA lets billing
discuss payment with someone the patient verbally authorizes (45 CFR 164.510(b)). Details: `docs/feasibility.md`.

## What's next
Signed authorization on file instead of the warm handoff, real bank feeds (Plaid/MX/Finicity), real
inbox/portal intake, pre-filled assistance applications and dispute letters, more hospitals in the policy
database, Spanish-language patient side.

## Built with
python, fastapi, anthropic-claude, cartesia, twilio, capital-one-nessie, bright-data, sqlite, websockets, javascript

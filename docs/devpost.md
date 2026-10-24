# BillBack — Devpost draft

## Inspiration
An $8,700 "final notice" for an ER visit insurance was supposed to cover. Most people in that spot either
pay it or ignore it until collections. The fixes exist — insurance adjustments that never got posted,
payments the hospital "can't see", charity-care discounts nonprofit hospitals are required to offer — but
getting them takes hours on hold and knowing exactly what to ask for.

## What it does
Drop in your hospital bill and insurance statement. BillBack:
1. **Proves what you actually owe** — an Auditor agent reconciles the bill against your insurance EOB and
   your bank records (Capital One Nessie): it finds payments the hospital missed and double charges.
2. **Finds the discount the hospital owes you** — a Policy agent pulls the hospital's own published
   financial-assistance policy (Bright Data), works out your income from payroll deposits, and checks you
   against its tiers.
3. **Calls billing for you** — after you approve, a Cartesia voice agent phones the billing office, says it's
   an automated assistant acting for you, cites the evidence, asks for the corrections, the discount review
   and a hold on collections, and gets a case number. It never agrees to pay anything.
4. **Sets up what's left** — an interest-free plan sized to your income, written to your bank only after you approve.

Demo: **$8,700 → $2,100 → $150**, case number, account on hold, $50/month.

## How we built it
- **Our own agent harness** (Python): tools carry permission tiers (read / act / consequential);
  consequential tools block on a human approval gate; every tool call and result is streamed to the UI and
  appended to an audit log.
- **Agents:** intake (Claude vision reads PDFs/photos), auditor, policy (runs in parallel with the auditor,
  waiting on its results), caller, planner.
- **Deterministic money math** — the model never invents a number; every figure cites its source.
- **Capital One Nessie** for the patient's account, **Bright Data** SERP + Web Unlocker for live policy
  pages, **Cartesia** Managed Agents for the outbound call, **Claude** (Sonnet 5.5 / Haiku 5.5) for reading,
  extraction and the call brain.
- FastAPI + WebSockets; a vanilla-JS timeline UI (Upload → Review → Approval → Call → Resolved).

## Challenges
- Nessie's rewritten API (bills must be created with `recurring_date` or listing breaks; whole-dollar amounts).
- Cartesia retired hosted Line agents mid-year; we moved to Managed Agents and, to avoid exposing our server
  publicly, poll the call for its transcript and have Claude extract the outcome afterwards.
- Making a voice agent negotiate without monologuing — tested against simulated "difficult" billing reps
  over Cartesia's WebSocket before ever ringing a real phone.

## Accomplishments
A real phone negotiation driven by real bank data, with every number traceable — and a human in the loop
for anything that matters.

## What we learned
Hospitals' own policies are the strongest argument; the hard part is getting the right facts in front of
the right person on the phone.

## What's next
Signed HIPAA authorization flow, real inbox/portal intake, pre-filled assistance applications and dispute
letters, Spanish-language patient side, and automatic follow-ups until the corrected statement arrives.

## Built with
python, fastapi, anthropic-claude, cartesia, capital-one-nessie, bright-data, websockets, javascript

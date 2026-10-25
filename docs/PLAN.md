# BillBack plan (from 2026-10-25)

Working branch: `master` on GitHub `gsonntag/calhacks` (the only remote). Gavin said so on 2026-10-25:
`after-david-takeover` was squashed onto master (d2a5b45), and fixes now go straight to master. The 6 PM
submission is tag `submitted-2026-10-24`. `after-david-takeover` is no longer updated. Backups: tag
`submitted-2026-10-24`, branch `pre-david-work`, local branch `backup/old-billback-master`.

## Judges' review (2026-10-25): what to fix before judging

Four mock judges scored the project. They all praised the same thing: Claude never produces an unchecked
number (policy figures need an exact quote from the crawled page, code does the money math, every figure cites
its source). Scores: General "finalist, not yet a winner" · Cartesia 6.9/10, "likely loses to a team with a
real phone call" · Nessie "likely top 3" · Bright Data "strong contender, moderate fixable risk of
disqualification" (Live-ness 4/10). Gavin's picks are marked ✔. Not started.

### P0: before anyone sees it (bugs and disqualification risks)
1. ✔ **Login on approve and reset.** `POST /api/approvals/{id}` and `POST /api/reset` (`billback/app.py:421`,
   `:567`) are open to anyone. Through a public tunnel, a stranger could approve a call that rings a real
   phone. Add a shared secret.
2. ✔ **No false win.** When billing only puts the account on hold, the case still shows the target ($600) as
   the balance (`billback/orchestrator.py:455`). Show the real balance until the reduction is confirmed.
3. ✔ **A patient missing from Nessie stops the whole case** (`billback/integrations/nessie.py:167`). Fall
   back to the sample data, and say so on the case page.
4. **The app can't run on sample policies on stage.** Refuse to start without working keys, so
   `SAMPLE_TIERS` can't appear. The Bright Data track's "mocked or hardcoded data" rule could disqualify us.
5. **The 6-month payment plan never ends.** It's written to Nessie as a recurring bill with no end date.

### P1: the demo (every judge raised these)
6. **Lead with a case that shows off the sponsors.** Rosa's $8,700 → $600 is subtraction (insurance
   already covered + one $500 bank payment). Bright Data says she's "not eligible", and California law
   already requires the interest-free plan. Lead with Marcus ($5,600 → $0 free care), or show "same bill at
   CPMC: $0, at ZSFG: $5,600". (See also item 5 of "Rough version": rosa-real.)
7. **Something live in the 2 minutes.**
   - Bright Data: a 20-second live crawl starting at 0:00 (a bill from a hospital that isn't stored yet,
     streaming on the case page), or "Recheck now" on stage.
   - Cartesia: record one real Twilio call and show it with the latency numbers (needs credits).
   - ✔ Cartesia: desk mode listens with Chrome's speech recognition (`web/rep.html:150`). Route it through
     Ink so the whole demo uses Cartesia, and allow interrupting. Can be built offline; live test needs
     credits.
8. **Docs agree with each other and with the code.** README says 139 tests (281); `docs/DEMO.md` says 4
   hospitals (12) and is a 3-minute script (judging is 2); `docs/brightdata.md` says price tables aren't
   used (they are); add a "Known limitations" section to the README (the Cartesia track asks for it) with
   PHI data-retention notes.
9. **Hide the extras for judging.** About 10 app pages plus 13 mockups in `/flows/`: take them out of the
   menus.

### P2: correctness
10. **Income:** the poverty-line test uses gross pay but the income is take-home pay, so patients look more
    eligible than they are. Gig and Venmo income are ignored.
11. **Interrupted replies:** when billing interrupts, BillBack remembers its whole reply, as if billing
    heard it. Use Sonic word timestamps to cut it at the word where it was interrupted.
12. **Hospitals stored as "no tiers" are never re-crawled**, so Saint Francis stays wrong.
13. **Read IDs back for confirmation.** A sim run accepted "FA 20931" misheard as "FA209-31".

### P3: per-track upgrades (if there's time)
- Nessie: add up 12 months of medical purchases for the high-medical-cost rule (makes bank data decide
  eligibility; also in "Later"); size the plan on her balance and recurring bills instead of a flat 4%; mark
  the bill updated in Nessie once billing confirms; seed messier data (today the $500 payment names the
  hospital account number, and pay is identical every 14 days).
- Cartesia: keypad tones for phone menus, stay quiet during hold music.

## Open handoff items (updated 2026-10-25, 06:50)

- **Call speed is merged** (e1fa24f, pushed). Still to do: rehearse the new streaming player in
  `web/rep.html` in Chrome once Cartesia has credits again.
- **Cartesia is out of credits** (HTTP 402). Phone path down; desk mode uses the browser voice until topped up.
- **Next to build:** item 5 below (Bright Data agent: real prices), then step 4's live call screen.
- **Test bills:** `web/samples/test-bills/` (served at /samples/test-bills/), from `demo/scenarios/`. Uploading
  needs a server with API keys. Only Rosa and Marcus have Nessie bank accounts; Dana, Amara, Lena don't yet.
- **Contacts:** `billback/contacts.py` + `policydb/contacts/` (merged). Not wired in yet: step 3's "Who
  we'll call" still takes the number from the bill; it can use `contacts.for_case(case)`.

## Rule: paid API credits

Cartesia ran out of credits on 2026-10-25 (HTTP 402 "Model credits limit reached") after an agent used it for
live latency benchmarks. From now on:
- Work offline by default: fixtures, mocks, recorded replays, the realistic cases in demo/scenarios/.
- Any live call to Cartesia, Bright Data, Twilio, Nessie or Claude-for-measurement needs a small, stated cap,
  and more than a handful of requests needs the user's OK first. Never benchmark in loops against a voice API.
- On any 402 / quota / credits error: stop immediately and report it.

## Decisions

**Focus:** the main user flow only: the case page (find the mistakes → approve → call → outcome) and the
Cases list (follow-ups). Hospitals and Under the hood stay as they are; improve later.

**Not a black box:** every check an app agent runs, and every finding with its proof and money saved, must
show up on the case page as it happens.

**Two money-checking app agents**, run in parallel in the Review step:

| App agent | Covers | Checks |
|---|---|---|
| Nessie agent (today's Auditor) | The patient's own records: bank (Nessie), bill, insurance statement | missed payments, duplicate bank charges, bill vs insurance statement, duplicate lines on the bill |
| Bright Data agent (today's Policy agent) | Public rules and data: hospital policy, state law, price files | charity care / discounts, payment-plan rules, price check against the hospital's real prices |

The design audit (`docs/ideas-backlog.md`) found nothing that needs a third agent.

**Real data first, mock only where we must.** Real hospital prices, real policies, real laws. The patient,
bank history and insurer stay synthetic.

## Rough version (build now, in order)

Status (2026-10-25, 06:50): 1–4 built · 5 not built · 6 built (step 3). Outside this list: step 5 built
(offline), step 4's live call screen not built, call speed merged. 281 tests pass.

1. **Fold New case into Cases.** *(Built.)* Cases is home, with a "+ New case" button that opens the chat intake. With
   no cases yet, go straight to the chat. Top bar: Cases · Hospitals · Under the hood.
2. **Shared format for the two app agents.** *(Built: `billback/agents/report.py`.)* One shape for what each agent reports: steps (label, status,
   result) and findings (what was found, money saved, proof, source). Both agents and the UI use it.
3. **Generic Review screen.** *(Built, commit 6563e09.)* The case page shows a live panel for each app agent that reports in, not
   hard-coded Auditor/Policy columns. Findings feed the "ranked by money saved" list.
4. **Nessie agent:** *(Built, commit 7fa5967: strict payment match, uninsured cases, credited payments.
   Merged into `after-david-takeover`.)* Fix the loose payment match first (any word like "Medical"/"Pacific" in a merchant name
   counts as a payment to the hospital, and there's no date check: `finance._matches_payee`).
5. **Bright Data agent: real prices.** *(Not built. The price tables (`policydb/prices/`) and the rosa-real
   scenario exist, but the agent has no price check or duplicate-line check, and Rosa's bill is still the
   old $8,700 everywhere.)* Rebuild Rosa's bill from CPMC's real price file and add a price
   check against it, plus a duplicate-line check on the bill. Update her numbers everywhere they appear
   (insurance statement, demo PDFs, replays, pitch).

Steps 4 and 5 can run as two coding agents in parallel, each on its own branch.

6. **Step 3: the plan (Decide agent) and in-place Ask / "This is wrong"** (see below). *(Built 2026-10-25:
   `billback/agents/decide.py`, data format in `docs/plan-format.md`; gaps listed under "Step 3: what's
   built".)*

## The flow (settled 2026-10-25)

1. Read your bill → 2. **Review** → 3. **The plan** → 4. Call → 5. Resolved.

- **Step 2, Review:** the Nessie agent and the Bright Data agent run side by side; you watch each one's
  checklist live. (Built: shared report format + one terminal per agent, commit 6563e09.)
- **Step 3, The plan:** the Decide agent lives only here. There is no separate "2B" stage: the decision is
  rules-based code and would flash by.
- **Every past stage stays reopenable** with its full record (each check, evidence and source, the plan and
  its versions, the call transcript). Nothing an agent did disappears.
- **Design rule:** anything that needs the user shows up inside the flow, in its step, never in a separate
  box at the top of the page. (The call approval card is gone: "Call now" is inside step 3. The payment-plan
  approval after the call is still a card above the steps; step 5 is built but didn't move it.)

### Asking and correcting in step 2 (in place, no general chat)
Users don't know what "Nessie" or "Bright Data" are, so they never have to pick an agent:
- Each finding has **"Ask"**: a small box opens under that finding and the agent that found it answers there,
  only from its own evidence, with the source; it says so when it doesn't know. Agent names show as small
  labels on answers (for judges). Asking never changes anything.
- Each finding has **"This is wrong"**: saved as an explicit correction on that finding (logged in the case
  history). Allowed until the call starts; a change after approval resets the approval.
- Reuse the existing "Ask BillBack" endpoint (`/api/cases/{id}/ask`) scoped to one agent's evidence.

### What reruns (no change-tracking needed)
- **Fetched data** (Nessie transactions, Bright Data policy/prices) is slow and costs money: fetched once and
  saved. Only an explicit **"Start over"** (new bill, different hospital, new bank data) fetches again.
- **Everything calculated** (what you paid, what you owe, the discount, the plan and asks) is plain code and
  takes milliseconds: any correction or strategy change **recalculates all of it from scratch**, then the UI
  shows the difference from the previous result ("owed $600 → $1,100; plan changed: no refund").
- The user's **strategy preferences** are stored separately from facts, so they survive corrections.

### Step 3 layout: vertical
- **While the Decide agent works:** its panel is big at the top, with filler text ("Building your plan…" and
  placeholder lines). The plan area below shows "filling in…".
- **When done:** the panel shrinks to a slim bar ("Talk to the planner ▸") and the plan takes the full width.
- Vertical, not side by side: one layout for phone and laptop, reads top to bottom, plan gets full width.
  When a chat change creates a new version, scroll to the plan and highlight the changed lines.

### The plan (a versioned report)
- **Money line:** $billed → $owed.
- **Who we'll call:** department and phone number (billing, financial assistance, or the collections agency).
- **Every ask the plan can back up**, in order, biggest money first; each shows its money and a "why ▸" link
  to the finding in step 2 that supports it. Show the top 4, then "Show N more ▾". Every ask goes on the call.
- **"How I decided ▸"** (closed by default): what came from Nessie, what came from Bright Data, the math, the
  path chosen, who to call, and the fallback if billing refuses.
- **Buttons:** "Call now" (approves the version on screen) · "Not now". Caller voice and the fixed rules
  ("never agrees to pay", "says it's an automated assistant") sit behind a small "Call settings" link.
- **Versions:** v1, v2…; changed lines are marked briefly; tap an older version to compare or go back.

### Talking to the planner (the Decide agent)
- Opened from the slim bar. The chat never puts text into the report.
- It has every finding from both agents with its proof, plus the math and the rules applied, and each plan
  line records which findings it came from, so "why did you do X with this data?" gets an answer that
  points at the exact data.
- **Questions** are answered in the chat only. **Change requests** ("refund first", "don't mention a plan",
  "I can do $150/mo", "ask for a supervisor if they say no") produce a new version of the plan.
- The decision is **rules-based code** (`billback/agents/decide.py`, using `finance.settle` and
  `finance.payment_plan`; it replaced `orchestrator.call_asks`); Claude writes the explanation and turns chat
  requests into plan changes, which the rules check.
- Guardrails: the chat changes strategy, never facts; numbers always come from the code; every ask must be
  backed by a finding (otherwise it explains why not); never agree to pay, always say it's automated.

### Step 3: what's built (2026-10-25)
- Built: the Decide agent panel (big while it works, then the slim bar), the plan (money line, who to call,
  every ask with "why ▸", top 4 then "Show N more", "How I decided"), Call now / Not now / Call settings,
  versions with changes marked and compare / go back, the planner chat, "Ask" and "This is wrong" with undo.
  The call brief is built from the approved version.
- Not built: **"Start over"** (re-fetching data). A new case does the same for now.
- Works differently from the text above:
  - "A change after approval resets the approval": built, but "Call now" starts the call at once, so there's
    almost never a gap to correct in.
  - The fallback in "How I decided" is text only. On the call, BillBack uses its built-in rule (restate the
    evidence once, then ask for a supervisor or a written review). A "supervisor" ask goes on the plan only
    when you ask for it in the chat.
  - The committed replays were recorded before step 3, so they show only the old ask list. Re-record after
    the next live run.

### Review and Ask changes (2026-10-25, commit e80d78d)
- **Step 2 is split by agent:** each agent's column shows its live panel, then "What it found" under it
  (biggest saving first); on a phone the columns stack. The merged "What they found" list is gone. An agent
  that found nothing says "Nothing to correct here." The hospital's policy quotes still sit below both
  columns (they are Bright Data's evidence; could move into its column).
- **Ask answers explain, not repeat:** Claude now gets the records behind the finding (Nessie: the full
  insurance statement, the bill, matched bank payments, duplicate charges, the math; Bright Data: policy
  quotes, eligibility, plan terms, income) and is told to answer the question and explain the numbers.
  Offline (no Claude key) it still just repeats the finding's proof. Tested by Gavin on 8002 with Claude on.
- **Ask is on the finding row** (commit 916731b): it shows once that finding's agent is done, without
  expanding the finding. Before a plan exists it's greyed out ("Available once the plan is ready"). Replays
  don't show it.
- **Uninsured call brief fixed:** `caller.build_brief` and the offline call script no longer crash on an
  empty insurance statement.

### Step 4: the call (fully autonomous) *(design only, not built)*
- No mid-call steering (no nudge box). When billing refuses an ask, BillBack follows the approved plan's
  fallback by itself; it doesn't pause to ask.
- On screen: the live transcript, the plan's asks as a checklist that ticks off live as the rep agrees
  ("✓ Insurance applied, rep agreed"), and a running new balance.
- Voice: Cartesia stock voices as today (no voice cloning: BillBack always says it's an automated assistant
  calling on the patient's behalf).
- Later: let the patient join the live call (Twilio conference) when billing insists on the patient.

### Step 5: What's next (the Decide agent again) *(built 2026-10-25, offline; see below)*
- Top: a scorecard of every ask: ✓ got it / ✗ refused / ⏳ promised later.
- Below: the **same Decide agent**, run again with the call outcome as new input (plus everything Nessie and
  Bright Data found and the original plan). Same screen pattern as step 3: what happened, what it concludes,
  the next plan (call again with a different strategy, written dispute, financial-assistance form, ask to
  pause collections, wait for a promised callback), the planner chat, versions, approve.
- If everything was won, it just confirms and schedules the check-back.
- A follow-up call reuses this plan with only the unfinished asks.

### Step 5: what's built (2026-10-25, offline while Cartesia has no credits)
- **Scorecard** (`billback/agents/scorecard.py`): after every call each ask on the plan the call used gets
  got / refused / promised later / unclear (supervisor: "not needed" when nothing was refused). Plain code
  reads what the call agent recorded and the rep's words; when Claude is on, the post-call transcript read
  (`followups.EXTRACT_SYSTEM`, now with an `asks` field) also marks each ask, but its mark only counts if its
  quote is really in the rep's words. A win is never claimed without evidence. Stored on the call
  (`calls[n].scorecard`) and sent as a `scorecard` event.
- **Decide agent, again** (`decide.next_round`): same rules, run with the scorecards. Plan versions now carry
  `round` (1 = first call; after call n the plan is round n+1). Done asks move to "Already done"; the rest are
  reworded for the caller (check a promise, ask again with a supervisor, get a clear answer); a refusal adds
  the supervisor ask. `next` says what to do: call now, wait until the promised date, or nothing left; plus
  other routes (apply directly, written dispute). Its panel is agent `decide:<round>`.
- **Case page step 5:** "What the call got", the Decide agent panel, the next plan (versions, "How I
  decided" with the last call), the planner chat (it moves to step 5), and "Call now (vN)", which approves
  that version and starts the follow-up call with only those asks (`POST /call-again {v}`, no second
  approval). Step 3 then shows that round's plan.
- **Fixture calls:** `demo/outcomes/` (all won, partly won, all refused, promised later), run by
  `tests/test_scorecard.py`. 280 tests pass.
- **Not done yet:** no live run (Claude-read marks are tested only with fixture data); the payment-plan
  approval card after the call is unchanged; the follow-up reminder ("I'll check back by…") is the old one
  and can disagree with "call again now"; corrections ("This is wrong") in step 5 work in the API but have
  no button there; replays don't have step 5 yet. The live call agent's prompt was not changed.

### Call speed *(built, merged 2026-10-25, commit e1fa24f)*
Claude's reply streams into Cartesia sentence by sentence, Claude starts on Ink's `turn.eager_end`, outcomes
are recorded off the critical path instead of with a second Claude call, and prompts are cached. Desk mode
streams too (`billback/voice/desk.py`). Measured live before the credits ran out: phone path 1.0–2.7 s →
0.44–1.1 s (10 of 11 turns under 0.8 s); desk mode median 3.0 s → 0.93 s. Details in `docs/voice.md`.
Not done: a Chrome rehearsal of the new desk player in `web/rep.html` (needs Cartesia credits). Moss (Cal
Hacks sponsor) was evaluated and skipped: it's fast retrieval, not LLM latency.

### Data on hand (added tonight)
- 12 hospitals in the policy DB (8 new Bay Area); real price tables for 11 in `policydb/prices/`
  (`billback/prices.py`). Check MarinHealth and El Camino tiers: they were read as "all patients".
- Realistic cases from real prices with known answers: `demo/scenarios/` (`scripts/make_scenario.py`):
  rosa-real ($15,414 → $1,575), marcus-real ($4,174 → $0), dana-altabates-refund, amara-kaiser-uninsured,
  lena-johnmuir. Making rosa-real the main demo case means updating demo/case.json, PDFs, Nessie seed, tests,
  docs and re-recording the replay.
- Nessie agent bugs: fixed and merged, commit 7fa5967 (loose payment matching, crash on uninsured patients,
  credited payments reported as missed).
- Poverty-line figures are the 2026 HHS guidelines (commit 4d4fe21); every case's FPL % dropped a few
  points, and no discount, balance or plan changed.

### Defaults chosen when building step 3 (2026-10-25; data format in `docs/plan-format.md`)
- **Planner chat refuses:** changing facts (points to "This is wrong"), asks no finding backs, agreeing to pay
  or giving card details, hiding that it's automated, dropping the case-number ask or a money ask, anything
  off-topic. It can change: ask order, leaving off the plan / hold / supervisor asks, what you can pay per
  month, "ask for a supervisor if they say no", reset.
- **"This is wrong":** on any finding, with an optional note. The finding is left out of the math and of the
  asks it backs. Household size and income can't be corrected directly yet.
- **Who it calls:** collections if the bill names an agency, financial assistance if a discount applies,
  else billing. The phone number comes from the bill (intake reads it); without one: "the number on your bill".
- **Demo:** the replay plays recorded step 3 events (versions, chat) as they happened; recordings from before
  step 3 show the asks from the old approval.

### Still to design
- A list of UI polish changes for steps 2–3 (to collect, then one polish pass).

(Done: the Cases page lost its totals cards and "Needs you" box; cases needing you sort to the top, commit
5046d09.)

## Later (when there's time)

From the audit, roughly in priority order. Details and sources are in `docs/ideas-backlog.md`.
- Collections and credit-report law check (California: no credit reporting, no lawsuit before day 180,
  application must come with collection notices). Rosa's "final notice" makes it a strong demo moment. The
  rules are researched in `docs/legal-check.md` (section 5, HSC 127425); the check itself isn't built.
- Split "$7,600 insurance" into the two real errors: $3,660 paid but not credited, $3,940 network discount.
- 12-month medical spending from the bank for the high-medical-cost write-off (today only this bill counts).
- Medicare-rate cap for low-income/uninsured patients (also unlocks Chinese Hospital's "% of Medicare" tiers).
- SF General has no discount tiers in the policy DB, so it always says "no discount".
- Legal claims are checked in `docs/legal-check.md` (commit 82b3a21): 501(r) for UCSF/SF General, NCCI on
  facility claims (partly right), 180-day appeal window (insurer appeals only), California itemized-bill
  rule (partly right), Marcus's Medi-Cal (probably not eligible). Fix the app and pitch wording to match
  before showing any of it.
- Roadmap (pitch only): the separate ER physician bill (No Surprises Act), insurance-denial appeals.

## Data on hand

Real price files (downloaded with plain HTTPS from each hospital's `cms-hpt.txt`), kept out of git in
`data/price_files/`: `cpmc.csv` (CPMC Van Ness, 16.9 MB, CMS template v3.0.0) and `chinese.csv` (46 MB).
UCSF's is a 91 MB JSON on Box (not downloaded); SF General's is a 3.9 MB CSV (not downloaded).

# Remy plan (from 2026-10-25)

Working branch: `master` on GitHub `gsonntag/calhacks` (the only remote). Gavin said so on 2026-10-25:
`after-david-takeover` was squashed onto master (d2a5b45), and fixes now go straight to master. The 6 PM
submission is tag `submitted-2026-10-24`. `after-david-takeover` is no longer updated. Backups: tag
`submitted-2026-10-24`, branch `pre-david-work`, local branch `backup/old-billback-master`.

## Judges' review (2026-10-25): what to fix before judging

Four mock judges scored the project. They all praised the same thing: Claude never produces an unchecked
number (policy figures need an exact quote from the crawled page, code does the money math, every figure cites
its source). Scores: General "finalist, not yet a winner" · Cartesia 6.9/10, "likely loses to a team with a
real phone call" · Nessie "likely top 3" · Bright Data "strong contender, moderate fixable risk of
disqualification" (Live-ness 4/10). Gavin's picks are marked ✔. Status (2026-10-25, master 0f25617): "✓ #n"
means PR #n merged and covers it; **open** means nothing on master covers it. #4 and #8 were closed without
merging; this docs PR is #10.

### P0: before anyone sees it (bugs and disqualification risks)
1. ✔ **✓ #2** **Login on approve and reset.** `POST /api/approvals/{id}` and `POST /api/reset` were open to
   anyone. Now every call-placing, credit-spending or data-changing route needs `REMY_ADMIN_TOKEN`
   (`X-Remy-Key`, or `?key=` once), or localhost when unset.
2. ✔ **✓ #3** **No false win.** When billing only puts the account on hold, the case showed the target as the
   balance. Now billing's figure stays until confirmed ("expected $0 once they apply the corrections").
3. ✔ **✓ #6** **A patient missing from Nessie stops the whole case.** Now it falls back to sample bank data and
   says so on the case page.
4. **✓ #2** (`REMY_REQUIRE_LIVE=1`) **The app can't run on sample policies on stage.** Judging mode refuses
   to start without the keys, and `SAMPLE_TIERS` is never used.
5. **✓ #3, then moot** **The 6-month payment plan never ends.** #3 wrote one scheduled Nessie bill per
   payment; since 98eef7d the planner only proposes the plan and writes nothing to the bank.

### P1: the demo (every judge raised these)
6. **→ #10** **Lead with a case that shows off the sponsors.** `docs/DEMO.md` leads with Marcus ($5,600 → $0
   free care at UCSF) and the 12-hospital comparison; Rosa is the backup.
7. **Something live in the 2 minutes.**
   - **✓ #5** Bright Data: live crawl on the case page and "Recheck now" (not yet run live).
   - **Open** Cartesia: record one real Twilio call and show it with the latency numbers (needs credits).
   - ✔ **✓ #7** Cartesia: desk mode listens through Ink, with barge-in (built offline; needs one live check).
8. **→ #10** **Docs agree with each other and with the code.** README, DEMO and PLAN checked against master
   (416 tests; 12 hospitals; 2-minute script; Known limitations with data-retention notes).
9. **Open** (#8 closed) **Hide the extras for judging.** Menus already show only Cases · Hospitals · Under the
   hood and `/flows/` is unlinked; #8's readability pass did not land.

### P2: correctness
10. **✓ #3, partly** **Income:** labelled take-home, gross estimated (÷ 0.80) with a warning, gig/Venmo counted.
    Eligibility still uses take-home: **open decision** (gross would flip only dana-altabates-refund now; Marcus
    is about 11.5% of estimated gross, still over UCSF's 10%).
11. **✓ #7** **Interrupted replies:** the call brain remembers only what was played (Sonic word timestamps).
12. **✓ #5** **"No tiers" hospitals are never re-crawled:** a "no tiers" result older than 6 hours is crawled
    again.
13. **✓ #7** **Read IDs back for confirmation.**

### P3: per-track upgrades (if there's time)
- **✓ #6, #3** Nessie: 12 months of bank medical spending for the high-medical-cost rule, bill marked corrected
  in Nessie once billing confirms, messier seed data (#6); plan sized on the bank's recurring bills (#3).
- **✓ #7** Cartesia: keypad tones for phone menus, quiet during hold messages.

Also merged: #1 (a line billed twice on insured bills, its own ask), #9 (the caller says only the plan's
asks), #11 (Ask Remy chat on the case page; hold-to-talk mic on the Cases page), #12 (the step bar fills
with real progress). 98eef7d removed Ask on findings, the desk-mode console link on the case page, and the
post-call payment-plan Yes/No card.

### Still open
- A recorded real Twilio call (needs Cartesia credits); one live check each of #7 (desk on Ink, barge-in,
  timestamps, read-back), #5 ("Recheck now", a stale re-crawl) and #11 (Ink batch STT on the Cases-page mic).
- Reseed the live Nessie sandbox with #6's messier histories (250–500 requests per generated scenario).
- Income: decide whether eligibility uses estimated gross (flips dana-altabates-refund).
- Re-record the replays: they predate #3, #5, #6 and 98eef7d.
- Not on master: #4 (cases overview, archive/delete) and #8 (readability pass), both closed.

## Open handoff items (updated 2026-10-25, master 0f25617)

- **416 tests pass offline** (`uv run pytest -q`, no keys).
- **Cartesia is out of credits** (HTTP 402). Phone path, desk mode on Ink and the Cases-page mic's Ink STT are
  untested live; each falls back to the browser's speech and voice.
- **Live checks still owed:** see "Still open" above. Each needs a small, stated credit cap (rule below).
- **Test bills:** `web/samples/test-bills/` (served at /samples/test-bills/), from `demo/scenarios/`. Uploading
  needs a server with API keys. Only Rosa and Marcus have Nessie bank accounts; Dana, Amara, Lena don't yet.
- **Contacts:** `remy/contacts.py` + `policydb/contacts/` (merged). Not wired in yet: step 3's "Who
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

Status (2026-10-25, master 0f25617): 1–4 built · 5 mostly built (#1, #5) · 6 built (step 3). Outside this
list: step 5 built (offline), step 4's live call screen not built, call speed merged. 416 tests pass.

1. **Fold New case into Cases.** *(Built.)* Cases is home, with a "+ New case" button that opens the chat intake. With
   no cases yet, go straight to the chat. Top bar: Cases · Hospitals · Under the hood.
2. **Shared format for the two app agents.** *(Built: `remy/agents/report.py`.)* One shape for what each agent reports: steps (label, status,
   result) and findings (what was found, money saved, proof, source). Both agents and the UI use it.
3. **Generic Review screen.** *(Built, commit 6563e09.)* The case page shows a live panel for each app agent that reports in, not
   hard-coded Auditor/Policy columns. Findings feed the "ranked by money saved" list.
4. **Nessie agent:** *(Built, commit 7fa5967: strict payment match, uninsured cases, credited payments.
   Merged into `after-david-takeover`.)* Fix the loose payment match first (any word like "Medical"/"Pacific" in a merchant name
   counts as a payment to the hospital, and there's no date check: `finance._matches_payee`).
5. **Bright Data agent: real prices.** *(Mostly built: the price check against `policydb/prices/`
   (#5, unit-aware) and the duplicate-line check (#1) run on every bill. Rosa's demo bill is still the old
   $8,700 one; the rosa-real scenario has the real-price rebuild.)* Rebuild Rosa's bill from CPMC's real price file and add a price
   check against it, plus a duplicate-line check on the bill. Update her numbers everywhere they appear
   (insurance statement, demo PDFs, replays, pitch).

Steps 4 and 5 can run as two coding agents in parallel, each on its own branch.

6. **Step 3: the plan (Decide agent) and in-place Ask / "This is wrong"** (see below). *(Built 2026-10-25:
   `remy/agents/decide.py`, data format in `docs/plan-format.md`; gaps listed under "Step 3: what's
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
- Reuse the existing "Ask Remy" endpoint (`/api/cases/{id}/ask`) scoped to one agent's evidence.

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
- The decision is **rules-based code** (`remy/agents/decide.py`, using `finance.settle` and
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
  - The fallback in "How I decided" is text only. On the call, Remy uses its built-in rule (restate the
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
- No mid-call steering (no nudge box). When billing refuses an ask, Remy follows the approved plan's
  fallback by itself; it doesn't pause to ask.
- On screen: the live transcript, the plan's asks as a checklist that ticks off live as the rep agrees
  ("✓ Insurance applied, rep agreed"), and a running new balance.
- Voice: Cartesia stock voices as today (no voice cloning: Remy always says it's an automated assistant
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
- **Scorecard** (`remy/agents/scorecard.py`): after every call each ask on the plan the call used gets
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
streams too (`remy/voice/desk.py`). Measured live before the credits ran out: phone path 1.0–2.7 s →
0.44–1.1 s (10 of 11 turns under 0.8 s); desk mode median 3.0 s → 0.93 s. Details in `docs/voice.md`.
Not done: a Chrome rehearsal of the new desk player in `web/rep.html` (needs Cartesia credits). Moss (Cal
Hacks sponsor) was evaluated and skipped: it's fast retrieval, not LLM latency.

### Data on hand (added tonight)
- 12 hospitals in the policy DB (8 new Bay Area); real price tables for 11 in `policydb/prices/`
  (`remy/prices.py`). Check MarinHealth and El Camino tiers: they were read as "all patients".
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

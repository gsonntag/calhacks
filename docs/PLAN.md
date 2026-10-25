# BillBack plan (from 2026-10-25)

Working branch: `after-david-takeover`. Backup of everything before: `pre-david-work`. `master` on GitHub is
left exactly as submitted (6 PM deadline, history cleaned for judging) until Gavin says otherwise. Push new
work to `after-david-takeover`, never to `master`.

## Open handoff items (2026-10-25 late night)

- **Merge call speed:** branch `worktree-agent-aff90078fb2699c8e` (backed up as `call-speed-rebased` on GitHub)
  is done and tested (242 passing on its own). It overlaps only `billback/agents/caller.py`; merge it into
  `after-david-takeover` once the session editing caller.py has committed, run the tests, push. Then rehearse
  the new streaming player in `web/rep.html` in Chrome once Cartesia has credits again.
- **Cartesia is out of credits** (HTTP 402). Phone path down; desk mode uses the browser voice until topped up.
- **Test bills:** `web/samples/test-bills/` (served at /samples/test-bills/), from `demo/scenarios/`. Uploading
  needs a server with API keys. Only Rosa and Marcus have Nessie bank accounts; Dana, Amara, Lena don't yet.
- **Contacts:** `billback/contacts.py` + `policydb/contacts/` (merged). Step 3's "Who we'll call" can use
  `contacts.for_case(case)`.

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

Status (2026-10-25, 06:10): 1–4 built · 5 not built ·
6 built (step 3).

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
  approval after the call is still a card above the steps, until step 5 is rebuilt.)

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
- **Ask is hard to find:** it shows only after you expand a finding, only once a plan exists (older cases and
  replays never show it). Proposed, not built: an Ask button on the finding row, shown once that agent is done.
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

### Call speed (in progress)
Reply latency is 1.4–2.7 s per turn (Claude 1.3–2.6 s). Fixes being built: stream Claude's reply into
Cartesia sentence by sentence, start Claude on Ink's `turn.eager_end`, record outcomes in the background
instead of a second Claude call, prompt caching. Goal: first audio under ~1 s after the rep stops, in both
the phone path and desk mode. Moss (Cal Hacks sponsor) was evaluated and skipped: it's fast retrieval, not
LLM latency.

### Data on hand (added tonight)
- 12 hospitals in the policy DB (8 new Bay Area); real price tables for 11 in `policydb/prices/`
  (`billback/prices.py`). Check MarinHealth and El Camino tiers: they were read as "all patients".
- Realistic cases from real prices with known answers: `demo/scenarios/` (`scripts/make_scenario.py`):
  rosa-real ($15,414 → $1,575), marcus-real ($4,174 → $0), dana-altabates-refund, amara-kaiser-uninsured,
  lena-johnmuir. Making rosa-real the main demo case means updating demo/case.json, PDFs, Nessie seed, tests,
  docs and re-recording the replay.
- Nessie agent bugs: fixed and merged, commit 7fa5967 (loose payment matching, crash on uninsured patients,
  credited payments reported as missed), not merged yet.

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
- Cases page: drop the totals cards and the "Needs you" box; cases needing you go to the top of the list.
- A list of UI polish changes for steps 2–3 (to collect, then one polish pass).

## Later (when there's time)

From the audit, roughly in priority order. Details and sources are in `docs/ideas-backlog.md`.
- Collections and credit-report law check (California: no credit reporting, no lawsuit before day 180,
  application must come with collection notices). Rosa's "final notice" makes it a strong demo moment.
- Split "$7,600 insurance" into the two real errors: $3,660 paid but not credited, $3,940 network discount.
- 12-month medical spending from the bank for the high-medical-cost write-off (today only this bill counts).
- Medicare-rate cap for low-income/uninsured patients (also unlocks Chinese Hospital's "% of Medicare" tiers).
- Say in the UI that the plan's "under 4% of income" cap is our choice, not the hospital's policy or law.
- SF General has no discount tiers in the policy DB, so it always says "no discount".
- Verify before showing anything legal: 501(r) for UCSF/SF General, NCCI on facility claims, 180-day
  appeal window, California itemized-bill rule, Marcus's Medi-Cal eligibility.
- Roadmap (pitch only): the separate ER physician bill (No Surprises Act), insurance-denial appeals.

## Data on hand

Real price files (downloaded with plain HTTPS from each hospital's `cms-hpt.txt`), kept out of git in
`data/price_files/`: `cpmc.csv` (CPMC Van Ness, 16.9 MB, CMS template v3.0.0) and `chinese.csv` (46 MB).
UCSF's is a 91 MB JSON on Box (not downloaded); SF General's is a 3.9 MB CSV (not downloaded).

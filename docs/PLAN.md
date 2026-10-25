# BillBack plan (from 2026-10-25)

Working branch: `after-david-takeover`. Backup of everything before: `pre-david-work`. `master` on GitHub is
left exactly as submitted (6 PM deadline, history cleaned for judging) until Gavin says otherwise. Push new
work to `after-david-takeover`, never to `master`.

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

1. **Fold New case into Cases.** Cases is home, with a "+ New case" button that opens the chat intake. With
   no cases yet, go straight to the chat. Top bar: Cases · Hospitals · Under the hood.
2. **Shared format for the two app agents.** One shape for what each agent reports: steps (label, status,
   result) and findings (what was found, money saved, proof, source). Both agents and the UI use it.
3. **Generic Review screen.** The case page shows a live panel for each app agent that reports in, not
   hard-coded Auditor/Policy columns. Findings feed the "ranked by money saved" list.
4. **Nessie agent:** fix the loose payment match first (any word like "Medical"/"Pacific" in a merchant name
   counts as a payment to the hospital, and there's no date check: `finance._matches_payee`).
5. **Bright Data agent: real prices.** Rebuild Rosa's bill from CPMC's real price file and add a price
   check against it, plus a duplicate-line check on the bill. Update her numbers everywhere they appear
   (insurance statement, demo PDFs, replays, pitch).

Steps 4 and 5 can run as two coding agents in parallel, each on its own branch.

6. **Step 3: the plan (Decide agent) and in-place Ask / "This is wrong"** (see below).

## The flow (settled 2026-10-25)

1. Read your bill → 2. **Review** → 3. **The plan** → 4. Call → 5. Resolved.

- **Step 2, Review:** the Nessie agent and the Bright Data agent run side by side; you watch each one's
  checklist live. (Built: shared report format + one terminal per agent, commit 6563e09.)
- **Step 3, The plan:** the Decide agent lives only here. There is no separate "2B" stage: the decision is
  rules-based code and would flash by.
- **Every past stage stays reopenable** with its full record (each check, evidence and source, the plan and
  its versions, the call transcript). Nothing an agent did disappears.
- **Design rule:** anything that needs the user shows up inside the flow, in its step, never in a separate
  box at the top of the page. (Today the call approval is still a card above the steps; step 3 replaces it.)

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
- The decision is **rules-based code** (today's logic: `finance.settle`, `orchestrator.call_asks`); Claude
  writes the explanation and turns chat requests into plan changes, which the rules check.
- Guardrails: the chat changes strategy, never facts; numbers always come from the code; every ask must be
  backed by a finding (otherwise it explains why not); never agree to pay, always say it's automated.

### Still to design
- What the planner chat refuses, in detail.
- "This is wrong": which things can be corrected (a transaction, income, household size, insurance) and
  how that looks.
- Who it calls: where department names and phone numbers come from.
- The demo version: how step 3 and the chat play in the replay without live typing.
- Cases page: drop the totals cards and the "Needs you" box; cases needing you go to the top of the list.
- A list of UI polish changes for steps 2–3 (to collect, then one polish pass).

## Later (when there's time)

From the audit, roughly in priority order. Details and sources are in `docs/ideas-backlog.md`.
- Collections and credit-report law check (California: no credit reporting, no lawsuit before day 180,
  application must come with collection notices). Rosa's "final notice" makes it a strong demo moment.
- Split "$7,600 insurance" into the two real errors: $3,660 paid but not credited, $3,940 network discount.
- 12-month medical spending from the bank for the high-medical-cost write-off (today only this bill counts).
- Medicare-rate cap for low-income/uninsured patients (also unlocks Chinese Hospital's "% of Medicare" tiers).
- Insurance status is guessed (`insured=bool(eob)`); 2025 poverty-line figures → 2026.
- Say in the UI that the plan's "under 4% of income" cap is our choice, not the hospital's policy or law.
- SF General has no discount tiers in the policy DB, so it always says "no discount".
- Verify before showing anything legal: 501(r) for UCSF/SF General, NCCI on facility claims, 180-day
  appeal window, California itemized-bill rule, Marcus's Medi-Cal eligibility.
- Roadmap (pitch only): the separate ER physician bill (No Surprises Act), insurance-denial appeals.

## Data on hand

Real price files (downloaded with plain HTTPS from each hospital's `cms-hpt.txt`), kept out of git in
`data/price_files/`: `cpmc.csv` (CPMC Van Ness, 16.9 MB, CMS template v3.0.0) and `chinese.csv` (46 MB).
UCSF's is a 91 MB JSON on Box (not downloaded); SF General's is a 3.9 MB CSV (not downloaded).

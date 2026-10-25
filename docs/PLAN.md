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

6. **Step 2B: the Decide agent** (see below), between the Review and Your approval.

## The flow: Step 2A and Step 2B

- **2A, Review:** the Nessie agent and the Bright Data agent run in parallel, each checklist visible live.
- **2B, Decide:** a third agent takes what both found and works out the plan. Then Your approval.

**Every past stage stays reopenable.** Click any finished stage to see everything it did: each check, each
piece of evidence and source, the decision, the call transcript. Nothing an agent did disappears.

### What the Decide agent shows
- What it got: "Nessie found you paid $500. Bright Data found you qualify for free care."
- What it concluded: "You owe $0, and the hospital owes you $500 back."
- The path and why: e.g. refund first, then confirm a $0 balance; or apply insurance, then a plan.
- Who it will negotiate with: billing, the financial-assistance office, or the collections agency.
- The exact asks, in order, with fallbacks. These feed the approval card.

The decision is **rules-based code** (today's logic lives in `finance.settle` and `orchestrator.call_asks`,
hidden from the user); Claude only writes the plain-English explanation.

### Talking to the Decide agent
The user can chat to change the strategy ("ask for the refund first", "don't mention a plan", "I can do
$150/mo", "ask for a supervisor if they say no"). The plan updates on screen with what changed and why, and
the user approves that plan; the caller follows it.

Guardrails: the chat changes strategy, never facts. Numbers always come from the code. Every ask must be
backed by something Nessie or Bright Data found; if not, the agent says why it can't. Hard rules stay: never
agree to pay on a call, always say it's an automated assistant.

### Questioning and correcting (when to rerun)
- **Asking** ("why do you think I paid $500?") changes nothing; the agent answers from its own evidence.
- **Correcting** ("that $500 was to my dentist") is saved as an explicit fix on that finding. Then only what
  depends on it is redone: the money math recalculates instantly; Nessie/Bright Data do NOT re-fetch (their
  data didn't change); the Decide agent re-plans and shows the difference.
- The user's strategy preferences are stored separately from facts, so they survive corrections.
- A full rerun only happens when inputs truly change (new bill, different hospital, new bank data), via an
  explicit "Start over" button, never silently.
- Corrections are allowed until the call starts. A change after approval resets the approval.
- Every correction is logged in the case history (what changed, and how the plan changed).

### Later: ask Nessie or Bright Data directly
Each agent answers questions only from what it found (transactions, policy text, quotes), shows the
source, and says when it doesn't know. Reuse the existing "Ask BillBack" endpoint (`/api/cases/{id}/ask`).

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

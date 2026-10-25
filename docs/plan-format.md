# Step 3: the plan's data format

The Decide agent (`remy/agents/decide.py`) turns the saved Review results, the user's corrections and
their strategy preferences into a plan version. It is plain code: every number comes from `finance.py`.
Claude only writes the "How I decided" explanation and turns planner-chat requests into preference changes.

## What is stored on the case (`data/cases/<id>.json`)

Three separate things, so a correction never loses a strategy choice and vice versa:

```jsonc
"facts": {            // saved once at the end of Review; never re-fetched (only "Start over" would)
  "rec":  {...},      // finance.reconcile() result: billed_due, patient_responsibility, payments[], duplicates[]
  "pol":  {...},      // Bright Data agent result: eligibility, income, plan_terms, policy quotes, source
  "findings": {"auditor:insurance": {key, agent, title, saved, value, proof, source}, ...}
},
"corrections": [      // "This is wrong" on a finding: a fact the user disputes
  {"id": "c1", "finding": "auditor:paid-txn42", "agent": "auditor", "title": "A payment you made was missed",
   "note": "that $500 was for my dentist", "ts": 1761400000.0, "active": true}   // undo sets active: false
],
"plan": {
  "prefs": {          // strategy, set only by the planner chat
    "first": ["refund"],          // ask kinds to move to the top, in this order
    "skip": ["plan"],             // ask kinds left off the call (case_number can't be skipped)
    "max_monthly": 150,           // what the patient can pay per month (replaces our 4%-of-income cap)
    "if_refused": "supervisor"    // add "ask for a supervisor" as the fallback ask
  },
  "versions": [ <version>, ... ], // v1, v2, … append-only; nothing is ever deleted
  "approved_v": null,             // set by "Call now"; reset by any new version before the call starts
  "chat": [{"role": "user"|"planner", "text": "...", "kind": "question"|"change"|"refused", "v": 2, "ts": ...}]
}
```

## A plan version

```jsonc
{
  "v": 2,
  "ts": 1761400000.0,
  "reason": {"kind": "initial"|"correction"|"strategy"|"restore", "text": "You said the $500 payment wasn't to the hospital"},
  "money": {"billed": 8700, "owed": 1100, "target": 1100, "refund": 0, "saved": 7600},
  "math": [           // the lines behind the money, each with its source and finding
    {"label": "Hospital says you owe", "amount": 8700, "source": "bill · acct CPMC-48213977"},
    {"label": "Insurance not applied", "amount": -7600, "source": "Golden State · claim C-…", "finding": "auditor:insurance"},
    {"label": "You owe", "amount": 1100, "total": true}
  ],
  "call": {"who": "billing"|"financial_assistance"|"collections",
           "department": "Patient Financial Services", "phone": null,   // null → "the number on your bill"
           "why": "No discount applies, so billing can fix the balance and set up the plan"},
  "asks": [           // every ask goes on the call, biggest money first, then prefs.first moves kinds up
    {"id": "insurance", "kind": "insurance", "money": 7600, "value": "−$7,600",
     "text": "Apply your insurance: the statement says you owe $1,100, not $8,700",   // what the patient reads
     "goal": "Get the insurance adjustment applied: …",                              // what the caller is told
     "findings": ["auditor:insurance"]},                                             // the "why ▸" links
    {"id": "plan", "kind": "plan", "money": 0, "value": "8 × $138/mo", "text": "…", "goal": "…",
     "findings": ["policy:plan"]},
    {"id": "hold", "kind": "hold", "money": 0, "findings": [], "basis": "standard", ...},
    {"id": "case_number", "kind": "case_number", "money": 0, "findings": [], "basis": "standard", ...}
  ],
  "skipped": [{"kind": "plan", "text": "Payment plan", "why": "You asked me not to mention a plan"}],
  "decided": {        // "How I decided ▸"
    "nessie": ["Your bank shows no payment to the hospital you didn't dispute", ...],
    "brightdata": ["Income 251% of the poverty line; the write-off needs bills over 10% of income: yours are 3%", ...],
    "math": ["$8,700 − $7,600 insurance = $1,100"],
    "path": "Fix the balance, then an interest-free plan under Sutter's billing policy",
    "fallback": "If billing says no: restate the evidence once, ask for a supervisor, then a written review and a case number",
    "explanation": "…"   // Claude-written paragraph (template offline); never contains numbers the code didn't produce
  },
  "prefs": {...},                   // snapshot of the prefs used
  "corrections": ["c1"],            // active corrections used
  "changes": ["You owe $600 → $1,100", "Plan: 6 × $100/mo → 8 × $138/mo"],   // vs the previous version
  "changed": ["plan"],              // ask ids new or changed vs the previous version of the same round
  "round": 1,                       // which call this plan is for (after call n: n+1)
  // only after a call (round 2+):
  "done": [{"id": "insurance", "kind": "insurance", "text": "…", "call": 1, "evidence": "…"}],
  "next": {"kind": "call"|"wait"|"done", "when": "2026-10-30"|null, "text": "…", "options": ["Apply … yourself: …"]},
  "after_call": {"n": 1, "date": "2026-10-24", "rep": "Diane", "case_number": "FA20931", "marks": [<scorecard mark>]}
}
```

Asks in round 2+ carry `last`: {status, label, evidence, call}, the mark from the last call they were on.
A scorecard mark (`agents/scorecard.py`): `{id, kind, text, status: got|refused|later|unclear|not_needed, label,
evidence, source: "call record"|"rep said"|""}`.

Ask kinds: `insurance`, `payment`, `discount`, `refund`, `plan`, `hold`, `case_number`, `supervisor`.
Money asks need a finding that's not marked wrong. `hold` and `case_number` are standard asks (no finding);
`supervisor` comes only from `prefs.if_refused`.

## Recalculation (every correction, strategy change or restore: from scratch, milliseconds)

- insurance share = EOB patient responsibility, or the billed amount if `auditor:insurance` is marked wrong
- paid = payments whose `auditor:paid-<txn>` finding isn't marked wrong
- discount = the policy tier, or 0 if `policy:discount` is marked wrong
- `finance.settle(share, paid, discount)` → target, refund
- plan (only with no discount and something owed, backed by `policy:plan`, not skipped):
  `finance.payment_plan(target, monthly income, policy cap, max_monthly=prefs.max_monthly)`

## Events (on `/ws/cases/{id}`, also in the audit log and replays)

| Kind | Fields | When |
|---|---|---|
| `agent_started` / `step` / `agent_finished` | shared report format, agent `decide` | the Decide agent's panel |
| `plan_version` | `plan` (a version) | each new version, v1 at the end of Review |
| `plan_approved` | `v` | "Call now" (the caller gate's `approval_resolved` still follows) |
| `plan_approval_reset` | `v`, `reason` | a new version after approval, before the call starts |
| `planner_message` | `role`, `text`, `type` (question · change · refused), `v?` | each planner-chat message, both sides |
| `correction` | `correction`, `v` | "This is wrong" saved (and the version it produced) |
| `correction_undone` | `id`, `v` | undo |
| `scorecard` | `n`, `plan_v`, `marks`, `summary` | after each call: one mark per ask on the plan it used |
| `finding_qa` | `finding`, `agent`, `question`, `answer`, `sources` | "Ask" under a finding (changes nothing) |

## Endpoints

- `POST /api/cases/{id}/planner {message}` → `{reply, kind, v?}`
- `POST /api/cases/{id}/plan/approve {v}` (must be the latest version) · `POST /api/cases/{id}/plan/decline`
- `POST /api/cases/{id}/plan/restore {v}`: makes a new version with v's preferences (facts stay current)
- `POST /api/cases/{id}/corrections {finding, note}` · `DELETE /api/cases/{id}/corrections/{cid}`:
  only until the call starts (409 after)
- `POST /api/cases/{id}/call-again {v?}`: with `v` (must be the latest), approves step 5's plan and starts the
  follow-up call with its asks; without it, the approval gate asks first
- `POST /api/cases/{id}/ask {question, finding?}`: with `finding`, answered only from that finding's agent's
  evidence (its findings and steps with proof); without it, the existing status answer

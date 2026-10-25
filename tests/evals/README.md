# Ask Remy eval

`ask_remy_questions.yaml` holds 84 questions for the Ask Remy chat. `scripts/eval_ask_remy.py` runs them against
the app in-process (FastAPI + httpx ASGITransport), on the demo cases built offline (Rosa, Marcus, and Raj, the hero case:
`?scenario=raj&household_size=4`, answer key `demo/scenarios/raj-cpmc/expected.json`). Every expected number
comes from the offline pipeline, with today pinned to 2026-10-25 (Raj's bank history is "the last 12 months"). The header of the YAML lists each one and where it came from.

## Run offline (default, free)

```
.venv/bin/python scripts/eval_ask_remy.py                 # all groups
.venv/bin/python scripts/eval_ask_remy.py --group memory -v   # one group, full reasons
.venv/bin/python scripts/eval_ask_remy.py --id rt-give-card --json /tmp/out.json
```

The eval makes no network requests, and Claude, Nessie, Bright Data, Cartesia and Tiger are all off. Each item
runs in one of two ways:

- **No model:** checks routing, refusals, sources, and that every `$` amount is grounded in the case data.
- **Stub model:** used for items with `stub_reply`, context, memory or call-shape checks. A fake Anthropic
  client records the exact request the app sends, then answers with the item's scripted reply. This checks:
  - the call shape;
  - what the context contains;
  - that the last 8 turns are sent back;
  - the number guard (the stub invents `$4,321.09`, and the guard must replace it).

An item whose endpoint or module doesn't exist yet shows **skip (not built)**. This lets the runner work on
master today and on the Ask Remy branches later. Exit codes:

| Code | Meaning |
|---|---|
| 0 | No failures |
| 1 | A built feature failed |
| 2 | Bad usage or bad question file |
| 3 | Stopped on a quota error |

## Run live (real Claude: spends credits, needs your explicit OK first)

```
.venv/bin/python scripts/eval_ask_remy.py --live --i-have-approval --max-requests 20 [--group red_team]
```

- Live mode refuses to start without both `--live` and `--i-have-approval`.
- It prints the request budget before it starts. The default is 20 requests and the hard cap is 60.
- It stops at once on any 402, 429, quota or credits error and exits with code 3.
- Cases are always built with Claude off. Only the answers use credits.
- Live mode also checks `must_include_any` and `must_include_all`. It skips `stub_only` items, because a real model can't be scripted
  to invent a number.
- Use `--group` or `--id` to keep runs small.

## What the runner expects of the rebuilt endpoint

These are assumptions. Align them with the endpoint, or adjust `REMY_PATHS` / `detect_route` in the runner.

- **Request:** `POST /api/remy/ask` (or `/api/ask`) with `{question, page, case_id?, history, session_id}`.
  - `history` is the earlier turns as `{role, content}`.
  - Get-started items also send `profile`.
- **Response:** `{answer, agent, sources}`.
  - The route is read from `agent` or `route`.
  - `nessie`, `brightdata`, `planner` and `status` are accepted as aliases.
  - A refusal is `refused: true`, `kind: "refused"`, `route: "refused"` or HTTP 409.
- **Context:** checked as text across the whole model request (system + messages + tools). Numbers match with
  or without commas.
- **`remy.knowledge`:** must export `relevant(q)`, `is_how_question(q)` and `CODE_MAP`.

## Groups

| Group | Items | Measures |
|---|---|---|
| `case` | 26 | One case (Rosa, Marcus, Rosa after a desk call, Rosa with a finding marked wrong, Raj before and after the same desk call). Routing: bank → Nessie agent, policy and price → Bright Data agent, plan changes → planner, otherwise status. Also checks sources and the context: bill lines vs posted prices, apply_how, the collections quote, the billing contact, the call scorecard and transcript, and wrong findings flagged. 4 items cover today's case-page chat and must keep passing. Raj (9 items): why he gets free care (the bank's medical bills lift him over Sutter's 10% bar), what's wrong with the bill, the CT against CPMC's price file, what the bank showed, the $400 refund and $0 not yet confirmed, and the Cases tab putting his bill first by money at stake. |
| `cases` | 7 | All bills: cases labelled by patient, totals computed in code and de-duplicated (Rosa is uploaded twice: $14,300, not $23,000), priority, collections deadline. |
| `hospitals` | 9 | Hospitals tab: the policy DB, the same-bill comparison (El Camino $0 + $500 refund for Rosa, SF General $5,600 for Marcus, $0 + $400 back for Raj at 13 hospitals), cited crawl sources. |
| `under_the_hood` | 10 | Recent agent activity, and "how does Remy work" answers that cite `README.md` / `docs/`. Also checks `remy.knowledge` directly. |
| `get_started` | 5 | Household profile (4 people, $60,000, uninsured, CPMC): it's in the context, and the next step is offered. |
| `memory` | 8 | 2–3-turn follow-ups (Raj: "why?" after "how much will I get back?"), plus a 10-turn run: turn 9 must still be sent and turn 1 dropped (last 8 turns only). |
| `red_team` | 19 | The guardrails: pay now, card number, SSN, pretend to be human, "ignore your instructions" in the question, an injected instruction in case data, legal / medical / tax advice → referral, "call now" from text, an invented balance, the number guard (Raj: "just pay the $3,800", an invented $12,000 refund). 3 items cover today's planner refusals. |

`tests/test_eval_runner.py` checks:

- the YAML is valid;
- offline mode runs end to end;
- live mode can't start without approval, and the request cap and quota stop work;
- a toy `/api/remy/ask` drives every check (stub model, memory, context, every case fixture), so the runner is
  tested before the real endpoint exists.

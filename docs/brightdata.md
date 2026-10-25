# Bright Data in BillBack: the hospital policy database

BillBack lowers a patient's hospital bill using the hospital's **own** financial-assistance (charity care)
policy. Those policies are public, but they sit on hospital websites as HTML pages and 10 to 50 page PDFs,
behind CDNs and bot protection, and Google often returns aggregators rather than the hospital. BillBack uses
Bright Data to find and fetch them, Claude to read them, and plain code to check every quote against the fetched
text before any number reaches the money math. The results go into a policy database that is committed to
this repo (`policydb/`) as evidence of the crawls.

## Architecture

```
                        scripts/crawl_policies.py crawl | recheck | show | compare
                        billback/agents/policy.py (live crawl when a case's hospital isn't in the DB)
                        billback/policies/routes.py (POST /api/hospitals/{id}/recheck)
                                         │
                                         ▼
 registry.py ──► discover.py ──────────► fetch.py ───────────────────► extract.py ──────────► store.py
 hospitals,      Bright Data SERP API    Bright Data Web Unlocker       Claude: tiers, plans    SQLite + raw/
 aliases,        Google results, parsed  page as markdown -> links ->   code: every quote must  policydb/policies.sqlite3
 domains         rank + explain choice   policy PDFs as bytes -> pypdf  appear in fetched text  policydb/raw/<hospital>/
                 (official > regulator   (English, not forms/notices)   and state its FPL limit policydb/policies.json
                 > other > aggregator)
                                         ▲                                                        │
                       monitor.py ───────┘  refetch same URLs, compare sha256 of normalized text, │
                                            re-extract only on change, new version + check row    │
                                                                                                  ▼
            compare.py: same bill + income through every stored policy (finance.py does the math)
            agents/policy.py: case's hospital -> policy DB (instant) -> discount -> caller quotes it
            web/hospitals.html: DB, verified quotes, sources, versions, recheck, "same bill, N hospitals"
```

All Bright Data traffic goes through one function, `billback/integrations/brightdata.py:request()`, and every
request made during a crawl goes through `billback/policies/crawl.py:Crawl`, which writes a `crawl_log` row
(product, URL, status, bytes, latency), saves the raw response under `policydb/raw/`, enforces a per-crawl
request budget, and streams an event to the dashboard.

## File map

| File | What it does |
|---|---|
| `billback/integrations/brightdata.py` | Bright Data client: `request()` (retries, `x-brd-*` error headers), `serp()`, `page()`, `document()` |
| `billback/policies/registry.py` | Tracked hospitals (`SEED`), aliases, official domains, `match()` a bill's provider name to a hospital |
| `billback/policies/discover.py` | Stage 1. SERP query, `rank()` with per-candidate reasons, `pick()`, retry query if no official page |
| `billback/policies/fetch.py` | Stage 2. Web Unlocker page fetch, `policy_doc_links()` (English policy PDFs), PDF fetch + `pdf_text()` |
| `billback/policies/extract.py` | Stage 3. Claude prompt, `verify()`: quote location, FPL-number check, dedupe, `finance.clean_tiers` |
| `billback/policies/store.py` | Stage 4. SQLite schema, versions, checks, crawl runs, request log, JSON export |
| `billback/policies/monitor.py` | Stage 5. `recheck()`: hash compare, re-extract on change, rule diff |
| `billback/policies/pipeline.py` | Wires the stages: `crawl_hospital()`; `load_documents()` rebuilds documents from committed artifacts |
| `billback/policies/crawl.py` | Crawl session: logging, artifacts, budget, events |
| `billback/policies/compare.py` | Same bill through every stored policy |
| `billback/policies/routes.py` | FastAPI router: `/api/hospitals`, `/api/hospitals/{id}`, `/api/compare`, `/api/hospitals/{id}/recheck`, raw artifacts |
| `billback/agents/policy.py` | Case flow: DB lookup, else live crawl, else (Bright Data not configured) the labelled offline test fixture |
| `scripts/crawl_policies.py` | CLI used to seed and recheck the DB |
| `web/hospitals.html` | The policy DB page and the comparison |
| `tests/test_policies.py` | Offline tests using the committed crawl artifacts as fixtures |
| `policydb/` | **Evidence**: the database, the raw responses, and a readable JSON export |

## Which Bright Data product does what, and why

| Product | Zone (env) | Used for | Why this product |
|---|---|---|---|
| **SERP API** | `BRIGHTDATA_SERP_ZONE` | Discover each hospital's policy page from a Google query | Structured Google results (title, link, description, rank) without running a browser or getting blocked; ~5 to 35 s per query |
| **Web Unlocker** | `BRIGHTDATA_UNLOCKER_ZONE` | Fetch the hospital's policy page as markdown, then each linked policy PDF as raw bytes | Hospital sites sit behind CDNs and bot protection (sitecorecloud, Cloudflare email obfuscation seen in our pages); Unlocker returns the page, and its markdown conversion keeps links so we can follow them to the PDFs |

### Exact request shapes (`brightdata.request`)

All requests: `POST https://api.brightdata.com/request` with `Authorization: Bearer $BRIGHTDATA_API_KEY`.

SERP API (discover):
```json
{"zone": "<SERP zone>", "url": "https://www.google.com/search?q=California+Pacific+Medical+Center+San+Francisco+financial+assistance+policy&hl=en&gl=us",
 "format": "json", "data_format": "parsed", "country": "us"}
```
Reply: `{"status_code", "headers", "body"}` where `body` is a JSON string with `organic: [{link, title, description, rank, ...}]`.
A failed Google fetch arrives as HTTP 200 with `status_code >= 400` inside (we saw 429 and 502); `serp()`
treats that as an error and retries once (after 16 s for 429, as Google's message asks).

Web Unlocker, page (fetch):
```json
{"zone": "<Unlocker zone>", "url": "https://www.sutterhealth.org/billing-insurance/financial-assistance",
 "format": "raw", "data_format": "markdown", "country": "us"}
```

Web Unlocker, PDF (fetch):
```json
{"zone": "<Unlocker zone>", "url": "https://edge.sitecorecloud.io/.../hospital-financial-assistance-policy-english.pdf",
 "format": "raw", "country": "us"}
```
Reply: the PDF bytes (`%PDF-` checked before parsing). Bright Data reports target failures as HTTP 200 with
`x-brd-status-code` / `x-brd-error` headers; `request()` reads those, so a blocked target is an error, not a
"successful" empty page.

## Pipeline details

**Discover** (`discover.py`). One Google query per hospital. Each organic result is scored and the reasons are
stored (`policy_versions.discovery`, plus a `crawl_log` note): official domain +10 (exact host match for known
hospitals, so `sfcommunityhospitals.ucsfhealth.org`, a different hospital, does not count as UCSF), California HCAI
+4, aggregators (careroute.ai, dollarfor.org, coveredusa.org ...) -10, topic words ("financial assistance",
"charity care", "help paying", "policy") add weight, off-topic words ("application", "job", "mychart") subtract,
and Google rank is a small tiebreaker. Google sometimes wraps results as opaque `google.com/goto?url=...`
redirects that Web Unlocker refuses; those are dropped, and if no official page is left a second query
(`site:<domain> financial assistance policy`) runs.

**Fetch** (`fetch.py`). The best official page is fetched as markdown. Its links are scored for "the actual
policy document": PDF, says policy, financial assistance/charity, English; other languages (-20), application
forms, notices, provider lists, media files are pushed down. The top policy documents (max 2) are fetched as
bytes and converted with `pypdf`. If the page links to no policy document, the next candidate page is tried
(max 3 pages). Every byte Bright Data returned is saved (`policydb/raw/<hospital>/page-<sha>.md`,
`pdf-<sha>.pdf` up to 2 MB, `pdf-<sha>.txt` always, `serp-<sha>.json` without base64 favicons).

**Extract and verify** (`extract.py`). Claude gets the documents (labelled, with URLs) and returns JSON: income
tiers (`max_fpl_pct`, `discount_pct`, `applies_to`, `min_oop_pct_income` for high-medical-cost rules),
non-percentage relief, collections protection, payment-plan terms (interest-free, monthly cap as a percent of
income) and how to apply, each with a verbatim quote. Then code checks:

1. **The quote exists.** Both quote and document are normalized (`squash`: NFKC, lowercase, letters and digits
   only, so PDF line breaks, hyphenation, curly quotes and markdown don't matter) and the quote must be a
   substring of one fetched document. At least 25 normalized characters.
2. **The quote states the number.** The tier's FPL limit (e.g. `400`) must appear in the quote, or in a second
   verbatim `limit_quote` that also has to be found (for rules split across numbered clauses, as in UCSF's PDF).
3. **The tier is usable.** `finance.clean_tiers` must accept it (numeric FPL limit and percent).

Tiers that fail are rejected and logged (`crawl_log` stage `verify`, `policy_versions.verification`). Only verified
tiers are stored in `extracted.tiers`, and only those reach `finance.charity_discount`. Each stored tier carries the
URL of the document its quote was found in. Unverified collections/apply quotes are blanked. Payment-plan terms
are kept only if their quote is found: "interest-free" needs its own quote, and the income cap's number must
appear in a second verified quote. For CPMC both come from Sutter's billing and collections PDF: "All payment
plans shall be interest-free." and a monthly payment of "not more than ten percent (10%) of the Patient's
monthly family income".

**Store** (`store.py`). Tables: `hospitals`, `policy_versions` (hospital, version, fetched_at, content_sha256,
source_urls, documents with per-document sha256 + artifact paths, discovery, extracted, verification,
tiers_verified/rejected, status), `policy_checks` (every crawl and recheck: hash, changed, note), `crawl_runs`,
`crawl_log` (stage, product, url, status, bytes, latency_ms, ok, note). `policies.json` is a full readable export.

**Monitor** (`monitor.py`). `recheck` refetches the documents of the current version through Web Unlocker (no
SERP query, no Claude call), hashes the normalized text, and compares. Unchanged: one `policy_checks` row. Changed:
re-extract and re-verify, store a new version, write a `policy changed: added ... removed ...` check row and emit a
`policy_changed` event. The hash is over normalized text with link targets removed, because some pages embed
per-request tokens (UCSF's Cloudflare email links changed on every fetch).

## Seeded hospitals (San Francisco), from the committed crawl

| Hospital | Sources fetched via Web Unlocker | Verified tiers |
|---|---|---|
| California Pacific Medical Center (Sutter Health) | sutterhealth.org financial-assistance page; Hospital Financial Assistance Policy PDF (46 pp); Billing/Collections Policy PDF (21 pp) | uninsured <= 400% FPL: 100%; insured <= 400% FPL with out-of-pocket > 10% of income: 100%. Payment plans: interest-free, capped at 10% of monthly family income |
| UCSF Medical Center | ucsfhealth.org help-paying-your-bill page; Patient Financial Assistance Policy PDF (12 pp); Self-Pay Discount Policy PDF (5 pp) | self-pay <= 400% FPL: 100%; insured with out-of-pocket > 10% of income (and <= 400% FPL via `limit_quote`): 100% |
| Chinese Hospital | chinesehospital-sf.org financial-assistance page; Financial Assistance Policy PDF (11 pp, dated 9-26-26) | self-pay <= 200% FPL: 100%. 200-400% FPL and high-medical-cost help are capped at Medicare rates (stored as verified "other relief", not a fixed %) |
| Zuckerberg San Francisco General | three zuckerbergsanfranciscogeneral.org pages | none: the site gives a 138% FPL limit for charity care and says discounts "depend on FPL" without percentages; the full policy is only on HCAI (see limitations) |

Same bill, different hospitals (`GET /api/compare`, `web/hospitals.html`, which defaults to Marcus): Marcus
(insured, 174% FPL, out-of-pocket 12.1% of income, $5,600 share) gets $0 under CPMC's and UCSF's policies, and
owes $5,600 under Chinese Hospital's (its free-care tier is self-pay only; a Medicare-rate cap may apply but
isn't a fixed percent) and ZSFG's (no fixed-percent tier published). Rosa (insured, 233% FPL, out-of-pocket 3%
of income, $1,100 share, $500 paid) owes $600 at every hospital: none of the high-medical-cost rules apply
below 10% of income. At CPMC, Sutter's billing policy then gives her an interest-free plan: 6 × $100/month.

## Reproduce

```bash
# .env: BRIGHTDATA_API_KEY, BRIGHTDATA_SERP_ZONE, BRIGHTDATA_UNLOCKER_ZONE, ANTHROPIC_API_KEY
uv run python scripts/crawl_policies.py crawl --force        # full pipeline for every seeded hospital
uv run python scripts/crawl_policies.py recheck              # change detection
uv run python scripts/crawl_policies.py show                 # tiers + verified quotes + sources
uv run python scripts/crawl_policies.py compare freecare     # same bill through every policy
uv run pytest -q tests/test_policies.py                      # offline, uses the committed artifacts
```
Each crawl costs about 4 Bright Data requests per hospital (1 SERP + 1 page + 2 PDFs); a recheck about 3.

## Evidence (`policydb/`)

- `policies.sqlite3`: the database. `crawl_log` has one row per Bright Data request (and per decision note).
- `policies.json`: the same data as readable JSON (runs, versions, verification checks, request log).
- `raw/<hospital>/`: SERP JSON, page markdown, policy PDFs and their extracted text, named by sha256. The stored
  `content_sha256` of each version can be recomputed from these files (`pipeline.load_documents` +
  `pipeline.content_hash`); `tests/test_policies.py` does exactly that, and re-verifies every stored quote.

Committed run history: one 4-hospital crawl (Chinese Hospital failed in it, both Google results pages had only
redirect links, so it was re-crawled after adding its domain), one recheck of all four (all unchanged).
Requests logged in the DB: 6 SERP API, 22 Web Unlocker. Development of the pipeline used roughly 50 more
requests that are not in the committed DB (the DB was reset before the final seed crawl).

## Honest limitations

- **California HCAI is blocked.** Every California hospital files its charity-care policy with HCAI, and Google
  returns those attachments, but Bright Data's Web Unlocker refuses government domains ("classified as Government
  and blocked"). We record the HCAI link as a reference and don't fetch it. That is why ZSFG, whose own site
  defers to HCAI, has no fixed-percent tier.
- **Verification proves the words, not the reading.** A verified quote is really in the hospital's document and
  states the FPL limit, but the discount %, `applies_to` and out-of-pocket threshold are Claude's reading of it.
  The quotes are shown next to every number so a person can check. Re-running extraction can classify borderline
  text differently (ZSFG's 138% sentence was a tier in one development run and "other relief" in the final one).
- **Health-system documents cover many facilities.** The prompt restricts rules to the named hospital; Sutter's
  policy also has a rule only for one rural hospital, which an early development run wrongly kept as a tier.
- **Physician bills are separate.** All four policies exclude separately billed physicians; BillBack applies the
  policy to the hospital bill only.
- **Non-percentage relief isn't in the math.** Caps like "no more than Medicare would pay" need the Medicare
  allowed amount, which BillBack doesn't have; they are stored, verified and shown, not computed.
- **Google variability.** SERP results differ run to run, sometimes as opaque redirect links; the ranking drops
  those and retries with a `site:` query, which needs the hospital's domain to be known.
- **Request counting.** `crawl_log` has one row per logical request; the client retries a 429/5xx once, so the
  billed count can be slightly higher.
- **Rechecks are on demand** (CLI, API, button), not scheduled; a cron entry running `crawl_policies.py recheck`
  is all that's needed.
- Income is compared to the 2025 HHS poverty guidelines (`finance.FPL_*`).

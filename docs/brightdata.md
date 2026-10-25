# Bright Data in Remy: the hospital policy database

Remy lowers a patient's hospital bill using the hospital's **own** financial-assistance (charity care)
policy. Those policies are public, but they sit on hospital websites as HTML pages and 10 to 50 page PDFs,
behind CDNs and bot protection, and Google often returns aggregators rather than the hospital. Remy uses
Bright Data to find and fetch them, Claude to read them, and plain code to check every quote against the fetched
text before any number reaches the money math. The results go into a policy database that is committed to
this repo (`policydb/`) as evidence of the crawls. The database covers 12 Bay Area hospitals (4 in San
Francisco, 8 across the East Bay, Peninsula, South Bay and Marin).

It also holds **real price tables** (`policydb/prices/`): for each hospital, about 67 common ER/outpatient
codes taken from the hospital's own CMS price-transparency file (see [Real price tables](#real-price-tables)).

## Architecture

```
                        scripts/crawl_policies.py crawl | recheck | show | compare
                        remy/agents/policy.py (live crawl when a case's hospital isn't in the DB)
                        remy/policies/routes.py (POST /api/hospitals/{id}/recheck)
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

All Bright Data traffic goes through one function, `remy/integrations/brightdata.py:request()`, and every
request made during a crawl goes through `remy/policies/crawl.py:Crawl`, which writes a `crawl_log` row
(product, URL, status, bytes, latency), saves the raw response under `policydb/raw/`, enforces a per-crawl
request budget (`HARD_CAP` = 10, retries included, whatever the caller asks for), and streams an event to the
case page.

## Live on the case page

A case reads the stored policy (instant) unless it has to go live. It goes live in three cases, and each one
streams into the Bright Data agent's panel on the case page, one line per request:

- **The hospital isn't in the policy DB:** a full crawl (SERP API, Web Unlocker, Claude, quote check), saved
  for next time.
- **The stored policy is stale** (`agents/policy.py:staleness`): stored as "no tiers" and last tried more
  than 6 hours ago (`NO_TIERS_RETRY_HOURS`), or last checked more than 30 days ago (`STALE_DAYS`). A "no tiers"
  hospital is crawled again from Google (refetching the same documents would find nothing new), with its URLs
  on record as the fallback. Any other is rechecked (same documents, hash compare). Before this, a "no tiers"
  result was never retried, so Saint Francis (a Spanish application form) and Stanford stayed wrong forever.
  Failed attempts count as tries, so a hospital that keeps failing isn't paid for on every case.
- **"Recheck now"** under the panel (`POST /api/cases/{id}/policy/recheck`): the same refresh, on demand. It
  says whether the rules this case's plan uses still hold; a changed policy is shown, not applied (a new case
  applies it). One at a time per case, at most 5 per case.

What each line shows (`policies/crawl.py`, `agents/policy.py:streamer`): the tool (SERP API or Web Unlocker),
the URL, and the outcome: **found** (size, seconds), **empty**, **blocked**, **timed out**, **rate-limited**,
**not found** or **server error**, plus "request n of at most 10". How failures are handled, in the open:

| What happened | What the crawl does |
|---|---|
| Timeout, 429, 5xx | Retried once as a second numbered request (the client's hidden retry is off for crawls) |
| Google unreachable (blocked, or timed out twice) | Falls back to the policy URLs on record, fetched with Web Unlocker |
| Google gives no official page | A second search (`site:<domain> ...`); then the URLs on record |
| A page blocked or empty | Next candidate page |
| A PDF blocked, timed out or not a PDF | Skipped; the crawl goes on with the other documents |
| The 10-request cap reached | Stops; goes on with the documents already fetched, if any |
| The whole live attempt fails (or 300 s pass) | The stored copy is used, labelled "the live check failed"; a new hospital gets no discount |

Each fallback is its own line ("Fallback: ... → ..."), and the verify line lists the quotes it accepted and
rejected with their source URLs. Tests: `tests/test_live_crawl.py` (Bright Data and Claude faked with replies
in the real shapes; nothing goes to the network).

## File map

| File | What it does |
|---|---|
| `remy/integrations/brightdata.py` | Bright Data client: `request()` (retries, `x-brd-*` error headers), `serp()`, `page()`, `document()` |
| `remy/policies/registry.py` | Tracked hospitals (`SEED`), aliases, official domains, `match()` a bill's provider name to a hospital |
| `remy/policies/discover.py` | Stage 1. SERP query, `rank()` with per-candidate reasons, `pick()`, retry query if no official page |
| `remy/policies/fetch.py` | Stage 2. Web Unlocker page fetch, `policy_doc_links()` (English policy PDFs), PDF fetch + `pdf_text()` |
| `remy/policies/extract.py` | Stage 3. Claude prompt, `verify()`: quote location, FPL-number check, dedupe, `finance.clean_tiers` |
| `remy/policies/store.py` | Stage 4. SQLite schema, versions, checks, crawl runs, request log, JSON export |
| `remy/policies/monitor.py` | Stage 5. `recheck()`: hash compare, re-extract on change, rule diff |
| `remy/policies/pipeline.py` | Wires the stages: `crawl_hospital()`; `load_documents()` rebuilds documents from committed artifacts |
| `remy/policies/crawl.py` | Crawl session: logging, artifacts, budget, events |
| `remy/policies/compare.py` | Same bill through every stored policy |
| `remy/policies/routes.py` | FastAPI router: `/api/hospitals`, `/api/hospitals/{id}`, `/api/compare`, `/api/hospitals/{id}/recheck`, raw artifacts |
| `remy/agents/policy.py` | Case flow: DB lookup (refreshed live first when stale), else live crawl, else (Bright Data not configured) the labelled offline test fixture; "Recheck now" |
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

## Seeded hospitals, from the committed crawl

### San Francisco (first seed)

| Hospital | Sources fetched via Web Unlocker | Verified tiers |
|---|---|---|
| California Pacific Medical Center (Sutter Health) | sutterhealth.org financial-assistance page; Hospital Financial Assistance Policy PDF (46 pp); Billing/Collections Policy PDF (21 pp) | uninsured <= 400% FPL: 100%; insured <= 400% FPL with out-of-pocket > 10% of income: 100%. Payment plans: interest-free, capped at 10% of monthly family income |
| UCSF Medical Center | ucsfhealth.org help-paying-your-bill page; Patient Financial Assistance Policy PDF (12 pp); Self-Pay Discount Policy PDF (5 pp) | self-pay <= 400% FPL: 100%; insured with out-of-pocket > 10% of income (and <= 400% FPL via `limit_quote`): 100% |
| Chinese Hospital | chinesehospital-sf.org financial-assistance page; Financial Assistance Policy PDF (11 pp, dated 9-26-26) | self-pay <= 200% FPL: 100%. 200-400% FPL and high-medical-cost help are capped at Medicare rates (stored as verified "other relief", not a fixed %) |
| Zuckerberg San Francisco General | three zuckerbergsanfranciscogeneral.org pages; + Charity Care Policy as filed with HCAI (13 pp, effective 2025-11-14, plain HTTPS) | all <= 138% FPL: 100% (from the HCAI-filed policy). <= 400% FPL capped at Medicare rates and the FPL-scaled Discount Payment Program are stored as verified "other relief". Version 1 (site pages only) had no tiers |

### Wider Bay Area (second seed, 8 hospitals)

Picked for a mix of systems and cities: Sutter (East Bay), Stanford (Peninsula), a county system (Alameda
Health System), Kaiser, two district/independent hospitals (MarinHealth, El Camino), John Muir, and Saint
Francis Memorial (now UCSF Health). Status as `scripts/crawl_policies.py show` prints it.

| Hospital | Sources fetched via Web Unlocker | Verified tiers / status |
|---|---|---|
| Alta Bates Summit Medical Center, Oakland (Sutter Health) | same Sutter page + 2 PDFs as CPMC (identical content hash: one system-wide policy) | same as CPMC: uninsured <= 400% FPL: 100%; insured with out-of-pocket > 10% of income: 100% |
| Highland Hospital, Oakland (Alameda Health System) | Hospital Financial Assistance and Charity Care PDF (alamedahealthsystem.org, uploaded 2021), found directly by Google | uninsured <= 350% FPL: 100%; payment-plan terms and 3 other-relief items verified |
| John Muir Medical Center, Walnut Creek | Patient Financial Assistance Policy PDF (3rd crawl, see below) | uninsured <= 400% FPL: 100%; underinsured <= 400% FPL with out-of-pocket > 10% of income: 100% |
| Kaiser Permanente Oakland Medical Center | kp.org "help paying your bill" + Medical Financial Assistance pages; MFA application PDF (Northern California, 2024) | all patients <= 200% FPG: 100% award; 201-400% FPG: 50% award. Source is the MFA program application, not a formal policy document |
| MarinHealth Medical Center, Greenbrae | help-paying-your-bill + community-benefit pages; Financial Assistance Policy PDF; 2024 program letter PDF | "all patients" <= 400% FPL: 100% (see limitations); insured high medical cost <= 400% FPL: 100% |
| El Camino Hospital, Mountain View | Financial assistance eligibility/procedures PDF (uploaded 2022-04), found directly by Google | <= 400% FPL: 100% ("full charity care to patients whose Family Income is at or below 400%") |
| Stanford Health Care, Palo Alto | stanfordhealthcare.org health-insurance-plans page; + Charity Care Policy as filed with HCAI (22 pp, effective 2025-03-12, plain HTTPS) | uninsured <= 400% FPL: 100%; insured <= 400% FPL with out-of-pocket > 10% of income: 100% (from the HCAI-filed policy). Version 1 had **no tiers**: Google's results had no Stanford Health Care policy (the top hit was Stanford Children's, a different hospital, correctly not treated as official), so only an insurance page was fetched |
| UCSF Health Saint Francis Hospital, San Francisco | a Spanish St. Mary's financial-assistance application PDF; + Charity Care Policy as filed with HCAI (12 pp, effective 2026-01-16, plain HTTPS) | self-pay <= 400% FPL: 100% (from the HCAI-filed policy); insured with out-of-pocket > 10% of income: written off, stored as verified "other relief" (no FPL limit stated). Version 1 had **no tiers**: first crawled as "Saint Francis Memorial Hospital (Dignity Health)": no official page, because Dignity sold the hospital to UCSF Health in 2024 and neither dignityhealth.org nor CommonSpirit's price index lists it any more. Re-crawled under UCSF's name: Google's only official hit was the Spanish application form, which the old ranking didn't demote |

Crawls in this round: 12 (the cap), 8 first crawls plus 4 re-runs: Stanford (after the 429), Saint Francis
(after the rename), John Muir twice. John Muir's first crawl picked `johnmuirhealth.com/secure.html` (a
login page, no tiers); the second picked a one-page program flyer PDF above the actual policy and stopped
there (no tiers extracted). That led to a ranking fix in `discover.score`: other-language documents (-20) and
flyers/notices (-3) are now demoted, as `fetch.py` already did for linked documents. The third crawl then
took the Patient Financial Assistance Policy PDF and verified both tiers. Saint Francis and Stanford were not
re-crawled again (cap reached); with the fix, Saint Francis's Spanish form would now be skipped, but Google
offered no English policy in its results either.

Same bill, different hospitals (`GET /api/compare`, `web/hospitals.html`, which defaults to Marcus), across
all 12: Marcus (insured, 170% FPL, out-of-pocket 12.1% of income, $5,600 share) gets $0 at 7 hospitals (CPMC,
Alta Bates Summit, UCSF, John Muir via their high-medical-cost rules; Kaiser, MarinHealth, El Camino via
all-patient tiers) and owes $5,600 at the other 5: Chinese Hospital (free-care tier is self-pay only; a
Medicare-rate cap may apply but isn't a fixed percent), Highland (uninsured-only tier), and ZSFG, Stanford and
Saint Francis (no verified tier). Rosa (insured, 228% FPL, out-of-pocket 3% of income, $1,100 share, $500 paid)
owes $600 at 9 hospitals, $50 at Kaiser (50% award for 201-400% FPG) and $0 at MarinHealth and El Camino, whose
<= 400% tiers Claude read as applying to all patients (see limitations). At CPMC, Sutter's billing policy
gives her an interest-free plan: 6 × $100/month.

## Real price tables

Every US hospital must publish a machine-readable file of its standard charges (45 CFR 180.50) and point to it
from `https://<its domain>/cms-hpt.txt`. These files are **public and mostly reachable with plain HTTPS**;
Bright Data is not technically required to read them. We route the requests through Web Unlocker first so
they are logged with the policy crawls (`crawl_log`, stage `prices`), and because some hosts do refuse plain
clients from our server: alamedahealthsystem.org times out entirely and John Muir's file host answers 403.
When the Unlocker can't return the file we fall back to plain HTTPS and log that too, with no Bright Data
product (`note: plain HTTPS (not Bright Data)`).

```
registry id ──► price_sources.SOURCES (cms-hpt.txt URL + campus regex)
   cms-hpt.txt via Web Unlocker ──► parse blocks (UTF-8 or UTF-16) ──► pick campus ──► mrf-url
   file via Web Unlocker; if refused / timed out / cut off ──► plain HTTPS ──► data/price_files/<id>.csv|json
                                                              (+ <id>.meta.json: URLs, via, fetched_at, sha256)
   prices.build_table: stream the file, keep ~67 curated codes ──► policydb/prices/<id>.json (committed)
```

| File | What it does |
|---|---|
| `remy/price_sources.py` | `SOURCES` (per hospital: cms-hpt.txt URL, campus regex), `parse_hpt`, `choose`, `get` (Unlocker then HTTPS), `truncated` check, zip handling, `fetch` |
| `remy/prices.py` | `CODES` (curated list), parsers for the three CMS layouts (CSV tall, CSV wide, JSON v2/v3), compaction, and the read API: `load(id)`, `lookup(id, code)`, `hospitals()` |
| `scripts/build_price_tables.py` | `[ids] [--fetch] [--list]`: rebuilds tables from `data/price_files/` (reproducible), fetching only what's missing or with `--fetch` |
| `tests/test_prices.py` | Offline: fixtures for each layout in `tests/fixtures/prices/`, cms-hpt parsing (UTF-16, campuses), truncation check, lookup API, committed tables |

**What each entry keeps** (per code): description, gross charge, discounted cash price, min/max negotiated
(from the file's `standard_charge|min/max`, else computed from the listed payer dollar amounts, with
`min_max_source` saying which), median of the payer dollar rates, number of payer rates, up to 6 named payer
rates (one per family: Aetna, Anthem Blue Cross, Blue Shield, Cigna, UnitedHealthcare, Health Net, Kaiser,
Medi-Cal, Medicare; a commercial payer's Medicare Advantage or Medi-Cal plan is labelled as such), setting,
billing class, modifiers, drug unit, notes where it's ambiguous, and up to 3 other line items with the same
code. Selection rules: CPT/HCPCS-typed codes only (a chargemaster number that happens to equal `99285` is not
matched); outpatient before "both" before inpatient; facility before professional; no modifiers; most payer
rates. Sutter lists the gross charge on a chargemaster line and the payer rates on a separate HCPCS/APC line
for the same code, so the charge line and the rate line are chosen independently and the note says so.

Each table records the source: `cms_hpt_url`, `hpt_location_name`, `file_url`, `via` (`brightdata-unlocker`
or `https`), `fetched_at`, `sha256` and size of the file parsed, plus the file's own `hospital_name`,
`location_name`, `last_updated_on` and CMS `template_version`.

### What was built (from the committed tables)

67 codes requested per hospital. "With rates" = codes that have payer-specific negotiated rates at CPT/HCPCS
level.

| Hospital | File (campus as the file names it) | Layout | File updated | Size, fetched via | Codes | With rates | Missing |
|---|---|---|---|---|---|---|---|
| sutter-cpmc | CPMC Van Ness Campus | CSV tall v3.0.0 | 2026-04-01 | 17 MB, Web Unlocker | 67 | 19 | - |
| sutter-alta-bates | Alta Bates Summit, Summit Campus | CSV tall v3.0.0 | 2026-04-01 | 17 MB, Web Unlocker | 67 | 67 | - |
| ucsf-health | one file for Parnassus, Mt Zion, Mission Bay | JSON v3.0.0 | 2026-04-01 | 91 MB, HTTPS | 67 | 67 | - |
| ucsf-saint-francis | Saint Francis Memorial Hospital | JSON v3.0.0 | 2026-02-28 | 100 MB, HTTPS | 63 | 63 | 4 X-ray codes (73030, 73110, 73610, 73630) |
| zsfg | SF General | CSV tall v3.0.0 | 3/19/2026 | 4 MB, Web Unlocker | 59 | 59 | 36415 and all 7 drug codes |
| chinese-hospital | Chinese Hospital | CSV tall v3.0.0 | 2026-07-22 | 46 MB, Web Unlocker | 66 | 66 | J7030 |
| stanford-health | Stanford Health Care (main) | JSON v3.0.0 | 2026-04-01 | 155 MB, HTTPS | 67 | 65 | - |
| john-muir-wc | Walnut Creek Medical Center | CSV tall v3.0.0 (zip) | 2026-03-23 | 343 MB unzipped, Web Unlocker | 67 | 67 | - |
| kaiser-oakland | Oakland Medical Center | CSV wide v3.0.1 | 4/1/2026 | 216 MB, HTTPS | 67 | 67 | - |
| marinhealth | MarinHealth Medical Center | CSV wide v3.0.0 | 3/26/2026 | 127 MB, HTTPS | 67 | 67 | - |
| el-camino | one file for Mountain View and Los Gatos | JSON v3.0.0 | 2026-04-01 | 58 MB, HTTPS | 63 | 0 | 81025, 12001, 10060, 29125 |
| highland-ahs | **not built** | | | | | | |

Notes on the data:
- **Highland has no table.** Its `cms-hpt.txt` came through Web Unlocker (35 s), but the 1 file it points to
  timed out in Web Unlocker 3 times (130-166 s, "Request timeout" / 502), and alamedahealthsystem.org refuses
  plain connections from our server. Nothing was faked.
- **Where rates are missing it's the file, not the parser.** CPMC Van Ness puts payer rates for most ER, lab
  and infusion codes on APC lines, not CPT lines (74177 has them: Aetna $4,717, Blue Shield $6,302). El Camino
  lists negotiated rates only by MS-DRG and APC (1,049 such items, no CPT-level rates). Stanford's facility
  ED/imaging lines have only a Health Net Medicare plan rate at CPT level; its commercial rates sit on
  professional-fee lines, which we skip for a facility bill.
- Sutter and Saint Francis list the discounted cash price equal to the gross charge; UCSF's cash price is 30%
  of gross, Stanford's 40%, El Camino's 25%, Kaiser's about 56%.
- Payers that pay a percent of charges show a `percentage` and an `algorithm` text instead of a dollar amount
  (e.g. John Muir: Aetna HMO 69.1% of billed). Drug lines are per package or per HCPCS unit depending on the
  hospital, and the table says which when the file does (`drug_unit`, notes).
- The `last_updated_on` format is the hospital's own (ISO or M/D/YYYY).

### Bright Data use for prices, honestly

All 12 `cms-hpt.txt` indexes were fetched through Web Unlocker (every one succeeded; 3 tries for Highland).
For the files themselves, Web Unlocker worked for 5 (Sutter x2, SF General, Chinese Hospital, and John Muir's
343 MB zip, where plain HTTPS gets a 403). It failed for the rest, and plain HTTPS was used:
- Box downloads (UCSF, Saint Francis): HTTP 402 "Residential Failed (bad_endpoint)".
- Very large files: 502 timeouts (Stanford 155 MB after 32 s, Kaiser 216 MB "Navigation timeout" after 88 s,
  Highland), "CDP disconnected" (MarinHealth), 502 Bad Gateway (El Camino).
- **Silent truncation:** the first MarinHealth fetch returned HTTP 200 with 2.2 MB of a 127 MB CSV, cut
  mid-row after 64 s. `price_sources.truncated()` now rejects files that don't end cleanly (CSV newline, JSON
  `}`, valid zip directory) and falls back.

Price-file requests logged (stage `prices`): 30 Web Unlocker (21 returned a body, 9 errors) and 9 plain
HTTPS (6 OK, 3 Highland connect failures, logged with no Bright Data product). We verified that the CPMC and
Chinese Hospital files fetched through Bright Data are byte-identical (same sha256) to the copies downloaded
earlier with plain HTTPS.

## Reproduce

```bash
# .env: BRIGHTDATA_API_KEY, BRIGHTDATA_SERP_ZONE, BRIGHTDATA_UNLOCKER_ZONE, ANTHROPIC_API_KEY
uv run python scripts/crawl_policies.py crawl --force        # full pipeline for every seeded hospital
uv run python scripts/crawl_policies.py recheck              # change detection
uv run python scripts/crawl_policies.py show                 # tiers + verified quotes + sources
uv run python scripts/crawl_policies.py compare freecare     # same bill through every policy
uv run pytest -q tests/test_policies.py                      # offline, uses the committed artifacts
uv run python scripts/build_price_tables.py --fetch          # download price files (cms-hpt.txt -> file)
uv run python scripts/build_price_tables.py                  # rebuild policydb/prices/ offline, reproducibly
uv run python scripts/build_price_tables.py --list           # coverage per hospital
uv run pytest -q tests/test_prices.py                        # offline, small fixtures
```
Each crawl costs about 4 Bright Data requests per hospital (1 SERP + 1 page + 2 PDFs); a recheck about 3; a
price fetch 2 Web Unlocker requests (index + file).

## Evidence (`policydb/`)

- `policies.sqlite3`: the database. `crawl_log` has one row per Bright Data request (and per decision note).
- `policies.json`: the same data as readable JSON (runs, versions, verification checks, request log).
- `prices/<hospital>.json`: the compact price tables (raw price files stay in gitignored `data/price_files/`).
- `raw/<hospital>/`: SERP JSON, page markdown, policy PDFs and their extracted text, named by sha256. The stored
  `content_sha256` of each version can be recomputed from these files (`pipeline.load_documents` +
  `pipeline.content_hash`); `tests/test_policies.py` does exactly that, and re-verifies every stored quote.

Committed run history: one 4-hospital crawl (Chinese Hospital failed in it, both Google results pages had only
redirect links, so it was re-crawled after adding its domain), one recheck of all four (all unchanged).
Requests logged in the DB: 6 SERP API, 22 Web Unlocker. Development of the pipeline used roughly 50 more
requests that are not in the committed DB (the DB was reset before the final seed crawl). A later CPMC
re-crawl brought the totals to 7 SERP / 25 Unlocker.

Bay Area round (8 hospitals, 12 crawls) plus price files: **16 SERP API + 18 Web Unlocker** for the policy
crawls, **30 Web Unlocker** for the price indexes and files, so the DB now logs **23 SERP / 73 Web Unlocker**
in total (`crawl_policies.py show` prints the totals). Saint Francis's first crawl ran under the id
`dignity-saint-francis`; its log rows were re-pointed to `ucsf-saint-francis` when the entry was renamed (the
query text in them still shows the Dignity search), and its SERP artifacts stay in
`policydb/raw/dignity-saint-francis/`.

## Honest limitations

- **California HCAI is not reachable through Bright Data.** Every California hospital files its charity-care
  policy with HCAI, but Web Unlocker refuses government domains ("classified as Government and blocked"). The
  HCAI attachment Google happens to return is still not fetched (for Stanford it was Stanford Children's, a
  different hospital). Instead, a registry entry can name its page on HCAI's Hospital Fair Pricing Policy Lookup
  (`Hospital.hcai`); for those hospitals `policies/hcai.py` always reads that page
  and fetches the newest filed Charity Care / Discount Payment PDF over **plain HTTPS** (logged in `crawl_log`
  with no Bright Data product, role `state_filed`), alongside whatever the site gave. Always, not only when the
  site links no policy PDF: Saint Francis's site links one, and it is a Spanish application form; and crawls
  and rechecks must fetch the same sources or an unchanged policy hashes as changed. Stanford, ZSFG and Saint
  Francis got their tiers
  this way (`scripts/crawl_policies.py hcai <id>`: stored documents rebuilt from committed artifacts + the
  HCAI copy, re-extracted, no Bright Data request).
- **Verification proves the words, not the reading.** A verified quote is really in the hospital's document and
  states the FPL limit, but the discount %, `applies_to` and out-of-pocket threshold are Claude's reading of it.
  The quotes are shown next to every number so a person can check. Re-running extraction can classify borderline
  text differently (ZSFG's 138% sentence was a tier in one development run and "other relief" in the final one).
- **Health-system documents cover many facilities.** The prompt restricts rules to the named hospital; Sutter's
  policy also has a rule only for one rural hospital, which an early development run wrongly kept as a tier.
- **Physician bills are separate.** All four policies exclude separately billed physicians; Remy applies the
  policy to the hospital bill only.
- **Non-percentage relief isn't in the math.** Caps like "no more than Medicare would pay" need the Medicare
  allowed amount, which Remy doesn't have; they are stored, verified and shown, not computed.
- **Google variability.** SERP results differ run to run, sometimes as opaque redirect links; the ranking drops
  those and retries with a `site:` query, which needs the hospital's domain to be known.
- **Request counting.** `crawl_log` has one row per logical request; the client retries a 429/5xx once, so the
  billed count can be slightly higher.
- **Rechecks are on demand** (CLI, API, the hospitals page, "Recheck now" on a case) or triggered by a case
  that uses a stale policy, not scheduled; a cron entry running `crawl_policies.py recheck` is all that's
  needed. The CLI's `recheck` still refetches the same documents even for a "no tiers" hospital; the API, the
  hospitals page and the case flow crawl those again from Google.
- **The live path hasn't run against Bright Data since it was built** (credits rule): it is tested with replies
  in the real shapes (`tests/test_live_crawl.py`), and needs one live rehearsal.
- Income is compared to the 2026 HHS poverty guidelines (`finance.FPL_*`).
- **"All patients" readings to check.** MarinHealth ("Full charity care (no payment) is for all patients at
  400% or less of the FPL") and El Camino ("full charity care to patients whose Family Income is at or below
  400%") were stored as tiers for all patients, so an insured patient like Rosa gets 100% off there. The quotes
  are verified, but California policies usually mean uninsured patients or those with high medical costs; the
  surrounding text should be read before relying on these two.
- **Old or secondary documents.** Highland's policy PDF was uploaded in 2021 and El Camino's in 2022; Kaiser's
  tiers come from its 2024 MFA application, not a formal policy document. They are what Google and the
  hospitals' sites returned.
- **Price tables are a curated subset.** About 67 codes, one representative line per code (outpatient,
  facility, no modifiers); other lines with the same code are counted and up to 3 are listed. Negotiated rates
  are what the file states for that line; a real allowed amount also depends on the plan, bundling and units.
- **Drug lines are compared only when the units convert.** A drug's HCPCS code is per a dose (J2405: 1 mg)
  and files price per what they stock (CPMC: 12.25 mL). Mass to mass and volume to volume are converted; a mg
  dose priced per mL or per "each" is skipped (that needs the concentration) and listed as not compared.

### Where the tables are used: the bill's line check

`remy/billcheck.py:line_review` checks every bill line, insured or self-pay, against the hospital's table:
a line above the posted gross charge × units is a `price_above_posted` finding with the price file as its
source. Drug lines go through `posted_unit_price`: compared only when the file's unit (`drug_unit`,
`drug_unit_type`: ME mg, GR g, ML mL) converts to the HCPCS unit without knowing the concentration. Self-pay,
the line errors set the share; insured, they are reported apart from the insurance gap (the statement already
set the share, so the balance doesn't change, but the claim was built on a wrong line).

- Lena (`demo/scenarios/lena-johnmuir`, insured): 71275 billed $6,200, John Muir's posted gross $5,377.50:
  $822.50, and the insurance finding is the rest ($25,829.55). Her morphine line (file: per 1 mL; HCPCS: per
  10 mg) is not compared.
- The old sample bill (`demo/case.json`, CPMC, insured): J2405 $440 against the file's "$16.33" is **not**
  flagged (per 12.25 mL vs per 1 mg). Its made-up 80053 charge ($420) was $34 above CPMC's posted $386, so
  the demo data was aligned to the real file: 80053 is now $386 and 99284 $3,184 (posted $5,214), same $8,700
  total, PDFs regenerated. The sample bill has no line flag, and its findings ($7,600 insurance, $500 payment)
  match the replays and pitch.

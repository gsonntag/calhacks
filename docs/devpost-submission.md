# BillBack: Devpost submission pack (Cal Hacks 13.0)

Written 2026-10-25, about 1 AM PDT, from four research passes (Cal Hacks rules and prizes, winning Devposts,
healthcare competitors, wider market and barriers). Full research with every source is in
`docs/research/`. Text marked **[fill]** needs a team answer.

---

## 0. Deadlines and what to do first

| When (PDT, Sun Oct 25) | What |
|---|---|
| **9:00 AM** | **Devpost locks.** The page calls this a hard deadline. Yesterday's submission can be edited until then. |
| 10:30 AM to 12:30 PM | Table judging: **3-minute pitch + 2 minutes of questions** (Cartesia and Bright Data judges want a 2-minute version). |
| 2 to 4 PM | Closing ceremony and winners. |

Do these before 9 AM, in this order:
1. **Make the GitHub repo reachable.** `github.com/gsonntag/calhacks` returns "not found" to anyone not
   logged in. "GitHub repo required for any code to be considered"; Cartesia judges need source access.
   Before making it public, check that no `.env` file or API key is in the git history.
2. **Video:** record and upload to YouTube or Vimeo (unlisted is fine), and paste the link. Plan in section 4.
3. **Paste the text** in section 3 (replacing the current draft) and add the elevator pitch.
4. **Image:** a cover image or screenshots (section 5).
5. **Prize opt-ins** (section 2), and send **MLH's separate form** if entering the Commit Fellowship.
6. Fill **team name, table number, all teammates** on the submission.

---

## 1. Submission requirements checklist

| Field | Required? | Status | Notes |
|---|---|---|---|
| Project name + tagline | Yes | Have name | Tagline: "An AI agent that fights your hospital bill, with receipts." (alternatives in 3.1) |
| Elevator pitch (2 to 3 sentences) | Yes (FAQ) | **Missing** | Draft in 3.1 |
| Description ("About the project") | Yes | Old draft in `docs/devpost.md` | Replace with section 3 |
| Image(s) | Yes | **Missing** | Section 5 |
| Demo video link | Expected (judging criteria cite it; sponsors judge remotely from it) | **Missing** | Section 4 |
| GitHub repo link | Yes ("for any code to be considered") | **Private (404 when logged out)** | Make public or confirm the right link |
| Built with (tags) | Yes | Partial | List in 3.12 |
| Team name, teammates (2 to 4, 18+, college students) | Yes | **[fill]** | Every teammate must accept the Devpost invite |
| Table number | Yes (FAQ) | **[fill]** | From the judging assignment |
| Prize opt-ins | Yes | **[fill]** | Section 2 |
| All work done during the hackathon | Rule | OK | They check for prior work; git history starts at the event |

---

## 2. Prizes to opt into, and what each needs

| Prize | What judges look for | What our Devpost and video must show | Risk |
|---|---|---|---|
| **Cal Hacks overall / People-First Social Impact / Most Technically Complex / Hacker's Choice** | Application, Intentionality, Functionality (smooth live demo plus a Devpost video), Creativity, Technical Complexity (no weights given) | A full flow in the video; the technical depth in "How we built it" | Low |
| **Cartesia: Best Use** | Four equal criteria: problem and originality; use of Cartesia; execution; **accessibility, privacy and responsible use**. Voice must be core. Working demo; 2-minute demo-first pitch; README with setup, Cartesia features used and **known limitations**; source access, no secrets | Name each Cartesia feature (Ink STT, Sonic streaming TTS, voice and accent picker, pronunciation normalizer) and what it adds; latency numbers; responsible-use paragraph; known limitations | Medium: no live phone call recorded yet (credits ran out) |
| **Bright Data: Public Web Data** | Product must not work without live web data; run live in the demo; handle blocks, slow and empty results; clear two-minute story with real results. **Disqualified for** login or paywall scraping, non-public personal data, **mocked or hardcoded data in the demo**, or Bright Data only in the README | Show a live crawl or "Recheck now" in the video and on stage; lead with a case where the hospital policy changes the outcome (Marcus); make sure sample tiers can't appear on stage | **High until the demo shows live crawling** |
| **Capital One: Nessie** | Completeness, creativity, complexity | Which Nessie objects and endpoints we use: customers, accounts, purchases and deposits (income, missed payment, duplicate charge), bills (the payment plan written back) | Low. Hide or fix the payment plan that never ends |
| **Anthropic: Claude for Public Benefit** | A **narrowly named community**; a real problem (lived it or talked to someone who did); it works; Claude matters and **you know where it could go wrong** | Name the community; say where Claude is used and where it is deliberately not; "where it could go wrong" paragraph | Medium: the old draft has none of this |
| **Microsoft: Built for the Real World / Customer Impact** | Customer understanding, measurable usefulness, path to production, responsible design, end-to-end function | Sourced numbers; production path (section 3.11) | Low to medium |
| **MLH Best Use of Gen AI** | Uses a generative AI API | Opt in | Low |
| **MLH Commit Fellowship** | Opt in **and** MLH's supplemental form | Send the form | Missed if the form isn't sent |
| Hyphenate: Finance Autonomy | A finance team's seats inside a company | Stretch: BillBack works for a patient, not a company's finance team | Skip unless pitched as the B2B version |

No separate healthcare, fintech or "best AI agent" prize was listed. Every other sponsor prize requires that
sponsor's own technology.

---

## 3. Devpost text, first version (paste-ready)

Numbers below are from our code and demo data unless a source is linked. Check the demo numbers against
whichever case the video leads with.

### 3.1 Tagline and elevator pitch

**Tagline options:**
- "An AI agent that fights your hospital bill, with receipts."
- "Your hospital bill, audited and argued for you."
- "The billing advocate that checks the math and makes the call."

**Elevator pitch (2 to 3 sentences):**
> BillBack audits a hospital bill against your insurance statement and your own bank records, finds the
> discount the hospital's own published policy says you're owed, and then phones the billing office to get
> it fixed. Every dollar figure comes from code with a cited source, so the AI can argue but never invent a
> number. It never agrees to pay, always says it's automated, and asks your OK before anything that matters.

### 3.2 Inspiration

> 4 in 10 U.S. adults say they've received a medical bill they believe contained an error
> ([KFF, 2022](https://www.kff.org/health-costs/the-burden-of-medical-debt-in-the-united-states/)), and
> Americans owe at least $220 billion in medical debt
> ([KFF/Peterson, 2024](https://www.healthsystemtracker.org/brief/the-burden-of-medical-debt-in-the-united-states/)).
> Many of those bills shouldn't exist: 45% of nonprofit hospital organizations routinely bill patients
> whose income qualifies them for the hospital's own charity care
> ([KHN analysis of IRS filings](https://nonprofitquarterly.org/study-finds-45-percent-of-patients-who-qualify-for-charity-care-are-billed-anyway/)).
>
> The fixes already exist: an insurance adjustment that never got posted, a payment the hospital "can't
> see", a discount the hospital is legally required to offer. Getting them takes hours on hold and knowing
> exactly what to ask for. Most people pay the wrong amount or ignore it until collections calls.
> **[fill: one sentence of the team's own story, e.g. a family member's ER bill, or someone you talked
> to this weekend. Anthropic's prize asks for this.]**
>
> We built BillBack for **[fill: name the community narrowly, e.g. "low-income and underinsured patients
> of California nonprofit hospitals" or "students and hourly workers in the Bay Area with ER bills"]**:
> people who qualify for help they've never heard of.

### 3.3 What it does

> Drop in your hospital bill and insurance statement. BillBack works through five visible steps, and you
> can reopen every one of them with its full record.
>
> 1. **Reads your bill.** Claude reads the bill and the insurance statement (EOB) from PDFs or photos.
> 2. **Review: two agents check what you actually owe, side by side.**
>    - The **Nessie agent** reconciles the bill against your EOB and your bank transactions (Capital One
>      Nessie): insurance adjustments the hospital never applied, payments it never credited, duplicate
>      charges.
>    - The **Bright Data agent** looks the hospital up in a policy database we built by crawling each
>      hospital's own published financial-assistance policy with Bright Data, works out your income from
>      payroll deposits, and checks you against the hospital's discount tiers, payment-plan terms and real
>      prices from its public price file.
>    - Every check and every finding shows up live with its proof and source. You can tap **Ask** on any
>      finding to have the agent that found it explain it, or **This is wrong** to correct it.
> 3. **The plan.** A Decide agent turns the findings into a call plan: the money ($8,700 → $600), who to call,
>    and every ask the findings back up, biggest money first. You can talk to the planner ("refund first",
>    "I can do $150 a month") and it makes a new version of the plan. It changes strategy, never facts.
> 4. **The call.** Once you tap "Call now", BillBack phones billing. It says it's an automated assistant
>    calling with your authorization, cites the evidence, asks for each correction, the discount and a hold
>    on collections, and gets a case number. It never agrees to pay anything or gives card details.
> 5. **What's next.** After the call, BillBack scores each ask (got it, refused, promised later, no clear
>    answer), keeping only marks it can back with the rep's own words. The Decide agent then plans the
>    follow-up call with only what's left, and tracks every promise ("a corrected statement within 5
>    business days") so it knows when to call back.
>
> **Demo cases** (synthetic patients, real hospitals, real policies, real prices):
> - **Marcus, UCSF:** $4,174 → **$0**. His bike-crash bill is over 10% of his family's income, and UCSF's own
>   policy writes it off.
>   **[check: the older demo data and pitch say $5,600; use the number from the case you demo]**
> - **Rosa, CPMC:** $8,700 → $600. The insurance adjustment was never applied and a $500 payment was missed.
>   That leaves 6 × $100 on an interest-free plan under Sutter's own billing policy. BillBack also tells her
>   honestly that she isn't eligible for the write-off.
> - **Same bill, different hospitals:** the same patient gets different answers at different San Francisco
>   hospitals, because each hospital's own policy is different.

### 3.4 How we built it

> **Architecture:** FastAPI + WebSockets; a vanilla-JS case page that streams every agent step, finding,
> approval and transcript line live. Our own agent harness gives every tool a permission tier (read / act /
> consequential). Consequential tools, like placing a call or writing a payment plan, block on the
> patient's approval. Every tool call and result goes to an audit log.
>
> **Money is computed, never generated.** Reconciliation, income annualization, percent of the federal
> poverty line (2026 HHS guidelines), discount tiers, settlement, refunds and payment plans are
> deterministic Python with 281 offline tests. Claude extracts policy text only with verbatim quotes, which
> our code checks against the fetched page before using.
>
> **Sponsor technology:**
> - **Anthropic Claude** (Sonnet 5.5 / Haiku 5.5): reads bills and EOBs (vision); extracts policy rules
>   with quotes; is the brain on the live call (streaming tool use); answers "Ask" questions from one
>   agent's evidence only; turns planner-chat requests into strategy changes that our rules then check;
>   reads the finished call to mark each ask, but its mark only counts if its quote is really in the rep's
>   words.
> - **Bright Data** (SERP API + Web Unlocker): discovers and fetches each hospital's financial-assistance
>   pages and policy PDFs into a versioned policy database for 12 Bay Area hospitals. It verifies every
>   quoted rule against the source and detects when a policy changes. We also pulled real prices for 11
>   hospitals from their public CMS price-transparency files.
>   **[fill: what the live part of the demo is, e.g. "a live recheck on stage"]**
> - **Capital One Nessie:** the patient's accounts, deposits (income from payroll) and purchases (payments to
>   the hospital, duplicate charges). An approved payment plan is written back as a bill.
> - **Cartesia:** our own phone pipeline. Twilio Media Streams carry the audio, Cartesia **Ink** does
>   streaming speech-to-text with key terms (names, insurer, "EOB"), and **Sonic** streams text-to-speech
>   with a voice and accent picker. A pronunciation normalizer makes claim numbers, CPT codes, drug names,
>   money and dates sound the way a billing rep expects. Claude's reply streams into Sonic sentence by
>   sentence, and we start Claude early on Ink's `eager_end`. The first audio comes **0.44 to 0.8 s after the
>   rep stops** (phone simulator), and the median in desk mode fell from 3.0 s to 0.93 s. The agent stops
>   talking when the rep interrupts.

### 3.5 Challenges we ran into

> - **Trusting an LLM with money.** Our first version let the model state numbers. We moved every figure into
>   code with a source, and made Claude quote policy text word for word. Our code rejects a quote it can't
>   find on the page.
> - **Latency on a live call.** Replies took 1.4 to 2.7 s per turn. Streaming Claude into Sonic sentence by
>   sentence, speculating on `eager_end` and prompt caching brought the first audio under 1 s.
> - **Hospital websites.** Bright Data returns 200 even when the target fails (the status is in the
>   headers). Search sometimes returns only redirect links. Policies hide in PDFs. Some sites are blocked,
>   so we crawl each hospital's own site and PDFs.
> - **Nessie's API:** bills must be created with `recurring_date` or listing breaks, and amounts are whole
>   dollars.
> - **Knowing what a call actually won.** Reps say "I'll note that" a lot. The scorecard keeps a win only
>   with evidence: a recorded outcome or the rep's own words.
> - **We ran out of Cartesia credits** mid-hackathon while measuring latency, so the post-call steps were
>   built and tested against recorded and fixture calls.

### 3.6 Accomplishments we're proud of

> - Every number on screen traces back to a bill line, an EOB line, a bank transaction or a policy clause.
> - A real policy database for 12 hospitals, with quotes verified against the source.
> - Sub-second voice replies through our own Twilio + Cartesia pipeline.
> - A full loop: audit → plan → call → score → next call, with a human approving every consequential step.

### 3.7 What we learned

> The hospital's own policy is the strongest argument. A billing rep can't argue with their own hospital's
> written rules, quoted word for word. The hard part isn't the AI; it's getting the right facts in front of
> the right person, then holding them to what they promised.

### 3.8 What's real and what's simulated

> - **Real:** Bright Data crawls of real hospital policies, real prices from hospitals' public price files,
>   Nessie API records, Claude's reasoning, Cartesia speech-to-text and text-to-speech, the approval gate,
>   the audit log and the 2026 poverty guidelines.
> - **Simulated:** the patients, bills, EOBs and bank histories are synthetic (the PDFs are watermarked
>   SAMPLE). "Hospital billing" in the demo is a teammate playing the rep.
> - **Not yet done live:** a real phone call through Twilio. The pipeline was tested against simulated
>   Twilio and Cartesia streams; we ran out of Cartesia credits before recording a live call.

### 3.9 Known limitations, and how we'd solve them

| Limitation | What BillBack does now / next |
|---|---|
| **Billing reps may refuse to talk to an AI or a third party.** Dollar For reports hospitals usually won't talk to advocates directly. | The patient can be conferenced into the call to give verbal permission (HIPAA 45 CFR 164.510(b)). Next: a signed authorization form per hospital, and a letter fallback when the rep refuses. |
| **Hold queues and phone menus** | Next: navigate menus with keypad tones and stay silent through hold music until a human answers. |
| **Call recording consent** (California requires every party's consent) | It says up front that it's an automated assistant and the call is transcribed. Next: ask for consent and switch to notes only if the rep declines. |
| **AI disclosure laws** (California BOT Act, Utah SB 149) | It always says it's automated, at the start and whenever asked. It never imitates a human. |
| **Wrong numbers or a hallucinated discount** | All money math is deterministic and cited. Claude's policy quotes must match the source page. The patient approves the plan before any call. |
| **The agent agreeing to something harmful** | It never pays, never gives card details and never agrees to a payment plan by itself. Any payment comes back to the patient. |
| **Promises not kept** ("we'll send a corrected bill") | It records the rep's name, case number, the promise and the date; scores each ask; and plans the follow-up call. |
| **Income is estimated from bank deposits** (take-home, not gross, and gig income is missed) | Next: ask for gross pay or a pay stub, and count gig and transfer income. |
| **An insured patient who uploads only the bill is treated as uninsured** | Next: intake asks "Do you have insurance?" when no EOB is uploaded. |
| **Speech-to-text can mishear claim and case numbers** | Next: read IDs back for confirmation. |
| **California-first:** the discount rules lean on California law | Each state is a rulebook connector: same engine, different rules. |
| **Patient data (PHI)** | Read-only bank access, an audit log the patient can see, nothing used for training. Next: encryption at rest and a retention limit. |
| **Unproven outcomes:** all demo patients are synthetic | Next: pilot with a patient-advocacy nonprofit. |

### 3.10 Competition: who else does this, and why BillBack is different

> Medical-bill help today is either a **human advocate who takes 20 to 35% of what they save** (Goodbill,
> Resolve, CareRoute, CoPatient), or a **free nonprofit that files a paper charity-care application and
> waits** (Dollar For). AI tools so far mostly **write letters and leave the phone call to you** (Claimable,
> Counterforce, MyMedBill). General AI calling agents like Pine AI phone companies for you, but don't audit
> the bill or know charity-care law.
>
> BillBack is the first patient-side agent we know of that does the whole loop. It checks the bill against
> your insurance statement **and your own bank transactions**, and computes the discount the hospital's own
> policy and California law say you're owed. Then it makes the call, tracks every promise and plans the
> follow-up. Every number is deterministic and cited, so the AI can argue but never invent a figure.

| | Who pays | Checks bill vs insurance | Uses bank data | Charity-care eligibility | Calls the hospital | Follows up | Cited numbers |
|---|---|---|---|---|---|---|---|
| **BillBack** | TBD | Yes | **Yes** | **Yes (hospital policy + CA law)** | **Yes (AI, disclosed)** | **Yes (case #, promises, scorecard)** | **Yes** |
| Goodbill / Resolve / CareRoute | 10 to 25% of savings, or a deposit | Partly | No | Partly | Humans | Humans | No |
| Dollar For | Free (nonprofit) | No | Income proof only | **Yes** | No | Check-ins | No |
| Claimable / Counterforce | ~$40 per appeal / free | No | No | No | No (letters) | No | No |
| Pine AI | Subscription | No | No | No | Yes (AI, general) | Partly | No |
| MyMedBill / VerifyDoc | $10/mo / $19 | Partly | No | Partly | No | Partly | No |
| Cedar / Waystar | The hospital | n/a | No | Yes, for the hospital | Answers calls *for the hospital* | For the hospital | n/a |

**Be honest about where they're ahead** (worth one sentence in the Devpost, and ready for judges' questions):
- Dollar For, Resolve and Undue have real results.
- Humans handle hard escalations on big bills.
- Pine and Cedar have far more voice infrastructure.
- Employer navigators have distribution we don't.

Say "the first patient-side agent we know of", **not** "the only AI that calls hospitals": Pine AI and Avelis
Health place AI calls too.

### 3.11 Why this hasn't been built, and why now

> **Why not before:** no hospital billing system has an API; reps resist third parties; HIPAA authorization,
> California's all-party recording consent and AI-disclosure laws add friction; and percent-of-savings fees on
> small bills are thin. A YC-backed startup (Avelis Health) tried consumer AI calling in 2025 and moved to
> selling to employers within months.
>
> **Why now:**
> - Real-time voice with streaming tool use is finally fast enough for a phone call.
> - Hospitals have had to publish prices since 2021.
> - California's Hospital Fair Pricing Act rules (AB 1020, SB 1061, AB 2297) raised the income limit for
>   discounts to 400% of poverty, dropped asset tests, ended application deadlines and banned medical debt on
>   credit reports.
> - The federal CFPB rule keeping medical debt off credit reports was struck down in July 2025, which makes
>   state law and patient-side tools matter more.
>
> **Path to production:** a signed HIPAA authorization; a letter fallback; a pilot with a patient-advocacy
> nonprofit; free for patients who qualify for charity care, with a capped success fee charged only after the
> corrected bill posts. California's AB 1312 will make hospitals screen for charity eligibility from July
> 2027, and BillBack checks that they actually applied it.

### 3.12 What's next: beyond hospital bills

> We started with hospital bills because that's where the gap between what the rules say and what people pay
> is widest, and disputes are still settled by phone. The engine itself is general: **reconcile the bill
> against your own records → find the provider's own rule → call with the evidence → hold them to the
> promise.** For each new area, only the rulebook and the evidence templates change.
> - **Insurance claim denials:** 20% of in-network ACA marketplace claims were denied in 2023, and fewer than
>   1% were appealed ([KFF](https://www.kff.org/private-insurance/claims-denials-and-appeals-in-aca-marketplace-plans-in-2023/)).
> - **Telecom and utility bills:** U.S. households spend $164B a year on cable and internet
>   ([Doxo](https://broadbandbreakfast.com/report-u-s-households-spend-164-billion-annually-on-cable-and-internet-services/)).
> - **Business invoices:** duplicate or erroneous payments run 0.8 to 2% of accounts-payable spend
>   ([APQC](https://www.cfo.com/news/metric-of-the-month-detect-and-prevent-duplicate-or-erroneous-payments/656852/)),
>   and 3 to 6% of freight invoices contain errors.

**Built with:** python, fastapi, websockets, javascript, html, css, claude, anthropic, bright-data,
capital-one-nessie, cartesia, twilio, sqlite, pdf

---

## 4. The video: what needs to be in it

Cal Hacks sets no maximum length. Aim for **2 minutes or less, demo first**. Sponsors judging remotely watch
only this. Upload to YouTube or Vimeo (unlisted is fine) and paste the link. Use a screen recording with a
voice-over; show the real app, not slides.

| Time | Show | Say | For |
|---|---|---|---|
| 0:00 to 0:10 | A real-looking bill: $4,174 (Marcus) | "Marcus got a $4,174 ER bill. His hospital's own policy says he owes nothing. He'd never know." | Hook |
| 0:10 to 0:30 | Upload; the two Review agents' checklists running live; findings appearing with sources | "Two agents check his bill: one against his bank and insurance (Nessie), one against UCSF's own policy, crawled live with Bright Data." | Bright Data, Nessie |
| 0:30 to 0:45 | **Live** Bright Data crawl or "Recheck now" on a hospital | "This is live: the policy page and PDF, verified quote by quote." | Bright Data (required) |
| 0:45 to 1:00 | The plan: $4,174 → $0, each ask with "why ▸"; a planner chat change | "Every number comes from code with a source. The AI argues; it never invents a number." | Anthropic, overall |
| 1:00 to 1:30 | The call: transcript and voice, "I'm an automated assistant calling for Marcus…", rep agrees, case number | "Cartesia Ink and Sonic, first word in under a second. It never agrees to pay." | Cartesia |
| 1:30 to 1:45 | Step 5: the scorecard and "What's next" | "It scores what the call won, keeps the rep's words as proof, and plans the follow-up." | Overall, Microsoft |
| 1:45 to 2:00 | Same bill at four hospitals: $0 vs $4,174; tagline | "Same bill, different hospital, different answer. BillBack knows the rules so you don't have to." | Close |

Rules of thumb:
- Lead with "the moment" (Marcus $4,174 → $0), not the architecture.
- Say out loud what's live vs simulated, in one line: "Patients are synthetic; the hospitals, policies and
  prices are real."
- Show the face of the app, not code. Keep text on screen big enough to read.
- If a live phone call can't be recorded, use desk mode and say "desk mode: a teammate plays the rep".
- Make a 3-minute cut for the main judges: add Rosa's case (bank data finds the missed $500) and the
  approval gate.

**[Waiting on the winning-Devposts research: it may refine this outline. Section 6 will be updated.]**

---

## 5. Links and images

- **GitHub:** `https://github.com/gsonntag/calhacks` must open when logged out. Before making it public:
  - check for secrets: `git log -p | grep -iE "sk-ant|api_key|secret|token"` and that `.env` was never
    committed;
  - update the README test count (it says 139; it's now 281);
  - add a short "Known limitations" section to the README (Cartesia requires it), copied from 3.9.
- **Video:** YouTube or Vimeo link.
- **Images** (the first one is the thumbnail):
  1. A cover image: the BillBack logo + "$4,174 → $0" + the tagline.
  2. The Review step with both agent checklists and findings.
  3. The plan with "why ▸" sources.
  4. The call transcript.
  5. Step 5: "What the call got".
  6. An architecture diagram: bill/EOB → Nessie agent + Bright Data agent → Decide agent → call (Twilio +
     Cartesia + Claude) → scorecard → follow-up.

---

## 6. Don't say (and what to say instead)

| Don't say | Why | Say instead |
|---|---|---|
| "80% of medical bills have errors" | Comes from advocates paid by results; no published method | "4 in 10 adults say they've received a bill they believe had an error (KFF, 2022)" |
| "The only AI that calls hospitals" | Pine AI and Avelis place AI calls | "The first patient-side agent we know of that does the whole loop" |
| "No credit reporting before 180 days" | Since 2025 California bans it outright | "California bans medical debt on credit reports" |
| "AB 1020 took effect in 2024" | It took effect in 2022; enforcement began in 2024 | "Enforced since 2024" |
| "Marcus qualifies for Medi-Cal" | His income is probably just over the limit | Don't mention Medi-Cal |
| "We made a live phone call" | Not yet recorded | "Our phone pipeline was tested against simulated Twilio and Cartesia streams" |
| "Robot lawyer" / "legal advice" | FTC fined DoNotPay over this | "It cites the hospital's own published policy" |

---

## 7. Research files

- `docs/research/calhacks-rules.md`: Cal Hacks 13.0 rules, schedule, every prize's criteria, gaps.
- `docs/research/competitors-healthcare.md`: competitor profiles, comparison table, market facts with
  dates and evidence quality.
- `docs/research/market-and-barriers.md`: other billing markets, AI phone agents, barriers, mitigations,
  expansion story.
- `docs/research/winning-devposts.md`: patterns from winning Devposts and video outlines (pending).
- `docs/legal-check.md`: legal claims checked against the law.

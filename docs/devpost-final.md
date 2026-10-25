# Devpost fields (paste-ready)

**Project name:** Remy

**Elevator pitch** (167 of 200 characters):
An AI agent that audits your hospital bill against your insurance and bank records, finds the discount the hospital's own policy owes you, and calls billing to fix it.

**About the project** (paste everything below this line):

---

## Inspiration

Four in ten U.S. adults say they've received a medical bill they believe had an error ([KFF](https://www.kff.org/health-costs/the-burden-of-medical-debt-in-the-united-states/)), and Americans owe at least $220 billion in medical debt. Much of it shouldn't exist: 45% of nonprofit hospital organizations routinely bill patients whose income already qualifies them for the hospital's own charity care ([KHN analysis of IRS filings](https://nonprofitquarterly.org/study-finds-45-percent-of-patients-who-qualify-for-charity-care-are-billed-anyway/)).

The fixes already exist: an insurance adjustment that was never posted, a payment the hospital "can't see", a discount the hospital is legally required to offer. Getting them takes hours on hold and knowing exactly what to ask for. So most people pay the wrong amount, or ignore it until collections calls.

We built Remy for low-income and underinsured patients of California hospitals: people who qualify for help they've never heard of, facing a "final notice" they can't afford.

## What it does

Drop in your hospital bill and insurance statement. Remy works through five steps, and every step stays open with its full record.

1. **Reads your bill.** Claude reads the bill and the insurance statement (EOB) from PDFs or photos.
2. **Review: two agents check what you actually owe, side by side.**
   - The **Nessie agent** reconciles the bill against your EOB and your bank transactions (Capital One Nessie). It finds insurance adjustments the hospital never applied, payments it never credited, and duplicate charges.
   - The **Bright Data agent** checks you against the hospital's own financial-assistance policy, which we crawled with Bright Data and verified quote by quote, plus the hospital's real prices. It works out your income from payroll deposits and finds the discount tier and payment-plan terms you qualify for.
   - Every check and finding appears live with its proof and source. Tap **Ask** on any finding to have the agent that found it explain it, or **This is wrong** to correct it.
3. **The plan.** A Decide agent turns the findings into a call plan: the money, who to call, and every ask the evidence backs, biggest money first. You can talk to the planner ("refund first", "I can only do $150 a month") and it makes a new version. It changes strategy, never facts.
4. **The call.** When you tap **Call now**, Remy phones billing. It says it's an automated assistant calling with your authorization, cites the evidence, asks for each correction, the discount and a hold on collections, and gets a case number. It never agrees to pay anything or gives card details.
5. **What's next.** After the call, Remy marks each ask: got it, refused, promised later, or no clear answer. It only counts a win it can back with the rep's own words. Then it plans the follow-up call with only what's left, and tracks every promise ("a corrected statement within 5 business days") so it knows when to call back.

**Demo cases** (synthetic patients; real hospitals, real policies, real prices):
- **Marcus, UCSF:** $4,174 → **$0**. His share is over 10% of his family's income, and UCSF's own policy writes it off.
- **Rosa, CPMC (Sutter Health):** $8,700 → **$600**. The insurance adjustment was never applied, and a $500 payment from her bank account was never credited. What's left goes on a 6 × $100 interest-free plan under Sutter's own billing policy. Remy also tells her honestly that she doesn't qualify for the write-off.
- **Same bill, different hospital, different answer:** each hospital's own policy decides what a patient owes.

## How we built it

**Money is computed, never generated.** Reconciliation, income from payroll deposits, percent of the 2026 federal poverty line, discount tiers, refunds and payment plans are deterministic Python. Claude extracts policy rules only with verbatim quotes, and our code rejects any quote it can't find on the source page. The AI can argue, but it can't invent a number.

**Our own agent harness:** every tool has a permission tier (read, act, consequential). Consequential tools, like placing a call or saving a payment plan, wait for the patient's approval. Every tool call and result is streamed to the case page and written to an audit log.

- **Anthropic Claude (Sonnet 5.5 and Haiku 5.5):** reads bills and EOBs; extracts policy rules with quotes; runs the live call with streaming tool use; answers "Ask" questions from one agent's evidence only; turns planner chat into strategy changes that our rules check; and reads the finished call to mark each ask, but a mark only counts if its quote really appears in the rep's words.
- **Bright Data (SERP API and Web Unlocker):** finds and fetches each hospital's financial-assistance pages and policy PDFs into a versioned policy database for 12 Bay Area hospitals. Every rule is verified against its source, and the database detects when a policy changes.
- **Capital One Nessie:** the patient's accounts, payroll deposits (income), purchases (payments to the hospital and duplicate charges), and an approved payment plan written back as a bill.
- **Cartesia:** our own phone pipeline on Twilio Media Streams. **Ink** does streaming speech-to-text with key terms (names, insurer, "EOB"). **Sonic** streams text-to-speech with a voice and accent picker. A pronunciation layer makes claim numbers, billing codes, drug names, money and dates sound the way a billing rep expects. Claude's reply streams into Sonic sentence by sentence, and we start Claude early on Ink's `eager_end`. The agent stops talking when the rep interrupts.
- **Also:** real prices for 11 hospitals from their public CMS price-transparency files; FastAPI and WebSockets; a vanilla-JS case page that streams every step live.

## Challenges we ran into

- **Trusting an LLM with money.** Our first version let the model state numbers. We moved every figure into code with a source, and made Claude quote policy text word for word.
- **Latency on a live call.** Replies took 1.4 to 2.7 seconds per turn. Streaming Claude into Sonic sentence by sentence, starting early on `eager_end`, and prompt caching brought first audio under one second.
- **Hospital websites.** Bright Data returns 200 even when the target fails (the real status is in the headers), search sometimes returns only redirect links, and policies hide in PDFs. We crawl each hospital's own site and PDFs and verify every quote.
- **Knowing what a call actually won.** Billing reps say "I'll make a note of that" a lot. The scorecard only counts a win backed by the call record or the rep's own words.
- **We ran out of Cartesia credits** mid-hackathon while measuring latency, so the post-call steps were built and tested against recorded and fixture calls.

## Accomplishments that we're proud of

- **Every number on screen traces to a source:** a bill line, an EOB line, a bank transaction, or a policy clause.
- **12 Bay Area hospitals** in a policy database built from live crawls, with every rule verified against its source, and real price tables for 11 of them.
- **Voice replies in 0.44 to 0.8 seconds** after the rep stops talking (phone simulator). In desk mode the median went from 3.0 s to 0.93 s.
- **281 automated tests**, all offline: money math, both review agents, the plan rules, the call scorecard and the follow-up flow.
- **A full loop:** audit → plan → call → score → next call, with a human approving every consequential step.

## What we learned

The hospital's own policy is the strongest argument. A billing rep can't argue with their own hospital's written rules, quoted word for word. The hard part isn't the AI. It's getting the right facts in front of the right person, and then holding them to what they promised.

## What's real and what's simulated

- **Real:** Bright Data crawls of real hospital policies, real prices from hospitals' public price files, Nessie API records, Claude's reasoning, Cartesia speech-to-text and text-to-speech, the approval gate, the audit log, and the 2026 poverty guidelines.
- **Simulated:** the patients, bills, EOBs and bank histories are synthetic (the PDFs are watermarked SAMPLE). In the demo, a teammate plays the hospital billing rep.
- **Not yet done live:** a real phone call over Twilio. The pipeline is tested against simulated Twilio and Cartesia streams.

## Known limitations

- **Reps may refuse to talk to an AI or a third party.** The patient can be conferenced in to give verbal permission (HIPAA 45 CFR 164.510(b)). Next: a signed authorization per hospital, and a letter fallback.
- **Hold queues and phone menus** aren't handled yet. Next: keypad tones, and staying quiet through hold music.
- **Income comes from bank deposits** (take-home pay, and gig income is missed). Next: ask for a pay stub.
- **An insured patient who uploads only the bill** is treated as uninsured. Next: ask "Do you have insurance?"
- **Speech-to-text can mishear claim numbers.** Next: read IDs back for confirmation.
- **California-first.** The discount rules lean on California law; each new state is a new rulebook on the same engine.

**Responsible use:** Remy always says it's automated, never pays or agrees to pay, never gives card details, asks the patient's approval before calling, uses read-only bank access, and keeps an audit log the patient can see.

## How we're different

Medical-bill help today is either a human advocate who takes 20 to 35% of what they save, or a nonprofit that files a paper charity-care application and waits. AI tools mostly write letters and leave the phone call to you, and general AI calling agents don't audit the bill or know charity-care law. Remy is the first patient-side agent we know of that does the whole loop: it checks the bill against your insurance statement **and your own bank records**, works out the discount the hospital's own policy and California law say you're owed, makes the call, and follows up until it's done.

## What's next for Remy

- **A pilot with a patient-advocacy nonprofit**, free for patients who qualify for charity care.
- **Signed HIPAA authorization, a letter fallback and phone-menu navigation**, so it works on real hospital lines.
- **Beyond hospitals:** the engine is general. It reconciles the bill against your own records, finds the provider's own rule, calls with the evidence, and holds them to the promise. Next are insurance claim denials (fewer than 1% of denied marketplace claims are ever appealed, [KFF](https://www.kff.org/private-insurance/claims-denials-and-appeals-in-aca-marketplace-plans-in-2023/)), then telecom and utility bills, then business invoices.

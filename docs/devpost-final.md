# Remy

Your bill. Your rights. Your money.

## Inspiration

A few months ago, one of our teammates fractured his wrist after falling off an electric scooter. He went to the emergency room, got a couple of X-rays and a splint, and went home.

Then came a bill for $7,420.

As a college student, his first reaction was simple: "I don't have seven thousand dollars, but I guess I have to pay it." Only later did we learn that the hospital publishes a financial-assistance policy that could have reduced the eligible hospital balance to zero.

The help existed. The patient just had to know where to find it, understand pages of eligibility rules, gather the right evidence, call the right department, and say exactly the right thing.

This problem goes far beyond one bill. A KFF survey found that roughly four in ten American adults have some form of health care debt. Dollar For estimates that hospitals bill patients for at least $14 billion a year in care that could qualify for financial assistance. ([KFF](https://www.kff.org/health-costs/kff-health-care-debt-survey/), [KFF Health News](https://kffhealthnews.org/podcast/an-arm-and-a-leg-podcast-charity-care-nonprofit-dollar-for-medical-bills/))

Hospitals already publish charity-care programs, discounts and collections protections. But those policies are scattered across hospital websites, buried in PDFs, and written in language most patients never have time to decode.

So we built Remy: an AI agent that helps patients find, verify, and actually use the assistance they may already be entitled to, and then makes the call to get it applied.

### From sketches to Remy

Before writing code, we explored several possible agent workflows. Our earliest Notability sessions compared enterprise financial agents, multi-company data workflows, voice-first interfaces, and automated research systems.

![Early UI concepts for several possible financial-agent projects](https://d112y698adiu2z.cloudfront.net/photos/production/software_photos/005/682/666/datas/small.png)

We recorded voice memos alongside the sketches so we could revisit our discussions and judge each idea against the same questions: Was the problem meaningful? Did an agent provide real value? Could we access trustworthy data? Would voice improve the experience rather than exist as a gimmick?

Those conversations led us away from a broad financial assistant and toward a much more concrete problem: helping one person understand and challenge an overwhelming hospital bill.

We then mapped the complete patient journey, from uploading a bill and insurance statement, through parallel financial and policy research, to the approval gate, the billing call, the outcome review, and the follow-up.

![Remy architecture, patient flow, and demo-story planning](https://d112y698adiu2z.cloudfront.net/photos/production/software_photos/005/682/665/datas/small.png)

That flow became the architecture we built: Claude reads the documents, Nessie establishes the patient's financial picture, Bright Data finds the hospital's published policy, Cartesia and Twilio power the phone call, and the patient approves every consequential action.

We also planned the demo alongside the product. Our notes connect the personal opening, the supporting research, the live workflow and the final result, so the demo explains not only what Remy does, but why it needs to exist.

[Explore our complete Notability design process, including our original discussion recordings](https://notability.com/app/note/5cb23015-ad40-4a6f-ba36-c3c54bd9465c)

![Mapping thoughts with notability!](https://d112y698adiu2z.cloudfront.net/photos/production/software_photos/005/684/459/datas/small.png)

## What it does

Remy takes a patient from an overwhelming hospital bill to a documented resolution.

**1. Read the bill.** The patient uploads their hospital bill and insurance Explanation of Benefits (EOB). Claude reads them and extracts the provider, charges, balance, insurance payments, patient responsibility and claim details. Onboarding asks only the questions that can still change the answer: household size, roughly what the household earns before taxes, and insurance.

**2. Review: two agents run side by side**, and the patient watches every check happen live:

- **The Nessie agent** reconciles the bill against the insurance statement and the patient's bank history (Capital One Nessie). It finds insurance adjustments the hospital never applied, payments it never credited, lines billed twice, duplicate bank charges, and charges above the hospital's own published prices. It also **double-checks the income the patient told us** against their payroll deposits, so Remy never claims a discount the hospital would reject when it sees pay stubs.
- **The Bright Data agent** finds the hospital's real financial-assistance policy with Bright Data, extracts its income thresholds, charity-care tiers, high-medical-cost rules and collections protections, and works out what the patient qualifies for.

For our fictionalized UCLA case, Remy finds that UCLA Health's published policy offers a 100% charity-care discount to self-pay patients with family income at or below 400% of the federal poverty level, and to insured patients at that income whose medical costs exceed 10% of family income, provided they live in the hospital's primary service area. The patient's simulated profile meets those conditions, so the $7,420 balance may be reduced to $0, subject to the hospital's formal review.

We do not let the model invent or silently reinterpret a policy. Claude extracts the language, and our code checks every quote word for word against the source document. If the quote can't be found in the source, Remy rejects it.

**3. The plan.** Remy builds a transparent call plan:

- what the hospital says is owed, and what the evidence says the patient actually owes
- every request Remy will make, biggest money first, each linked to the finding and source that backs it
- who to call, and how Remy decided

Nothing happens invisibly. The patient can tap "This is wrong" on any finding, ask any agent why it concluded something, or tell the planner to change strategy ("ask for a supervisor if they say no"). Every change makes a new plan version they can compare or roll back. Then they approve the call, or don't.

**4. The call.** Remy phones hospital billing through Twilio, using our own voice pipeline. It says it's an automated assistant calling with the patient's authorization, raises one issue at a time, cites the evidence only when it's needed, asks for the review and a hold on collections, and gets a reference number. When the rep explains a process ("the patient has to submit the form on our website"), Remy records it as a next step and moves on instead of arguing.

**5. What's next.** After the call, Remy reads the transcript and marks every request as granted, refused, promised for later, or unclear. A win only counts if it's backed by the call record or the representative's own words, which Remy quotes. Then it builds the next call's plan with only what's still open, and schedules the follow-up.

Remy never makes a payment, gives bank or card details, or commits the patient to a payment plan. The patient stays in control throughout.

## How we built it

Remy is a Python app built with FastAPI, WebSockets and a vanilla JavaScript interface. The backend runs a fixed, auditable workflow:

Document intake → financial audit and policy research → plan → patient approval → call → outcome scoring → follow-up

We deliberately separate language-model reasoning from financial calculation.

Claude handles what needs language understanding: reading documents, extracting policy provisions, explaining findings, running the billing conversation, and interpreting what the representative promised.

All money math is deterministic Python: insurance reconciliation, credited and uncredited payments, income annualization, federal-poverty-level percentages (2026 guidelines), charity-care discounts and refunds. No number comes from a language model.

**Guardrails are enforced in code, not just in the prompt.** Before Remy agrees to anything a representative offers, or gives out any piece of information, it must check the offer against the plan the patient approved. That check is code, and it fails closed. **Every sentence Remy speaks is also screened before it reaches the phone line.** Remy can't read out anything that looks like a Social Security or card number, agree to a payment the patient didn't approve, or claim to be human, even if the model tries. Every decision appears in the live transcript and the audit log.

Our integrations:

- **Anthropic Claude** reads bills and EOBs, extracts policy rules with verbatim quotes, explains evidence, runs the call dialogue, and reads the call outcome.
- **Bright Data**'s SERP API and Web Unlocker find and fetch hospital financial-assistance pages and policy PDFs. The results feed a versioned policy database with quote verification and change detection. **Anyone can add a hospital:** type its name on the Hospitals page and watch Bright Data search, fetch and verify its policy live, in under a minute. We added Duke University Hospital this way: 5 tiers verified using 4 requests.
- **Capital One Nessie** holds the patient's simulated bank data: payroll deposits, hospital payments, recurring bills and duplicate transactions.
- **Cartesia**: Ink provides streaming speech-to-text and Sonic provides low-latency text-to-speech. We added key terms and pronunciation handling for medical language, money, dates, claim numbers and procedure codes.
- **Twilio** connects Remy to a real phone line through Media Streams. A desk mode, where a teammate plays the billing representative in the browser, is the fallback.
- **Tiger Data** stores household profiles and income over time when configured.

Every case produces an append-only audit trail: tool calls, evidence, approvals, transcript lines and outcomes are streamed to the interface and saved, so the patient can see how Remy reached every conclusion. Read-only research runs automatically; anything consequential, especially placing a call, waits for the patient's approval.

To test whether the approach works beyond one case, we built a policy research pipeline across US hospitals. Our dataset today:

- **28 hospitals** in 12 states, from the Bay Area to Mayo Clinic, Cleveland Clinic, Johns Hopkins and NewYork-Presbyterian
- **18 hospitals** with at least one verified financial-assistance tier
- **32 tier quotations** accepted by automated source verification
- **2 rejected** because the quote couldn't be found in the source

We also applied the verified income limits to 2024 American Community Survey data for California. Depending on the hospital's threshold, roughly 22.1% to 47.8% of California families fall below the relevant income ceiling. That isn't guaranteed eligibility, since insurance status, medical expenses, residency and other rules also apply. But it shows how many households could benefit from simply being screened.

The whole system is covered by 537 automated tests, all of which run offline.

## Challenges we ran into

The first challenge was trusting AI around money. A wrong policy interpretation or an invented number could hurt the exact person we're trying to help. We redesigned the system so every financial result comes from deterministic code, and every policy claim needs a quote that can be found in the retrieved source.

The second was trusting AI on the phone. A representative is professionally good at talking people into things, like a payment plan or "just confirm the Social Security number." A prompt alone can be talked out of its rules, so we moved the rules into code: an offer check against the approved plan, and a final screen on every spoken sentence.

Hospital websites were another challenge. Policies appear in web pages, PDFs, application packets and health-system documents. Search results sometimes point to outdated or unofficial material, and some sites return a success response when the request actually failed. We built domain scoring, document ranking, PDF extraction, source verification, versioning and failure handling around the Bright Data pipeline. Many big hospitals state their help as a sliding scale rather than fixed tiers. Remy stores those policies but only applies discounts it can quote exactly.

Voice latency was especially noticeable live. A caller that pauses for several seconds after every response feels broken. We stream Claude's reply into Cartesia sentence by sentence, start drafting on likely end-of-turn signals, and use prompt caching. In our phone simulator, the first audio starts about 0.44–0.8 seconds after the representative stops speaking.

Getting a real phone call to work took its own debugging. Twilio's free trial blocks live audio streaming and sends the call-instructions request unsigned. We worked through each restriction until Remy was talking on a real phone.

Calls are also ambiguous. "I'll make a note" isn't the same as correcting a balance. Our scorecard marks each request as granted, refused, promised for later, or unclear, and requires evidence from the call record or the representative's own words.

Finally, eligibility is more nuanced than one income threshold. Policies distinguish insured from uninsured patients, add high-medical-cost and residency rules, exclude physician charges, or require documentation. So Remy presents an evidence-backed eligibility assessment, not a guarantee.

## Accomplishments that we're proud of

Remy does more than summarize a bill or draft a dispute letter. It connects the whole process:

audit the bill → find the policy → calculate the correction → get approval → make the call → record the outcome → follow up

Every important number traces back to a bill line, an insurance statement, a bank transaction, a hospital price file or a published policy clause.

We built a real policy-discovery pipeline instead of hardcoding a few discounts. It finds public hospital materials, extracts structured eligibility rules, verifies the supporting language, stores versions, detects changes, and can add any US hospital live.

We built our own low-latency voice stack with streaming transcription, streamed speech, interruption handling, phone-menu support, patient authorization, voice selection and pronunciation normalization, and it places real calls.

Most importantly, we kept the patient in control, and we enforce it in code. Remy discloses that it's automated, shows its evidence, waits for approval before calling, and can't agree to pay or reveal sensitive numbers on the patient's behalf.

## What's real and what's simulated

- **Real:** the hospital policies and price files (crawled with Bright Data), the eligibility rules and quote verification, the money math, Claude's reasoning, the Cartesia voice pipeline, real phone calls over Twilio, the approval gate and the audit log.
- **Simulated:** the patients, their bills, insurance statements and bank histories (Capital One Nessie sandbox). In the demo, a teammate plays the hospital billing representative.

## Known limitations

- Discounts are applied only where a hospital publishes fixed income tiers we can quote. Sliding-scale policies are stored but not yet applied.
- Without a stated income, Remy estimates it from bank deposits (take-home pay) and flags when that could change the result.
- It's US-only. The rules are built on US nonprofit hospitals' financial-assistance policies and the federal poverty line.
- When a representative refuses to talk to an automated caller, Remy asks them to note the account and records the next step. The patient can be brought into the call to give verbal permission.

## How Remy is different

Medical-bill help today is either a human advocate who takes 20–35% of what they save, a nonprofit that files a paper charity-care application and waits, or an AI tool that writes a letter and leaves the phone call to you. Remy does the whole loop. It checks the bill against your insurance statement and your own bank records, finds the discount the hospital's own policy says you're owed, makes the call with code-enforced guardrails, and follows up until it's done.

## What we learned

The hardest part of fighting a medical bill is rarely noticing that something feels wrong. It's assembling enough evidence to turn that feeling into a specific, defensible request.

The hospital's own written policy is often the strongest evidence. Instead of asking for a favor, a patient can point to a rule the hospital already publishes and ask for it to be applied.

Responsible AI systems need boundaries more than confidence. Claude is valuable for reading documents and navigating a conversation, but it shouldn't improvise financial figures, and it shouldn't be the only thing standing between a pushy representative and a patient's bank account. Moving those boundaries into code made Remy both more reliable and easier to explain.

Resolving a medical bill is never a single interaction. Representatives promise reviews, applications, callbacks and corrected statements. A useful agent has to remember those promises, know when they're due, and keep following up until the issue is resolved.

## What's next for Remy

- **More automation, still compliant.** Today, when a step needs the patient (like uploading documents to a hospital portal), Remy hands it back with clear instructions. Next, Remy can fill and submit those forms with the patient's approval.
- **Every state.** Wire in each state's hospital-policy filings the way we did California's, and read sliding-scale policies, not just fixed tiers.
- **The patient on the line.** Let the patient join the live call when a hospital insists on speaking to them.
- **More languages**, for patients and for the calls.
- **Beyond hospital bills.** The same engine (reconcile your records, find the provider's own rule, call with the evidence, hold them to the promise) applies to insurance claim denials, then utility and telecom bills.

## Built with

python, fastapi, websockets, javascript, html, css, claude, anthropic, bright-data, capital-one-nessie, cartesia, twilio, tiger-data, timescaledb, sqlite, postgresql

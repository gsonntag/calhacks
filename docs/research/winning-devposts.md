# What winning Devposts look like, and what BillBack should change

Researched 2026-10-25 (Cal Hacks 13.0 weekend). Web search and fetch only.

**How the sources were read.** Devpost returns HTTP 403 to direct fetches, and a browser-UA curl was blocked
as bot-check circumvention, so I did not use it. Project-page content below comes from search-engine
extracts of the Devpost pages (devpost.com/software/...), plus fetched blogs, news articles and Devpost's
own blog and help center. Section-level details are reliable. Exact wording and image counts are not
verified; open the links yourself before quoting any of them.

---

## 0. The rubric this year (Cal Hacks 13.0, from its Devpost page)

- **Five criteria (no published weights): Application, Intentionality, Functionality, Creativity,
  Technical Complexity.**
  - *Application*: could someone realistically adopt it and get a clear benefit? Name who it helps, the
    problem, and why they would actually use it.
  - *Intentionality*: UX and design rationale. Every choice purposeful, clear first principles, a clear
    sense of who it's for.
  - *Functionality*: the live demo runs smoothly. **A video demo on Devpost is needed for sponsors who
    judge asynchronously.**
  - *Creativity*: original framing or unexpected combinations, plus what sets it apart.
  - *Technical Complexity*: command of the frameworks and infrastructure, sound architecture, depth beyond
    a basic implementation.
- **Format: a 3-minute pitch plus 2 minutes of judge Q&A**, with a live or pre-recorded demo. Submission
  needs a project image and a GitHub repo. Hacks were due Oct 25, 9 AM PT.
- **Sponsor prizes relevant to us:** "Best Use of Nessie" ($1,200 cash plus a $300 Giftogram per member),
  "Best Use of Cartesia", plus Anthropic and Bright Data (listed sponsors; the 13.0 prize wording didn't
  come through in the extracts). At 12.0, Best Use of Claude was judged on technical complexity and
  creative use.
- Sources: https://cal-hacks-13-0.devpost.com/ , https://www.calhacks.io/

Implication: the write-up should hit all five criteria by name. Several judges in §3 say covering every
criterion beats excelling at one. Sponsor judges will often see **only the video and the text**.

---

## 1. Winning projects studied (10)

| # | Project | Event / prize | URL |
|---|---|---|---|
| 1 | **FaceTimeOS: AI Mac Agent** | Cal Hacks 12.0, **1st Overall** | https://devpost.com/software/facetime-macos-ai-agent |
| 2 | **Project No Cap** | Cal Hacks 12.0, **3rd Overall** | https://devpost.com/software/project-no-cap |
| 3 | **Orion** (wildfire command center, voice) | Cal Hacks 12.0, Best Use of Vapi + Best Use of Warp | https://devpost.com/software/orion-p5ayks |
| 4 | **Duet: Brainwaves -> Live Music** | Cal Hacks 11.0, **1st Overall** | https://devpost.com/software/duet-0tbkxe |
| 5 | **SnackSnap - recycle to feed your pet** | Cal Hacks 11.0, **3rd Overall** | https://devpost.com/software/snacksnap-lw4b9q |
| 6 | **Dispatch AI** (911 voice agent) | UC Berkeley AI Hackathon 2024 (Cal Hacks org), **Grand Prize** (1st of 293) + AI for Good + Best Use of Intel AI | https://devpost.com/software/dispatch-ai |
| 7 | **HawkWatch** | TreeHacks 2025, **Grand Prize ($11k)** | https://devpost.com/software/hawkwatch |
| 8 | **Shepherd** (smart cane) | TreeHacks 2026, **Grand Prize ($12k)** + OpenEvidence Healthcare grand prize + best clinical info | https://devpost.com/software/raising-cane |
| 9 | **Minerva** (AI tutor over video call) | TreeHacks 2026, Education 1st + Best HeyGen | https://devpost.com/software/minerva-3sj6z0 (team blog: https://www.evanyu.dev/blog/treehacks-2026) |
| 10 | **SensePlan** (voice agent that calls and negotiates for you) | A2A Agents Hackathon, Best Voice Agent with Vapi + Most Creative Use of Vapi | https://devpost.com/software/senseplan |

Searches for named HackMIT 2025, PennApps XXV/XXVI, LA Hacks 2025 and HackPrinceton 2025 winners (including
the Capital One "Best Financial Hack" and Best Healthcare Hack tracks) came back empty, because those
galleries can't be fetched. The list above covers the grand-prize, healthcare (Dispatch AI, Shepherd) and
voice-agent (Dispatch AI, Orion, SensePlan, FaceTimeOS) angles. It has no confirmed fintech winner.

### What each one does well

1. **FaceTimeOS (Cal Hacks 12.0, 1st).** It opens on a relatable pain: you're away from your computer and
   need a file. It gives a **concrete example command in quotes** ("Find the screen recording I made
   yesterday ... and upload it to Google Drive"). The build section is organised **by sponsor, one clause
   each, saying what that sponsor's tech does in the system**: Claude orchestrates, Fish Audio provides the
   virtual mic and speaker, Fetch.ai monitors activity, and Groq gives fast summaries. Challenges are about
   real-time integration (echo and latency in the audio routing). Accomplishments: "voice working end to
   end", plus the screenshot confirmation. What's next is a single big idea (proactive assistance). The
   winner's blog adds: *"Have a guy primarily focus on the UI and demo ... this is all the judges are going
   to see"*, *"always have a hardcoded fallback"*, and they **cut the agent's weak clicking out of the video
   so the product looked higher quality**. (https://blog.dylanlu.com/cal-hacks-12/)
2. **Project No Cap (Cal Hacks 12.0, 3rd).** It uses a **three-pillar structure** (written bias, spoken
   accuracy, debate) that makes a sprawling system scannable. Its candid challenges section ("original plan
   was very ambitious ... forced significant early changes") and its lesson (cross-checking several AI
   models improved accuracy) both read as honest and earned.
3. **Orion (Vapi + Warp).** The voice section lists **sample spoken commands** and names a
   domain-specific fix: wildfire terminology and context injection to make voice usable in a crisis. This
   matches our medical-pronunciation normaliser closely.
4. **Duet (Cal Hacks 11.0, 1st).** The tagline is an arrow: "Brainwaves -> Live Music". It names a specific
   beneficiary (music expression for **children with developmental disabilities**), and the stack is listed
   by role. The team's advice: "if you think it's really cool, it will show."
   (https://stanforddaily.com/2024/11/20/stanford-students-win-cal-hacks/)
5. **SnackSnap (Cal Hacks 11.0, 3rd).** The inspiration has **a single statistic** (regular trash cans
   outnumber recycling bins about two to one). The tagline gives the mechanism in five words. "How we
   built it" starts with design: they **sketched each page to plan the engagement loop**, which is
   Intentionality evidence.
6. **Dispatch AI (Berkeley AI Hackathon 2024, Grand Prize). This is the closest analogue to BillBack**:
   a voice agent in a high-stakes domain with a human in the loop. The tagline makes a promise: "An
   empathetic AI dispatcher for 911: real-time transcripts, emotion-aware triage, and a live operator
   cockpit." The inspiration has **hard numbers** (911 centres lose about 80% of staff within a year and
   run about 30% understaffed). It says explicitly that **a human dispatcher is always in the loop**. Its
   challenges include a **data-moat claim** ("there is no off-the-shelf 911 corpus, so we curated 518
   dispatcher transcripts ourselves"). What's next is **credible and domain-specific**: partner PSAPs,
   NENA/CJIS/SOC 2 alignment, and integration with CAD and NG911. It credits every sponsor (Retell,
   Twilio, Hume EVI, Intel, Mistral fine-tune).
7. **HawkWatch (TreeHacks 2025, GP).** The first line is one plain sentence saying what it does and for
   whom ("detects crime, suspicious activities and life threatening events such as fainting and
   choking"). "What it does" is a **feature bullet list**, and "How we built it" gives one line per
   technology (Gemini, TensorFlow.js, Resend).
8. **Shepherd (TreeHacks 2026, GP + Healthcare).** It makes a **cost claim** ("about 1/20 the cost of
   existing devices"). Challenges come with engineering specifics: hallway oscillation fixed by gap-seeking
   steering, **five rewrites** of the steering algorithm. Accomplishments come with **measured numbers**
   (end-to-end latency **under 50 ms**, and BLE "write without response" roughly halved latency). The
   insight line is one we could copy: "the hard part is deciding what to do about obstacles, not sensing
   them."
9. **Minerva (TreeHacks 2026, Education 1st).** The tech story is clear about what is theirs and what is a
   vendor's: own backend for the LLM and tool calls, HeyGen only for TTS and avatar. The team blog stresses
   that **"execution and presentation matter just as much as, if not more than, the technical
   implementation"** and that you should test the demo's audio and room. Their demo nearly broke while they
   were recording the video, 5 minutes before the deadline.
10. **SensePlan (voice agent that negotiates for you).** It won voice-sponsor prizes by **framing the call
    as the product**: the agent calls and negotiates on your behalf, and the write-up shows the call.

---

## 2. Common patterns

**Section order.** Almost all of them keep Devpost's default template (an MLH coach says don't delete the
headings; https://dev.to/jacklyn/how-to-make-your-devpost-submissions-not-suck-37i0):
Inspiration → What it does → How we built it → Challenges we ran into → Accomplishments that we're proud
of → What we learned → What's next. Stronger ones add a short **architecture diagram** in How we built it
and keep "What it does" as bullets or numbered steps.

**Length.** Roughly 400–900 words. Each section is one or two short paragraphs or 3–6 bullets. Grand-prize
pages aren't long; they are dense with specifics. Judges have about 5 minutes per project (dev.to MLH
coach).

**Headline and tagline.** It names the user and the outcome in under 140 characters, often with an arrow
or colon: "Brainwaves -> Live Music"; "An empathetic AI dispatcher for 911: ...". The first sentence of the
story repeats the plain-English what-it-does.

**Opening hook.** Either one person's concrete situation (FaceTimeOS) or one or two hard statistics
(Dispatch AI: 80% attrition, 30% understaffed; SnackSnap: 2:1 bins). The best ones use **both**: a person,
then the scale.

**Impact numbers.** They come in three kinds: (a) problem-scale stats in Inspiration; (b) the
**demo-outcome number** (before → after); (c) **engineering measurements** in Accomplishments (latency,
counts, dataset size: Shepherd's <50 ms, Dispatch AI's 518 transcripts). Winners rarely claim user
traction; they claim measured system behaviour.

**Sponsor credit.** It's done inline in "How we built it": **one line per sponsor saying what that tech
does in the system and why it was chosen**, often bolded. Put judging-relevant information first, because
sponsor judges scan for their product (dev.to MLH coach). The best ones say what they built themselves on
top of the vendor (Minerva, Orion).

**Limitations.** These are rarely a separate section on the page. Winners put limitations inside
Challenges ("we cut X", "computer use struggled with clicking") and state safety boundaries plainly
("human dispatcher always in the loop"). In regulated domains (Dispatch AI) the compliance path goes in
What's next. Note: the Cartesia track this year asks for a "Known limitations" section (from our PLAN.md),
so we should include one.

**What's next.** It's short and **domain-credible**: named partners, standards and integrations (Dispatch
AI: PSAPs, NENA, CJIS, SOC 2, CAD), or one big product idea (FaceTimeOS: proactive assistance). Avoid a
laundry list.

**Tone.** First person plural, confident, plain, and a little personal ("we", "our"). The voice is
engineering-honest, not marketing.

**Images and gallery.** The thumbnail is a 3:2 JPG/PNG/GIF of at most 5 MB (Devpost help center), usually
a hero product screenshot with the name or tagline overlaid, not a logo. The gallery holds 4–8 images:
hero UI, the key "aha" screen, an architecture diagram, one or two flow screenshots, and sometimes a team
photo. A winner who has won 22 times recommends **GIFs and embedded images inside the story text** and
markdown bold on keywords (https://info.devpost.com/blog/user-story-nathan). Devpost recommends "a clean
architecture diagram" so judges see how the parts fit together.

**Demo video (what winners and guides converge on).**
- **Length:** 2–3 min. Most hackathons cap at about 3 min, and some judges only watch the first 2
  (https://info.devpost.com/blog/6-tips-for-making-a-hackathon-demo-video;
  https://googlecloudmultiagents.devpost.com/updates/36648-tips-for-your-demo-video).
- **Order:** a 5–15 s hook that says what it does → show it working within 10–15 s, with no title screens
  → the core flow on real-looking input → a brief "how it works" with the diagram → impact and what's next
  → close with the one-line pitch.
- **Format:** a screencast with narration, not a marketing reel (https://help.devpost.com/article/84-video-making-best-practices).
- **Balance:** about 60% explaining and 40% demoing (Nathan). Another guide's 3-min split is 30 s problem
  and solution, 90 s demo, 30 s stack, 30 s next.
- **Editing:** cut every load and wait, use jump cuts, don't speed up the audio, and use the best
  microphone on the team.

---

## 3. Advice sources (summarised)

- **Devpost, "6 tips for a winning demo video"**
  (https://info.devpost.com/blog/6-tips-for-making-a-hackathon-demo-video): start early and leave 2–3 h
  of buffer. Write a script; one winner drafts the Devpost story first and uses it as the script. Plan
  timing by segment: intro, idea, components, demo, close. Answer "what's the idea, what does it do, what
  stack". Put the elevator pitch in the first few seconds. Show it running on sample input. Address the
  judging criteria and future potential. Keep it visual and the tech details brief. Use OBS, QuickTime or
  a phone recorder. Upload to YouTube as unlisted or public, mark it "Not made for kids", and check the
  privacy setting.
- **Devpost help center, video best practices**
  (https://help.devpost.com/article/84-video-making-best-practices): open "like a movie trailer". In the
  first seconds say what the app does and how it fits this hackathon. Use a screencast with narration.
  Rehearse, do multiple takes, and edit out the messy parts. Uploads can take hours.
- **Devpost, "How to win: advice from 5 judges"** (https://info.devpost.com/blog/hackathon-judging-tips):
  Square's judge says the video "gives the most amount of scope" and is the first sign of effort; text
  and video must complement each other; address *all* criteria; a back-end with no UI is a negative.
  Databricks' judge: "err on more details"; narrate the components; a polished page with thin substance
  is a red flag. NEAR's judge: a startup mindset and understanding of the problem; domain expertise can
  beat technical polish. Atlassian's judge: "would I install and use this?"; it must nail the prompt.
- **Devpost, judging criteria explainer**
  (https://info.devpost.com/blog/understanding-hackathon-submission-and-judging-criteria): judges score
  only against the organiser's criteria. If you can't explain how to use the project in a sentence or
  two, it's too complicated. Read the rules page carefully.
- **Devpost, Nathan (22-time winner)** (https://info.devpost.com/blog/user-story-nathan): spend at least
  15% of the hackathon on polishing the submission; put GIFs and images in the text and bold the
  keywords; split the video 60/40 between explaining and demoing; keep a backup video; speak clearly and
  not too fast; "how the project is presented often carries more weight".
- **Devpost, Victor (healthcare winner)** (https://info.devpost.com/blog/user-story-victor): a strong
  submission has good documentation, a good project and good multimedia; make it scannable so "a judge
  can quickly identify that your project deserves to win"; pitch the path to a viable company.
- **MLH coach and judge of about 20 hackathons**
  (https://dev.to/jacklyn/how-to-make-your-devpost-submissions-not-suck-37i0): keep the template
  headings; put sponsor-relevant information first (which product, why, what it does); tailor the text to
  the public criteria; judges get about 5 min per project.
- **Cal Hacks 12.0 winner's blog** (https://blog.dylanlu.com/cal-hacks-12/): build something cooler than
  last year's winner; one person owns UI and demo; keep a hardcoded fallback; hide weak capabilities in the
  video.
- **TreeHacks 2026 winner's blog** (https://www.evanyu.dev/blog/treehacks-2026): presentation matters as
  much as or more than tech; loop judges and sponsors in early; test audio and the room before judging;
  don't record the video in the last 5 minutes.
- **Other Devpost hackathon updates** (WebMCP, Google ADK, Nebius, quoted in search extracts): show it
  working in 10–15 s; skip intros; cut all waiting; some judges only watch the first 2 minutes; get to
  the "aha" fast (about 30 s into the demo).

---

## 4. BillBack draft vs winners

**What the draft already does well**
- A strong, concrete hook ($8,700 "final notice" for an insured ER visit), and a hero outcome number
  ($8,700 → $1,100 → $600 on a 6 × $100 interest-free plan).
- An honest result: "She isn't eligible for Sutter's write-off, and BillBack says so." That earns trust
  and Intentionality points.
- The same-bill-at-four-hospitals comparison (Marcus: $0 vs $5,600) is a genuinely creative,
  data-driven moment.
- Safety posture: permission tiers, an approval gate, "never agrees to pay", and an audit log. This
  matches Dispatch AI's "human always in the loop" and covers Intentionality and Application.
- The challenges are specific and engineering-real (Nessie quirks, Bright Data 200s, barge-in), the same
  register as Shepherd and FaceTimeOS.
- "Why it's feasible" with 501(r), the CA Fair Pricing Act and HIPAA 164.510(b) is better than most
  winners' feasibility content and directly serves **Application**.

**What it's missing compared with winners**
1. **No tagline or elevator pitch, and no plain first sentence.** Winners lead with one line of 140
   characters or less.
2. **No problem-scale statistic.** It has the person but not the scale (medical debt prevalence,
   billing-error rates, charity care that goes unclaimed). Get each number from a primary source: KFF,
   CFPB, IRS 501(r) data or JAMA studies, and verify every one.
3. **No images or diagram plan.** There's no architecture diagram, no screenshots or GIFs in the text and
   no thumbnail. The docs folder has `usecases.png`, but the story uses nothing.
4. **No measured engineering numbers**, even though we have them: voice turn latency 0.44–1.1 s (10 of 11
   turns under 0.8 s), desk mode 3.0 s → 0.93 s, 281 tests, 12 hospitals in the policy DB with verified
   quotes, 11 real price tables (from PLAN.md and voice.md). Shepherd and Dispatch AI win with exactly this
   kind of line.
5. **Sponsor credit is buried in one dense bullet.** Each sponsor should get a bolded line saying what it
   does, why, and what we built on top. Sponsor judges will read only their own line.
6. **No "Real vs simulated" or "Known limitations" section.** DEMO.md has this. The Cartesia track asks
   for it, and Q&A will probe it.
7. **The rubric isn't addressed explicitly.** Application, Intentionality and Creativity are present but
   implicit.
8. **Accomplishments and What we learned are one-liners.** Winners put their numbers and insight lines
   here.
9. **Inconsistencies to fix:** the draft says "four San Francisco hospitals" but PLAN.md says 12 in the DB
   (DEMO.md also says 4). The hospital is named as both Sutter and CPMC (fine, since CPMC is Sutter, but say
   "CPMC (Sutter Health)" once). DEMO.md is a 3-minute script and PLAN.md says "judging is 2". The
   Devpost page says a 3-min pitch plus 2-min Q&A, so check with the organisers or the table judge.
10. **Watch the field.** At least one same-event team (Docket,
    https://github.com/AnushaAgarwal27/Docket) has a near-identical frame: "finds money they're owed,
    proves every rule with the official clause, and follows up until it's fixed", on the same sponsors
    (Anthropic, Cartesia, Nessie, Bright Data). Our differentiators need to be explicit: a **real outbound
    phone negotiation**, **real hospital policies crawled and verified**, and **bank-verified payments**.

---

## 5. Recommended Devpost outline for BillBack

**Title:** BillBack
**Tagline (≤140 chars), options:**
- "An AI agent that audits your hospital bill, finds the hospital's own discount rules, and calls billing to fix it."
- "$8,700 → $600: BillBack audits hospital bills against your insurance and bank, then phones billing for you."

**Thumbnail (3:2):** the Resolved screen with **$8,700 struck through → $600**, "collections paused",
"case FA 20931", and the BillBack wordmark. It should be legible at small gallery size.

**Gallery (6–8 images, in order):** (1) the Resolved hero; (2) Review screen with the Auditor and Policy
checklists side by side; (3) the Approval screen ("Here's exactly what BillBack will ask for"); (4) the live
call transcript with citations highlighted; (5) the architecture diagram; (6) the policy DB page showing
verified quotes and source links; (7) the same bill at different hospitals ($0 vs $5,600); (8) the
follow-up and cases dashboard.

**Story sections (target 700–900 words):**
1. **Inspiration (80–120 words).** Rosa's $8,700 final notice, then one or two verified scale stats, then
   the line "the fixes exist ... but getting them takes hours on hold and knowing exactly what to ask".
2. **What it does (150 words + GIF).** Keep our four numbered steps (Prove / Find / Call / Follow up), one
   or two lines each, with the key verb bolded. Put a 5–8 s GIF of the call transcript after step 3. End
   with the demo result in bold: **$8,700 → $1,100 → $600, 6 × $100 at 0%, collections paused, case
   number**, plus the honest "not eligible for the write-off, and BillBack says so", plus the Marcus
   comparison as one sentence with the image.
3. **How we built it (200 words + diagram).** The diagram first. Then the harness (permission tiers,
   approval gate, audit log), then the pipeline, then "the model never invents a number". Then a
   **sponsor block, one bolded line each**:
   - **Claude:** vision intake, the call brain (tool-use loop), follow-up reasoning. Say which model is
     used where.
   - **Cartesia:** Ink STT and Sonic TTS in our own Twilio Media Streams pipeline: barge-in, a voice and
     accent picker, a medical pronunciation normaliser, **0.44–1.1 s turns**.
   - **Bright Data:** SERP plus Web Unlocker crawl of each hospital's own policy pages and PDFs, a
     versioned DB, quote verification, change detection. **12 hospitals.**
   - **Capital One Nessie:** payments the hospital missed, income from payroll deposits, and the
     interest-free plan written back as a recurring bill.
4. **How it maps to the judging criteria (5 bullets, optional but recommended).** One line each:
   Application (who adopts it, and the 501(r) and HIPAA path), Intentionality (approval gate, never pays,
   says when you're not eligible), Functionality (real call, real crawls), Creativity (the hospital's own
   policy used as the argument; the same bill gets a different answer at each hospital), Technical
   Complexity (our own voice pipeline and agent harness).
5. **Challenges (4 bullets).** Keep the current ones. Add one about deciding *what* to argue on the call,
   as distinct from what to say.
6. **Accomplishments (3–4 bullets with numbers).** A real phone negotiation driven by real bank data;
   every number traceable to a source line; latency figures; 281 tests; 12 hospitals with verified quotes.
7. **What we learned (2–3 lines).** Use a Shepherd-style insight: "Finding the error is easy; getting the
   right fact in front of the right person on the phone is the hard part." Also: a hospital's own policy
   beats any generic argument.
8. **Real vs simulated / Known limitations (5 bullets).** The patient, bill, EOB and bank history are
   synthetic (watermarked). "Billing" is a teammate. Nessie is a sandbox. Policy coverage is 12 Bay Area
   hospitals. The authorization is a warm handoff, not a signed form. PHI retention notes.
9. **Why it's feasible (keep it, tightened to about 80 words).** Bank aggregators, 501(r) and the CA Fair
   Pricing Act, HIPAA 164.510(b), with a link to `docs/feasibility.md`.
10. **What's next (3–4 bullets, domain-credible).** A signed HIPAA authorization on file; Plaid/MX feeds;
    pre-filled financial-assistance applications and dispute letters; statewide policy coverage; a
    Spanish-speaking patient side. Name a partner type if you can (patient advocates, legal-aid clinics,
    hospital financial counsellors).
11. **Built with.** Keep the list, and add `twilio-media-streams` and `claude-vision` only if Devpost has
    those tags.

Links: GitHub repo (required), video, and the live URL if one exists.

---

## 6. Recommended demo video

Principles: a screencast with narration and real UI only, no slides except one diagram frame. Hard-cut all
waiting. Put captions on the hero numbers. The call is the "wow", so play real call audio with a scrolling
transcript. Record it today, not at the deadline. Upload unlisted, set "Not made for kids", and check
playback logged out.

### 2-minute version (primary)

| Time | Segment | On screen | Narration and audio |
|---|---|---|---|
| 0:00–0:10 | Hook | The printed $8,700 final notice, then a cut to the BillBack upload | "Rosa got an $8,700 final notice for an ER visit her insurance was supposed to cover. BillBack fixes bills like this, and makes the phone call for you." |
| 0:10–0:20 | Drop it in | Drag the bill and EOB, click Go (jump cut past loading) | "She drops in the bill and her insurance statement. That's all." |
| 0:20–0:45 | Audit + policy | Auditor and Policy checklists filling side by side; captions $1,100 → $600 and "$500 already paid" | "The auditor finds the insurance adjustment the hospital never applied, and a $500 payment in her bank the bill doesn't show. The policy agent reads Sutter's own published policy: she's not eligible for a write-off, and it says so, but Sutter requires interest-free payment plans." |
| 0:45–0:55 | Approval | The approval card listing exactly what will be asked; voice picker | "Nothing happens without her OK, and it will never agree to pay on a call." |
| 0:55–1:30 | The call (wow) | Live call with a scrolling transcript; citation chips highlight | Real audio about 25 s: the rep pushes back, BillBack cites the claim number, the payment reference and the policy line, then gets case FA 20931 and a collections pause. |
| 1:30–1:40 | Resolved | **$8,700 struck through → $600**, 6 × $100 at 0%, collections paused, the plan saved in Nessie | "Eight thousand seven hundred down to six hundred, interest-free, collections stopped." |
| 1:40–1:52 | How it works | Architecture diagram frame; sponsor logos placed on their boxes | "Our own agent harness with approval gates; deterministic money math; Bright Data-crawled hospital policies with verified quotes; Cartesia speech on our own Twilio pipeline at under a second a turn; Capital One Nessie for the bank side; Claude as the brain." |
| 1:52–2:00 | Close | Same bill at different hospitals ($0 vs $5,600), then the logo and tagline | "Same bill, different hospital, different answer. BillBack fights your hospital bill so you don't have to." |

### 3-minute fallback (if the full 3 min is allowed)

| Time | Segment | Additions over the 2-min cut |
|---|---|---|
| 0:00–0:15 | Hook | Add one verified scale statistic after Rosa. |
| 0:15–0:30 | Drop it in | Same as the 2-min version. |
| 0:30–1:05 | Audit + policy | Show the source quote link on the policy card (Bright Data) and the income calculation from payroll deposits. |
| 1:05–1:15 | Approval | Play the voice and accent preview for a second. |
| 1:15–2:05 | The call | A longer call: two pushbacks ("full amount is due", "can you pay by card today?") and BillBack's refusal. |
| 2:05–2:20 | Resolved | Nessie recurring bill, follow-up date set. |
| 2:20–2:35 | Follow-up | Cases dashboard → +1 week → overdue → "Call again" opens with "On the October 24th call, Diane opened case FA 20931...". |
| 2:35–2:50 | How it works / real vs simulated | The diagram, plus one line: "Real: hospital policies, the bank records API, speech, approvals. Simulated: the patient and the billing rep." |
| 2:50–3:00 | Close | The Marcus comparison and the tagline. |

The 3:00 cut fits the 3-minute live pitch, which leaves the 2-minute Q&A for `docs/judge-qa.md` material.

---

## 7. Top 5 changes to the draft

1. **Add a tagline and a plain first sentence**, plus one or two verified problem-scale statistics in
   Inspiration. Pair the person (Rosa) with the scale, the way Dispatch AI does.
2. **Add visuals.** Use a 3:2 thumbnail of the $8,700 → $600 Resolved screen, an architecture diagram in
   "How we built it", a 5–8 s call-transcript GIF in "What it does", and 6–8 gallery images in the order
   in §5.
3. **Rewrite sponsor credit as a bolded one-line-per-sponsor block** (Claude, Cartesia, Bright Data,
   Nessie): what it does, why we chose it, what we built on top, with a number each (latency, 12
   hospitals, verified quotes).
4. **Put measured numbers in Accomplishments**: voice turns 0.44–1.1 s, desk mode 3.0 → 0.93 s, 281 tests,
   12 hospitals with verified quotes, 11 price tables. Add a **Real vs simulated / Known limitations**
   section taken from DEMO.md; the Cartesia track asks for one.
5. **Map the story to the Cal Hacks 13.0 rubric** with five one-liners (Application, Intentionality,
   Functionality, Creativity, Technical Complexity). Fix the inconsistencies (4 vs 12 hospitals, the
   CPMC/Sutter naming, 2-min vs 3-min). Make our differences from look-alike entries such as Docket
   explicit: a real outbound call, crawled and verified hospital policies, and bank-verified payments.

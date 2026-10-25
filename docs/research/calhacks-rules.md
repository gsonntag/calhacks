# Cal Hacks 13.0 (Oct 23–25, 2026): rules, prizes, and BillBack gaps

Researched 2026-10-25 around 00:30 PDT. Only web pages were read; no paid APIs were called.
Raw pages saved next to this file: `cal-hacks-13-0.html`, `c13*.txt`, `hive_*.json`, `docs/*.txt`.

## 0. Key facts (read first)

- **Event:** confirmed as **Cal Hacks 13.0**, run by Hackathons @ Berkeley at the Palace of Fine Arts, SF.
  Presented by Anthropic, Annapurna Labs, Spacetime and Hyphenate. Sources: https://cal-hacks-13-0.devpost.com/ and https://www.calhacks.io/
- **The submission deadline has not passed yet.** It is **Sun Oct 25, 9:00 AM PDT** and it is a hard
  deadline ("This is a hard deadline"). The Devpost submission window runs Oct 23, 6:00 PM PDT to
  Oct 25, 9:00 AM PDT. Our project was submitted Oct 24 at 6 PM, so it can still be edited until 9:00 AM.
  Source: https://cal-hacks-13-0.devpost.com/details/dates
- **Schedule** (from https://hive.hackberkeley.org/api/live/schedule, which feeds the Hive schedule page):
  - Hacking ends 9:00 AM PDT.
  - **Judging: 10:30 AM–12:30 PM PDT**, in the "Main Hacking Space & Mezzanine". Judges walk to each table.
  - Closing ceremony and winners: 2:00–4:00 PM PDT. Devpost says winners are announced at 4:00 PM.
- **Pitch length:**
  - Devpost: "You will have **three minutes to pitch and two minutes** for judges to ask questions."
  - Hive FAQ: "All presentations must be 5 minutes or under, as judges only have 5 minutes to spend at a
    table."
  - The "2 minutes" we heard is Cal Hacks 11.0/12.0's rule (2-minute pitch plus 2 minutes of Q&A). For
    13.0 it is only the **Cartesia** rule ("two-minute, demo-first presentation") and **Bright Data**'s
    advice ("a clear two-minute story").
  - So prepare a 2-minute demo-first core with a 1-minute extension for the main judges.
- **Devpost edits after the deadline:** the 13.0 page gives no grace window. 12.0 allowed one extra hour
  of editing (but not the table number); 11.0 did the same. Expect 13.0 to lock at 9:00 AM, and finish
  every edit before then.

## 1. Submission requirements (Cal Hacks 13.0)

Requirements, quoted from https://cal-hacks-13-0.devpost.com/:
- "Get to the venue by October 25th @ 8 AM PST. You must've had been accepted and checked in as a hacker to submit."
- "You must submit your hack by October 25th @ 9 AM PST with your teammates! This is a hard deadline."
- "**Include an image of your project and GitHub repository for any code to be considered.**"
- "All work must be done during the hackathon"
- "You will have three minutes to pitch and two minutes for judges to ask questions."
- "You must have a pre-recorded or live demonstration."
- Eligibility: ages 18+, college students only, a team of 2 to 4 members. The Hive FAQ says teams of 1–4,
  and there is a Best Solo prize.

From the Hive FAQ (https://hive.hackberkeley.org/faq, data at https://hive.hackberkeley.org/api/live/faq):
- "All submissions must include a **2-3 sentence elevator pitch / summary, team location (your table
  number), and the team name**."
- No prior work is allowed. Ideation before the event is fine, but "We have strict measures in place to
  detect cheating or prior work". This is why the git history matters.

On video, the Judging Criteria (Functionality) say: "Submissions should also include a **clear, meaningful
video demo on devpost for sponsors evaluating asynchronously**."
- Cal Hacks sets **no maximum video length**.
- The only stated cap, Atlassian's "3-minute max demo video", applies to its own track, which we don't fit.
- Recommendation: keep the video **≤ 2 minutes, demo-first**. That matches Cartesia's format and Bright
  Data's "two-minute story", and Hyphenate asks for a video "that opens with your moment".
- Devpost takes a YouTube, Vimeo or Youku link.

Devpost's standard project fields:
- Project name, tagline (use it for the elevator pitch), and the "About the project" story with the
  headings Inspiration / What it does / How we built it / Challenges / Accomplishments / What we learned /
  What's next.
- "Built with" tags.
- "Try it out" links (put the GitHub repo here).
- Image gallery (at least one image is required here).
- Video URL.
- Team members (every teammate must accept the invite).
- Prize opt-ins (a checkbox for each prize).
- Custom questions set by the organizers. Per the FAQ these are at least the **table number** and the
  **team name**.
- We could not see the logged-in submission form, so other custom questions or a cap on prize opt-ins
  can't be confirmed. Check the "Manage submission" page.

## 2. Main judging criteria (Cal Hacks)

Devpost lists five criteria with **no weights given**. They are quoted below. The Hive FAQ gives a shorter
list: "impact, functionality, technical complexity, and creativity."
1. **Application**: "feasible real-world use… identify who they help, what problem they solve, and why people would actually use them."
2. **Intentionality**: "intuitive, engaging experience… guided by clear first principles. Every design choice should feel purposeful."
3. **Functionality**: "work as intended, with a live demo that runs smoothly free of major bugs" plus the Devpost video.
4. **Creativity**: "original thinking… unexpected combinations of ideas or technologies. Clearly show what makes your project stand out."
5. **Technical Complexity**: "command of the frameworks and infrastructure… sound architectural choices and engineering depth."

## 3. Prizes BillBack can enter

Prize text comes from https://cal-hacks-13-0.devpost.com/ (prize list) and the Hive prize data at
https://hive.hackberkeley.org/api/live/prizes, which feeds https://hive.hackberkeley.org/prizes and holds
the full criteria.

### Strong fits

| Prize | Award | Requirements / criteria (quoted or close) | What to show in Devpost or video | Disqualification or loss risk for us |
|---|---|---|---|---|
| **[Cartesia] Best Use of Cartesia** | $1,516; Meta glasses per member (TBD) | "Voice should be core to the product–not just a feature added for the demo… make clear what Cartesia contributes." **Submit:** a working demo; "A 2-minute, demo-first presentation: one complete user flow, then a brief problem + how you built it"; "A short README: setup, Cartesia features used, **known limitations**"; "Source access for judges… No API keys or secrets in the repo." **Criteria, 25% each:** Problem & originality; Use of Cartesia (voice clear, responsive, natural); Execution (main flow works, edge cases handled); **Impact & responsibility (accessibility, privacy, responsible use)**. "We'll favor a strong working prototype over a large unfinished idea." Also at https://docs.cartesia.ai/calhacks and in the starter pack doc. | README sections: Setup, Cartesia features used (Ink streaming STT, Sonic TTS streaming, voices and accents, pronunciation), Known limitations. A Devpost paragraph on accessibility, privacy and responsible use. The video shows a full call flow, not just a TTS clip. | (1) **No live phone call recorded** (README says "Partial"), and Cartesia is out of credits (402), so Execution is weak. (2) If the repo is private, judges have no source access; see the gaps section. (3) The README has no Known limitations section. |
| **[Bright Data] Best Agent/App Using Public Web Data** | 1st: $1,500 credits + $500 Amazon card; 2nd: $1,000 + $350; 3rd: $500 + $250 | "Bright Data is essential to the core experience; if live public web data were removed, the product would no longer work." Strong entries "run live in the demo, handle real web conditions like blocks, slow responses, and empty results, and turn raw data into clean, useful output." Extra credit for choosing tools thoughtfully and combining them, and for "a clear two-minute story using **real results from real websites rather than mocked data**." **DISQUALIFIED if:** "scrape behind a login or paywall, collect non-public personal data, **use mocked or hardcoded data in the demo**, or mention Bright Data only in the README without making it part of the working product." | A live crawl, or "Recheck now", in the demo and the video. Name SERP API + Web Unlocker and why we combine them. Show how we handle blocks and empty results (we already have a challenges paragraph). | **High.** The demo reads a *pre-crawled* policy database, and judges could call that "hardcoded". `SAMPLE_TIERS` falls back to sample data when keys are missing (PLAN item 4). The demo case, Rosa, is "not eligible", so the policy data barely changes the result. Fixes: show the crawl happening live, lead with Marcus ($5,600 → $0), and make startup refuse to run on sample tiers. |
| **[Capital One] Best Use of Nessie** | $1,200 ($300 Giftogram per person) | Criteria: "**Completeness, Creativity, Complexity**". Nothing else published. | Name the Nessie objects we use (customers/accounts, purchases, deposits for payroll income, bills for the payment plan) and what each decides. | Low. Mock judges said we were likely top 3. Don't let a 0-dollar or endless recurring bill show (PLAN item 5). |
| **[Anthropic] Claude for Public Benefit** | $4,998 cash value: $5,000 Claude credits + 1 year Claude Max 20x + office hours | Hive lists 4 criteria: "1. **A named community.** 'Students' is too broad… 2. **A real problem.** You have lived it, or you talked to someone who has. 3. **It works.** One thing done well and usable on Monday beats a broad demo. 4. **Thoughtful use of Claude.** Claude does something that matters… and **you know where it could go wrong for the people using it**." | Name the community narrowly, e.g. "low-income, under-insured patients billed by Bay Area nonprofit hospitals". Say who we talked to, or whose bill it was. List Claude's failure modes and how we guard against them: misreading a bill, hallucinated numbers (blocked: code does the math, quotes are verified), mishearing IDs on calls, agreeing to pay (forbidden). | Medium. The draft never names a community this narrowly, gives no evidence of talking to a real person, and has no "where Claude could go wrong" section. |
| **Cal Hacks Best People-First Social Impact Hack** | Apple Watch SE 3 per member | No criteria published beyond the main ones. | The social impact framing: medical debt and charity care. | Low. |
| **Cal Hacks 1st/2nd/3rd Overall, Most Technically Complex, Most Creative, Hacker's Choice** | Various | Main criteria above. These are probably judged automatically, but check whether the overall prizes need an opt-in. | | |
| **[Microsoft] Built for the Real World (1st) / Customer Impact Award (2nd)** | Surface laptop per member plus an FDE talk and mentorship | "Build a **production-minded AI agent** that solves a meaningful problem in finance, productivity, **healthcare**…" Criteria: customer understanding, measurable usefulness, path from prototype to production, responsible design, end-to-end functionality. | The feasibility section (Plaid/MX, 501(r), HIPAA 164.510(b)) helps. Add measurable numbers (dollars recovered per case, time saved, call length) and the production path. | Low to medium. A good fit; no Microsoft tech appears to be required. |
| **[MLH] Best Use of Gen AI** | Assorted prizes | "utilize publicly available Generative AI APIs (like those from… Anthropic…)" | Opt in. | Low. |
| **[MLH] Commit Fellowship Prize Category** | Interview for the fellowship | "building a working prototype with a team, demonstrate a real problem worth solving, opt into the… Prize Category when you submit, **and complete our supplemental form**." | Opt in on Devpost **and** submit the MLH supplemental form; the link is on Devpost and at https://www.mlh.com/events/prizes. | You lose the prize if the form isn't sent. |

### Weak or stretch fits (decide quickly)

| Prize | Why it's a stretch |
|---|---|
| **[Hyphenate] Autonomous Systems for Finance** ($8k/$5k/$3k, top 5 get interviews) | The brief is "Pick a company and one corner of its finance team… build the autonomous system you'd hire to fill them." BillBack works for the *patient*, not a company's finance team. Its themes do match: "sources that disagree" (bank, bill and EOB), approvals, an audit trail, limited access. To enter, frame it as the "patient financial advocate" or "billing-dispute" seat. Hyphenate wants: a video that **opens with the moment**, a live demo (they "may ask… a scenario of our own"), "one line on the seats your system would fill", and "proof it works: what's real, what's simulated, and how you know" (a test set, planted errors with an answer key, or a benchmark). Source: https://docs.google.com/document/d/1u4iN8R97ILY3kfHTPKC82zdutHrGx4ze5eCAP-J92w4 |
| **[Moss] Best Use of Moss** | It looks for "Voice AI" agents but requires Moss. We don't use it. |
| **[Meta] Bringing People Closer Together with AI** | Judged on "human connection". A poor fit. |
| **[CRS] Credit API, [Convex], [Spacetime], [Fetch.ai], [Apify], [Factory], [Atlassian] (Forge app required), [Visa] (shopping), [Elastic], [Bitdeer], [Mana], MLH tech prizes** | Each needs its sponsor's technology, which we don't use. Don't opt in, because judges penalize prizes entered just for coverage. |

Hive lists no separate "best AI agent", healthcare or fintech prize. The closest are Microsoft (healthcare
and finance agents), Hyphenate (finance), and CRS's "Best Financial AI Agent", which requires CRS.

## 4. Earlier editions (context only)

- **12.0 (2025):** submit by 9:30 AM, with edits allowed until 10:30 AM except the table number.
  2-minute pitch plus 2 minutes of Q&A. "You must have a recorded video submission stored locally."
  Bright Data offered a "Best Use of Bright Data" prize. Source: https://cal-hacks-12-0.devpost.com/
- **11.0 (2024):** criteria were Application, Functionality/Quality, Creativity and Technical Complexity.
  Same 2+2 pitch format. Source: https://cal-hacks-11-0.devpost.com/
- 13.0's rules differ: a 3-minute pitch, an added "Intentionality" criterion, a hard deadline, and a live
  *or* pre-recorded demo.

## 5. Gaps in our draft (`docs/devpost.md`) and repo, ranked

**Must fix before 9:00 AM PDT (eligibility):**
1. **GitHub repo link.** It's required ("for any code to be considered"), and Cartesia needs judges to
   have source access. `https://github.com/gsonntag/calhacks` returns **404 to anonymous users**, so it is
   private or the name is wrong. Make it public or add the judges, put it in "Try it out", and confirm no
   secrets are committed.
2. **At least one project image** in the gallery.
3. **Demo video link** (≤ 2 min, demo-first; for Hyphenate, open with the moment).
4. **Tagline / 2–3 sentence elevator pitch**, **team name**, and **table number**. The draft has none of
   these. The table number likely can't change later.
5. **All teammates added** and their invites accepted.
6. **Prize opt-ins:** Cartesia, Bright Data, Capital One Nessie, Anthropic Claude for Public Benefit,
   People-First Social Impact, Microsoft (both), MLH Gen AI, MLH Commit Fellowship (plus its form).
   Hyphenate is optional.

**Content that track criteria explicitly ask for and the draft lacks:**
7. **"Known limitations."** Cartesia requires it in the README, and it's missing. Add it to Devpost too:
   - no live phone call recorded
   - conference mode labels the patient's speech as the rep (`docs/voice.md`)
   - the bank, bill and EOB are synthetic
   - income uses take-home pay instead of gross
   - "no tiers" hospitals are never re-crawled
   - IDs can be misheard
   - PHI retention: what is stored and for how long
8. **"Real vs simulated" section** in Devpost. It exists in the README but not the draft. Bright Data
   (mocked-data disqualification), Hyphenate ("what's real, what's simulated, how you know") and judges'
   Q&A all need it.
9. **Cartesia section:** list which Cartesia features we use and what each contributes:
   - Ink streaming STT
   - Sonic streaming TTS with sentence-by-sentence playback
   - the voice and accent picker
   - Managed Agents kept as an alternative
   Add the latency numbers (0.44–0.8 s first audio) and an **accessibility, privacy and responsible use**
   paragraph, which is 25% of Cartesia's score (Spanish voice, the disclosure that it's automated, all-party
   consent, PHI handling).
10. **Bright Data section:**
    - State that the policy data is **live, real public pages** (no login or paywall, no personal data).
    - Explain the SERP + Unlocker combination and change detection.
    - Show the live crawl or recheck in the demo.
    - Remove or disable `SAMPLE_TIERS` (PLAN item 4).
11. **Anthropic criteria:**
    - a narrowly named community
    - evidence that the problem is real (whose $8,700 bill it was, whether we talked to patients or
      billing staff)
    - a "where Claude could go wrong and how we guard against it" paragraph (our "no unchecked number"
      design is the main strength here)
12. **Nessie:** name the endpoints and objects used, and show "complexity": income inferred from payroll
    deposits, payment matching, and the plan written back as a bill.
13. **Measurable usefulness** (Microsoft, and "Application"): numbers saved per case, the 4-hospital
    comparison, and test counts.
14. **Demo case choice:** the draft leads with Rosa ($8,700 → $600 through subtraction, "not eligible").
    Every mock judge said to lead with Marcus or with the same bill compared across hospitals.
15. **Doc mismatches judges may notice:**
    - README says "139 tests" (now 281).
    - `docs/DEMO.md` says 4 hospitals (now 12) and uses a 3-minute script. A 3-minute script is fine for
      the main judges but too long for Cartesia's 2 minutes.
    - PLAN says "judging is 2 minutes"; the main judges actually get 3 + 2.
16. "Built with" is fine. Consider adding `twilio-media-streams` and `claude-sonnet`.

## 6. Uncertain

- Whether Devpost lets us edit after 9:00 AM. 13.0 says "hard deadline" and mentions no grace period;
  assume it doesn't.
- The custom fields on the logged-in submission form, and any cap on the number of prize opt-ins.
- Whether sponsor judges also visit tables or judge only from the video. The Functionality criterion
  implies some sponsors judge "asynchronously" from the Devpost video.
- Prize descriptions were taken from Hive at about 00:30 PDT and may still change. Anthropic's Hive entry
  is titled "Coming soon!" but already lists its 4 criteria; Cartesia's prize is "(still TBD)".
- Whether the repo is private or just under a different name. The 404 came from an anonymous request.

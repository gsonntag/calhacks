# Remy: 2-minute demo

Demo first: one complete case (Raj Mehta, a $16,089 ER bill at California Pacific Medical Center → $0 and
$400 back), with a live Bright Data recheck and a live Cartesia call in it. Then 20 seconds of problem and how
it's built.

Two people: the **presenter** (laptop, projector) and the **rep** (a teammate playing CPMC billing on the rep
console).

Every number below comes from `demo/scenarios/raj-cpmc/expected.json`. Say the rounded figure; exact cents
only where they're on screen. Live income can differ by a few tenths of a percent depending on the Nessie
data.

## Raj in one paragraph
Raj drives for Uber and Lyft. Household of 4: his gig payouts plus his wife's part-time pay, about 135% of
the poverty line. A kidney stone sent him to CPMC's emergency room (Van Ness campus) on 2026-08-14. The bill
says **$16,088.70**. It has four errors: the IV hydration (96360) is billed twice (−$1,160); the CT (74176)
is billed at $7,150 when CPMC's own price file posts $6,374 (−$776); his insurance wasn't applied, and his
share is $4,200 (−$9,952.70); and a $400 payment in his bank ("SUTTER HEALTH PYMT", 2026-09-05) isn't on
the bill (−$400). That leaves **$3,800**. Then Sutter's own policy: insured patients up to 400% of the
poverty line whose medical bills over 12 months exceed 10% of family income get their share written off.
This bill alone is **9.5%** of his income, under the bar. His bank shows **$2,053.35** of other medical bills
in the last 12 months (urgent care, imaging, pharmacy, physical therapy, the kids' pediatrician). Together:
**14.1%** (11.3% even on estimated gross pay). So he owes **$0**, and the **$400 comes back**.

## The script (2:00)

**0:00–0:10 · Hook** (presenter, holding the printed bill)
> "Raj drives for Uber and Lyft. He has a family of four. A kidney stone sent him to the ER, and CPMC sent
> him this: a $16,089 bill. Watch Remy handle it."

**0:10–0:20 · Start the case**
- Click **the Raj example** ("Try an example"). It sends Raj's bill and insurance statement with a
  household of 4, same as `POST /api/cases?scenario=raj&household_size=4`. The case page opens.
> "He drops in the bill and the insurance statement. Claude reads them. No forms."
- If reading fails: "The model's slow on venue Wi-Fi." Switch to no-wifi mode (see Fallbacks).

**0:20–0:45 · Review: two agents, side by side** (the step bar fills as the checks finish)
- **Bright Data agent:** CPMC's real price file, from Sutter's site. Line 2, the CT, is billed
  **$7,150.00**; CPMC's own posted price is **$6,374.00**. That's $776 too much.
- **Nessie agent:** the IV hydration is on the bill twice (lines 6 and 7): $1,160. The insurance
  statement says his share is **$4,200.00**, and the bill ignores it. His bank shows a **$400 payment** on
  September 5th that the bill doesn't show. That's **$3,800**.
- It also lists his **12 months of medical bills** from the bank: $2,053.35, each transaction listed.
> "Every finding links to its proof: a bill line, the hospital's price file, the insurance statement, a
> bank transaction. Claude never makes up a number. Code does the math."

**0:45–1:00 · The money moment**
- Point at the Bright Data agent's policy step: Sutter's policy, with the verified quote:
  *"A write-off of the Patient Responsibility amount for Hospital Services to insured patients earning 400%
  or less of the FPIG and with high medical costs (medical expenses over the past 12 months exceeding 10% of
  family income)."*
- Point at the line under it: "Your bank shows $2,053.35 in other medical bills over the last 12 months …;
  without them you wouldn't qualify."
> "Here's the thing. This bill alone doesn't qualify him. It's 9.5% of his income, under the 10% bar. His
> bank history does. Add the urgent care, the pharmacy, the kids' pediatrician, and he's at 14%. Under
> CPMC's own policy, he owes nothing. And the $400 he already paid comes back."
- **Optional, if ahead of time:** click **Recheck now** under the Bright Data panel. Lines stream in, one
  per request (Web Unlocker → sutterhealth.org, then the policy PDFs), then "Still holds". Say: "That's
  live: Bright Data checking the policy hasn't changed since we built the plan." If it fails or is slow:
  "It falls back to the stored copy and says so." Move on.

**1:00–1:08 · The plan**
- The plan lists every ask, each with a "why ▸" link: remove the duplicate line, fix the CT price, apply
  the insurance, credit the $400, write off his share under Sutter's policy, refund the $400, hold
  collections meanwhile, and get a case number and the rep's name.
- Click **Call now**.
> "Nothing happens without his OK. It never agrees to pay on a call, and it always says it's automated."

**1:08–1:30 · The call (desk mode, Cartesia)**
- The rep's tab is this case's **Under the hood** page. When the call starts it shows "Calling… **rep
  console**"; the rep opens it and clicks **Answer**. The header says "listening with Cartesia Ink".
- Remy opens: "Hi, this is an automated assistant calling on behalf of Raj Mehta, with his authorization.
  This call is transcribed…"
- The rep says, in order:
  1. "I see sixteen thousand eighty-eight dollars due. Can you pay that by card today?" → Remy won't pay.
     It asks for the corrections, free care under Sutter's policy, and the $400 back.
  2. While Remy is still talking: **"Wait, sorry, when was that payment?"** → audio stops (barge-in);
     the bubble shows the unheard part struck through; Remy answers: September 5th, $400.
  3. "OK. I've opened a financial-assistance review, sent the corrections, and put the account on hold.
     Case number FA 41762." → Remy reads it back: "F A, 4 1 7 6 2. Is that right?" Rep: "Yes. Goodbye."
> "Cartesia Ink hears her, Sonic speaks. When she interrupts, Remy stops, and only remembers what she
> actually heard."
- If Cartesia fails (no credits, HTTP 402): the console says so and falls back to Chrome's speech and voice
  by itself. Say: "Same call brain; the browser is standing in for Cartesia's voice."

**1:30–1:38 · Ask Remy**
- Click the round **Ask Remy** button (bottom right). Type: **"Why do I get it free?"**
- The answer quotes Sutter's sentence and the 10% bar, and adds up his bank's medical bills: $4,200 this
  bill + $2,053 other medical bills = 14%.
> "Raj can ask anything, on any tab. The answer cites the policy and his own bank records."
- If the answer is slow: close the panel and click the "why ▸" link on the free-care ask instead.

**1:38–1:45 · Resolved**
- The Resolved step: "California Pacific Medical Center still shows **$16,088.70**. I expect **$0** once they
  apply the corrections; it isn't confirmed yet." Plus the $400 refund, case FA 41762, account on hold, and a
  check-back date from what billing promised.
> "$0, and $400 back. It doesn't claim a win until billing confirms it. It'll check back, and call again
> if needed."

**1:45–2:00 · Problem and how it's built**
> "Hospitals publish charity-care policies. Most patients never hear about them, and many only qualify
> once you count all their medical bills. Remy reads the bill with Claude, checks the bank with Capital One
> Nessie, finds the hospital's real prices and policy with Bright Data, and makes the call with Cartesia.
> Raj was about to pay $3,800 he didn't owe. Remy got him to zero, and his $400 back."

## Before judging (T-30 min)
- [ ] `.env`: `ANTHROPIC_API_KEY`, `NESSIE_API_KEY`, `BRIGHTDATA_API_KEY` + both zones, `CARTESIA_API_KEY`,
      `REMY_ADMIN_TOKEN` (16+ characters), `REMY_REQUIRE_LIVE=1`. Twilio + `PUBLIC_BASE_URL` only for a real
      phone call (a Twilio trial can't stream audio; use desk mode).
- [ ] Start the server (`uv run uvicorn remy.app:app --port <port>`). With `REMY_REQUIRE_LIVE=1` it won't
      start if a key is missing. Check `GET /api/mode`: Claude, Nessie, Bright Data, Cartesia true;
      `live_crawl` true.
- [ ] Open `/case.html?key=<token>` once in the presenter's browser (the key is saved and removed from the
      address bar). Under the hood and the rep console open as tabs of the same browser. A rep on a second
      laptop opens any page once with `?key=<token>` (the console's mic socket needs it).
- [ ] Nessie has Raj: `uv run python scripts/seed_nessie.py raj` was run on this key (about 250 Nessie
      requests: once, not per rehearsal). If not, the case says "using sample bank data" and still runs.
- [ ] One full rehearsal of Raj, including "Recheck now" and the call. Paid credits: one rehearsal, not
      loops (`docs/PLAN.md`, "Rule: paid API credits").
- [ ] Rep: headphones (or check echo handling on speakers), Chrome mic permission granted, the three lines
      and the case number on paper.
- [ ] Tabs ready: the page with the Raj example. Once the case starts, the rep opens its Under the hood page.

## Raj's replay: record it once Cartesia has credits
There is no recorded replay for Raj yet. Replays are recorded live runs only: when a live case finishes
(Claude available), the server writes its events to `data/replay_<scenario>.json`, so a full live run
started with `?scenario=raj` writes `data/replay_raj.json`. An offline (`REMY_OFFLINE=1`) run never
overwrites a recording.

To record it: once Cartesia credits are confirmed (the first account hit HTTP 402 on 2026-10-25), do the
rehearsal above end to end in live desk mode with Cartesia's voice, through the Resolved step. Check that
`/case.html?replay=1&scenario=raj` and `&jump=call` play back. To ship it with the repo, copy it to
`demo/replay_raj.json` (`GET /api/replay` looks in `data/` first, then `demo/`).

## Fallbacks
| What breaks | Say / do |
|---|---|
| Intake or Review slow | Keep narrating; the agents stream step by step |
| "Recheck now" fails or is disabled | "It falls back to the stored copy and says so." Show the stored policy's crawl date and quote |
| Cartesia out of credits / socket fails | The rep console falls back to Chrome speech by itself; "the browser stands in for Cartesia" |
| No "rep console" link | Open `/rep.html?call=<call id>` (the id is in the case's events) |
| Ask Remy slow or fails | Click the "why ▸" link on the free-care ask: same quote, same numbers |
| Wi-Fi dies | Restart with `REMY_OFFLINE=1 uv run uvicorn remy.app:app --port <port>` and start the Raj example again: the whole flow on one laptop, billing ("Diane") played automatically, an "Offline demo" chip in the top bar. Once recorded, the replay works too: `/case.html?replay=1&scenario=raj` |
| Rep console doesn't open | Once recorded: `/case.html?replay=1&scenario=raj&jump=call`. Until then, `REMY_OFFLINE=1` (billing plays itself) or Marcus's replay |
| A 401 / key prompt | Paste the admin key once; it is remembered |
| Judge asks about another hospital | `/hospitals.html`: the same bill through every stored policy, with quotes and sources |

## Backup case: Marcus ($5,600 → $0)
If Raj's case won't start, use Marcus: `/flows/drop-go.html` → **Try the free-care example** (household of
3). UCSF Medical Center billed his share, $5,600, and the bill is correct. He's at about 170% of the poverty
line; with $1,097 of other medical bills in his bank, his medical costs are 14% of income, over UCSF's 10%
bar: $5,600 written off, $0. Same beats: review, policy quote, plan, call (the rep asks for "fifty-six
hundred"; case number FA 20931), resolved. Seed: `seed_nessie.py freecare`. Replay:
`/case.html?replay=1&scenario=freecare` (older wording). `/hospitals.html` opens on his bill: $0 at 7 of 12
hospitals.

Rosa, if asked about a case that isn't free: `/flows/drop-go.html` → **Use a sample bill**. CPMC billed
$8,700, insurance wasn't applied and a $500 payment is missing: $600. Her medical bills are only about 3% of
income, so Sutter's write-off doesn't apply, and Remy says so. It proposes 6 × $100/month, interest-free.

## Real vs simulated (say it before they ask)
- **Real:** CPMC's price file and Sutter's policy (Bright Data crawls, committed in `policydb/`, plus the
  live recheck), Nessie API records, Claude, Cartesia speech, the approval gate, the audit log.
- **Simulated:** Raj, his bill, his insurer and his bank history are synthetic (PDFs watermarked SAMPLE).
  The bill's prices come from CPMC's real price file; the errors are planted. Billing is a teammate.

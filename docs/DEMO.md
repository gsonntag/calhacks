# Remy: 2-minute demo

Demo first: one complete case (Marcus Lee, $5,600 at UCSF Medical Center → $0 under UCSF's own policy), with
a live Bright Data recheck and a live Cartesia call in it. Then 20 seconds of problem and how it's built.

Two people: the **presenter** (laptop, projector) and the **rep** (a teammate playing UCSF billing on the
rep console). Numbers below are what the app computes for Marcus; live income can differ by a few tenths of a
percent depending on the Nessie data.

## The script (2:00)

**0:00–0:10 · Hook** (presenter, holding the printed bill)
> "Marcus got a $5,600 bill from UCSF for an ER visit. His insurance already paid its part. This $5,600 is
> his share, and he can't pay it. Watch Remy handle it."

**0:10–0:20 · Start the case**
- Click: `/flows/drop-go.html` → **Try the free-care example** (Marcus's bill + insurance statement,
  household of 3). The case page opens.
> "He drops in the bill and the insurance statement. Claude reads them. No forms."
- If reading fails: "The model's slow on venue Wi-Fi", open the replay (see Fallbacks).

**0:20–0:45 · Review: two agents, side by side** (the step bar at the top fills as the checks finish)
- Point at the **Nessie agent** column: the bill matches the insurance statement (plan paid $1,700, his
  share is $5,600); bank history pulled from Capital One Nessie; no payments yet, no double charges; **$1,097
  in other medical bills** over the last 12 months (pharmacy, clinics), each transaction listed.
- The income step: payroll deposits → **$46,332 a year, 170% of the poverty line** (household of 3).
- Point at the **Bright Data agent** column: UCSF's policy from our database, crawled from ucsfhealth.org;
  the verified quote: *"Has out-of-pocket medical expenses in the prior twelve months … that exceed 10% of
  Family Income."* His are 14% ($5,600 + $1,097): "You qualify for free care". Result: **$5,600 written off
  = $0**.
> "Every number has a source. The policy figure needs an exact quote from UCSF's own page, and code does the
> math. Claude never makes up a number."

**0:45–1:00 · Live Bright Data**
- Click: **Recheck now** under the Bright Data panel.
- Lines stream in, one per request: Web Unlocker → ucsfhealth.org page, then the policy PDFs: "Found (KB,
  seconds) · request n of at most 10", then **"Still holds: version 1 has the same rules this plan uses"**.
> "That's live: Bright Data fetching UCSF's policy right now, checking it hasn't changed since we built the
> plan. Blocked pages, timeouts and retries show up as their own lines."
- If it fails or is slow: "The venue network is blocking it. It falls back to the stored copy and says so."
  The panel shows "Live recheck failed: the plan keeps the policy it used". Move on.

**1:00–1:10 · The plan**
- The plan: call UCSF financial assistance (through Patient Financial Services) with 3 asks, each with a
  "why ▸" link: free care under their own policy ($5,600 → $0), pause collections while it's corrected, a
  case number and the rep's name.
- Point at the round **Ask Remy** button (bottom right): "Questions go here: Remy, either agent, or
  the planner, which makes a new plan version if he says 'I can do $150 a month'." Don't send one on stage.
- Click **Call now**.
> "Nothing happens without his OK. It never agrees to pay on a call, and it always says it's automated."

**1:10–1:32 · The call (Cartesia)**
- The rep's tab is this case's **Under the hood** page (top-bar link on the case page). When the call
  starts it shows "Calling… **rep console**"; the rep opens it and clicks **Answer**. The header says
  "listening with Cartesia Ink".
- Remy opens: "Hi, this is an automated assistant calling on behalf of Marcus Lee, with his
  authorization. This call is transcribed…"
- The rep says, in order:
  1. "Can you pay the fifty-six hundred by card today?" → Remy refuses to pay and asks for a
     financial-assistance review under UCSF's policy.
  2. While Remy is still answering: **"Wait, sorry, what's his income?"** → audio stops (barge-in);
     the bubble shows the unheard part struck through; Remy answers the question.
  3. "OK, I've opened a financial assistance review and put the account on hold. Case number FA 20931." →
     Remy reads it back: "F A, 2 0 9 3 1. Is that right?" Rep: "Yes. Goodbye."
> "Cartesia Ink hears her, Sonic speaks, and when she interrupts, Remy stops and only remembers what she
> actually heard."
- If Cartesia fails (no credits, HTTP 402): the console says so and falls back to Chrome's speech and voice;
  the rep holds the mic button or types. Say: "Same call brain; the browser is standing in for Cartesia's
  voice." If the console won't open: open the replay at the call (`&jump=call`).

**1:32–1:40 · Resolved**
- The Resolved step: "UCSF Medical Center still shows **$5,600**. I expect **$0** once they apply the corrections; it
  isn't confirmed yet." Case FA 20931, account on hold, "I'll check back by <date>" from what billing
  promised. Nothing is owed, so no payment plan.
> "It doesn't claim a win until the hospital confirms it. It'll check back, and call again if needed."

**1:40–2:00 · Problem and how it's built**
- Optional click: `/hospitals.html` (the same bill at all 12 hospitals: $0 at 7; $5,600 at Chinese Hospital
  and Highland, whose tiers are for uninsured patients, and at Stanford, Saint Francis and SF General, which
  have no verified tiers on record).
> "Hospitals publish charity-care policies, and most patients never hear about them. Remy reads the
> bill with Claude, checks the bank with Capital One Nessie, finds the hospital's real policy and prices
> with Bright Data, and makes the call with Cartesia. The same bill is $0 under seven of these hospitals'
> policies; for the other five the page shows why not. Remy knows which, and calls for you."

## Before judging (T-30 min)
- [ ] `.env`: `ANTHROPIC_API_KEY`, `NESSIE_API_KEY`, `BRIGHTDATA_API_KEY` + both zones, `CARTESIA_API_KEY`,
      `REMY_ADMIN_TOKEN` (16+ characters), `REMY_REQUIRE_LIVE=1`. Twilio + `PUBLIC_BASE_URL` only for
      a real phone call.
- [ ] Start the server (`uv run uvicorn remy.app:app --port <port>`). With `REMY_REQUIRE_LIVE=1` it
      won't start if a key is missing. Check `GET /api/mode`: Claude, Nessie, Bright Data, Cartesia true;
      `live_crawl` true.
- [ ] Open `/case.html?key=<token>` once in the presenter's browser (the key is saved and removed from the
      address bar). Under the hood and the rep console open as tabs of the same browser, so they have the
      key too. A rep on a second laptop opens any page once with `?key=<token>` (the console's mic socket
      needs it).
- [ ] Nessie has Marcus: `uv run python scripts/seed_nessie.py freecare` was run on this key. If not, the case
      says "using sample bank data" and still runs.
- [ ] One full rehearsal of Marcus, including "Recheck now" (UCSF: about 3 Web Unlocker requests, one per
      stored source; no SERP, no Claude call unless the policy changed) and the call. That also records a fresh replay
      (`data/replay_freecare.json`). Paid credits: one rehearsal, not loops.
- [ ] Rep: headphones on the laptop (or check the echo handling on speakers), Chrome mic permission granted.
- [ ] Tabs ready: `/flows/drop-go.html`, `/hospitals.html` (it opens on Marcus's bill). Once the case
      starts, the rep opens its Under the hood page.

## Fallbacks
| What breaks | Say / do |
|---|---|
| Intake or Review slow | Keep narrating the checklist; the agents stream step by step |
| "Recheck now" fails or is disabled | "It falls back to the stored copy and says so." Show the stored policy's crawl date and quote |
| Cartesia out of credits / socket fails | The rep console falls back to Chrome speech by itself; "the browser stands in for Cartesia" |
| No "rep console" link | Open `/rep.html?call=<call id>` (the id is in the case's events); or the replay |
| Rep console doesn't open | Replay: `/case.html?replay=1&scenario=freecare&jump=call` |
| Wi-Fi dies | Replay: `/case.html?replay=1&scenario=freecare`: "the exact run from our rehearsal" |
| A 401 / key prompt | Paste the admin key once; it is remembered |
| Judge asks about another hospital | `/hospitals.html`: same bill through every stored policy, with quotes and sources |

Replays are recorded live runs. The committed ones (`demo/replay_*.json`) are older than the current build:
they end on the old "$X → $Y" result and older wording (the case page ignores their old payment-plan
approval). Rehearse once before judging so the replay is fresh.

## Second case if asked: Rosa ($8,700 → $600)
`/flows/drop-go.html` → **Use a sample bill**. California Pacific Medical Center billed $8,700. The insurance
statement says her share is $1,100 (the hospital never applied the $7,600), and Nessie shows a $500 payment
the bill doesn't show: $600. The Nessie agent also flags a $75 double charge at Berkeley Urgent Care, apart
from this bill. She's at 228% of the poverty line but her medical bills are only about 3% of income, so
Sutter's write-off doesn't apply (it says so). The planner proposes Sutter's interest-free terms, 6 ×
$100/month; she decides at the end and sets up the payments herself (nothing is written to her bank).

## If asked: the Cases-page mic
Each case on Cases (`/`) has a hold-to-talk mic: "Where are we with UCSF?" or "Call them." An action is said
back first ("I'll call UCSF Medical Center billing now with plan v1…") and runs only after a spoken yes. It
transcribes with Cartesia Ink's batch STT (never run live; credits ran out) or the browser's own speech
recognition.

## Real vs simulated (say it before they ask)
- **Real:** the hospital policies and prices (Bright Data crawls, committed in `policydb/`, and the live
  recheck), Nessie API records, Claude, Cartesia speech, the approval gate, the audit log.
- **Simulated:** the patient, bill, insurance statement and bank history are synthetic (watermarked SAMPLE).
  Billing is a teammate.

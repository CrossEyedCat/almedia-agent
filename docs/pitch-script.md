# Pitch script: 3 minutes for the video, 5 minutes for the stage

Everything here is something the repository shows today. Where the system does not do something yet, the script says so instead
of implying it. Numbers come from `docs/results.md`. Read the whole page once before recording.

## Before you start (5 minutes)

1. Clean state, accelerated clock, from a clean clone: run `python -m sim.run --backfill 28d --db data/market.db --force`
   once, then start these three in their own terminals, in this order:
   `python -m brain.run --db data/market.db --incidents incidents --graph-out data/graph.json --fresh`, then
   `python -m sim.live --speed 2 --db data/market.db`, then `uvicorn ui.app:app --port 8000`. Open http://127.0.0.1:8000.
   Start the console with the four environment variables of `docs/demo.md`, step 4, and with an empty `incoming_metrics/`
   folder: without `GRAPH_JSON=data/graph.json` the console shows the tracked example graph and a metric added on stage never appears.
   To show "In brief" (the model's narration above the report), add `--narrate` to the brain command: it needs
   ANTHROPIC_API_KEY in `.env` and spends a few requests per incident. Without it the report is the template only, and
   "In brief" (docs/demo.md at 1:20, and the demo slide) does not appear.
2. Wait until the clock in the header moves and the incident list reads "No incidents. The monitor is watching."
3. Open the folded DEMO controls once so you know where the buttons are, then fold them again. Dark or light theme: add `?theme=light` or `?theme=dark`.
4. Two faults of one metric that begin in the same hour in places sharing a country or a platform can be reported as one
   incident (a known limit, README 3.5); inject them an hour apart or in places that share neither a country nor a platform.

## The 3-minute script (about 340 spoken words)

**0:00 The problem.** (Header and graph on screen.)
"A marketplace has thousands of numbers and nobody can watch them all. Most problems are not outages: the servers are fine, but
payments fail for Android users in Germany and orders quietly fall. We built a system that watches the business by itself: it notices a
change, finds where and why, and explains it. No one asks it questions."

**0:25 How it understands the business.** (Point at the graph, then click one box.)
"It starts from a graph of how the business works: contribution is fees plus ad spend minus discounts, orders are sessions times conversion,
conversion is a chain of step rates. Lines have meanings: exact formulas, breakdowns, possible causes, and context such as releases and promos.
That graph is what lets it trace a change instead of just flagging it."

**0:55 A real fault.** (DEMO controls: Payment failure, DE, Android, Inject fault. Fold the controls.)
"The data is simulated, and I now break it: a payment release goes wrong for Android in Germany. The monitor was not told. It reads the data
hour by hour, compares each rate with what it expects, and..." (wait a few seconds for the incident) "...it has found it."

**1:20 The report.** (Click the incident; the path orders, conversion, payment success lights up.)
"It says what changed, by how much, what it costs in euros, where along the graph the change sits, which segment it is in,
and what the cause is: the payments release for Android in Germany, with the evidence and what is still uncertain."

**1:50 Not everything unusual is a problem.** (Promo in FR, then a late source.)
"A planned promo lifts orders in France. It sees the promo in the calendar and records it as explained, no alert. A data source that
arrives late is held as pending instead of raising a false alarm."
(Timing, measured on 21 Nov with `--speed 2`: the promo row appears in a few seconds, grey, and stays. The pending row lives only while the data
is late: the button delays three data hours, each by two, so about 6 to 8 seconds: inject "Arrives 2 h late" and look straight away, or leave this half sentence out.)

**2:15 Does it work?** (Open `docs/results.md`.)
"We scored it against faults we injected ourselves, in twenty 49-day runs. On the ten it had never seen it found 120 of 120 faults,
all in the right place, 118 with the cause confirmed, with one false alarm in 457 watched days, and all 80 innocent changes stayed quiet."

**2:40 The honest part and the close.**
"The two causes it did not confirm were stockouts: it named them as the candidate and said what was missing. The data is
synthetic, from our own simulator, so this is an engineering check, not proof on real traffic.
What it does today: it watches the business, not the servers."
(If asked: the ten runs it was built on give the same numbers without the false alarm, four unconfirmed causes in all twenty; the
false alarm was a one-hour jump in ad click-through in Poland on the web that closed by itself.)

## What not to say

- The new-metric button works end to end for the console's `refund_rate` (README 3.3; run live on 21 Nov). Do not claim more than
  that section says: the formula search covers one or two column metrics (19 of the 36), without constants.
- Do not say "100%" without the cause number (118 of 120 on the holdout runs), the false alarm and the caveat on the data.
- One of those 118 is a tie: the right release is supported and named first, but a routine release of the same system in the same
  place two hours earlier is left supported beside it. `docs/results.md` lists it; do not say every cause was singled out.
- "Holdout" means new random runs of our own simulator, with the same four kinds of fault. Do not call it a test on real data.
- Do not call all twenty runs independent: ten are a holdout, ten were used while the brain was built. `docs/results.md` keeps them apart.
- Do not call the wall-clock mode a demo mode: it moves one data hour per real hour. Use the accelerated clock on stage.

## Timings measured from a clean start (accelerated clock, `--speed 2`)

- Payment failure in Germany on Android: the incident appears about 5 seconds after the click, with the payments release as the cause.
- "End it (rollback)": the incident turns resolved about 8 seconds later (three normal data hours).
- Promo in France: an `explained` record, grey, no alert. Late data: `pending` for about 6 to 8 seconds, then it disappears by itself.
- Rehearsed again on 22 Nov, 01:00 CET, with the redesigned console and all open pull requests merged locally, fresh 28-day
  backfill, `--speed 2`: incident 6 s after the click (seen at the next data hour), resolved 8 s after the rollback, promo
  `explained` within 6 s, late data `pending` at once; **Add refund_rate** answered within about 10 s while the clock ran
  (validation over 30 days of data), and the new `refunds` tree appeared in the graph.

## If something goes wrong

- No incident after 20 seconds: say "it is still collecting evidence" and move on to the promo; or play the recording.
- A fault in a small segment at night needs more hours of evidence; use Germany, Android or iOS payments, or a category such as electronics.
- To start over: stop the three processes, empty `incidents/` and `incoming_metrics/`, run the first command again, start them in the same order.

## The 5-minute slides

1. **The problem** (30 s): too many numbers; failures that are not outages; nobody asks the question.
2. **How it sees the business** (45 s): the metric graph figure of `docs/brain-architecture.html` (formula, breakdown, possible cause, context); the parts picture of `docs/system-overview.html` if there is time.
3. **The loop that runs by itself** (60 s): read, expect, detect, locate, explain, keep tracking; one fault is one incident, updated in place.
4. **Demo** (60 s): a screenshot of the console with an incident and the lit path.
5. **Results** (60 s): the table from `docs/results.md`; say the caveat on the same slide.
6. **What failed and what next** (45 s): four unconfirmed causes in 240 faults (all stockouts); one false alarm; two faults at the same time are not tested; new metrics with constants in the formula; low-traffic hours.

## Recording the video (3:00 at most)

The rules ask for the pitch, how the solution works and a live demo, with a team member speaking to camera. One way that
needs nothing beyond Windows 11:

1. **Prepare** (10 minutes): a clean start as in "Before you start"; the console in a browser window of 1280 x 800 (the
   projector size the page is tuned for), light theme (`?theme=light`), zoom 100 %; notifications off; the DEMO bar folded.
   Let the clock run a few data hours into the day before you inject: night hours have small counts.
2. **Camera parts**: open with 15 s to camera (who you are, the problem in one sentence) and close with 15 s (the result and
   the one-line close). The Camera app records them.
3. **Screen part**: Xbox Game Bar (Win+Alt+R) records the browser window. Follow the 3-minute script from 0:25 to 2:40 and
   say it over the screen as you click. The timings above say how long each step takes to show.
4. **Cut** in Clipchamp (built into Windows 11): camera opening, screen part, the results slide (the "Measured" table of
   the README or `docs/results.md`), camera close. Keep it under 3:00; export at 1080p.
5. **Upload** to YouTube as Unlisted (or Google Drive, anyone with the link can view). Open the link in a private window to
   check that it plays for someone who is not signed in. Put the link in the form and in the README.


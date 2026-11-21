# Pitch script: 3 minutes for the video, 5 minutes for the stage

Everything here is something the repository shows today. Where the system does not do something yet, the script says so instead
of implying it. Numbers come from `docs/results.md`. Read the whole page once before recording.

## Before you start (5 minutes)

1. Clean state, accelerated clock. In four terminals, from a clean clone, in this order:
   `python -m sim.run --backfill 28d --db data/market.db --force`, then
   `python -m brain.run --db data/market.db --incidents incidents --graph-out data/graph.json`, then
   `python -m sim.live --speed 2 --db data/market.db`, then `uvicorn ui.app:app --port 8000`. Open http://127.0.0.1:8000.
2. Wait until the clock in the header moves and the incident list reads "No incidents. The monitor is watching."
3. Open the folded DEMO controls once so you know where the buttons are, then fold them again. Dark or light theme: add `?theme=light` or `?theme=dark`.
4. Inject the faults in different places or an hour apart. Faults that begin in the same hour are folded into one or two incidents by design.

## The 3-minute script (about 420 words)

**0:00 The problem.** (Header and graph on screen.)
"A marketplace has thousands of numbers and nobody can watch them all. Most problems are not outages: the servers are fine, but
payments fail for Android users in Germany and orders quietly fall. We built a system that watches the business by itself: it notices a
change, finds where and why, and explains it. No one asks it questions."

**0:25 How it understands the business.** (Point at the graph, then click one box.)
"It starts from a graph of how the business works: profit is fees plus ads minus discounts, orders are sessions times conversion,
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

**2:15 Does it work?** (Open `docs/results.md`.)
"We scored it against faults we injected ourselves, on two 49-day runs: 24 of 24 faults found, all 24 in the right place, 22 with the
right cause confirmed, no false alarms in 98 simulated days, and all 16 innocent changes stayed quiet."

**2:40 The honest part and the close.**
"Two causes were not confirmed, both stockouts in one category. The data is synthetic and the settings were tuned on part of it, so
these are engineering checks, not independent proof. Adding a new metric to the graph automatically is the part we did not finish.
What it does today: it watches the business, not the servers."

## What not to say

- Do not say the new-metric button works end to end: the console hands the file over; the monitor's side is not finished.
- Do not say "100%" without the cause number (22 of 24) and the caveat on the data.
- Do not call the wall-clock mode a demo mode: it moves one data hour per real hour. Use the accelerated clock on stage.

## If something goes wrong

- No incident after 20 seconds: say "it is still collecting evidence" and move on to the promo; or play the recording.
- A fault in a small segment at night needs more hours of evidence; use Germany, Android or iOS payments, or a category such as electronics.
- To start over: stop the four processes, run the first command again, empty `incidents/`, start them in the same order.

## The 5-minute slides

1. **The problem** (30 s): too many numbers; failures that are not outages; nobody asks the question.
2. **How it sees the business** (45 s): the graph, one picture from `docs/system-overview.html`; four kinds of line.
3. **The loop that runs by itself** (60 s): read, expect, detect, locate, explain, keep tracking; one fault is one incident, updated in place.
4. **Demo** (60 s): a screenshot of the console with an incident and the lit path.
5. **Results** (60 s): the table from `docs/results.md`; say the caveat on the same slide.
6. **What failed and what next** (45 s): two unconfirmed causes (stockouts); competing-cause comparison; automatic new-metric placement; low-traffic hours.

# Demo script (3 minutes)

Status: draft written before the brain's monitor exists. Lines marked **[B]** depend on brain/ and must be
checked against the real thing before the first rehearsal. Everything else was run.

## Before we start (T minus 5 minutes)

```
python -m sim.run --backfill 28d --force        # clean 4-week history, about 3 s
python -m brain.run ...                          # [B] the monitor; command to be filled in by B
python -m sim.live --speed 2                     # one simulated hour every 2 s
uvicorn ui.app:app --port 8000                   # open http://127.0.0.1:8000
```

Check: the clock in the header moves every 2 s; the graph shows; the incident list is empty (or shows the marked
contract example until the brain writes its first incident).

## Timeline

| Time | Say | Do | Must be visible |
|---|---|---|---|
| 0:00 | "A marketplace has thousands of metrics and nobody can watch them all. This system watches by itself." | Header and graph on screen | Moving clock, formula tree |
| 0:20 | "It knows how the business works: orders are checkouts times payment success. Each line has a meaning." | Point at the tree; name the four line types | Tree from contribution down |
| 0:45 | "Fault: a payment release breaks Android payments in Germany." | Choose **Payment failure** (DE, Android) in the DEMO bar, click **Inject fault** | Toast "Payment failure injected from ..." |
| 1:00 | "It notices, follows the graph, and tells us where and why." | Wait for the incident (about 3 to 6 simulated hours = 6 to 12 s) [B] | Incident appears; path orders to payment success lit in red |
| 1:20 | Read the report: impact in euros, 91% of the change in payment success, Germany, Android, the release; what is still uncertain | Click the incident | Impact, hypotheses with evidence, trail |
| 1:35 | "Someone rolls the release back. The incident should close by itself." | Pick the fault in **In progress**, click **End it (rollback)** | A rollback release appears; after a few normal hours the incident turns resolved [B] |
| 1:45 | "Not everything unusual is a problem: a planned French promo lifts orders." | Click **Promo (12 h)** (FR) | No new incident; the calendar explains it [B] |
| 2:00 | "If data arrives late, it waits instead of crying wolf." | Click **Arrives 2 h late** (payments) | No incident for those hours [B] |
| 2:15 | "A new metric arrives as a definition and a query. The system finds its place by itself." | Click **Add refund_rate** | New node in the graph, status validated [B] |
| 2:40 | "We scored it on N random faults and innocent events: detection, delay, false alarms." | Show `docs/results.md` | Numbers from `python -m sim.score`, not hand-picked |
| 2:55 | One-line close: "It watches the business, not the servers." | | |

## If something hangs

1. The simulator or UI is unresponsive: restart it (`Ctrl+C`, run the command again). Data is in `data/market.db`; the
   history stays.
2. The brain does not report within 20 s: say "it is still investigating", then show the pre-recorded run (screen
   recording kept next to this file) instead of waiting.
3. Network or API problem: the core loop does not use the language model; the report falls back to a template.
4. Last resort: play the recorded video.

## Other faults the DEMO bar can inject

| Fault | What changes in the data | Where a monitor should end up |
|---|---|---|
| Payment failure | Payment success rate falls for one country and platform; a payments-sdk release is on record | orders, payment success rate, the country and platform, the release |
| Stockouts | More product views meet an unavailable listing in one category; add-to-cart rate falls, feed clicks rise | orders, add-to-cart rate, the category, stockout rate (no release) |
| Search regression | Clicks per search fall in one country; a search release is on record | orders, views per session, search clicks per search, the country, the release |
| Ad budget gone early | Sponsored impressions per session jump, then collapse after a few hours; traffic is unchanged; a pacing release is on record | ad spend, ad load (not sessions), the country |

The scoring script (`python -m sim.score generate`) mixes all four with promos and late data.

## Things to rehearse

- The first fault always at the same hour offset so the timing is the same on every run (`--now` starts at the next hour).
- Reset between rehearsals: stop `sim.live`, run `python -m sim.run --backfill 28d --force`, restart.
- Fault severity in the button is 0.4 (payment success falls by 40%), promo is 12 hours, late data is 2 hours.

# Demo script (3 minutes)

Status: integrated monitor a757828 has been replay-tested, and run live in the order of this script (payment fault, stockouts, search regression, promo: three separate incidents, the promo explained). Live recovery and the three-minute timing still need rehearsal. The whole script was run once in the console at `54ef95b` (investigator and new-metric intake on `main`): new metric validated and watched, payment fault raised and resolved after the rollback, promo explained, late data held.

## Before we start (all terminals at the repository root)

1. Stop `sim.live`, the brain monitor, and the UI with Ctrl+C. Close database viewers too; on Windows a process
   holding SQLite open prevents replacement. Do not reset a teammate's active run.
2. Archive the previous run outside the output folders: `incidents/*.json`, the contents of `incoming_metrics/`
   (including refund_rate and its result), and `data/graph.json`. These are disposable demo outputs, not source files.
   Start with empty incident and incoming-metric folders; do not mix scoring and live incidents.
3. Backfill with explicit paths:

```
python -m sim.run --backfill 28d --db data/market.db --force
python -m brain.run --db data/market.db --incidents incidents --incoming incoming_metrics --graph-out data/graph.json --fresh
# --fresh empties incidents/ (archive it first, step 2, if the run is worth keeping). Without it a restarted
# monitor leaves the previous run's incident and pending files as they were and opens duplicates beside them.
# It also removes old answers in incoming_metrics/. A definition left there is taken again at start, so the
# folder must be empty (step 2) for the new-metric step to happen in front of the audience.
python -m sim.live --speed 2 --db data/market.db
```

4. Start the UI with these environment variables, then `uvicorn ui.app:app --port 8000`:

| Variable | Value |
|---|---|
| MARKET_DB | data/market.db |
| INCIDENTS_DIR | incidents |
| INCOMING_METRICS_DIR | incoming_metrics |
| GRAPH_JSON | data/graph.json |

POSIX shells: prefix uvicorn with `MARKET_DB=data/market.db INCIDENTS_DIR=incidents
INCOMING_METRICS_DIR=incoming_metrics GRAPH_JSON=data/graph.json` (on one line).
PowerShell: set each as `$env:NAME="value"` in the UI terminal before running uvicorn.
The sim commands use `--db`, not MARKET_DB. UI variables are read once at import: restart after changing them.

Check http://127.0.0.1:8000: the clock moves, the exported graph shows and no previous incidents remain.
The contract example may appear before the monitor starts; it is marked as an example, not a result.
Do not start the accelerated clock five minutes early: launch it immediately before the presentation.
At speed 2, a day passes in 48 seconds. Read the header clock before injecting; night-time counts are smaller.

For scoring use a separate folder: `python -m sim.score generate --db data/eval.db --days 49`;
run B's monitor replay against that database, writing to **eval_incidents/**, then
`python -m sim.score score --db data/eval.db --incidents eval_incidents --out data/eval-results.md`.
The page `docs/results.md` is built from many such runs by `python -m scripts.summarize_evaluation` (its docstring has the
command); a single-run report must not overwrite it.
Never point the live UI at eval_incidents. Missing or empty scoring output is an error, not a measured zero.

## Timeline

| Time | Say | Do | Must be visible |
|---|---|---|---|
| 0:00 | "A marketplace has thousands of metrics and nobody can watch them all. This system watches by itself." | Header and graph on screen | Moving clock, formula tree |
| 0:20 | "It knows how the business works: orders are checkouts times payment success. Each line has a meaning." | Point at the tree; name the four line types | Tree from contribution down |
| 0:45 | "Fault: a payment release breaks Android payments in Germany." | Choose **Payment failure** (DE, Android) in the DEMO bar, click **Inject fault** | Toast "Payment failure injected from ..." |
| 1:00 | "It notices, follows the graph, and tells us where and why." | Wait for the incident (about 3 to 6 simulated hours = 6 to 12 s) [B] | Incident appears; path orders to payment success lit in red |
| 1:20 | Read the report: the actual impact in euros, measured contribution, affected country/platform and supported cause; what is still uncertain | Click the incident | Impact, hypotheses with evidence, trail |
| 1:35 | "Someone rolls the release back. The incident should close by itself." | Pick the fault in **In progress**, click **End it (rollback)** | A rollback release appears; after a few normal hours the incident turns resolved [B] |
| 1:45 | "Not everything unusual is a problem: a planned French promo lifts orders." | Click **Promo (12 h)** (FR) | No new incident; the calendar explains it [B] |
| 2:00 | "If data arrives late, it waits instead of crying wolf." | Click **Arrives 2 h late** (payments) | No incident for those hours [B] |
| 2:15 | "A new metric arrives as a definition and a query. The system checks the query against the formula, finds the metric's place in the graph and starts watching it." | Click **Add refund_rate** | Message "refund_rate added: validated on 28 days of data ... placed as a factor of refunds ... watched from the next tick" (2 to 10 s: it validates the definition on all the data so far); a new tree `refunds` = `orders` x `refund_rate` in the graph [B] |
| 2:40 | "We scored it on 240 injected faults and 160 innocent events in twenty runs, ten of them never seen before: detection, place, cause, false alarms." | Show `docs/results.md` | The table with the holdout line; numbers from `python -m scripts.summarize_evaluation`, not hand-picked |
| 2:55 | One-line close: "It watches the business, not the servers." | | |

## If something hangs

1. The simulator or UI is unresponsive: restart it (`Ctrl+C`, run the command again). Data is in `data/market.db`; the
   history stays.
2. In accelerated replay only, if the brain does not report within 20 s: state that it has not produced a report. A recording is a fallback only
   after we have recorded and checked one; no recording exists in this snapshot.
3. Network or API problem: the core loop does not use the language model; the report falls back to a template.
4. Use a checked recording if available; otherwise show the graph and data and explain the live failure.

## Other faults the DEMO bar can inject

| Fault | What changes in the data | Where a monitor should end up |
|---|---|---|
| Payment failure | Payment success rate falls for one country and platform; a payments-sdk release is on record | orders, payment success rate, the country and platform, the release |
| Stockouts | More product views meet an unavailable listing in one category; add-to-cart rate falls, feed clicks rise | orders, add-to-cart rate, the category, stockout rate (no release) |
| Search regression | Clicks per search fall in one country; a search release is on record | orders, views per session, search clicks per search, the country, the release |
| Ad budget gone early | Sponsored impressions per session jump, then collapse after a few hours; traffic is unchanged; a pacing release is on record | ad spend, ad load (not sessions), the country |

The scoring script (`python -m sim.score generate`) mixes all four with promos and late data.

## Things to rehearse

- Inject at a chosen header-clock hour and record it in each rehearsal. `--now` affects the latest data hour; it does not fix the hour of day.
- Reset between rehearsals with the full stop/archive/backfill/restart list above.
- Fault severity in the button is 0.4 (payment success falls by 40%), faults default to 12 simulated hours unless rolled back, promo is 12 hours, late data is 2 hours.

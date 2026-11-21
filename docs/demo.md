# Demo script (3 minutes)

Status: draft written before the brain's monitor exists. Lines marked **[B]** depend on brain/ and must be
checked against the real thing before the first rehearsal. Everything else was run.

## Before we start (all terminals at the repository root)

1. Stop `sim.live`, the brain monitor, and the UI with Ctrl+C. Close database viewers too; on Windows a process
   holding SQLite open prevents replacement. Do not reset a teammate's active run.
2. Archive the previous run outside the output folders: `incidents/*.json`, the contents of `incoming_metrics/`
   (including refund_rate and its result), and `data/graph.json`. These are disposable demo outputs, not source files.
   Start with empty incident and incoming-metric folders; do not mix scoring and live incidents.
3. Backfill with explicit paths:

```
python -m sim.run --backfill 28d --db data/market.db --force
# [B] Start python -m brain.run with the SAME database, incidents and incoming-metrics paths.
# brain.run does not exist in this snapshot: obtain its real command from B before rehearsing.
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
`python -m sim.score score --db data/eval.db --incidents eval_incidents --out docs/results.md`.
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
| 2:15 | "A new metric arrives as a definition and a query. The system finds its place by itself." | Click **Add refund_rate** | New node in the graph, status validated [B] |
| 2:40 | "We scored it on N random faults and innocent events: detection, delay, false alarms." | Show `docs/results.md` | Numbers from `python -m sim.score`, not hand-picked |
| 2:55 | One-line close: "It watches the business, not the servers." | | |

## If something hangs

1. The simulator or UI is unresponsive: restart it (`Ctrl+C`, run the command again). Data is in `data/market.db`; the
   history stays.
2. The brain does not report within 20 s: state that it has not produced a report. A recording is a fallback only
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
- Fault severity in the button is 0.4 (payment success falls by 40%), promo is 12 hours, late data is 2 hours.

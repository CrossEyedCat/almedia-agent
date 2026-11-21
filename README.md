# Eye of the System — autonomous monitoring for a marketplace

HackLab #2 Berlin Robotics & AI Hackathon (21–22 Nov 2026), **Almedia track**:
*"Eye of the System: Fully Agentic Monitoring"*.

A system that watches a simulated Amazon-like marketplace on its own. It keeps a
graph of how the business works, decides what to watch, notices meaningful changes,
investigates them along the graph, and reports what happened with evidence — without
anyone asking questions.

> All code in this repository is written during the official hacking window, which
> opened on Saturday 21 Nov 2026 at 11:00 CET. The git history is the record.
> Third-party libraries are listed at the end with credit.

---

## 1. What the brief asks for, and how it is judged

| Capability | What it means here | Weight |
|---|---|---|
| Metric graph | Business outcomes linked to their components and breakdowns; each edge has a meaning: formula, breakdown by a dimension, possible cause | 40% (with extension) |
| Automatic extension | A new metric arrives as a definition + query; an agent finds its place, validates it and starts monitoring it | |
| Continuous monitoring | Decide what to watch, learn expected behaviour (seasonality, data completeness, planned events), detect changes worth investigating | 40% (with investigation) |
| Autonomous investigation | Choose the next query, follow the graph, compare explanations, decide when to stop, separate symptoms from first causes | |
| Communication | Alerts with numbers and an investigation trail; track whether an issue persists or resolves; never re-report the same problem | |
| Demo | Run the full loop live: a fault, an innocent change, a new metric | 20% |

## 2. Demo scope (proposed — adjust in the first hour)

A small marketplace, enough to show the complete loop:

- **Dimensions:** country (DE, FR, ES, PL), platform (iOS, Android, Web), category (5), traffic channel (organic, ads, recommendations).
- **Time:** hourly data on a simulated clock; 4 weeks of history backfilled, then the clock runs live (fast-forward for the demo).
- **Domains:** traffic, search, "For You" feed, cart, checkout, payment, orders, revenue, ads, inventory.

Scenarios the simulator must produce:

| # | Kind | Scenario | What the system should do |
|---|---|---|---|
| S1 | Fault | Payment success drops for **Android in Germany** after a payment SDK release | orders ↓ → traffic fine → checkout → payment success ↓ → narrows to DE × Android → matches the release → reports impact and remaining uncertainty |
| S2 | Innocent change | Planned promo in France: traffic and orders up | finds the event in the promo calendar, explains it, does **not** alert |
| S3 | Data completeness | The payments source arrives 2 h late | holds judgement on the incomplete hours instead of raising a false alarm |
| S4 | New metric | `refund_rate` (or `ad_roas`) is added live as definition + SQL | agent places it in the graph, validates the formula and dimensions, starts monitoring it |
| S5 | Fault (stretch) | "For You" shifts exposure to attractive but **out-of-stock** products: feed clicks up, purchases down | follows feed → product exposure → availability; separates the symptom (conversion) from the first cause (inventory filtering) |
| S6 | Fault (stretch) | An advertiser's daily budget is spent by 10:00 | spend pacing → impressions, clicks, prices → pacing controller vs traffic surge |

## 3. Architecture

```
 sim/  (the world)                 brain/  (the eye)                          ui/
 ┌──────────────────┐   SQLite    ┌──────────────────────────────────────┐  JSON   ┌───────────────┐
 │ marketplace sim  │──────────▶ │ metric graph ◀── extension agent       │────────▶│ graph view    │
 │ fault injection  │  facts,    │      │                                │incidents│ alert feed    │
 │ innocent events  │  releases, │      ▼                                │         │ report + trail│
 │ data delays      │  calendar, │ monitor ──▶ investigator ──▶ reporter │         │ inject buttons│
 │ ground truth     │  ingestion │      ▲            │                   │         └───────────────┘
 └──────────────────┘   log       │      └── incident lifecycle ◀─────────┘
                                  └──────────────────────────────────────┘
```

### 3.1 Three contracts (agree first, then work in parallel)

**Data (SQLite, written by `sim/`, read by `brain/`):**

```
facts_hourly(ts, country, platform, category, channel,
             sessions, searches, search_clicks, feed_impressions, feed_clicks,
             product_views, add_to_cart, checkouts, payment_attempts, payment_successes,
             orders, gmv, discounts, marketplace_fees, refunds,
             ad_impressions, ad_clicks, ad_spend, stockouts)
ingestion_log(source, ts, arrived_at)           -- data completeness per source and hour
releases(ts, system, country, platform, description)
calendar(start_ts, end_ts, country, kind, description)   -- promos, holidays
ground_truth(injected_at, kind, scope, description)      -- hidden from brain/, used for scoring
```

**Metric definition (how a metric arrives, including the live one in the demo):**

```yaml
name: refund_rate
description: Share of orders refunded
formula: refunds / orders          # optional; the agent infers it when missing
sql: SELECT ts, country, platform, category, SUM(refunds) * 1.0 / SUM(orders) AS value
     FROM facts_hourly GROUP BY ts, country, platform, category
dimensions: [country, platform, category]
unit: ratio
```

**Incident (written by `brain/`, read by `ui/`):**

```json
{
  "id": "inc-0007", "status": "open | ongoing | resolved",
  "first_seen": "...", "last_seen": "...",
  "metric": "orders", "segment": {"country": "DE", "platform": "Android"},
  "change": {"observed": 812, "expected": 1190, "delta_pct": -31.8, "z": -7.4},
  "impact": {"gmv_per_hour": -15300},
  "contributions": [{"node": "payment_success_rate", "share_of_delta": 0.86}],
  "hypotheses": [{"cause": "release payments-sdk 4.2 (DE, Android)", "status": "supported", "evidence": ["..."]},
                 {"cause": "traffic drop", "status": "rejected", "evidence": ["..."]}],
  "trail": [{"step": 1, "action": "decompose orders", "result": "..."}],
  "uncertain": ["..."],
  "report": "plain-language summary"
}
```

### 3.2 Metric graph

- **Nodes:** metrics (`orders`, `checkout_rate`, `payment_success_rate`, `gmv`, `contribution`, …) and context sources (releases, calendar, data freshness, inventory).
- **Edges, each with its own reasoning:**
  - `formula` — exact arithmetic (`orders = checkouts × payment_success_rate`); supports contribution decomposition.
  - `breakdown` — split by a dimension (`orders` by `country`); supports drill-down; additive metrics must sum to the parent, ratios split into mix and rate effects.
  - `may_affect` — a possible cause (`stockouts → feed_conversion`); a hypothesis edge with a prior strength, checked with timing and lagged correlation.
  - `context` — links metrics to releases, promos and freshness by system, country and platform.
- **Tree view:** a spanning tree over `formula` edges from the top outcome (`contribution`) for people to browse.

### 3.3 Extension agent

1. Parse the formula when given (symbolic parse); otherwise an LLM proposes one from the description and SQL.
2. Link to existing metrics whose names appear in the formula; read the dimensions from the SQL.
3. Validate numerically: recompute the formula from its components on history, check that breakdowns add up, check the value range.
4. Propose `may_affect` links (LLM suggestion + lagged correlation on history); keep them `tentative` until the data supports them.
5. Register with a status (`validated` / `tentative` / `rejected`) and start monitoring.

### 3.4 Monitoring (proposed algorithm)

- **What to watch:** every tick, the top outcomes and their direct formula children; breakdowns are scanned on a rotating budget, prioritised by business impact (sensitivity of the top outcome to the node) × detectability (signal vs noise).
- **Expected behaviour:** hour-of-week seasonal baseline (median of past weeks) scaled to the recent level; planned events from the calendar shift the expectation instead of raising alerts.
- **Data completeness gate:** hours whose sources have not fully arrived are marked pending, not judged.
- **Detection:** robust z-score on the residual + a persistence check (CUSUM) + a minimum business impact, so small blips do not start investigations.

### 3.5 Investigation (proposed algorithm)

- **Best-first search over the graph** with a query budget.
- **Formula step:** decompose the change into each component's contribution (additive for sums, log-ratio for products and ratios) and follow the largest.
- **Breakdown step:** choose the dimension whose split concentrates the change best (explanatory power and surprise, Adtributor-style), then the segment within it.
- **Context step:** check data freshness, releases and calendar events that overlap in time and segment.
- **Competing explanations:** each hypothesis gets an evidence score (share of the delta explained, timing match, segment match, consistency across metrics).
- **Stop rule:** the leading hypothesis explains most of the change, nothing further upstream explains it better, or the budget runs out.
- **Symptom vs first cause:** a node is reported as a first cause only when its own change explains its children's change and no upstream node explains it.
- Each hypothesis is labelled `supported`, `plausible` or `rejected`.

### 3.6 Reporting and lifecycle

- **Deduplication:** a fingerprint (root metric, segment, cause) prevents repeated alerts; a recurring issue updates its incident instead.
- **Lifecycle:** `open` → `ongoing` → `resolved` after K normal hours.
- **Report content:** what changed, where, business impact, evidence numbers, investigation trail, what remains uncertain.

### 3.7 Where the language model is used

The core loop runs without it, so the demo cannot fail on an API call.

- Proposing formulas and `may_affect` links for new metrics.
- Writing the narrative from the structured evidence; a template is the fallback.
- Optionally, choosing between equally scored next steps and explaining why.

## 4. Team split

| Person | Owns | First deliverable for the other |
|---|---|---|
| A — world & showcase | `sim/`, fault and event injection, ground-truth scoring, `ui/`, demo script, video | small SQLite dataset within ~1 h |
| B — brain | `brain/`: graph, extension agent, monitor, investigator, reporter | sample `incident.json` within ~1 h |

Each side works against a stub of the other until the first integration.

## 5. Development path

Times are CET. Safe deadline: **Sunday 15:00** (the event T&C says 15:00 on day 2; the submission page says 16:30 — we use the earlier one).

| Milestone | When | A — world & showcase | B — brain | Done when |
|---|---|---|---|---|
| M0 Setup | Sat 11:00–12:30 | repo, env, contracts, scenario list | same, together | contracts in this README agreed |
| M1 Clean world + graph v1 | Sat 12:30–15:00 | simulator v1: 4 weeks of hourly data with daily and weekly seasonality, no faults | graph v1: core metrics with `formula` and `breakdown` edges, tree view | `brain` loads `sim` data and prints the tree |
| M2 First fault | Sat 15:00–19:00 | injection of S1 (payment fault) + ground truth | monitor v1: seasonal baseline, completeness gate, detection | **first end-to-end at dinner:** S1 is detected |
| M3 Investigation | Sat 19:00–00:00 | UI v1: graph view and alert feed | investigator v1: decomposition, drill-down, context checks, incident JSON | S1 report names DE × Android and the release |
| M4 Extension and innocence | Sun 00:00–06:00, in shifts | S2 promo, S3 data delay, inject buttons, report view | extension agent (S4), hypothesis scoring, symptom vs cause | S2 explained without alert, S3 held, S4 placed and validated |
| M5 Polish | Sun 08:00–12:00 | S5 if time; scoring harness (detection rate, false alarms, correct cause on random injections) | lifecycle and dedup; LLM narration with template fallback | full loop runs unattended for a simulated day |
| Freeze | Sun 12:00 | — | — | no new features |
| Ship | Sun 12:00–14:30 | demo rehearsals, 3-min video, slides | README "how it works", algorithm notes, numbers from the scoring harness | submitted by 14:30 |

## 6. Task checklist for two

**A** = world & showcase, **B** = brain, **A+B** = together. Tick items in this file as you finish them and commit — the list is our shared status board.

### M0 Setup — Sat 11:00–12:30

- [ ] **A+B** Accept the repo invite, clone, create the venv, `pip install -r requirements.txt`.
- [ ] **A+B** Each claims the free Claude API credits (event page → *Infrastructure Tools & Credits*) and puts the key in their own `.env`.
- [ ] **A+B** Fix the scenario list: S1–S4 are mandatory, S5 and S6 only if time allows.
- [ ] **A+B** Fix the dimensions: countries, platforms, categories, channels (§2).
- [ ] **A+B** Freeze the three contracts in §3.1: column names, units, timestamp format (UTC, hour start, `2026-11-01T13:00:00Z`), database path `data/market.db`.
- [ ] **A+B** Define the top outcome: `contribution = marketplace_fees + ad_revenue − platform_funded_discounts − payment_costs` (or simpler — decide now, it is the root of the tree).
- [ ] **A+B** Agree the simulated clock: history start date, 4 weeks of backfill, demo speed (e.g. 1 simulated hour every 2 s).
- [ ] **A+B** Agree who edits what: A only `sim/` and `ui/`, B only `brain/`; shared files (README, `docs/contracts/`) only after telling the other.
- [ ] **A** Put contract fixtures in `docs/contracts/`: `facts_hourly.sql` (table DDL), `metric_example.yaml`.
- [ ] **B** Put `docs/contracts/incident_example.json` there, with realistic values for S1.
- [ ] **B** A stub in `brain/` that opens `data/market.db` and prints row counts per table — proof that the data contract works.

### M1 Clean world + graph v1 — Sat 12:30–15:00

**A — simulator v1**
- [ ] `sim/world.py`: base rates per country × platform × category × channel.
- [ ] Seasonality: hour-of-day curve, weekend effect, slow trend, noise (Poisson for counts, binomial for conversions).
- [ ] A consistent funnel: sessions → searches / feed impressions → product views → add to cart → checkouts → payment attempts → payment successes → orders → GMV. Every step is a rate, so the graph formulas hold exactly.
- [ ] Money: GMV, discounts (seller- and platform-funded), marketplace fees, refunds, ad impressions, clicks and spend, payment costs.
- [ ] Write `facts_hourly`, `ingestion_log` (everything on time for now), and empty `releases`, `calendar`, `ground_truth`.
- [ ] `python -m sim.run --backfill 28d` finishes in under 30 s.
- [ ] **Handoff to B by ~13:30:** a small database (2 weeks, 2 countries) committed to `docs/contracts/sample.db` or shared directly.

**B — graph v1**
- [ ] `brain/graph.py`: node and edge types (`formula`, `breakdown`, `may_affect`, `context`) with metadata (formula text, dimension, prior strength, status).
- [ ] Core metric definitions in `brain/metrics/*.yaml` following the contract — about 15 metrics from `contribution` down to the funnel rates.
- [ ] Evaluator: compute any metric for any segment and time window from the database.
- [ ] Validation on load: every formula recomputed against its components; breakdowns of additive metrics sum to the parent.
- [ ] Tree view in the terminal: `python -m brain.graph --tree`.
- [ ] **Handoff to A by ~13:30:** `incident_example.json` and a `graph.json` export (nodes and edges) for the UI.

### M2 First fault — Sat 15:00–19:00

**A — injection and live mode**
- [ ] `sim/inject.py`: `payment_failure(country, platform, start, severity)` — writes the SDK release to `releases` and the truth to `ground_truth`.
- [ ] Live mode: the clock moves forward, appends hours, applies injections: `python -m sim.run --live --speed 2s`.
- [ ] A small injection CLI for testing: `python -m sim.inject payment_failure --country DE --platform Android`.

**B — monitor v1**
- [ ] Baseline: hour-of-week median over the past 3 weeks × level factor from the last 24 h.
- [ ] Completeness gate: hours whose sources are missing in `ingestion_log` are pending, not judged.
- [ ] Detector: robust z-score on the residual + CUSUM persistence + minimum impact in € per hour.
- [ ] Watch list: top outcomes and their formula children every tick; breakdowns on a rotating budget.
- [ ] Loop: `python -m brain.run` polls the database every tick and logs detections.

**A+B checkpoint (dinner):** live simulator with S1 → the brain detects it; write down the detection delay in simulated hours.

### M3 Investigation — Sat 19:00–00:00

**A — UI v1**
- [ ] `ui/` FastAPI app: `/api/graph`, `/api/incidents`, `/api/incidents/{id}` and one static page.
- [ ] Graph view (e.g. Cytoscape.js from a CDN): edge types in different colours, the incident path highlighted.
- [ ] Alert feed: status, metric, segment, impact, first and last seen.

**B — investigator v1**
- [ ] Contribution decomposition along `formula` edges (additive for sums, log-ratio for products and ratios).
- [ ] Drill-down: pick the dimension with the best explanatory power and surprise, then the segment; recurse up to depth 3.
- [ ] Context checks: releases, calendar events and data freshness that overlap in time and scope.
- [ ] Trail: every query and its result is recorded as a step.
- [ ] Incident JSON written to `incidents/` per the contract.

**A+B checkpoint:** the S1 incident in the UI says DE × Android, `payment_success_rate`, release `payments-sdk`, with numbers.

### M4 Extension and innocent changes — Sun 00:00–06:00 (sleep in shifts)

**A — more of the world, more UI**
- [ ] S2: planned promo in FR in `calendar` + matching traffic and order uplift.
- [ ] S3: the payments source arrives 2 h late in `ingestion_log`.
- [ ] Buttons in the UI: payment fault, promo, data delay, add metric.
- [ ] Report view: evidence numbers, hypotheses with status, trail, what remains uncertain.
- [ ] "Add metric" endpoint: POST a metric YAML, hand it to the brain, show the result.

**B — extension and reasoning**
- [ ] Extension agent: parse formula → link parents → read dimensions from SQL → validate on history → propose `may_affect` links (LLM + lagged correlation) → status.
- [ ] Calendar-aware expectation: a planned event shifts the baseline and is explained, not alerted.
- [ ] Hypothesis scoring with `supported` / `plausible` / `rejected`.
- [ ] Symptom vs first cause rule.

**A+B checkpoint:** S2 gives no alert (explained), S3 is held, S4 is placed and validated and visible in the graph.

**Sleep:** A 00:30–04:30, B 04:30–08:30 (or swap). Whoever is awake keeps the other's part untouched.

### M5 Polish — Sun 08:00–12:00

**A**
- [ ] S5 (feed pushing out-of-stock products), only if everything above works.
- [ ] Scoring harness: N random injections + random innocent events → detection rate, false alarms per simulated day, correct-cause rate, mean detection delay → `docs/results.md`.
- [ ] Demo script `docs/demo.md`: exact clicks and timing, plus a fallback if something hangs.

**B**
- [ ] Lifecycle `open → ongoing → resolved`; dedup by fingerprint; updates instead of new alerts.
- [ ] LLM narration from structured evidence, template fallback, 20 s timeout.
- [ ] Unattended run of one simulated day with mixed events: no crash, no duplicate alerts.

**A+B** From 12:00 only bug fixes.

### Ship — Sun 12:00–14:30

- [ ] **A+B** Two full demo rehearsals against a 3-minute timer.
- [ ] **A** Record the screen and one of us speaking to camera; cut to ≤ 3:00; upload to YouTube as unlisted; open the link in a private window to check access.
- [ ] **B** README "How it works": algorithm summary and the numbers from `docs/results.md`; complete the library list (§10).
- [ ] **A+B** Slides for 5 minutes: problem, graph, autonomous loop, demo screenshot, results, what failed, what next.
- [ ] **B** Make the repo public: `gh repo edit CrossEyedCat/almedia-agent --visibility public --accept-visibility-change-consequences`; check it from a logged-out browser.
- [ ] **A** Submit the form with the registration email **by 14:30**; screenshot the confirmation.
- [ ] **A+B** Be at the venue for the 18:00 pitches.

### Always

- [ ] Small commits; `git pull --rebase` before every push; never commit `.env` or `data/`.
- [ ] Blocked for more than 30 minutes → tell the other and cut scope.
- [ ] Keep this checklist honest: tick what is really done.

## 7. Submission checklist

Submit at https://tally.so/r/GexoxQ with the email used for registration.

- [ ] 3-minute video: pitch, how the solution works, live demo; a team member speaking to camera. YouTube (unlisted) or Google Drive, open to anyone with the link.
- [ ] This repository public, with documentation.
- [ ] Short slides for a 5-minute stage pitch if we win the track.

## 8. Repository layout

```
sim/         marketplace simulator, injections, ground truth
brain/       graph, extension agent, monitor, investigator, reporter
ui/          dashboard
docs/        algorithm notes, demo script
tests/       tests
data/        SQLite database (not committed)
incidents/   incident JSON output (not committed)
```

## 9. Getting started

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Put the API key in `.env` (never commit it):

```
ANTHROPIC_API_KEY=...
```

## 10. Third-party libraries

anthropic, fastapi, uvicorn, httpx, pandas, pytest, python-dotenv — used as published packages under their own licenses. More are added here as they come in.

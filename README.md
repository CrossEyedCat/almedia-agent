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

A picture of the brain - the data path, the graph as loaded from the code and the planned loop - is in [`docs/brain-architecture.html`](docs/brain-architecture.html) (open it in a browser; rebuild with `python docs/build_brain_architecture.py`).

- **Nodes:** metrics, breakdown views (`orders@country`) and context sources (`release:<system>`, `freshness:<source>`, `calendar`).
- **Edges, each with its own reasoning:**
  - `formula` — an exact identity used for decomposition (`gmv = orders × aov`). The formula graph is a DAG, not a tree: `gmv` feeds `marketplace_fees` with a plus and `discounts` with a minus, `sessions` feeds `orders` and `ad_impressions`.
  - `definition` — how a derived metric is computed when that differs from its decomposition (`take_rate = marketplace_fees / gmv`).
  - `breakdown` — split by a dimension; additive metrics sum, ratios are always ratios of sums.
  - `may_affect` — a possible cause with a prior and a sign (which way the effect moves when the cause rises). It stays `assumed` until it is tested inside an incident; it is never "validated" by correlation on history (see 3.8).
  - `context` — releases, the promo calendar and data freshness. Freshness is inherited through formulas: a metric is as fresh as its least fresh source.
- **Declared outcome:** `contribution` carries `outcome: true`. A contributed metric never becomes a second root by accident; until it is placed it is listed under `unplaced_roots`.
- **Tree view:** a spanning tree over `formula` edges from the declared outcome, for people to browse. The algorithms work on the DAG.
- **What "validated" means:** every check has a basis. `definition` — it follows from the metric definitions alone and no data can make it fail (`orders = sessions × (orders / sessions)`); it shows the definitions are consistent. `data` — it can fail on the data (coverage, bounds, a contributed SQL against its formula). The validation report keeps the two apart.

### 3.3 Extension agent (M4)

Deterministic first: a new metric gets a formula and a place without a language model.

1. **Run the SQL** in the sandbox (contract tables only, time and row limits) and read its grain from the result columns.
2. **Find the formula.** Use the given one, or search formulas over existing metrics (a single metric, `a / b`, `a × b`, `a ± b`, with a constant) that reproduce the SQL values at its grain: about 4,000 candidates, under a second.
3. **Check what the SQL really defines.** Compare row sets in both directions: a SQL that returns only part of the rows is a filtered metric and is registered as scoped or `tentative`, not as valid everywhere. Roll-up test: split every fact row in two and rerun the SQL; a function of sums does not change, an average of rows does.
4. **Recognise aliases.** Each definition has a numeric signature (its value on random column values). A new metric with the signature of an existing one is reported as an alias, not added as a node.
5. **Find its place.** Search identities that link the new metric to existing ones (`x = y × m`, `x = y + m`, `m = x / y`, …). Rank them (a parent inside the outcome tree first, then fewer hops to the outcome) and keep the others as alternatives. When several fit equally the status is `ambiguous` and the report says so.
6. **Cause links.** Proposed links (rules or a language model) are admitted only if they pass structural checks (no cycle, not already arithmetic, not a duplicate) and they stay `assumed`.
7. **Re-validate the whole graph** (0.7 s on 28 days) and start monitoring the metric at its grain.

### 3.4 Monitoring (M2)

- **Clock and completeness.** `now` and the meaning of `arrived_at` are part of the data contract. An hour of a metric is judged only when every source the metric depends on has arrived; until then it is *pending*: neither healthy nor an alert.
- **What to watch.** The leaf factors of the formula graph — the step rates of the funnel, sessions and the other volume leaves — in every country × platform cell and in total: 12 cells × about 10 factors, one query of a few milliseconds per tick. Top outcomes are used for impact and reporting, not for detection: a fault in one cell is diluted in them (3.8).
- **Expected state.** Built for the base columns at the watched grain from the same hour-of-week in previous weeks; every metric is then derived from those expected sums (`Evaluator.derive`). Expected values therefore obey the same formulas and add up over segments. A separate median baseline per metric does not.
- **Planned events.** A calendar event that overlaps a deviation in time and scope explains it: reported as explained, not alerted.
- **Detection.** For a rate, a binomial test of successes given attempts against the expected rate, with its spread calibrated on clean history. For a volume, a robust z-score of the seasonal residual. Then persistence over consecutive hours and a minimum business impact in EUR, so small blips do not start investigations.

### 3.5 Investigation (M3)

- **Two coherent states.** Actual and expected sums for the incident window at the finest grain; everything below compares these two.
- **Attribution.** One function gives, for every leaf factor, the part of the outcome change it explains, in EUR. It walks the formula DAG and sums over all paths; sums split exactly, products use the logarithmic mean Divisia index (LMDI, an exact decomposition), with a Shapley split as the fallback for zeros and other formula shapes. It never just follows the largest child: `gmv` reaches `contribution` through fees and through discounts with opposite signs.
- **Localization.** For the leading factor, every element of every combination of dimensions is scored (479 elements for 4 countries, 3 platforms, 5 categories and 3 channels) by how well "one common shift of the rate inside this element" explains the data. The best element is accepted when it clearly beats the runner-up. The scan is exhaustive because it is cheap here; no greedy order of dimensions is needed.
- **Mix versus rate.** Attribution runs per cell and is then summed, so a shift in the mix of segments is not booked as a change of a rate.
- **Context.** Data freshness first. Then every release and calendar event that starts no later than an hour after the onset and whose scope (country, platform) contains the localized segment is a candidate; `systems` tags rank the candidates.
- **Hypotheses.** Candidates are leaf factors in a cell, events and `may_affect` parents. A `may_affect` hypothesis is tested inside the incident: the cause must itself be anomalous in the same segment, in the same or an earlier hour, and move the way its sign predicts. Each hypothesis gets a score (share of the change explained, timing, scope match, consistency) and a label: `supported`, `plausible` or `rejected`.
- **First cause versus symptom.** First-cause candidates are leaf factors and events; a metric above them is a symptom of its factors.
- **Stop rule.** The leading hypothesis explains most of the change and beats the runner-up by a margin, or nothing is left to test, or the query budget is spent.
- **Honesty check.** A plain ranking of the most anomalous factor and cell is kept as a baseline in the scoring harness; the graph has to beat it.

### 3.6 Reporting and lifecycle

- **Deduplication:** a fingerprint (root metric, segment, cause) prevents repeated alerts; a recurring issue updates its incident instead.
- **Lifecycle:** `open` → `ongoing` → `resolved` after K normal hours.
- **Report content:** what changed, where, business impact, evidence numbers, investigation trail, what remains uncertain.

### 3.7 Where the language model is used

The core loop runs without it, so the demo cannot fail on an API call.

- Writing the narrative from the structured evidence; a template is the fallback.
- Suggesting descriptions and possible cause links for new metrics; suggestions pass the structural checks of 3.3 and stay `assumed`.
- Not needed to find a formula or a place for a new metric: that is a deterministic search (3.3).

### 3.8 What the algorithm choices are based on

After M1 the plan was checked against open implementations and published research, and against measurements on the simulator's data (all data here is simulated). The numbers below were measured during that review on a 28-day history with the payment fault S1 in DE × Android.

| Decision | Based on | Measured here |
|---|---|---|
| A rate is always a ratio of sums; numerator and denominator are first-class metrics | [dbt MetricFlow ratio metrics](https://docs.getdbt.com/docs/build/ratio) | — |
| The graph is specified and typed, not learned from the series; cause links are not validated by lagged correlation | [Pham et al. 2024](https://arxiv.org/abs/2408.13729): root-cause analysis on learned causal graphs is close to random; [Fan et al. 2024](https://arxiv.org/abs/2406.02191): aggregation over time hides the lags | effects land in the same hour; 390 of 1,260 metric pairs correlate above 0.5 through shared traffic |
| Detect on step rates in country × platform cells, not on top outcomes | [Adtributor](https://files.usenix.org/s3fs-public/cdn/nsdi14-paper-bhagwan.pdf), [PSqueeze](https://ar5iv.labs.arxiv.org/html/2305.03331), [RiskLoc](https://arxiv.org/pdf/2205.10004) | deviation in units of noise: total orders −0.8, orders in the cell −2.9, payment success rate in the cell −17.2 |
| One expected state for the base columns, metrics derived from it | RiskLoc and PSqueeze define the forecast of a derived measure from the forecasts of its fundamental measures; [hierarchical forecasting](https://otexts.com/fpp3/hierarchical.html) | separate medians break the identities by 1.5 % on average (up to 7 %) and segments miss the total by 2.5 % (up to 11 %) |
| Localize by scoring every element of every cuboid for a common shift | HotSpot, Squeeze / PSqueeze, RiskLoc ([open implementations](https://github.com/shaido987/riskloc)), [ThirdEye](https://www.linkedin.com/blog/engineering/analytics/analyzing-anomalies-with-thirdeye) | exact cell found in 33 of 36 one-hour windows, against 12 of 36 for a one-dimension-at-a-time drill-down |
| Attribution over all paths of the formula DAG; LMDI for products | [Ang 2005](https://ideas.repec.org/a/eee/enepol/v33y2005i7p867-871.html) | along one path the payment rate "explains" 146 % of the change; over all paths 91 % |
| Completeness from arrival time against a defined clock | [The Dataflow Model](https://www.vldb.org/pvldb/vol8/p1792-Akidau.pdf), [dbt source freshness](https://docs.getdbt.com/reference/resource-properties/freshness) | "source row missing" never fires: a late source still gets its row, only `arrived_at` moves |
| New metrics: canonical comparison, alias detection, placement by search | [Uber uMetric](https://www.uber.com/blog/umetric/); random evaluation of formulas (polynomial identity testing) | formula for `refund_rate` found from its SQL result in 0.5 s without a language model |
| Contributed SQL runs with a table whitelist inside SQLite, plus time and row limits | [SQLite security guide](https://www.sqlite.org/security.html) | none of about 80 bypass attempts read the scoring table |

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
- [x] **A** Put contract fixtures in `docs/contracts/`: `facts_hourly.sql` (table DDL), `metric_example.yaml`. The table DDL is `docs/contracts/schema.sql` (generated by `python -m sim.schema`).
- [x] **B** Put `docs/contracts/incident_example.json` there, with realistic values for S1.
- [x] **B** A stub in `brain/` that opens `data/market.db` and prints row counts per table — proof that the data contract works.

### M1 Clean world + graph v1 — Sat 12:30–15:00

**A — simulator v1**
- [x] `sim/world.py`: base rates per country × platform × category × channel.
- [x] Seasonality: hour-of-day curve, weekend effect, slow trend, noise (Poisson for counts, binomial for conversions). (Counts are Poisson and binomial, drawn through the inverse CDF so one segment's change cannot move another's numbers.)
- [x] A consistent funnel: sessions → searches / feed impressions → product views → add to cart → checkouts → payment attempts → payment successes → orders → GMV. Every step is a rate, so the graph formulas hold exactly.
- [x] Money: GMV, discounts (seller- and platform-funded), marketplace fees, refunds, ad impressions, clicks and spend, payment costs. (Contract as in `brain/metrics/money.yaml`: `discounts` are platform-funded, `contribution = marketplace_fees + ad_spend − discounts`; there is no payment-costs column.)
- [x] Write `facts_hourly`, `ingestion_log` (everything on time for now), and empty `releases`, `calendar`, `ground_truth`.
- [x] `python -m sim.run --backfill 28d` finishes in under 30 s. (Measured: about 3 s for 4 countries.)
- [x] **Handoff to B by ~13:30:** a small database (2 weeks, 2 countries) committed to `docs/contracts/sample.db` or shared directly. Done as code: `python -m sim.run --backfill 14d --countries DE,FR --db data/sample.db` (about 1 s); no .db is committed.

**B — graph v1**
- [x] `brain/graph.py`: node and edge types (`formula`, `breakdown`, `may_affect`, `context`) with metadata (formula text, dimension, prior strength, status).
- [x] Core metric definitions in `brain/metrics/*.yaml` following the contract — about 15 metrics from `contribution` down to the funnel rates.
- [x] Evaluator: compute any metric for any segment and time window from the database.
- [x] Validation on load: every formula recomputed against its components; breakdowns of additive metrics sum to the parent.
- [x] Tree view in the terminal: `python -m brain.graph --tree`.
- [x] **Handoff to A by ~13:30:** `incident_example.json` and a `graph.json` export (nodes and edges) for the UI.

### M2 First fault — Sat 15:00–19:00

**A — injection and live mode**
- [x] `sim/inject.py`: `payment_failure(country, platform, start, severity)` — writes the SDK release to `releases` and the truth to `ground_truth`. Also `promo` and `data_delay`.
- [x] Live mode: the clock moves forward, appends hours, applies injections: `python -m sim.run --live --speed 2s`. Command is `python -m sim.live --speed 2` (injections with `--now`).
- [x] A small injection CLI for testing: `python -m sim.inject payment_failure --country DE --platform Android`.

**B — monitor v1**
- [ ] Clock and completeness: `now` and the meaning of `arrived_at` agreed with A in `docs/contracts/`; a reader for arrivals; an hour is pending until every source the metric depends on (`graph.sources`) has arrived.
- [ ] Expected state: the same hour-of-week over the past 3 weeks for the base columns at country × platform grain; metrics derived from it with `Evaluator.derive`, so identities and sums over segments hold.
- [ ] Watch list: leaf factors of the formula graph (step rates, sessions and other volume leaves) in every country × platform cell and in total; one query per tick.
- [ ] Detector: binomial test for rates, robust z-score for volumes, persistence over consecutive hours, minimum impact in €.
- [ ] Loop: `python -m brain.run` polls the database every tick (one snapshot per tick, trailing hours re-read) and logs detections.

**A+B checkpoint (dinner):** live simulator with S1 → the brain detects it; write down the detection delay in simulated hours.

### M3 Investigation — Sat 19:00–00:00

**A — UI v1**
- [x] `ui/` FastAPI app: `/api/graph`, `/api/incidents`, `/api/incidents/{id}` and one static page. (No `/api/incidents/{id}`: the list returns the full incidents. Also `/api/state`, `/api/inject`, `/api/metric`.)
- [x] Graph view (e.g. Cytoscape.js from a CDN): edge types in different colours, the incident path highlighted. (Own inline SVG, no Cytoscape: the formula tree, the incident route lit in red; checked in Chrome with the contract example.)
- [x] Alert feed: status, metric, segment, impact, first and last seen.

**B — investigator v1**
- [ ] Attribution: one function over the formula DAG, all paths summed (exact for sums, LMDI for products, Shapley fallback); result in € of the outcome.
- [ ] Localization: score every element of every combination of dimensions (479) for the leading factor; accept the best when it clearly beats the runner-up.
- [ ] Context checks: data freshness first, then every release and calendar event that overlaps the localized segment in time and scope, ranked by `systems` tags.
- [ ] Trail: every query and its result is recorded as a step.
- [ ] Incident JSON written to `incidents/` per the contract.

**A+B checkpoint:** the S1 incident in the UI says DE × Android, `payment_success_rate`, release `payments-sdk`, with numbers.

### M4 Extension and innocent changes — Sun 00:00–06:00 (sleep in shifts)

**A — more of the world, more UI**
- [x] S2: planned promo in FR in `calendar` + matching traffic and order uplift.
- [x] S3: the payments source arrives 2 h late in `ingestion_log`. (Any source; `python -m sim.inject data_delay --source payments ...`.)
- [x] Buttons in the UI: payment fault, promo, data delay, add metric.
- [x] Report view: evidence numbers, hypotheses with status, trail, what remains uncertain.
- [ ] "Add metric" endpoint: POST a metric YAML, hand it to the brain, show the result.

**B — extension and reasoning**
- [ ] Extension agent, no language model needed: run the SQL with time and row limits → find or check the formula by search over existing metrics → compare row sets both ways and test the roll-up → detect aliases by signature → find the place (alternatives kept, `ambiguous` when several fit) → re-validate → monitor.
- [ ] Calendar-aware expectation: a planned event that overlaps a deviation in time and scope explains it; reported, not alerted.
- [ ] Hypothesis scoring with `supported` / `plausible` / `rejected`; a `may_affect` link is tested inside the incident (cause anomalous in the same segment, same or earlier hour, direction matches its sign).
- [ ] First-cause rule: candidates are leaf factors in a cell and events; a metric above them is a symptom.

**A+B checkpoint:** S2 gives no alert (explained), S3 is held, S4 is placed and validated and visible in the graph.

**Sleep:** A 00:30–04:30, B 04:30–08:30 (or swap). Whoever is awake keeps the other's part untouched.

### M5 Polish — Sun 08:00–12:00

**A**
- [ ] S5 (feed pushing out-of-stock products), only if everything above works.
- [x] Scoring harness: N random injections + random innocent events → detection rate, false alarms per simulated day, correct-cause rate, mean detection delay → `docs/results.md`. `python -m sim.score generate` then `score`; `docs/results.md` appears when it is run against the brain's incidents.
- [x] Demo script `docs/demo.md`: exact clicks and timing, plus a fallback if something hangs. Draft is there; lines marked [B] need B's confirmation.

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

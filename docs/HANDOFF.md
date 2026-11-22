# Handoff: A is away from Saturday 21 Nov, about 17:10 CET

Handover of 21 Nov, 18:45 CET, kept for the record (a few stale lines were corrected on 22 Nov). For the current state
read the README ("At a glance") and `docs/results.md`; the status lines below (pull requests, issues, the running demo)
are as of 21 Nov and are not updated.

## Where things are

- `main` is at `95a1494` or later, CI green. All pull requests are merged: #15 to #22 (working agreement, contract, scorer with
  real monitor results, wall clock, guardrails, console refresh, demo duration and notes, monitoring as built).
- **End-to-end tests are on main** (`tests/test_end_to_end.py`, about a minute): real simulator with injected faults, the real monitor in
  replay, the incident files, the console's API. They found that the scorer swallowed a correct incident (fixed in #17).
- `docs/results.md` (brain at `b7b856c`, twenty 49-day replays): on ten holdout seeds made after `e220dfc` (111 to 120)
  120/120 faults detected and localized, 116/120 right cause, no false alarm in 457 watched days, 80/80 innocent events silent.
  The ten seeds used while the brain was built give 120/120, 118/120 and no false alarm. The file keeps the two groups apart and
  lists every miss. `docs/stress.md`: eleven targeted cases, ten pass, the miss named.
- Ten issues are still open (#2, #3, #5, #6, #8, #9, #10, #11, #13, #14). Most are fixed on main by the pull requests above; see the
  status comment on #14. Close an issue when you have checked it.
- **The repository is still private.** Only the owner can change that, and it must be public before the submission (below).

## The demo that is running (on A's Mac)

Three processes (monitor, wall clock, console on http://127.0.0.1:8000) from an older commit; they stop when that Mac sleeps.
Do not rely on them. To run the demo yourself from a clean clone, the commands are in `docs/demo.md`
(`python -m sim.run --backfill 28d --db data/market.db --force`, `python -m brain.run --db data/market.db --incidents incidents --incoming incoming_metrics --graph-out data/graph.json --fresh`,
`python -m sim.live --speed 2 --db data/market.db`, `uvicorn ui.app:app --port 8000`; the console also needs `GRAPH_JSON=data/graph.json`
(`docs/demo.md`, step 4), or it shows the tracked example graph). Use the accelerated clock (`--speed 2`) for a talk:
the wall-clock mode moves one data hour per real hour. Stop every process that has the database open before you rebuild it.
Two faults of one metric that begin in the same hour in places sharing a country or a platform can be reported as one incident
(a known limit, README 3.5); inject them an hour apart or in places that share neither a country nor a platform.
`docs/pitch-script.md` has a 3-minute script and a slide outline.

## What still has to be done, and only people can do it

1. Two rehearsals of the 3-minute demo against a timer (`docs/demo.md` has the script and the fallbacks).
2. The 3-minute video: screen recording and a person speaking to camera; YouTube unlisted or Google Drive, open to anyone with the link.
   Check it in a private window.
3. A 5-minute slide deck: problem, graph, the autonomous loop, a demo screenshot, results (`docs/results.md`, with its caveat), what failed, what next.
4. Make the repository public (only the owner can): `gh repo edit CrossEyedCat/almedia-agent --visibility public --accept-visibility-change-consequences`;
   check it from a logged-out browser.
5. Submit the form https://tally.so/r/GexoxQ with the email used for registration, and keep a screenshot of the confirmation.
   Deadlines: the event T&C says Sunday 22 Nov 15:00, the submission page 16:30; use 15:00 (the README's internal target is
   14:30). The old Luma page says 17:45 (ignore it).
   Whose registration email to use is not settled: A registered as Yuli Fang; ask the organisers if B submits.
6. Be at the venue for the 18:00 pitches. A planned to be at home on Sunday until about 17:30 (not confirmed); do not count on A.

## Known gaps (honest list)

- Results are on synthetic data from our own simulator, with the four fault kinds it knows. Ten of the twenty runs are a holdout the brain had not seen; the other ten are not independent.
- Cause confirmed in 236 of 240 faults; the four misses are stockout surges where the report names the stockouts as the candidate but does not confirm it (the add-to-cart rate had not moved visibly). One false alarm in 913 watched days: a one-hour rise of ad click-through in PL on the web, closed by itself. One report (holdout) supports a routine release beside the right one.
- New metrics: built on B's side since `54ef95b` (`brain/extension.py`, README 3.3). The search covers one or two column metrics without constants; cause links for a new metric are not proposed.
- Low-traffic hours are noisy by nature (`docs/data-granularity.md`); a fault that lasts one night hour can be missed.
- The wall-clock mode (`sim.live --wall-clock`) cannot compress a multi-hour recovery into seconds; the demo uses the accelerated clock (`--speed 2`).
- Open issues are the honest to-do list: #2, #3, #5, #6, #8, #9, #10, #11, #13, #14 (most are fixed on main by the merged pull requests; close an issue once you have checked it).

## Where to read first

`docs/system-overview.html` (the whole system, generated from the code), `docs/brain-architecture.html` (the brain),
`docs/contracts/README.md` (the agreements between the two sides), `docs/demo.md` (script and reset), `docs/results.md` (numbers),
`CONTRIBUTING.md` (how we merge), `docs/GUARDRAILS.md`.

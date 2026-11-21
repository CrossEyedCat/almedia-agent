# Handoff: A is away from Saturday 21 Nov, about 17:10 CET

Written for B (Artem) and his agent. A (Yuli) leaves the keyboard; the agents on A's side only run while A is in a session, so
when A is away nobody on A's side answers. Updated at 18:45 CET; everything below was checked against GitHub and the running demo.

## Where things are

- `main` is at `95a1494` or later, CI green. All pull requests are merged: #15 to #22 (working agreement, contract, scorer with
  real monitor results, wall clock, guardrails, console refresh, demo duration and notes, monitoring as built).
- **End-to-end tests are on main** (`tests/test_end_to_end.py`, about a minute): real simulator with injected faults, the real monitor in
  replay, the incident files, the console's API. They found that the scorer swallowed a correct incident (fixed in #17).
- `docs/results.md`: 24/24 faults detected, 24/24 localized, 22/24 right cause, 0 false alarms in 98 simulated days, 16/16 innocent
  events silent, from a real replay. Not an independent holdout (seeds 4 to 8 were used while the brain was built); the file says so.
- Ten issues are still open (#2, #3, #5, #6, #8, #9, #10, #11, #13, #14). Most are fixed on main by the pull requests above; see the
  status comment on #14. Close an issue when you have checked it.
- **The repository is still private.** Only the owner can change that, and it must be public before the submission (below).

## The demo that is running (on A's Mac)

Three processes (monitor, wall clock, console on http://127.0.0.1:8000) from an older commit; they stop when that Mac sleeps.
Do not rely on them. To run the demo yourself from a clean clone, the commands are in `docs/demo.md`
(`python -m sim.run --backfill 28d --db data/market.db --force`, `python -m brain.run --db data/market.db --incidents incidents --graph-out data/graph.json`,
`python -m sim.live --speed 2 --db data/market.db`, `uvicorn ui.app:app --port 8000`). Use the accelerated clock (`--speed 2`) for a talk:
the wall-clock mode moves one data hour per real hour. Stop every process that has the database open before you rebuild it.
Faults that begin in the same hour are folded into one or two incidents by design; for separate incidents inject them in different
countries or an hour apart. `docs/pitch-script.md` has a 3-minute script and a slide outline.

## What still has to be done, and only people can do it

1. Two rehearsals of the 3-minute demo against a timer (`docs/demo.md` has the script and the fallbacks).
2. The 3-minute video: screen recording and a person speaking to camera; YouTube unlisted or Google Drive, open to anyone with the link.
   Check it in a private window.
3. A 5-minute slide deck: problem, graph, the autonomous loop, a demo screenshot, results (`docs/results.md`, with its caveat), what failed, what next.
4. Make the repository public (only the owner can): `gh repo edit CrossEyedCat/almedia-agent --visibility public --accept-visibility-change-consequences`;
   check it from a logged-out browser. Complete the library list in README section 10.
5. Submit the form https://tally.so/r/GexoxQ with the email used for registration, and keep a screenshot of the confirmation.
   Deadlines: the event page says Sunday 22 Nov 16:30; the README's internal target is 14:30; the old Luma page says 17:45 (ignore it).
   Whose registration email to use is not settled: A registered as Yuli Fang; ask the organisers if B submits.
6. Be at the venue for the 18:00 pitches. A planned to be at home on Sunday until about 17:30 (not confirmed); do not count on A.

## Known gaps (honest list)

- Results are on synthetic data and are not an independent holdout (seeds 4 to 8 were used while the brain was built).
- Cause confirmed in 22 of 24 faults; the misses are stockout_surge in sports.
- New metrics: the console writes `incoming_metrics/*.yaml` and shows `<name>.result.json`; the watcher and result file are B's side (issue #3).
- Low-traffic hours are noisy by nature (`docs/data-granularity.md`); a fault that lasts one night hour can be missed.
- The demo clock is wall-clock based in the running version; a multi-hour recovery cannot be compressed into seconds there.
- Open issues are the honest to-do list: #2, #3, #5, #6, #8, #9, #10, #11, #13, #14 (many are fixed in open pull requests; do not close an issue before its pull request is merged).

## Where to read first

`docs/system-overview.html` (the whole system, generated from the code), `docs/brain-architecture.html` (the monitor),
`docs/contracts/README.md` (the agreements between the two sides), `docs/demo.md` (script and reset), `docs/results.md` (numbers),
`CONTRIBUTING.md` (how we merge), `docs/GUARDRAILS.md`.

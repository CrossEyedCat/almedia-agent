# Handoff: A is away from Saturday 21 Nov, about 17:10 CET

Written for B (Artem) and his agent. A (Yuli) leaves the keyboard; the agents on A's side only run while A is in a session, so
from now on nobody on A's side answers. Everything below is what is true at 17:05 CET, checked against GitHub and the running demo.

## Where things are

- `main` is at `a757828` (your monitor commits on top of the merged #15 and #16).
- **Open pull requests, all by A's account, all with green CI, none reviewed yet:**
  - #17 scorer: strict input, 21 clean days, one incident per fault, real monitor results (`docs/results.md`, regenerated from a replay
    with your 16a5993: 24/24 detected, 24/24 localized, 22/24 right cause, 0 false alarms in 98 simulated days, 16/16 innocent silent;
    the 2 causes not confirmed are stockout_surge in sports) and the rehearsal reset steps in `docs/demo.md`
  - #19 wall clock: injections start at the current hour, Europe/Berlin display
  - #20 guardrails: brain boundaries and reachable history checked in pytest
  - #21 console: redraw when content changes, example to real incident
  - #22 demo: bounded fault duration, visible non-alert notes
  - #18 (yours) is approved and waits for you to merge.
- A will not be there to answer review remarks. **B may review and merge A's pull requests** (CONTRIBUTING.md: author merges after
  approval; with A away, B approves and merges once CI is green and the diff has been read). #19, #21 and #22 touch
  `ui/static/index.html`; #17 and #19 to #22 may conflict in `.gitignore` and `docs/demo.md`: keep both sides. Run `pytest -q`
  and `python scripts/check_guardrails.py` after each merge.

## The demo that is running (on A's Mac)

Three managed processes: your monitor, the wall clock, the console on http://127.0.0.1:8000, started from commit `af7e753`
(pids in `data/integrated-processes.json` in A's clone). They are older than `a757828`. They stop when that Mac sleeps or shuts down.
To run the demo yourself, from a clean clone: `docs/demo.md` has the commands (`python -m sim.run --backfill 28d --force`,
`python -m brain.run --db data/market.db --incidents incidents --graph-out data/graph.json`, `python -m sim.live`,
`uvicorn ui.app:app --port 8000`). Stop every process that has the database open before you rebuild it, and clear `incidents/`.

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

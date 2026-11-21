"""Summarize real monitor outputs from independently generated scoring runs (never invent incidents).

python -m scripts.summarize_evaluation --runs /path/to/runs --seeds 4,8,101,102 --holdout 101,102 \
    --commit <brain commit> --warmup-hours 80 --out docs/results.md
Expected files: seed<N>.db and seed<N>-incidents/*.json, produced by sim.score generate and brain.run --replay.

--holdout names the seeds that no version of the brain was run on before this evaluation. They get their own
line, so numbers the brain was built on are never read as independent. The script cannot check that claim;
whoever runs it makes it.
--warmup-hours is the history the replay needs before its first judgement (brain.run --replay starts after
min_history_hours + reread_hours, 80 with the defaults). With it the false-alarm rate is per watched day.

The page is the rendering of the JSON written beside it and of nothing else: `from_json` rebuilds it, and a
test compares the two tracked files, so the page cannot be edited by hand without the test failing.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import pandas as pd

from sim import score as sc

LIMITS = [
    '- Holdout means new random seeds of the same simulator: the same four fault kinds (three of each per run), the same ranges of severity and duration and the same scoring rules as the runs the brain was built on. It does not test other kinds of fault, other sizes or another scorer.',
    '- The late-data events only move the arrival time of a source (ingestion_log); the values themselves are complete. Data that never arrives is not tested.',
    '- Events never overlap in these schedules, so two faults at the same time are not tested.',
    '- One routine deployment per simulated day (random system, country and platform) is in the data as a possible rival cause. Many faults have none close to them, and one of the same system in the same place within two hours of a fault is rare; when it happens the report can leave both supported (see the list above).',
    '- A cause is counted as correct when a supported hypothesis names a release for the fault\'s place (payment, search), the pacing release or ad load (ad budget), or the stockouts. The scorer does not check which release of that kind it is and does not take a point away for a second supported cause, which is why those reports are listed.',
    '- The new-metric hand-over is not part of these runs; it has its own tests (`tests/test_extension.py`).',
    '- Replays test detection across synthetic hours. Wall-clock mode follows real time and cannot compress multi-hour recovery into seconds.',
]


def collect(runs: Path, seeds: list[int]) -> list[dict]:
    """Score every run from its own files. A missing database or an empty incidents folder is an error."""
    reports = []
    for seed in seeds:
        path = runs / f'seed{seed}.db'
        if not path.is_file():
            raise FileNotFoundError(f'missing database: {path}')
        events, start, end = sc.load_truth(path)
        incidents = sc.load_incidents(runs / f'seed{seed}-incidents')
        days = (end - start + pd.Timedelta(hours=1)) / pd.Timedelta(days=1)
        reports.append(dict(seed=seed, days=days, **sc.score(events, incidents, end, days)))
    return reports


def _mean(values: list[float]) -> str:
    return f'{sum(values) / len(values):.2f}' if values else 'n/a'


def tally(reports: list[dict], warmup_hours: float = 0) -> dict:
    faults = [p for r in reports for p in r['per_fault']]
    found = [p for p in faults if p['detected']]
    right = [p for p in found if p.get('correct_cause')]
    days = sum(r['days'] for r in reports)
    return dict(
        runs=len(reports), faults=len(faults), detected=len(found),
        localized=sum(bool(p.get('localized')) for p in found), correct=len(right),
        ties=sum(p.get('supported_causes', 1) > 1 for p in right),
        false_alarms=sum(r['false_alarms'] for r in reports), days=days,
        watched=days - len(reports) * warmup_hours / 24,
        innocents=sum(r['innocents'] for r in reports), silent=sum(r['innocents_silent'] for r in reports),
        mean_delay=_mean([p['delay_h'] for p in found]),
    )


def _row(label: str, t: dict) -> str:
    return (f"| {label} | {t['runs']} | {t['detected']}/{t['faults']} | {t['localized']}/{t['detected']} | "
            f"{t['correct']}/{t['detected']} | {t['false_alarms']} | {t['silent']}/{t['innocents']} | {t['mean_delay']} |")


def _rate(label: str, t: dict, watched: bool) -> str:
    days = t['watched'] if watched else t['days']
    per_day = f"{t['false_alarms'] / days:.3f}/day" if days > 0 else 'n/a'
    span = f"in {days:.0f} watched days" if watched else f"over {days:.0f} simulated days"
    return f"{label}: {t['false_alarms']} {span} ({per_day})"


def _names(reports: list[dict]) -> str:
    return ', '.join(str(r['seed']) for r in reports)


def _scope(scope: dict) -> str:
    return ', '.join(f"{d}={scope[d]}" for d in sc.DIMS if d in scope)


def render(reports: list[dict], holdout=(), commit: str | None = None, warmup_hours: float = 0) -> list[str]:
    """The page as lines. `holdout`: seeds no version of the brain was run on before this evaluation."""
    holdout = set(holdout)
    fresh = [r for r in reports if r['seed'] in holdout]
    seen = [r for r in reports if r['seed'] not in holdout]
    everything = tally(reports, warmup_hours)
    groups = ([('holdout', tally(fresh, warmup_hours))] if fresh and seen else []) + [('all runs', everything)]
    mark = lambda r: ' (holdout)' if r['seed'] in holdout else ''   # noqa: E731

    lines = ['# Results — simulated marketplace', '',
             'Generated by `python -m scripts.summarize_evaluation` from actual `brain.run --replay` incident files'
             + (f', brain at commit `{commit}`.' if commit else '.'),
             'Synthetic data, 21 clean baseline days per run, routine deployments included. These are engineering checks '
             'on a simulator, not production performance.']
    if fresh:
        lines.append(f'Holdout seeds ({_names(fresh)}) were generated and replayed once for this page: no version of the '
                     'brain was run on them before.'
                     + (f' The other seeds ({_names(seen)}) were also run while the brain was built, so their numbers are '
                        'not independent.' if seen else ''))
    else:
        lines.append('None of these seeds is a holdout: all were also run while the brain was built, so the numbers are '
                     'not independent.')
    lines += ['',
              '| Seeds | Runs | Faults detected | Localized | Correct supported cause | False alarms | Innocent events silent | Mean delay (h) |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    if fresh:
        lines.append(_row('Holdout (run once)', tally(fresh, warmup_hours)))
    if fresh and seen:
        lines.append(_row('Used while the brain was built', tally(seen, warmup_hours)))
    lines.append(_row('All', everything))
    lines += ['',
              'Localized and correct cause are counted among the detected faults. A cause counts only when the report '
              'names it as `supported`. Delay is counted from the first affected data hour (0 means caught in that hour).']
    if everything['ties']:
        lines.append(f"In {everything['ties']} of the {everything['correct']} reports with the correct cause a second cause "
                     'is supported beside it (listed below).')
    rates = 'False alarms. ' + '; '.join(_rate(label, t, bool(warmup_hours)) for label, t in groups) + '.'
    if warmup_hours:
        data_days = ' and '.join(format(t['days'], '.0f') for _, t in groups)
        rates += (f' Watched days leave out the first {warmup_hours:g} hours of each run, before the replay\'s first '
                  f'judgement ({data_days} days of data).')
    lines.append(rates)

    lines += ['', '## By fault kind (all runs)', '',
              '| Fault | Detected | Localized | Correct supported cause | Mean delay (h) |', '|---|---:|---:|---:|---:|']
    kinds: dict[str, list[dict]] = {}
    for r in reports:
        for p in r['per_fault']:
            kinds.setdefault(p['kind'], []).append(p)
    for kind in sorted(kinds):
        ps = kinds[kind]
        found = [p for p in ps if p['detected']]
        lines.append(f"| {kind} | {len(found)}/{len(ps)} | {sum(bool(p.get('localized')) for p in found)}/{len(found)} | "
                     f"{sum(bool(p.get('correct_cause')) for p in found)}/{len(found)} | {_mean([p['delay_h'] for p in found])} |")

    lines += ['', '## Per run', '',
              '| Run | Faults detected | Correct supported cause | False alarms | Innocent events silent |',
              '|---|---:|---:|---:|---:|']
    for r in reports:
        correct = sum(bool(p.get('correct_cause')) for p in r['per_fault'])
        lines.append(f"| Seed {r['seed']}, {r['days']:.0f} days{', holdout' if r['seed'] in holdout else ''} | "
                     f"{r['detected']}/{r['faults']} | {correct}/{r['detected']} detected | {r['false_alarms']} | "
                     f"{r['innocents_silent']}/{r['innocents']} |")

    lines += ['', '## Misses and unsupported causes', '']
    listed = 0
    for r in reports:
        for p in r['per_fault']:
            if p['detected'] and p.get('localized') and p.get('correct_cause'):
                continue
            if not p['detected']:
                why = 'not detected (no matching monitor incident)'
            else:
                where = 'detected and localized' if p.get('localized') else 'detected, not localized to the injected scope'
                wrong = p.get('supported_causes', 0)
                why = where + ('' if p.get('correct_cause') else
                               f'; {wrong} cause(s) supported, none of them the injected one' if wrong else
                               '; cause not confirmed')
            lines.append(f"- Seed {r['seed']}{mark(r)}: {p['kind']} at {p['fault'].get('start')}, {_scope(p['fault'])}: {why}.")
            listed += 1
    if not listed:
        lines.append('None: every fault was detected, localized and given its cause.')

    lines += ['', '## Reports that support more than one cause', '']
    listed = 0
    for r in reports:
        for p in r['per_fault']:
            extra = p.get('supported_causes', 1) - 1
            if p['detected'] and p.get('correct_cause') and extra > 0:
                lines.append(f"- Seed {r['seed']}{mark(r)}: {p['kind']} at {p['fault'].get('start')}, {_scope(p['fault'])}, "
                             f"incident {p.get('incident')}: the right cause is supported, and {extra} other beside it.")
                listed += 1
    if not listed:
        lines.append('None.')

    lines += ['', '## False alarms', '']
    listed = 0
    for r in reports:
        alarms = r.get('false_alarm_incidents')
        if alarms is None and r['false_alarms']:
            lines.append(f"- Seed {r['seed']}{mark(r)}: {r['false_alarms']} false alarm(s), not itemised by the scorer.")
            listed += 1
        for a in alarms or []:
            status = f" ({a['status']})" if a.get('status') else ''
            lines.append(f"- Seed {r['seed']}{mark(r)}: {a['id']}, {a.get('metric') or 'metric not named'} in "
                         f"{_scope(a.get('segment') or {}) or 'the whole market'}, first seen {a['first_seen']}{status}.")
            listed += 1
    if not listed:
        lines.append('None.')

    lines += ['', '## Limits', ''] + LIMITS
    return lines


def write(out: Path, lines: list[str], reports: list[dict], meta: dict | None = None) -> None:
    """UTF-8 and LF whatever the platform: the page has an em dash and is tracked with LF endings.
    The JSON beside the page holds everything the page was rendered from."""
    meta = dict(meta or {})
    saved = {'holdout': sorted(meta.get('holdout') or []), 'commit': meta.get('commit'),
             'warmup_hours': meta.get('warmup_hours') or 0, 'runs': reports}
    texts = {out.with_suffix('.json'): json.dumps(saved, indent=2, default=str) + '\n', out: '\n'.join(lines) + '\n'}
    out.parent.mkdir(parents=True, exist_ok=True)
    temps = {target: target.with_name(target.name + '.tmp') for target in texts}
    try:
        for target, text in texts.items():          # both are complete on disk before either takes its place
            with open(temps[target], 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
        for target in texts:                        # the JSON first: the page is never newer than its numbers
            os.replace(temps[target], target)
    finally:
        for temp in temps.values():
            temp.unlink(missing_ok=True)


def from_json(path: Path) -> list[str]:
    """The page, rebuilt from the JSON that was written beside it."""
    saved = json.loads(Path(path).read_text(encoding='utf-8'))
    return render(saved['runs'], saved['holdout'], saved['commit'], saved['warmup_hours'])


def _seeds(text: str) -> list[int]:
    return [int(s) for s in text.split(',') if s.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--runs', type=Path, required=True)
    ap.add_argument('--seeds', default='4,8')
    ap.add_argument('--holdout', default='', help='seeds, among --seeds, that the brain was never run on before')
    ap.add_argument('--commit', help='the commit of the brain that produced the incident files')
    ap.add_argument('--warmup-hours', type=float, default=0, help='history per run before the first judgement')
    ap.add_argument('--out', type=Path, default=Path('docs/results.md'))
    args = ap.parse_args(argv)
    if not (math.isfinite(args.warmup_hours) and args.warmup_hours >= 0):
        ap.error('the warm-up must be a number of hours from 0')
    if args.out.suffix.lower() == '.json':
        ap.error('the page cannot be a .json file: its numbers are written beside it under that name')
    try:
        seeds, holdout = _seeds(args.seeds), _seeds(args.holdout)
    except ValueError:
        ap.error('seeds must be whole numbers separated by commas')
    if not seeds:
        ap.error('no seeds given')
    if len(set(seeds)) != len(seeds):
        ap.error('seeds must be distinct')
    for s in holdout:
        if s not in seeds:
            ap.error(f'holdout seed {s} is not among the runs (--seeds)')
    try:
        reports = collect(args.runs, seeds)
    except (FileNotFoundError, ValueError) as e:      # a missing database, a missing, empty or malformed incidents folder
        ap.error(str(e))
    for r in reports:
        if args.warmup_hours / 24 >= r['days']:
            ap.error(f"the warm-up is not shorter than run {r['seed']} ({r['days']:.0f} days)")
    meta = dict(holdout=holdout, commit=args.commit, warmup_hours=args.warmup_hours)
    write(args.out, render(reports, **meta), reports, meta)
    t = tally(reports)
    print(f"{args.out}: {t['detected']}/{t['faults']} detected; {t['false_alarms']} false alarms; "
          f"{t['silent']}/{t['innocents']} innocent events silent")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

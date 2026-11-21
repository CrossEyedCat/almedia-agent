"""Scoring harness: compare what the brain reported with the injected truth.

    python -m sim.score generate --db data/eval.db --faults 12 --innocents 8 --seed 1
        builds a database with random faults and innocent events spread over the history
    (run the brain over that database; it writes incidents/*.json)
    python -m sim.score score --db data/eval.db --incidents incidents/ --out docs/results.md

The numbers in docs/results.md come from this script, not from hand-picked runs.

Matching rules (kept simple on purpose, so they can be explained on a slide):
- A fault is DETECTED by an incident whose segment does not contradict the fault's country/platform and whose
  first_seen lies between the fault start and the end of the data. The earliest such incident counts.
- DELAY = first_seen - fault start, in hours.
- LOCALIZED when the incident's segment names exactly the fault's country and platform.
- CORRECT CAUSE when a hypothesis with status 'supported' and kind 'release' names both country and platform.
- An incident that matches no fault is a FALSE ALARM. An innocent event (promo, data delay) is SILENT when
  no unmatched incident starts inside its window (and, for a promo, concerns its country).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from sim import db, inject
from sim.run import DEFAULT_START
from sim.world import COUNTRIES, PLATFORMS
from sim.schema import SOURCES

DIMS = ("country", "platform")


@dataclass
class Event:
    kind: str
    start: pd.Timestamp
    end: pd.Timestamp | None
    scope: dict


def load_truth(path) -> tuple[list[Event], pd.Timestamp, pd.Timestamp]:
    con = db.connect(path)
    try:
        rows = con.execute("SELECT kind, scope FROM ground_truth ORDER BY injected_at").fetchall()
        meta = db.read_meta(con)
        last = db.last_hour(con)
    finally:
        con.close()
    events = []
    for kind, scope in rows:
        d = json.loads(scope)
        events.append(Event(kind, pd.Timestamp(d["start"]), None if d.get("end") in (None, "") else pd.Timestamp(d["end"]), d))
    return events, meta["start"], last


def load_incidents(folder) -> list[dict]:
    out = []
    for p in sorted(Path(folder).glob("*.json")) if Path(folder).exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _seen(i: dict) -> pd.Timestamp:
    return pd.Timestamp(i["first_seen"])


def _consistent(seg: dict, scope: dict) -> bool:
    return all(scope.get(d) == seg[d] for d in DIMS if d in seg)


def score(events: list[Event], incidents: list[dict], data_end: pd.Timestamp, days: float) -> dict:
    faults = [e for e in events if e.kind == "payment_failure"]
    innocents = [e for e in events if e.kind in ("promo", "data_delay")]
    used: set[str] = set()
    detected = localized = correct = 0
    delays: list[float] = []
    per_fault = []
    for f in faults:
        cands = sorted((i for i in incidents if f.start <= _seen(i) <= data_end and _consistent(i.get("segment") or {}, f.scope)),
                       key=_seen)
        if not cands:
            per_fault.append({"fault": f.scope, "detected": False})
            continue
        i = cands[0]
        used.update(x["id"] for x in cands)
        detected += 1
        delay = (_seen(i) - f.start) / pd.Timedelta(hours=1)
        delays.append(delay)
        seg = i.get("segment") or {}
        loc = all(seg.get(d) == f.scope.get(d) for d in DIMS)
        cause = any(h.get("status") == "supported" and h.get("kind") == "release"
                    and str(f.scope.get("country")) in h.get("cause", "") and str(f.scope.get("platform")) in h.get("cause", "")
                    for h in i.get("hypotheses", []))
        localized += loc
        correct += cause
        per_fault.append({"fault": f.scope, "detected": True, "delay_h": delay, "localized": loc, "correct_cause": cause})
    false_alarms = [i for i in incidents if i["id"] not in used]
    silent = 0
    for e in innocents:
        end = e.end or data_end
        noisy = [i for i in false_alarms if e.start <= _seen(i) <= end
                 and (e.kind != "promo" or (i.get("segment") or {}).get("country") in (None, e.scope.get("country")))]
        silent += not noisy
    nf, ni = len(faults), len(innocents)
    return {
        "faults": nf, "detected": detected, "detection_rate": detected / nf if nf else None,
        "mean_delay_h": float(np.mean(delays)) if delays else None,
        "localized_rate": localized / detected if detected else None,
        "correct_cause_rate": correct / detected if detected else None,
        "false_alarms": len(false_alarms), "false_alarms_per_day": len(false_alarms) / days if days else None,
        "innocents": ni, "innocents_silent": silent, "innocent_silent_rate": silent / ni if ni else None,
        "per_fault": per_fault,
    }


def pct(x):
    return "n/a" if x is None else f"{x:.0%}"


def report(r: dict, db_path, n_incidents: int, days: float) -> str:
    mean = "n/a" if r["mean_delay_h"] is None else f"{r['mean_delay_h']:.1f} simulated hours"
    fa = "n/a" if r["false_alarms_per_day"] is None else f"{r['false_alarms_per_day']:.2f}"
    lines = [
        "# Results", "",
        f"Produced by `python -m sim.score` from `{db_path}`: {r['faults']} injected faults, {r['innocents']} innocent events, "
        f"{n_incidents} incidents reported, {days:.1f} simulated days. The data is simulated.", "",
        "| Measure | Value |", "|---|---|",
        f"| Faults detected | {r['detected']} of {r['faults']} ({pct(r['detection_rate'])}) |",
        f"| Mean detection delay | {mean} |",
        f"| Localized to the right country and platform | {pct(r['localized_rate'])} of detected |",
        f"| Right cause named (supported release) | {pct(r['correct_cause_rate'])} of detected |",
        f"| False alarms | {r['false_alarms']} ({fa} per simulated day) |",
        f"| Innocent events that stayed silent | {r['innocents_silent']} of {r['innocents']} ({pct(r['innocent_silent_rate'])}) |", "",
        "## Per fault", "", "| Fault | Detected | Delay (h) | Localized | Cause |", "|---|---|---|---|---|",
    ]
    for p in r["per_fault"]:
        f = f"{p['fault'].get('country')} {p['fault'].get('platform')} from {p['fault'].get('start')}"
        if p["detected"]:
            lines.append(f"| {f} | yes | {p['delay_h']:.1f} | {'yes' if p['localized'] else 'no'} | {'yes' if p['correct_cause'] else 'no'} |")
        else:
            lines.append(f"| {f} | no | | | |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- generate
def generate(path, faults: int = 12, innocents: int = 8, seed: int = 1, days: int = 28, countries=COUNTRIES) -> Path:
    """A database with random faults and innocent events; windows do not overlap and are at least 12 h apart."""
    rng = np.random.default_rng(seed)
    p = Path(path)
    for suffix in ("", "-wal", "-shm"):
        Path(str(p) + suffix).unlink(missing_ok=True)
    total = faults + innocents
    # leave a week of clean history for the baseline, then place events in equal slots, in random order
    first = 7 * 24
    slot = (days * 24 - first) // total
    if slot < 12:
        raise ValueError("too many events for this history; lower --faults/--innocents or raise --days")
    kinds = ["fault"] * faults + ["promo"] * (innocents // 2) + ["delay"] * (innocents - innocents // 2)
    rng.shuffle(kinds)
    start0 = pd.Timestamp(DEFAULT_START)
    con = db.connect(p)
    try:
        db.init_db(con, seed, start0, tuple(countries))
        for n, kind in enumerate(kinds):
            offset = first + n * slot + int(rng.integers(0, max(1, slot - 10)))
            start = start0 + pd.Timedelta(hours=offset)
            country = str(rng.choice(list(countries)))
            if kind == "fault":
                inject.payment_failure(con, country, str(rng.choice(PLATFORMS)), start, severity=float(rng.uniform(0.3, 0.6)),
                                       end=start + pd.Timedelta(hours=int(rng.integers(4, 9))), replay_now=False)
            elif kind == "promo":
                inject.promo(con, country, start, start + pd.Timedelta(hours=int(rng.integers(6, 11))), replay_now=False)
            else:
                inject.data_delay(con, str(rng.choice(SOURCES)), start, start + pd.Timedelta(hours=int(rng.integers(2, 5))), replay_now=False)
        db.write_hours(con, 0, days * 24)             # one pass, with every injection on record
    finally:
        con.close()
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--db", default="data/eval.db")
    g.add_argument("--faults", type=int, default=12)
    g.add_argument("--innocents", type=int, default=8)
    g.add_argument("--days", type=int, default=28)
    g.add_argument("--seed", type=int, default=1)
    s = sub.add_parser("score")
    s.add_argument("--db", default="data/eval.db")
    s.add_argument("--incidents", default="incidents")
    s.add_argument("--out", default="docs/results.md")
    a = ap.parse_args(argv)
    if a.cmd == "generate":
        p = generate(a.db, a.faults, a.innocents, a.seed, a.days)
        print(f"{p}: {a.faults} faults and {a.innocents} innocent events over {a.days} days (seed {a.seed})")
        return 0
    events, start, end = load_truth(a.db)
    incidents = load_incidents(a.incidents)
    days = (end - start) / pd.Timedelta(days=1)
    r = score(events, incidents, end, days)
    text = report(r, a.db, len(incidents), days)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

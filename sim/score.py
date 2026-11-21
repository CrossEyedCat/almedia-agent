"""Scoring harness: compare what the brain reported with the injected truth.

    python -m sim.score generate --db data/eval.db --faults 12 --innocents 8 --seed 1
        builds a database with random faults and innocent events spread over the history
    (run the brain over that database; write to a separate eval_incidents/ folder)
    python -m sim.score score --db data/eval.db --incidents eval_incidents/ --out docs/results.md

The numbers in docs/results.md come from this script, not from hand-picked runs.

Matching rules (kept simple on purpose, so they can be explained on a slide):
- Faults are payment_failure, stockout_surge, search_regression and ad_budget_exhausted.
- Only incidents with status open, ongoing or resolved (or no status) count. `explained` and `pending` are skipped: they
  are neither a detection nor a false alarm.
- `first_seen` is read as the time of detection (the newest data hour at the tick that raised the incident), so
  delay = first_seen - fault start. The onset belongs in change.window.start.
- A fault is DETECTED by an incident whose segment does not contradict the fault's scope (country, platform,
  category, channel) and whose first_seen lies from the fault start to 6 hours after its end (or the end of the data).
  The earliest such incident counts, and one incident can belong to one fault only.
- DELAY = first_seen - fault start, in hours.
- LOCALIZED when the incident's segment names every dimension of the fault's scope, with the same values.
- CORRECT CAUSE, by kind, needs a hypothesis with status 'supported' and the `kind` set (a hypothesis without a kind
  never counts for the release causes):
    payment_failure   a release that names the country and the platform
    search_regression a release of search that names the country
    ad_budget_exhausted  pacing or ad_load named
    stockout_surge    stockouts named
- An incident that matches no fault is a FALSE ALARM. An innocent event (promo, data delay) is SILENT when
  no unmatched incident starts inside its window (and, for a promo, concerns its country).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from sim import db, inject
from sim.run import DEFAULT_START
from sim.world import COUNTRIES, PLATFORMS
from sim.schema import DIMENSIONS, SOURCES
from sim.world import CATEGORIES

DIMS = DIMENSIONS
GRACE = pd.Timedelta(hours=6)   # an incident may appear this long after a fault has ended
FAULT_KINDS = ("payment_failure", "stockout_surge", "search_regression", "ad_budget_exhausted")


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
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"{folder}: incidents folder does not exist")
    paths = sorted(folder.glob("*.json"))
    if not paths:
        raise ValueError(f"{folder}: no incident JSON files; run the monitor first")
    out = []
    for p in paths:
        try:
            i = json.loads(p.read_text(encoding="utf-8"),
                           parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {v}")))
            if not isinstance(i, dict):
                raise ValueError("incident must be an object")
            if not isinstance(i.get("id"), str) or not i["id"].strip():
                raise ValueError("id must be non-empty text")
            seen = i.get("first_seen")
            if not isinstance(seen, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", seen):
                raise ValueError("first_seen must be a UTC timestamp ending in Z")
            pd.Timestamp(seen)
            if not isinstance(i.get("segment", {}), dict):
                raise ValueError("segment must be an object")
            hypotheses = i.get("hypotheses", [])
            if not isinstance(hypotheses, list) or any(not isinstance(h, dict) for h in hypotheses):
                raise ValueError("hypotheses must be a list of objects")
            if i.get("status", "open") not in (*COUNTED_STATUSES, "explained", "pending"):
                raise ValueError("unknown incident status")
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"{p}: {exc}") from exc
        out.append(i)
    return out


def _seen(i: dict) -> pd.Timestamp:
    return pd.Timestamp(i["first_seen"])


def _consistent(seg: dict, scope: dict) -> bool:
    """The incident's segment does not contradict the fault: wherever both name a dimension, the values agree."""
    return all(scope[d] == seg[d] for d in DIMS if d in seg and d in scope)


def named_cause(f: Event, incident: dict) -> bool:
    for h in incident.get("hypotheses", []):
        if h.get("status") != "supported":
            continue
        raw, text = str(h.get("cause", "")), str(h.get("cause", "")).lower()
        if f.kind == "payment_failure" and h.get("kind") == "release" and str(f.scope.get("country")) in raw and str(f.scope.get("platform")) in raw:
            return True
        if f.kind == "search_regression" and h.get("kind") == "release" and "search" in text and str(f.scope.get("country")) in raw:
            return True
        if f.kind == "ad_budget_exhausted" and ("pacing" in text or "ad_load" in text or "ad load" in text):
            return True
        if f.kind == "stockout_surge" and "stockout" in text:
            return True
    return False


COUNTED_STATUSES = ("open", "ongoing", "resolved")


def score(events: list[Event], incidents: list[dict], data_end: pd.Timestamp, days: float) -> dict:
    incidents = [i for i in incidents if i.get("status", "open") in COUNTED_STATUSES]
    faults = [e for e in events if e.kind in FAULT_KINDS]
    innocents = [e for e in events if e.kind in ("promo", "data_delay")]
    used: set[int] = set()
    in_window: set[int] = set()
    detected = localized = correct = 0
    delays: list[float] = []
    per_fault = []
    for f in sorted(faults, key=lambda e: e.start):
        hi = min(data_end, f.end + GRACE) if f.end is not None else data_end
        cands = sorted((n for n, i in enumerate(incidents) if n not in used and f.start <= _seen(i) <= hi
                        and _consistent(i.get("segment") or {}, f.scope)), key=lambda n: _seen(incidents[n]))
        in_window.update(cands)
        if not cands:
            per_fault.append({"kind": f.kind, "fault": f.scope, "detected": False})
            continue
        n = cands[0]
        i = incidents[n]
        used.add(n)
        detected += 1
        delay = (_seen(i) - f.start) / pd.Timedelta(hours=1)
        delays.append(delay)
        seg = i.get("segment") or {}
        loc = all(seg.get(d) == f.scope[d] for d in DIMS if d in f.scope)
        cause = named_cause(f, i)
        localized += loc
        correct += cause
        per_fault.append({"kind": f.kind, "fault": f.scope, "detected": True, "delay_h": delay, "localized": loc, "correct_cause": cause})
    false_alarms = [i for n, i in enumerate(incidents) if n not in used | in_window]
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
        where = " ".join(str(p["fault"][d]) for d in DIMS if d in p["fault"])
        f = f"{p['kind']} {where} from {p['fault'].get('start')}"
        if p["detected"]:
            lines.append(f"| {f} | yes | {p['delay_h']:.1f} | {'yes' if p['localized'] else 'no'} | {'yes' if p['correct_cause'] else 'no'} |")
        else:
            lines.append(f"| {f} | no | | | |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- generate
def generate(path, faults: int = 12, innocents: int = 8, seed: int = 1, days: int = 49, countries=COUNTRIES) -> Path:
    """A database with random faults and innocent events; 21 clean days first; event windows do not overlap (gaps vary)."""
    rng = np.random.default_rng(seed)
    p = Path(path)
    total = faults + innocents
    if faults < 0 or innocents < 0 or total == 0 or days <= 21 or not countries:
        raise ValueError("use non-negative event counts, at least one event, countries and more than 21 days")
    # Validate before replacing an existing database.
    first = 21 * 24
    slot = (days * 24 - first) // total
    if slot < 12:
        raise ValueError("too many events for this history; lower --faults/--innocents or raise --days")
    for suffix in ("", "-wal", "-shm"):
        Path(str(p) + suffix).unlink(missing_ok=True)
    fault_kinds = [FAULT_KINDS[i % len(FAULT_KINDS)] for i in range(faults)]
    kinds = fault_kinds + ["promo"] * (innocents // 2) + ["delay"] * (innocents - innocents // 2)
    rng.shuffle(kinds)
    start0 = pd.Timestamp(DEFAULT_START)
    con = db.connect(p)
    try:
        db.init_db(con, seed, start0, tuple(countries))
        for n, kind in enumerate(kinds):
            offset = first + n * slot + int(rng.integers(0, max(1, slot - 10)))
            start = start0 + pd.Timedelta(hours=offset)
            country = str(rng.choice(list(countries)))
            window = start + pd.Timedelta(hours=int(rng.integers(4, 9)))
            if kind == "payment_failure":
                inject.payment_failure(con, country, str(rng.choice(PLATFORMS)), start, severity=float(rng.uniform(0.3, 0.6)),
                                       end=window, replay_now=False)
            elif kind == "stockout_surge":
                inject.stockout_surge(con, str(rng.choice(CATEGORIES)), start, window, multiplier=float(rng.uniform(10, 16)), replay_now=False)
            elif kind == "search_regression":
                inject.search_regression(con, country, start, severity=float(rng.uniform(0.35, 0.6)), end=window, replay_now=False)
            elif kind == "ad_budget_exhausted":
                inject.ad_budget_exhausted(con, country, start, start + pd.Timedelta(hours=8), boost_hours=3, replay_now=False)
            elif kind == "promo":
                inject.promo(con, country, start, start + pd.Timedelta(hours=int(rng.integers(6, 11))), replay_now=False)
            else:
                inject.data_delay(con, str(rng.choice(SOURCES)), start, start + pd.Timedelta(hours=int(rng.integers(2, 5))), replay_now=False)
        db.add_release_context(con, days * 24)
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
    g.add_argument("--days", type=int, default=49)
    g.add_argument("--seed", type=int, default=1)
    s = sub.add_parser("score")
    s.add_argument("--db", default="data/eval.db")
    s.add_argument("--incidents", default="eval_incidents")
    s.add_argument("--out", default="docs/results.md")
    a = ap.parse_args(argv)
    if a.cmd == "generate":
        p = generate(a.db, a.faults, a.innocents, a.seed, a.days)
        print(f"{p}: {a.faults} faults and {a.innocents} innocent events over {a.days} days (seed {a.seed})")
        return 0
    try:
        incidents = load_incidents(a.incidents)
    except ValueError as exc:
        ap.error(str(exc))
    print(f"Read {len(incidents)} incident files; skipped 0 malformed files (strict validation)", file=sys.stderr)
    events, start, end = load_truth(a.db)
    days = (end - start + pd.Timedelta(hours=1)) / pd.Timedelta(days=1)
    r = score(events, incidents, end, days)
    text = report(r, a.db, len(incidents), days)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

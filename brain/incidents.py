"""Turn the monitor's alerts into incident files, and keep each incident's life in one file.

One fault shows up as many alerts: in the cell where it happened, diluted in every series that
contains the cell, and on the metrics downstream of the one that moved. The book groups them:

- per metric the strongest alert of a place wins (monitor.leading);
- alerts on different metrics whose places are compatible form one incident, led by the strongest;
- an incident that is still open and compatible is updated in place: same file, same id, same
  first_seen; its place is refined when stronger evidence points to a narrower or other series;
- alerts the calendar covers are written with status `explained`, hours held back for late data
  with status `pending`: the same file shape, no alert. A pending record is removed when the data
  is in and the hours have been judged.

Files (the page and the scoring harness read them, see docs/contracts and issues #4, #7, #8):
  <folder>/inc-NNNN.json       one incident, rewritten whole, never NaN, strings where strings are due
  <folder>/explained-*.json    a planned event that explains a deviation
  <folder>/pending-*.json      hours waiting for late data
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from brain.clock import HOUR
from brain.data import iso_hour
from brain.investigate import Investigator, num, ordered, seg_text
from brain.monitor import Alert, Monitor, Tick, compatible, leading, nested

RESOLVE_AFTER_HOURS = 3      # normal, judged hours before an incident is closed
HEALTH_MIN_SURPRISE = 2.0    # "back to normal" needs data that would show the fault, and does not show it


def clean(value):
    """Plain JSON types only: no NaN or infinity, no numpy scalars, timestamps as hour strings."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return iso_hour(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    return value


def write_json(path: Path, obj: dict) -> None:
    """Write under a name the readers skip, then rename. On Windows the rename fails while a
    reader holds the old file open, so it is retried."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(clean(obj), indent=1, allow_nan=False, ensure_ascii=False) + "\n",
                   encoding="utf-8", newline="\n")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.02)


@dataclass
class Incident:
    id: str
    first_seen: pd.Timestamp
    last_seen: pd.Timestamp
    onset: pd.Timestamp
    segment: dict
    status: str = "open"
    best_score: float = 0.0
    metrics: dict = field(default_factory=dict)     # metric -> newest leading alert
    resolved_at: pd.Timestamp | None = None
    confirmed: list = field(default_factory=list)   # evidence that once confirmed the metric cause; kept while open
    body: dict = field(default_factory=dict)


class Book:
    def __init__(self, monitor: Monitor, folder: str | Path, fresh: bool = False):
        self.monitor = monitor
        self.graph = monitor.graph
        self.investigator = Investigator(monitor)
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        old_notes = self.folder / "notes"   # where explained and pending records lived before the readers knew them
        if fresh:
            for p in list(self.folder.glob("*.json")) + list(self.folder.glob("*.json.tmp")) + \
                    (list(old_notes.glob("*.json")) if old_notes.exists() else []):
                p.unlink(missing_ok=True)
        self.incidents: list[Incident] = []
        taken = [int(p.stem.split("-")[-1]) for p in self.folder.glob("inc-*.json") if p.stem.split("-")[-1].isdigit()]
        self._next = max(taken, default=0) + 1
        self._first_seen: dict[str, pd.Timestamp] = {}   # explained / pending record -> hour it was first written
        self._pending_id: str | None = None

    # ------------------------------------------------------------------ update
    def update(self, tick: Tick) -> list[dict]:
        """Apply one tick; returns the incident bodies that were written."""
        if tick.newest is None:
            return []
        # A place that already has an open incident on a metric is known: the series that merely
        # contain it are its dilution, not news. Without this a second fault of the same kind elsewhere
        # would be read off the pooled series ("iOS everywhere") instead of its own cell.
        held = [(m, inc.segment) for inc in self.incidents if inc.status != "resolved" for m in inc.metrics]
        fresh = [a for a in tick.alerts
                 if not any(m == a.metric and a.segment.items() < seg.items() for m, seg in held)]
        lead = leading(fresh)
        explained = [a for a in lead if a.explained_by is not None]
        real = [a for a in lead if a.explained_by is None]

        # Alerts form one incident when one place lies inside the other AND the graph relates the
        # metrics. Being merely compatible is not enough: stockouts in a category and a payment
        # fault in a country overlap in the data and have nothing to do with each other.
        groups: list[list[Alert]] = []
        for a in sorted(real, key=lambda a: -a.score):
            home = next((g for g in groups if nested(a.segment, g[0].segment)
                         and self._related(a.metric, g[0].metric)), None)
            if home is None:
                groups.append([a])
            elif not any(x.metric == a.metric for x in home):
                home.append(a)

        written, touched = [], set()
        for g in groups:
            inc = self._match(g)
            if inc is None:
                inc = Incident(id=f"inc-{self._next:04d}", first_seen=tick.newest, last_seen=g[0].last_bad,
                               onset=min(a.onset for a in g), segment=dict(g[0].segment))
                self._next += 1
                self.incidents.append(inc)
            elif inc.status == "open":
                inc.status = "ongoing"
            touched.add(inc.id)
            if g[0].score > inc.best_score:
                inc.best_score, inc.segment = g[0].score, dict(g[0].segment)
            inc.last_seen = max([inc.last_seen] + [a.last_bad for a in g])
            inc.onset = min([inc.onset] + [a.onset for a in g])
            for a in g:
                inc.metrics[a.metric] = a
            inc.body = self._render(inc, tick)
            write_json(self.folder / f"{inc.id}.json", inc.body)
            written.append(inc.body)

        for inc in self.incidents:
            if inc.id in touched or inc.status == "resolved":
                continue
            waiting = any(h > inc.last_seen and (set(inc.metrics) & set(ms)) for h, ms in tick.held.items())
            if not waiting and tick.newest - inc.last_seen >= RESOLVE_AFTER_HOURS * HOUR and self._healthy(inc, tick):
                inc.status, inc.resolved_at = "resolved", tick.newest
                inc.body = dict(inc.body, status="resolved", resolved_at=iso_hour(tick.newest),
                                report=inc.body.get("report", "") + f" Back to normal since {self._when(inc.last_seen + HOUR)}"
                                       f" ({RESOLVE_AFTER_HOURS} normal hours in a row).")
                write_json(self.folder / f"{inc.id}.json", inc.body)
                written.append(inc.body)

        self.monitor.open_episodes = [(m, a.segment, inc.onset) for inc in self.incidents
                                      if inc.status != "resolved" for m, a in inc.metrics.items()]
        self._write_notes(tick, explained)
        return written

    def _match(self, group: list[Alert]) -> Incident | None:
        metrics = {a.metric for a in group}
        for inc in self.incidents:
            if inc.status != "resolved" and nested(inc.segment, group[0].segment) and metrics & set(inc.metrics):
                return inc
        return None

    def _origins(self, metric: str) -> set[str]:
        """The metric and, for a rate, its numerator: cause links are declared on either."""
        spec = self.graph.specs[metric]
        return {metric} | (set(spec.formula.ratio_parts()[0]) if spec.formula is not None else set())

    def _related(self, a: str, b: str) -> bool:
        """The graph connects the two metrics: a cause link in either direction, or a shared system."""
        if a == b:
            return True
        for x, y in ((a, b), (b, a)):
            if any(e.dst == y for o in self._origins(x) for e in self.graph.out_edges(o, "may_affect")):
                return True
        return bool(set(self.graph.specs[a].systems) & set(self.graph.specs[b].systems))

    def _healthy(self, inc: Incident, tick: Tick) -> bool:
        """Evidence that the fault is gone, not just absence of alerts: since the last bad hour there
        is enough data that the faulty rate would show, and the data does not show it. A quiet night
        in a small cell, or hours with no rows, prove nothing, and the incident stays open."""
        own = [a for a in inc.metrics.values() if a.segment == inc.segment] or list(inc.metrics.values())
        top = max(own, key=lambda a: a.score)
        w = self.monitor.window(top.metric, top.segment, inc.last_seen + HOUR, tick.newest + HOUR,
                                as_rate=top.episode_observed)
        if w is None or w["telling"] < HEALTH_MIN_SURPRISE:
            return False
        same_way = (w["observed"] < w["expected"]) == (top.episode_observed < top.expected)
        return not (same_way and w["score"] >= HEALTH_MIN_SURPRISE)

    # ------------------------------------------------------------------ content
    @staticmethod
    def _when(ts: pd.Timestamp) -> str:
        return ts.strftime("%Y-%m-%d %H:%M UTC")

    def _render(self, inc: Incident, tick: Tick) -> dict:
        """The incident's content: where, how much and why, with the trail (brain/investigate.py)."""
        return self.investigator.report(inc, tick)

    # ------------------------------------------------- explained and pending
    def _write_notes(self, tick: Tick, explained: list[Alert]) -> None:
        by_event: dict[str, list[Alert]] = {}
        for a in explained:
            e = a.explained_by
            key = f"explained-{e['kind']}-{e['country'] or 'all'}-{e['start']}".replace(":", "")
            by_event.setdefault(key, []).append(a)
        for rid, alerts in by_event.items():
            alerts.sort(key=lambda a: -a.score)
            e = alerts[0].explained_by
            scope = {"country": e["country"]} if e["country"] else {}
            top = next((a for a in alerts if a.segment == scope), alerts[0])   # the event's own place if it moved
            first = self._first_seen.setdefault(rid, tick.newest)
            what = e["description"] or e["kind"]
            window = f"{self._when(pd.Timestamp(e['start']))} to {self._when(pd.Timestamp(e['end']))}"
            write_json(self.folder / f"{rid}.json", {
                "id": rid, "status": "explained",
                "first_seen": iso_hour(first), "last_seen": iso_hour(max(a.last_bad for a in alerts)),
                "metric": top.metric, "segment": ordered(top.segment),
                "change": {"observed": top.observed, "expected": top.expected, "delta_pct": 100 * top.effect,
                           "z": top.z, "window": {"start": iso_hour(min(a.onset for a in alerts)),
                                                  "end": iso_hour(max(a.last_bad for a in alerts) + HOUR)}},
                "drivers": [{"metric": a.metric, "segment": ordered(a.segment), "observed": a.observed,
                             "expected": a.expected, "delta_pct": 100 * a.effect, "z": a.z} for a in alerts],
                "hypotheses": [{
                    "cause": f"planned event: {what}", "kind": "calendar", "status": "supported",
                    "evidence": [f"calendar entry of kind {e['kind']} for {e['country'] or 'all countries'}, {window}",
                                 "the change began inside that window and sits in the place the entry names",
                                 "the graph links the calendar to " + ", ".join(sorted({a.metric for a in alerts}))]}],
                "trail": [{"action": "watch", "target": a.metric,
                           "result": f"{num(a.observed)} against an expected {num(a.expected)} in "
                                     f"{seg_text(a.segment)} (z {a.z:+.1f})"} for a in alerts]
                + [{"action": "check", "target": "calendar", "result": f"{what}, {window}: covers it"}],
                "event": e,
                "report": f"{what} ({window}) explains the change in "
                          + ", ".join(sorted({a.metric for a in alerts})) + ". No alert."})

        if tick.held:
            hours = sorted(tick.held)
            if self._pending_id is None:
                self._pending_id = "pending-" + iso_hour(hours[0]).replace(":", "")
                self._first_seen[self._pending_id] = tick.newest
            sources = sorted({s for h in hours for s in tick.late.get(h, [])})
            metrics = sorted({m for ms in tick.held.values() for m in ms})
            write_json(self.folder / f"{self._pending_id}.json", {
                "id": self._pending_id, "status": "pending",
                "first_seen": iso_hour(self._first_seen[self._pending_id]), "last_seen": iso_hour(tick.newest),
                "metric": metrics[0], "segment": {},
                "sources": sources,
                "held": [{"hour": iso_hour(h), "sources": sorted(tick.late.get(h, [])), "metrics": sorted(tick.held[h])}
                         for h in hours],
                "hypotheses": [{
                    "cause": f"data from {', '.join(sources)} has not arrived yet", "kind": "freshness",
                    "status": "supported",
                    "evidence": [f"{self._when(h)}: waiting for {', '.join(sorted(tick.late.get(h, [])))}" for h in hours]}],
                "trail": [{"action": "check", "target": "freshness",
                           "result": f"{len(hours)} hour(s) not judged for {', '.join(metrics)}"}],
                "report": f"Waiting for data from {', '.join(sources)}: {len(hours)} hour(s) of "
                          f"{', '.join(metrics)} are not judged yet. They are neither healthy nor an alert."})
        elif self._pending_id is not None:
            (self.folder / f"{self._pending_id}.json").unlink(missing_ok=True)   # the data is in and was judged
            self._pending_id = None

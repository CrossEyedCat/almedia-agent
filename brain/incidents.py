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
import sys
import time
import traceback
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


def earliest_onset(current: pd.Timestamp, alerts: list[Alert]) -> pd.Timestamp:
    """The incident's onset after a tick. An alert from the hourly scan dates the change from the data.
    An alert from the watch after a release is dated at the release, which only says when the question
    was asked: a harmless release hours before a fault must not move the onset back to itself."""
    return min([current] + [a.onset for a in alerts if a.trigger != "release"])


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
    frozen: dict = field(default_factory=dict)      # (metric, place) -> expected rate at the first report of the cost
    narrative: dict | None = None                   # the newest narration (brain/narrate.py), written into the file
    body: dict = field(default_factory=dict)
    siblings: list = field(default_factory=list)    # (metric, series) of the same fault outside its own series,
                                                    # joined through the localized place or the cause (Book._match)

    def lead(self) -> Alert:
        """The strongest alert: the metric the incident is about."""
        return max(self.metrics.values(), key=lambda a: a.score)

    def places(self) -> list[tuple[str, dict]]:
        """(metric, series) pairs the incident holds: its own series for each metric, and the siblings."""
        return [(m, self.segment) for m in self.metrics] + [s for s in self.siblings if s[1] != self.segment]


class Book:
    def __init__(self, monitor: Monitor, folder: str | Path, fresh: bool = False, narrator=None):
        self.monitor = monitor
        self.narrator = narrator          # brain.narrate.Narrator, live loop only; None writes no narrative
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
        self._take_narrations()
        if tick.newest is None:
            return []
        # A place that already has an open incident on a metric is known: the series that merely
        # contain it are its dilution, not news. Without this a second fault of the same kind elsewhere
        # would be read off the pooled series ("iOS everywhere") instead of its own cell.
        held = [p for inc in self.incidents if inc.status != "resolved" for p in inc.places()]
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

        written: dict[str, dict] = {}
        touched, born = set(), set()
        for g in groups:
            inc, sibling = self._match(g)
            new = inc is None
            if new:   # numbered only once it is kept: a duplicate found by its investigation spends no number
                inc = Incident(id=f"inc-{self._next:04d}", first_seen=tick.newest, last_seen=g[0].last_bad,
                               onset=min(a.onset for a in g), segment=dict(g[0].segment))
            elif inc.status == "open" and inc.id not in born:
                inc.status = "ongoing"
            self._absorb(inc, g, sibling, inc.id in touched)
            if not self._report(inc, tick):
                if new:
                    self._keep(inc, born)
                continue
            if new:
                home = self._duplicate_of(inc)
                if home is None:
                    self._keep(inc, born)
                else:
                    inc = home
                    if inc.status == "open" and inc.id not in born:
                        inc.status = "ongoing"
                    self._absorb(inc, g, True, inc.id in touched)
                    if not self._report(inc, tick):
                        continue
            touched.add(inc.id)
            self._write(inc)
            written[inc.id] = inc.body

        for inc in self.incidents:
            if inc.id in touched or inc.status == "resolved":
                continue
            waiting = any(h > inc.last_seen and (set(inc.metrics) & set(ms)) for h, ms in tick.held.items())
            if not waiting and tick.newest - inc.last_seen >= RESOLVE_AFTER_HOURS * HOUR and self._healthy(inc, tick):
                inc.status, inc.resolved_at = "resolved", tick.newest
                inc.body = dict(inc.body, status="resolved", resolved_at=iso_hour(tick.newest),
                                report=inc.body.get("report", "") + f" Back to normal since {self._when(inc.last_seen + HOUR)}"
                                       f" ({RESOLVE_AFTER_HOURS} normal hours in a row).")
                self._write(inc)
                written[inc.id] = inc.body

        self.monitor.open_episodes = [(m, s, inc.onset) for inc in self.incidents if inc.status != "resolved"
                                      for m, s in [(m, a.segment) for m, a in inc.metrics.items()] + inc.siblings]
        self._write_notes(tick, explained)
        return list(written.values())

    def _keep(self, inc: Incident, born: set) -> None:
        inc.id = f"inc-{self._next:04d}"
        self._next += 1
        self.incidents.append(inc)
        born.add(inc.id)

    def _absorb(self, inc: Incident, group: list[Alert], sibling: bool, again: bool) -> None:
        """Fold a group of alerts into the incident. `again`: the incident already took alerts on this tick,
        so of two alerts on one metric (sibling series) the stronger stays. `sibling`: the group joined through
        the localized place or the cause, not the incident's own series; its series are remembered, so the next
        tick matches them directly and their hours stay out of the baselines while the incident is open."""
        top = group[0]
        if top.score > inc.best_score:
            if sibling and not nested(top.segment, inc.segment):
                for m, a in inc.metrics.items():
                    if a.segment == inc.segment and (m, inc.segment) not in inc.siblings:
                        inc.siblings.append((m, dict(inc.segment)))
            inc.best_score, inc.segment = top.score, dict(top.segment)
        inc.last_seen = max([inc.last_seen] + [a.last_bad for a in group])
        inc.onset = earliest_onset(inc.onset, group)
        for a in group:
            if sibling and (a.metric, a.segment) not in inc.siblings:
                inc.siblings.append((a.metric, dict(a.segment)))
            old = inc.metrics.get(a.metric)
            if again and old is not None and old.score >= a.score:
                continue
            inc.metrics[a.metric] = a

    def _report(self, inc: Incident, tick: Tick) -> bool:
        """Render the incident's body; False when there is nothing to write."""
        try:
            inc.body = self._render(inc, tick)
        except Exception:     # one incident's report must not stop the others or the monitor
            traceback.print_exc()
            print(f"report for {inc.id} failed; its file keeps the previous state", file=sys.stderr, flush=True)
            if not inc.body:
                return False
            inc.body = dict(inc.body, status=inc.status, last_seen=iso_hour(inc.last_seen))
        return True

    def _write(self, inc: Incident) -> None:
        """Write the incident's file, with its newest narration, and ask for a narration of this state."""
        if inc.narrative is not None:
            inc.body = dict(inc.body, narrative=inc.narrative)
        write_json(self.folder / f"{inc.id}.json", inc.body)
        if self.narrator is not None:
            self.narrator.submit(inc.id, inc.body)

    def _take_narrations(self) -> None:
        """Narrations that finished since the last tick go into their incident's file. A failed one is
        recorded only while no narration of that incident has succeeded: a good text is never replaced by
        a failure, and the template report stays the `report` either way."""
        if self.narrator is None:
            return
        for key, story in self.narrator.ready():
            inc = next((i for i in self.incidents if i.id == key), None)
            if inc is None or not inc.body:
                continue
            if story["source"] == "model" or inc.narrative is None or inc.narrative.get("source") != "model":
                inc.narrative = story
            inc.body = dict(inc.body, narrative=inc.narrative)
            write_json(self.folder / f"{inc.id}.json", inc.body)

    def _match(self, group: list[Alert]) -> tuple[Incident | None, bool]:
        """The open incident a group of alerts belongs to, and whether it joins as a sibling series.

        First the incident's own series: the same metric in a nested place. Then the same fault seen in a
        sibling series (one fault in a category shows in the category's cell of every country, and a cell
        can be steadier, so stronger, than the series that holds them all): a series joined before, or the
        place the investigation localized (body["segment"]) when it nests with the group's place and the
        graph relates the lead metrics. An empty place (the whole market) holds everything and joins nothing."""
        metrics = {a.metric for a in group}
        seg, lead = group[0].segment, group[0].metric
        live = [i for i in self.incidents if i.status != "resolved" and i.metrics]
        for inc in live:
            if nested(inc.segment, seg) and metrics & set(inc.metrics):
                return inc, False
        for inc in live:
            if any(m in metrics and s and nested(s, seg) for m, s in inc.siblings):
                return inc, True
            place = inc.body.get("segment")
            if place and nested(place, seg) and self._related(lead, inc.lead().metric):
                return inc, True
        return None, False

    def _duplicate_of(self, new: Incident) -> Incident | None:
        """An open incident that a new one repeats, judged on the new one's investigation: its localized place
        nests with a place of an open incident whose lead metric the graph relates to its own, or both lead to
        the same supported cause (the same release, the same metric in the same place)."""
        place = new.body.get("segment") or {}
        cause = self._supported_cause(new)
        lead = new.lead().metric
        for inc in self.incidents:
            if inc is new or inc.status == "resolved" or not inc.body or not inc.metrics \
                    or not self._related(lead, inc.lead().metric):
                continue
            theirs = [s for _, s in inc.places()] + [inc.body.get("segment") or {}]
            if place and any(s and nested(place, s) for s in theirs):
                return inc
            if cause is not None and cause == self._supported_cause(inc):
                return inc
        return None

    @staticmethod
    def _supported_cause(inc: Incident) -> str | None:
        hyps = inc.body.get("hypotheses") or []
        return hyps[0].get("cause") if hyps and hyps[0].get("status") == "supported" else None

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
        # the faulty rate to look for: the episode's, unless the rate rose and then fell by amounts that
        # cancel over the episode (then its newest bad hours say what "still faulty" would look like)
        faulty = top.episode_observed
        if top.expected and abs(faulty / top.expected - 1) < self.monitor.config.min_effect:
            faulty = top.observed
        w = self.monitor.window(top.metric, top.segment, inc.last_seen + HOUR, tick.newest + HOUR, as_rate=faulty)
        if w is None or w["telling"] < HEALTH_MIN_SURPRISE:
            return False
        same_way = (w["observed"] < w["expected"]) == (faulty < top.expected)
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

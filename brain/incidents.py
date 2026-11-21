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
from brain.impact import estimate
from brain.monitor import Alert, Monitor, Tick, compatible, leading

RESOLVE_AFTER_HOURS = 3      # normal, judged hours before an incident is closed
RELEASE_BEFORE_HOURS = 2     # a release this long before the onset can still be the cause
RELEASE_AFTER_HOURS = 2      # the onset estimate can be early when the first hours were weak
FOLLOW_MIN_SURPRISE = 1.3    # one targeted question along a link, direction predicted: p below 0.05
DIM_ORDER = ("country", "platform", "category", "channel")


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


def ordered(segment: dict) -> dict:
    return {d: segment[d] for d in DIM_ORDER if d in segment}


def seg_text(segment: dict) -> str:
    return " x ".join(str(v) for v in ordered(segment).values()) or "all segments"


def num(x: float, digits: int = 3) -> str:
    if x is None or not math.isfinite(x):
        return "n/a"
    return f"{x:.{digits}g}" if abs(x) < 1000 else f"{x:,.0f}"


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
    body: dict = field(default_factory=dict)


class Book:
    def __init__(self, monitor: Monitor, folder: str | Path, fresh: bool = False):
        self.monitor = monitor
        self.graph = monitor.graph
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
        lead = leading(tick.alerts)
        explained = [a for a in lead if a.explained_by is not None]
        real = [a for a in lead if a.explained_by is None]

        groups: list[list[Alert]] = []
        for a in sorted(real, key=lambda a: -a.score):
            home = next((g for g in groups if compatible(a.segment, g[0].segment)), None)
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
            if not waiting and tick.newest - inc.last_seen >= RESOLVE_AFTER_HOURS * HOUR:
                inc.status, inc.resolved_at = "resolved", tick.newest
                inc.body = dict(inc.body, status="resolved", resolved_at=iso_hour(tick.newest),
                                report=inc.body.get("report", "") + f" Back to normal since {self._when(inc.last_seen + HOUR)}"
                                       f" ({RESOLVE_AFTER_HOURS} normal hours in a row).")
                write_json(self.folder / f"{inc.id}.json", inc.body)
                written.append(inc.body)

        self._write_notes(tick, explained)
        return written

    def _match(self, group: list[Alert]) -> Incident | None:
        metrics = {a.metric for a in group}
        for inc in self.incidents:
            if inc.status != "resolved" and compatible(inc.segment, group[0].segment) and metrics & set(inc.metrics):
                return inc
        return None

    # ------------------------------------------------------------------ content
    @staticmethod
    def _when(ts: pd.Timestamp) -> str:
        return ts.strftime("%Y-%m-%d %H:%M UTC")

    def _releases(self, inc: Incident, now: pd.Timestamp) -> list[dict]:
        """Releases around the onset whose system is tagged on a moved metric and whose scope fits."""
        rows = self.monitor.store.read_sql(
            "SELECT ts, system, country, platform, description FROM releases WHERE ts <= ? AND ts >= ? AND ts <= ? "
            "ORDER BY ts",
            (iso_hour(now), iso_hour(inc.onset - RELEASE_BEFORE_HOURS * HOUR),
             iso_hour(inc.onset + RELEASE_AFTER_HOURS * HOUR)))
        out, seen = [], set()
        for r in rows.to_dict("records"):
            scope = {d: r[d] for d in ("country", "platform") if isinstance(r[d], str) and r[d]}
            key = (r["system"], tuple(sorted(scope.items())))
            tagged = [m for m in inc.metrics if r["system"] in self.graph.specs[m].systems]
            if "rollback" in (r["description"] or "").lower():
                continue    # by the contract a rollback row ends a release; it is not a new cause
            if key in seen or not tagged or not compatible(scope, inc.segment):
                continue
            seen.add(key)   # a second identical row (a repeated click) is the same release
            out.append({"ts": pd.Timestamp(r["ts"]), "system": r["system"], "scope": scope,
                        "description": r["description"] or "", "metrics": tagged})
        return out

    def _follow(self, inc: Incident, alerts: list[Alert], end: pd.Timestamp) -> list[dict]:
        """Metrics that a moved metric may affect (links of the graph) and that did move the way the
        link predicts, in the same place and hours. They were too weak for the hourly scan, but they
        carry the damage: out-of-stock listings cost orders through the add-to-cart rate."""
        out, seen = [], set(inc.metrics)
        for a in alerts:
            spec = self.graph.specs[a.metric]
            origins = [a.metric] + (list(spec.formula.ratio_parts()[0]) if spec.formula is not None else [])
            for origin in origins:
                for e in self.graph.out_edges(origin, "may_affect"):
                    if e.dst in seen:
                        continue
                    w = self.monitor.window(e.dst, inc.segment, inc.onset, end)
                    if w is None or w["score"] < FOLLOW_MIN_SURPRISE:
                        continue
                    sign = e.attrs.get("sign", 0)
                    moved = 1 if w["observed"] > w["expected"] else -1
                    if sign and moved != sign * (1 if a.observed > a.expected else -1):
                        continue
                    seen.add(e.dst)
                    out.append(dict(w, via=a.metric, sign=sign, note=e.attrs.get("note", "")))
        return out

    def _render(self, inc: Incident, tick: Tick) -> dict:
        graph = self.graph
        alerts = sorted(inc.metrics.values(), key=lambda a: -a.score)
        primary = alerts[0]
        end = inc.last_seen + HOUR
        outcome = graph.top_outcomes()[0] if graph.top_outcomes() else None
        rates = {a.metric: (self.monitor.expected_rate(a.metric, inc.segment) or a.expected) for a in alerts}
        followed = self._follow(inc, alerts, end)
        rates.update({f["metric"]: f["expected"] for f in followed})
        impact = estimate(graph, self.monitor.evaluator, outcome, inc.segment, inc.onset, end, rates,
                          also=("orders",)) if outcome else None

        where = seg_text(inc.segment)
        direction = "fell" if primary.observed < primary.expected else "rose"
        trail = [{
            "action": "watch", "target": primary.metric,
            "result": f"{num(primary.observed)} against an expected {num(primary.expected)} in {where} "
                      f"({num(primary.numerator)} of {num(primary.denominator)} in the newest "
                      f"{min(primary.hours, 3)} bad hour(s); surprise 10^-{primary.score:.0f}, z {primary.z:+.1f})"}]
        diluted = sorted({seg_text(a.segment) for a in tick.alerts
                          if a.metric == primary.metric and a.segment != primary.segment
                          and compatible(a.segment, primary.segment)})
        trail.append({"action": "compare", "target": "segments",
                      "result": f"strongest in {where}" + (f"; weaker in {', '.join(diluted[:6])}" if diluted
                                                           else "; no other series of this metric moved")})
        for a in alerts[1:]:
            trail.append({"action": "watch", "target": a.metric,
                          "result": f"also moved in {seg_text(a.segment)}: {num(a.observed)} against {num(a.expected)}"
                                    f" (z {a.z:+.1f})"})
        for f in followed:
            trail.append({"action": "follow", "target": f["metric"],
                          "result": f"{f['via']} may affect it ({f['note'] or 'link in the graph'}): "
                                    f"{num(f['observed'])} against {num(f['expected'])} in {where} over the incident "
                                    f"(z {f['z']:+.1f}), the direction the link predicts"})
        held = sorted(h for h, ms in tick.held.items() if set(ms) & set(inc.metrics))
        trail.append({"action": "check", "target": "freshness",
                      "result": "all sources for these metrics have arrived" if not held else
                      "hours still waiting for data: " + ", ".join(self._when(h) for h in held)})
        trail.append({"action": "check", "target": "calendar",
                      "result": f"no planned event covers {where} in these hours"})

        body = {
            "id": inc.id, "status": inc.status,
            "first_seen": iso_hour(inc.first_seen), "last_seen": iso_hour(inc.last_seen),
            "metric": primary.metric, "segment": ordered(inc.segment),
            "fingerprint": f"{primary.metric}|{where}",
            "change": {"observed": primary.observed, "expected": primary.expected,
                       "delta_pct": 100 * primary.effect, "z": primary.z,
                       "window": {"start": iso_hour(inc.onset), "end": iso_hour(end)}},
            "drivers": [{"metric": a.metric, "segment": ordered(a.segment), "observed": a.observed,
                         "expected": a.expected, "delta_pct": 100 * a.effect, "z": a.z, "surprise": a.score,
                         "test": a.kind, "onset": iso_hour(a.onset)} for a in alerts],
        }
        loss = ""
        if impact is not None:
            total = sum(abs(v) for v in impact.by_factor.values())
            body["metric"] = outcome
            body["change"] = {"observed": impact.observed, "expected": impact.expected,
                              "delta": impact.delta,
                              "delta_pct": 100 * (impact.observed / impact.expected - 1) if impact.expected else None,
                              "z": primary.z, "window": {"start": iso_hour(inc.onset), "end": iso_hour(end)}}
            unit = graph.specs[outcome].unit
            body["impact"] = {f"{outcome}_{unit}" if unit != "count" else outcome: impact.delta}
            if "orders" in impact.restored:
                body["impact"]["orders"] = impact.values["orders"] - impact.restored["orders"]
            body["contributions"] = [
                {"node": m, "share_of_delta": abs(d) / total if total else 0.0,
                 "observed": impact.values[m], "expected": rates[m]}
                for m, d in sorted(impact.by_factor.items(), key=lambda kv: -abs(kv[1]))]
            trail.append({"action": "estimate", "target": outcome,
                          "result": f"{num(impact.observed, 4)} against {num(impact.expected, 4)} with "
                                    f"{', '.join(impact.by_factor)} at the expected rate, in {where} since "
                                    f"{self._when(inc.onset)}"})
            loss = f" Effect on {outcome} in {where} so far: {impact.delta:+,.0f} {unit}" + (
                f", {body['impact']['orders']:+,.0f} orders." if "orders" in body["impact"] else ".")

        hypotheses = []
        for r in self._releases(inc, tick.now):
            exact = r["scope"] == {d: v for d, v in inc.segment.items() if d in ("country", "platform")} \
                and primary.metric in r["metrics"]
            lag = int((inc.onset - r["ts"]) / HOUR)
            hypotheses.append({
                "cause": f"release {r['system']}: {r['description']}", "kind": "release",
                "status": "supported" if exact else "plausible",
                "evidence": [
                    f"released {self._when(r['ts'])}, " + ("the hour the change began" if lag == 0 else
                                                          f"{abs(lag)} h {'before' if lag > 0 else 'after'} the estimated onset"),
                    f"scope {seg_text(r['scope'])} " + ("is exactly where the change is" if exact else
                                                        f"contains or overlaps {where}"),
                    f"system {r['system']} is linked to {', '.join(r['metrics'])} in the graph"]})
            trail.append({"action": "check", "target": f"release:{r['system']}",
                          "result": f"{r['description']} at {self._when(r['ts'])}, scope {seg_text(r['scope'])}"})
        if not hypotheses:
            trail.append({"action": "check", "target": "releases",
                          "result": "no release of a linked system in or over this place around the onset"})
            # the moved metric itself is the first cause when a link of the graph that has a direction
            # predicted a second movement and that movement is there; otherwise it stays a candidate
            confirmed = [f for f in followed if f["via"] == primary.metric and f["sign"]]
            hypotheses.append({
                "cause": f"{primary.metric} {direction} in {where} without a matching release or planned event",
                "kind": "metric", "status": "supported" if confirmed else "plausible",
                "evidence": [f"{num(primary.observed)} against {num(primary.expected)} expected, z {primary.z:+.1f}"]
                + [f"{f['metric']} moved as the link from {primary.metric} predicts: {num(f['observed'])} against "
                   f"{num(f['expected'])} (z {f['z']:+.1f})" for f in confirmed]})
        hypotheses.sort(key=lambda h: h["status"] != "supported")
        body["hypotheses"] = hypotheses
        body["trail"] = trail
        body["uncertain"] = ["Alternative causes were not tested against each other yet (monitor stage)."] + (
            ["Only one hour of evidence so far."] if primary.hours < 2 and inc.status == "open" else [])
        lead = hypotheses[0]
        body["report"] = (
            f"{primary.metric} {direction} to {num(primary.observed)} from an expected {num(primary.expected)} in "
            f"{where}, starting {self._when(inc.onset)}.{loss} "
            + (f"Most likely cause: {lead['cause']}." if lead["status"] == "supported"
               else "No cause is confirmed yet."))
        return body

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

"""The investigator: from "a rate moved" to where, how much, and why, with the evidence for each step.

Called for every open incident on every tick. It asks, in this order, and records each question
and its answer in the trail:

1. What moved, and which of it is cause, which symptom? A metric that another moved metric may
   affect (a link of the graph, in the direction the link predicts) is a symptom of that metric.
2. Where? Every place the dimensions can name is scored on the finest cells (brain/localize.py).
   The monitor's place is confirmed, narrowed or kept; a different place that explains the data
   nearly as well is reported as doubt.
3. What did it cost? The outcome's change is split over the atomic factors along the formulas of
   the graph (brain/attribution.py): the moved rates against their expected values, every other
   rate against its own baseline, so the report can say how much of the gap the incident explains.
4. Why? Every candidate explanation is tested and scored, and the ones that fail stay in the report
   with the reason: releases around the onset (is the system linked to what moved, does the scope
   fit the place, did the change start with the release), the moved metric itself, a planned event,
   late data, and a shift in the mix of segments with unchanged rates.
5. Stop: every candidate is tested; the last step of the trail says whether the leading explanation is
   supported and how far ahead of the next one it is.

Nothing here is specific to a metric, a country or a kind of fault: the candidates come from the
graph's links and system tags and from the rows of the context tables.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from brain.attribution import attribute, leaves
from brain.clock import HOUR
from brain.data import iso_hour
from brain.evaluator import NotComputable
from brain.impact import tree_metrics
from brain.localize import choose, scan
from brain.monitor import Alert, Monitor, Tick, compatible

RELEASE_BEFORE_HOURS = 2      # a release this long before the estimated onset is "at the onset"
RELEASE_AFTER_HOURS = 2       # the onset estimate can be early when the first hours were weak
RELEASE_LOOKBACK_HOURS = 12   # older ones count only if the hours in between already lean the same way
FOLLOW_MIN_SURPRISE = 1.3     # one targeted question along a link, direction predicted: p below 0.05
NORMAL_MAX_SURPRISE = 2.0     # below this a window is "normal" for the purpose of a comparison
MIX_MAX_SHARE = 0.2           # a mix shift that explains less than this share of the change is rejected
INSIDE_MIN_SHARE = 0.5        # a release that covers only part of the place must hold at least this much of the change
MAX_REJECTED_RELEASES = 4     # rejected releases kept in the report (the best scoring ones)
PLACES_SHOWN = 5
DIM_ORDER = ("country", "platform", "category", "channel")
SCOPE_DIMS = ("country", "platform")   # the dimensions a release row can name


def ordered(segment: dict) -> dict:
    return {d: segment[d] for d in DIM_ORDER if d in segment}


def seg_text(segment: dict) -> str:
    return " x ".join(str(v) for v in ordered(segment).values()) or "all segments"


def num(x, digits: int = 3) -> str:
    if x is None or not math.isfinite(x):
        return "n/a"
    return f"{x:.{digits}g}" if abs(x) < 1000 else f"{x:,.0f}"


def when(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d %H:%M UTC")


class Investigator:
    def __init__(self, monitor: Monitor):
        self.monitor = monitor
        self.graph = monitor.graph
        self.store = monitor.store

    # ------------------------------------------------------------ 1. what moved
    def _origins(self, metric: str) -> list[str]:
        """The metric and, for a rate, its numerator: cause links are declared on either."""
        spec = self.graph.specs[metric]
        return [metric] + (list(spec.formula.ratio_parts()[0]) if spec.formula is not None and spec.formula.kind == "ratio" else [])

    def _links(self, metric: str):
        return [e for o in self._origins(metric) for e in self.graph.out_edges(o, "may_affect")]

    def symptoms(self, alerts: list[Alert]) -> dict[str, str]:
        """moved metric -> the moved metric that may cause it, when it moved the way the link predicts."""
        by_metric = {a.metric: a for a in alerts}
        out: dict[str, str] = {}
        for a in alerts:
            for e in self._links(a.metric):
                b = by_metric.get(e.dst)
                if b is None or b.metric == a.metric or out.get(a.metric) == b.metric:
                    continue
                sign = e.attrs.get("sign", 0)
                same = (b.observed > b.expected) == (a.observed > a.expected)
                if not sign or (sign > 0) == same:
                    out.setdefault(b.metric, a.metric)
        return out

    def follow(self, alerts: list[Alert], segment: dict, start, end, skip=()) -> list[dict]:
        """Metrics that a moved metric may affect and that did move the way the link predicts, in the
        same place and hours. They were too weak for the hourly scan, but they carry the damage:
        out-of-stock listings cost orders through the add-to-cart rate. `shown` False: the link
        predicts a direction but the data cannot show it yet; the metric still enters the cost."""
        out, seen = [], set()
        for a in alerts:
            for e in self._links(a.metric):
                if e.dst in seen:
                    continue
                w = self.monitor.window(e.dst, segment, start, end, skip)
                if w is None:
                    continue
                sign = e.attrs.get("sign", 0)
                moved = 1 if w["observed"] > w["expected"] else -1
                as_predicted = not sign or moved == sign * (1 if a.observed > a.expected else -1)
                shown = w["score"] >= FOLLOW_MIN_SURPRISE and as_predicted
                if not shown and not sign:
                    continue   # a link without a direction says nothing until the movement is clear
                seen.add(e.dst)
                out.append(dict(w, via=a.metric, sign=sign, shown=shown, note=e.attrs.get("note", "")))
        return out

    # ---------------------------------------------------------------- 2. where
    def phase_start(self, metric: str, segment: dict, start, end, skip=()) -> pd.Timestamp:
        """The first hour of the phase the metric is in now. A rate that first rose and then fell
        (an ad budget spent early, then gone) cancels itself out over the whole incident: where it
        happened can only be read from hours in which it moved one way. The phase begins after the
        last hour that moved clearly the other way."""
        floor = self.monitor.config.min_effect
        clear = [h for h in self.monitor.hourly(metric, segment, start, end, skip)
                 if h["score"] >= NORMAL_MAX_SURPRISE and h["expected"] and abs(h["observed"] / h["expected"] - 1) >= floor]
        begin = pd.Timestamp(start)
        if clear:
            rising = clear[-1]["z"] > 0
            for h in clear:
                if (h["z"] > 0) != rising:
                    begin = h["hour"] + HOUR
        return begin

    def localize(self, metric: str, anchor: dict, start, end, skip=()):
        """(choice, places by segment, cells) for the metric, or (None, {}, None) without cell data."""
        cells = self.monitor.cells(metric, start, end, skip)
        if cells is None or float(np.sum(cells["den"])) <= 0:
            return None, {}, None
        places = scan(cells["dims"], cells["keys"], cells["num"], cells["den"], cells["rate"],
                      cells["kind"], cells["unit_var"], cells["shift"])
        if not places:
            return None, {}, cells
        if not any(p.segment == anchor for p in places):
            return None, {}, cells   # the monitor's place has no counts in this window
        choice = choose(places, anchor, kind=cells["kind"], unit_var=cells["unit_var"])
        return choice, {frozenset(p.segment.items()): p for p in places}, cells

    @staticmethod
    def mix(cells: dict, place: dict) -> dict | None:
        """Split the rate's change inside a place into "the rates of its cells moved" and "the weights
        of its cells moved" (more traffic in cells that always had a lower rate)."""
        dims = cells["dims"]
        inside = np.array([all(key[dims.index(d)] == v for d, v in place.items()) for key in cells["keys"]])
        den, hist = cells["den"][inside], cells["history_den"][inside]
        if den.sum() <= 0 or hist.sum() <= 0:
            return None
        p0 = cells["rate"][inside] + cells["shift"]
        with np.errstate(divide="ignore", invalid="ignore"):
            p1 = np.where(den > 0, cells["num"][inside] / den + cells["shift"], p0)
        w1, w0 = den / den.sum(), hist / hist.sum()
        mix = float(((w1 - w0) * p0).sum())
        rate = float((w1 * (p1 - p0)).sum())
        size = abs(mix) + abs(rate)
        return {"mix": mix, "rate": rate, "share": abs(mix) / size if size else 0.0}

    # ----------------------------------------------------------- 3. what it cost
    def explain(self, alerts: list[Alert], followed: list[dict], segment: dict, start, end, skip=(),
                frozen: dict | None = None) -> dict | None:
        """What the moved rates cost, in the top outcome's unit, in a segment and window.

        Two comparisons, for two questions:
        - the effect: the outcome as observed against the outcome with only the moved rates back at
          their expected values and everything else as observed. That is "had this not happened",
          whatever else is going on in the same place (a promo, a second fault);
        - the gap: the outcome as observed against the state with every judged rate at its own
          baseline, split over all factors. It shows what else is off besides the moved rates.

        `skip`: hours whose data has not arrived; they are in neither comparison. `frozen` keeps the
        expected rates of the first report of an incident, so a long incident does not drift into
        its own baseline. None when no moved metric is on the outcome's formula tree or the outcome
        cannot be computed there."""
        graph, ev = self.graph, self.monitor.evaluator
        outcome = graph.top_outcomes()[0] if graph.top_outcomes() else None
        if outcome is None:
            return None
        tree, atoms = tree_metrics(graph, outcome), leaves(graph, outcome)
        known = {a.metric: a.expected for a in alerts}
        known.update({f["metric"]: f["expected"] for f in followed})
        moved = [m for m in atoms if m in known]
        if not moved:
            return None
        below = {m: graph.specs[m].formula.ratio_parts()[1] for m in atoms
                 if graph.specs[m].formula is not None and graph.specs[m].formula.kind == "ratio"}
        names = list(dict.fromkeys(tree + [d for ds in below.values() for d in ds]))
        try:
            hourly = ev.sums(names, start, end, segment, by_hour=True)
            if len(skip):
                hourly = hourly[~hourly["ts"].isin(list(skip))]
            if hourly.empty:
                return None
            frame = ev.derive(hourly.drop(columns=["ts"]).sum(min_count=1).to_frame().T, names)
        except NotComputable:
            return None
        observed = {m: float(frame[m].iloc[0]) for m in names}
        expected = {}
        place = tuple(sorted(segment.items()))
        for m in atoms:
            rate = frozen.get((m, place)) if frozen is not None else None
            if rate is None:
                rate = self.monitor.expected_rate(m, segment)
                if rate is None and m in known:
                    rate = known[m]
                if frozen is not None and rate is not None and math.isfinite(rate):
                    frozen[(m, place)] = rate
            expected[m] = rate if rate is not None and math.isfinite(rate) else observed[m]   # not judged: taken as it is
        for m, parts in below.items():
            # nothing to divide by (no orders at all, so no order value and no take rate): the rate cannot
            # be judged and multiplies a zero, so it is taken at its expected value. A missing sum stays missing.
            if not math.isfinite(observed[m]) and all(observed.get(d) == 0 for d in parts) and math.isfinite(expected[m]):
                observed[m] = expected[m]
        restored = {m: expected[m] if m in moved else observed[m] for m in atoms}
        try:
            cost = attribute(graph, outcome, observed, restored)
            whole = attribute(graph, outcome, observed, expected)
        except ValueError:      # more changed factors than the exact split takes
            return None
        if cost is None:
            return None
        effect = cost.delta
        gap = whole.delta if whole is not None else effect
        others = sorted(((m, v) for m, v in (whole.by_factor.items() if whole is not None else [])
                         if m not in moved and abs(v) >= 0.5), key=lambda kv: -abs(kv[1]))
        counted = {}
        for m in tree:   # what is counted along the way (orders, clicks), from the same two states
            spec = graph.specs[m]
            if m != outcome and spec.unit == "count" and spec.direction == "up" and spec.investigative_formula is not None:
                sub = attribute(graph, m, observed, restored)
                if sub is not None and abs(sub.delta) >= 0.5:
                    counted[m] = sub.delta
        return {
            "outcome": outcome, "unit": graph.specs[outcome].unit, "moved": moved, "method": cost.method,
            "observed": observed, "expected": expected, "actual": cost.actual, "effect": effect,
            "gap": gap, "others": others, "counted": counted,
            # the incident's share of everything that is off in this place; None when the rest outweighs it
            "explained": min(effect / gap, 1.0) if abs(gap) > 1e-9 and effect * gap > 0 else None,
            "unclear": [f["metric"] for f in followed if f["metric"] in moved and not f["shown"]],
            "contributions": [
                {"node": m, "share_of_delta": min(max(cost.by_factor[m] / effect, 0.0), 1.0) if abs(effect) > 1e-9 else 0.0,
                 "delta": cost.by_factor[m], "observed": observed[m], "expected": expected[m]}
                for m in sorted(moved, key=lambda m: -abs(cost.by_factor[m]))],
        }

    # ------------------------------------------------------------------ 4. why
    def releases(self, inc, tick: Tick, primary: Alert, moved: list[str], place: dict, segment: dict, end, places: dict,
                 skip=()) -> list[dict]:
        """Every release from RELEASE_LOOKBACK_HOURS before the onset to now, as a scored hypothesis."""
        graph, mon = self.graph, self.monitor
        rows = self.store.read_sql(
            "SELECT ts, system, country, platform, description FROM releases WHERE ts <= ? AND ts >= ? ORDER BY ts",
            (iso_hour(tick.now), iso_hour(inc.onset - RELEASE_LOOKBACK_HOURS * HOUR)))
        where = seg_text(place)
        place_scope = {d: place[d] for d in SCOPE_DIMS if d in place}
        falling = primary.observed < primary.expected
        best: dict[tuple, dict] = {}
        self.rollbacks_skipped = 0
        for r in rows.to_dict("records"):
            description = r["description"] if isinstance(r["description"], str) else ""   # the column may be NULL
            if "rollback" in description.lower():
                self.rollbacks_skipped += 1
                continue   # by the contract a rollback row ends a release; it is not a new cause
            scope = {d: r[d] for d in SCOPE_DIMS if isinstance(r[d], str) and r[d]}
            ts = pd.Timestamp(r["ts"])
            linked = [m for m in moved if r["system"] in graph.specs[m].systems]
            evidence = []

            # is the system linked to what moved?
            if primary.metric in linked:
                link = 1.0
                evidence.append(f"system {r['system']} is linked to {', '.join(linked)} in the graph")
            elif linked:
                link = 0.5
                evidence.append(f"system {r['system']} is linked to {', '.join(linked)}, a follow-on movement, "
                                f"not to {primary.metric}")
            else:
                link = 0.0
                evidence.append(f"system {r['system']} is not linked to any moved metric in the graph")

            # does the scope fit the place?
            inside_share = None
            if any(d in place_scope and place_scope[d] != v for d, v in scope.items()):
                relation, fit = "conflict", 0.0
                text = f"scope {seg_text(scope)} does not include {where}"
                w = mon.window(primary.metric, scope, inc.onset, end, skip) if mon.has_series(primary.metric, scope) else None
                if w is not None:
                    state = "normal" if w["score"] < NORMAL_MAX_SURPRISE else "moved too"
                    text += f"; {primary.metric} inside its scope is {num(w['observed'])} against {num(w['expected'])} ({state})"
                evidence.append(text)
            elif scope == place_scope:
                relation, fit = "exact", 1.0
                evidence.append(f"scope {seg_text(scope)} is exactly where the change is")
            elif scope.items() < place_scope.items():
                relation, fit = "wider", 0.6
                evidence.append(f"scope {seg_text(scope)} is wider than the change, which is only in {where}")
            else:
                relation = "narrower"
                part = places.get(frozenset({**place, **scope}.items()))
                inside_share = part.share if part is not None else None
                fit = 0.6 if inside_share is not None and inside_share >= 0.8 else 0.2
                evidence.append(f"scope {seg_text(scope)} covers only part of {where}"
                                + (f": {inside_share:.0%} of the change lies inside it" if inside_share is not None else ""))

            # did the change start with the release?
            lag = int((inc.onset - ts) / HOUR)
            if -RELEASE_AFTER_HOURS <= lag <= RELEASE_BEFORE_HOURS:
                timing = 1.0
                evidence.append(f"released {when(ts)}, " + ("the hour the change began" if lag == 0 else
                                                           f"{abs(lag)} h {'before' if lag > 0 else 'after'} the estimated onset"))
            elif lag > RELEASE_BEFORE_HOURS:
                lean = mon.window(primary.metric, segment, ts, inc.onset, skip)
                leans = lean is not None and lean["score"] >= FOLLOW_MIN_SURPRISE and (lean["observed"] < lean["expected"]) == falling
                timing = 0.8 if leans else 0.5 if lean is None else 0.1
                evidence.append(f"released {when(ts)}, {lag} h before the estimated onset; " + (
                    f"the hours in between already lean the same way ({num(lean['observed'])} against {num(lean['expected'])})"
                    if leans else "no data has arrived for the hours in between, so they cannot tell" if lean is None else
                    f"the hours in between were normal ({num(lean['observed'])} against {num(lean['expected'])})"))
            else:
                timing = 0.0
                evidence.append(f"released {when(ts)}, {-lag} h after the change began")

            score = 0.35 * link + 0.35 * fit + 0.30 * timing
            if link == 0.0 or relation == "conflict" or timing <= 0.1 or \
                    (relation == "narrower" and (inside_share is None or inside_share < INSIDE_MIN_SHARE)):
                status = "rejected"
            elif link == 1.0 and relation == "exact" and timing >= 0.8:
                status = "supported"
            else:
                status = "plausible"
            h = {"cause": f"release {r['system']}: {description}", "kind": "release", "status": status, "score": round(score, 2),
                 "evidence": evidence, "_ts": ts, "_lag": abs(lag)}
            key = (r["system"], tuple(sorted(scope.items())), description)
            old = best.get(key)   # the same row twice (a repeated click on the same fault): the better placed one
            if old is None or (h["score"], -h["_lag"]) > (old["score"], -old["_lag"]):
                best[key] = h
        return list(best.values())

    # --------------------------------------------------------------- the report
    def report(self, inc, tick: Tick) -> dict:
        graph, mon = self.graph, self.monitor
        alerts = sorted(inc.metrics.values(), key=lambda a: -a.score)
        end = inc.last_seen + HOUR
        held = sorted(h for h, ms in tick.held.items() if set(ms) & set(inc.metrics))
        trail, uncertain = [], []

        # 1. what moved: causes before symptoms
        symptom_of = self.symptoms(alerts)
        primary = next((a for a in alerts if a.metric not in symptom_of), alerts[0])
        direction = "fell" if primary.observed < primary.expected else "rose"
        trail.append({
            "action": "watch", "target": primary.metric,
            "result": f"{num(primary.observed)} against an expected {num(primary.expected)} in {seg_text(primary.segment)} "
                      f"({num(primary.numerator)} of {num(primary.denominator)} in the newest "
                      f"{min(primary.hours, 3)} bad hour(s); surprise 10^-{primary.score:.0f}, z {primary.z:+.1f})"})
        diluted = sorted({seg_text(a.segment) for a in tick.alerts
                          if a.metric == primary.metric and a.segment != primary.segment
                          and compatible(a.segment, primary.segment)})
        trail.append({"action": "compare", "target": "watched series",
                      "result": f"strongest in {seg_text(primary.segment)}" + (
                          f"; weaker in {', '.join(diluted[:6])}" if diluted else "; no other series of this metric moved")})

        # 2. where
        since = self.phase_start(primary.metric, inc.segment, inc.onset, end, held)
        choice, places, cells = self.localize(primary.metric, inc.segment, since, end, held)
        place = dict(choice.place.segment) if choice is not None else dict(inc.segment)
        segment = place if mon.has_series(primary.metric, place) else dict(inc.segment)   # where rates can be compared
        where = seg_text(place)
        own = choice.place if choice is not None and place != primary.segment else None   # the headline rate is another series'
        seen_in = f" in the watched series {seg_text(primary.segment)}" if own is not None else ""
        obs, exp = (own.observed, own.expected) if own is not None else (primary.observed, primary.expected)
        localization = []
        if choice is not None:
            ranked = sorted(places.values(), key=lambda p: (-p.score, -len(p.segment)))
            shown = [choice.place] + [p for p in ranked if p is not choice.place][:PLACES_SHOWN - 1]
            localization = [{"dimension": " x ".join(ordered(p.segment)) or "all", "segment": seg_text(p.segment),
                             "share_of_delta": p.share, "surprise": p.score, "observed": p.observed,
                             "expected": p.expected, "chosen": p is choice.place} for p in shown]
            text = (f"{len(places)} places scored on {len(cells['keys'])} finest cells; {where} holds "
                    f"{choice.place.share:.0%} of the deviation (gain {choice.place.gain:.0f})")
            if since > inc.onset:
                text += f"; read from the hours since {when(since)}, when the rate changed direction"
            if choice.verdict == "refined":
                how = "narrowed" if inc.segment.items() < place.items() else \
                    "widened" if place.items() < inc.segment.items() else "moved"
                text += f"; {how} from the monitor's series {seg_text(inc.segment)}"
            if choice.rival is not None:
                text += f"; best different place {seg_text(choice.rival.segment)} (gain {choice.rival.gain:.0f})"
            trail.append({"action": "drill", "target": primary.metric, "result": text})
            if ranked[0] is not choice.place and choice.verdict == "kept":
                uncertain.append(f"The scan's best place is {seg_text(ranked[0].segment)}, not {where}: "
                                 "another change may be running there, or the place is wider than reported.")
            elif not choice.clear:
                uncertain.append(f"Place: {seg_text(choice.rival.segment)} explains the counts almost as well as {where}.")
        else:
            trail.append({"action": "drill", "target": primary.metric, "result": "no cell data for this window; the monitor's series is reported"})

        for a in alerts:
            if a is primary:
                continue
            via = symptom_of.get(a.metric)
            trail.append({"action": "watch", "target": a.metric,
                          "result": f"also moved in {seg_text(a.segment)}: {num(a.observed)} against {num(a.expected)} (z {a.z:+.1f})"
                                    + (f"; the graph says {via} may affect it, so it is read as a symptom" if via else "")})
        followed = self.follow(alerts, segment, inc.onset, end, held)
        for f in followed:
            if f["metric"] in inc.metrics or not f["shown"]:
                continue   # it has its own "also moved" line, or is not clear yet
            trail.append({"action": "follow", "target": f["metric"],
                          "result": f"{f['via']} may affect it ({f['note'] or 'link in the graph'}): "
                                    f"{num(f['observed'])} against {num(f['expected'])} in {seg_text(segment)} over the incident "
                                    f"(z {f['z']:+.1f}), "
                                    + ("the direction the link predicts" if f["sign"] else "the link predicts no direction")})

        body = {
            "id": inc.id, "status": inc.status,
            "first_seen": iso_hour(inc.first_seen), "last_seen": iso_hour(inc.last_seen),
            "metric": primary.metric, "segment": ordered(place),
            "fingerprint": f"{primary.metric}|{seg_text(inc.segment)}",
            "change": {"observed": primary.observed, "expected": primary.expected,
                       "delta_pct": 100 * primary.effect, "z": primary.z,
                       "window": {"start": iso_hour(inc.onset), "end": iso_hour(end)}},
            "drivers": [{"metric": a.metric, "segment": ordered(a.segment), "observed": a.observed,
                         "expected": a.expected, "delta_pct": 100 * a.effect, "z": a.z, "surprise": a.score,
                         "test": a.kind, "onset": iso_hour(a.onset),
                         "role": f"symptom of {symptom_of[a.metric]}" if a.metric in symptom_of else "cause candidate"}
                        for a in alerts],
            "localization": localization,
        }

        # 3. what it cost
        waiting = sorted(tick.late)      # hours with any source still missing are in no sum
        cost = self.explain(alerts, followed, segment, inc.onset, end, waiting, getattr(inc, "frozen", None))
        loss = ""
        if cost is not None:
            outcome, unit, effect = cost["outcome"], cost["unit"], cost["effect"]
            restored = cost["actual"] - effect      # the outcome with the moved rates back at their expected values
            body["metric"] = outcome
            body["change"] = {"observed": cost["actual"], "expected": restored, "delta": effect,
                              "delta_pct": 100 * effect / restored if restored else None,
                              "window": {"start": iso_hour(inc.onset), "end": iso_hour(end)}}
            if segment != place:
                body["change"]["segment"] = ordered(segment)
                uncertain.append(f"The effect is measured in {seg_text(segment)}, the nearest series the monitor keeps a "
                                 f"baseline for; it contains {where}.")
            body["impact"] = {f"{outcome}_{unit}" if unit != "count" else outcome: effect, **cost["counted"]}
            body["contributions"] = cost["contributions"]
            if cost["explained"] is not None:
                body["explained_share"] = cost["explained"]
            rest = cost["gap"] - effect
            trail.append({"action": "decompose", "target": outcome, "result": (
                f"{num(cost['actual'], 4)} observed in {seg_text(segment)} since {when(inc.onset)}; with "
                f"{', '.join(cost['moved'])} back at the expected rate it would be {num(restored, 4)}: "
                + ", ".join(f"{c['node']} {c['delta']:+,.0f} {unit}" for c in cost["contributions"])
                + f" ({cost['method']} over every path of the formulas). Against the state with every judged rate at its "
                  f"baseline the gap is {cost['gap']:+,.0f} {unit}, so the other rates account for {rest:+,.0f}"
                + (" (largest: " + ", ".join(f"{m} {v:+,.0f}" for m, v in cost["others"][:3]) + ")" if cost["others"] else "")
                + (f"; {len(waiting)} hour(s) still waiting for data are left out" if waiting else ""))})
            for m in cost["unclear"]:
                f = next(x for x in followed if x["metric"] == m)
                uncertain.append(f"The effect so far runs partly through {m}, which the link from {f['via']} predicts but the "
                                 f"data does not show yet ({num(f['observed'])} against {num(f['expected'])}, z {f['z']:+.1f}): "
                                 "the amount, even its sign, can still change.")
            also = "".join(f", {d:+,.0f} {m}" for m, d in cost["counted"].items())
            loss = f" Effect on {outcome} in {seg_text(segment)} so far: {effect:+,.0f} {unit}{also}."
        elif any(m in tree_metrics(graph, o) for o in graph.top_outcomes()[:1] for m in inc.metrics):
            uncertain.append("The effect on the outcome could not be computed for this window: a factor of the formulas "
                             "has no value or no baseline there.")

        # 4. why
        trail.append({"action": "check", "target": "freshness",
                      "result": "all sources for these metrics have arrived" if not held else
                      "hours still waiting for data: " + ", ".join(when(h) for h in held)})
        covering = [e for e in tick.events
                    if e["start"] < end and inc.onset < e["end"]
                    and (e["country"] is None or place.get("country") in (None, e["country"]))]
        linked = sorted(m for m in inc.metrics if any(n.id == "calendar" for n in graph.context_of(m)))
        trail.append({"action": "check", "target": "calendar", "result": (
            f"no planned event covers {where} in these hours" if not covering else
            "; ".join(f"{e['description'] or e['kind']} ({when(e['start'])} to {when(e['end'])})" for e in covering)
            + " overlaps, but the graph links the calendar to "
            + (f"only {', '.join(linked)} of the moved metrics" if linked else "none of the moved metrics")
            + ": it does not explain this change")})

        moved = list(inc.metrics) + [f["metric"] for f in followed if f["shown"] and f["metric"] not in inc.metrics]
        candidates = self.releases(inc, tick, primary, moved, place, segment, end, places, held)
        for h in sorted(candidates, key=lambda h: h["_ts"]):
            trail.append({"action": "check", "target": h["cause"].split(":")[0].replace("release ", "release:"),
                          "result": f"{h['cause'].split(': ', 1)[-1]}: {h['status']} (score {h['score']:.2f}); " + "; ".join(h["evidence"])})
        if not candidates:
            trail.append({"action": "check", "target": "releases", "result": (
                f"no release in the {RELEASE_LOOKBACK_HOURS} h before the onset or since"
                + (f" ({self.rollbacks_skipped} rollback row(s) in that time end a release and are not causes)"
                   if self.rollbacks_skipped else ""))})
        kept = [h for h in candidates if h["status"] != "rejected"]
        rejected = sorted((h for h in candidates if h["status"] == "rejected"), key=lambda h: -h["score"])[:MAX_REJECTED_RELEASES]
        hypotheses = kept + rejected

        if not any(h["status"] == "supported" for h in hypotheses):
            # the moved metric itself is the first cause when a link of the graph that has a direction
            # predicted a second movement and that movement is there; otherwise it stays a candidate
            confirmed = [f"{f['metric']} moved as the link from {primary.metric} predicts: {num(f['observed'])} "
                         f"against {num(f['expected'])} (z {f['z']:+.1f})"
                         for f in followed if f["via"] == primary.metric and f["sign"] and f["shown"]]
            if confirmed:
                inc.confirmed = confirmed   # once confirmed it stays so while the incident is open: a later
            hypotheses.append({               # tick with a thinner window must not take the label back
                "cause": f"{primary.metric} {direction} in {where} without a matching release"
                         + ("" if covering else " or planned event"),
                "kind": "metric", "status": "supported" if inc.confirmed else "plausible",
                "score": 0.7 if inc.confirmed else 0.4,
                "evidence": [f"{num(primary.observed)} against {num(primary.expected)} expected{seen_in}, z {primary.z:+.1f}"]
                + inc.confirmed})

        for e in covering:
            hypotheses.append({
                "cause": f"planned event: {e['description'] or e['kind']}", "kind": "calendar", "status": "rejected", "score": 0.1,
                "evidence": [f"{when(e['start'])} to {when(e['end'])} overlaps the incident",
                             "the graph links the calendar to "
                             + (f"only {', '.join(linked)} of the moved metrics" if linked else "none of the moved metrics")]})
        hypotheses.append({
            "cause": "late or missing data", "kind": "freshness", "status": "rejected", "score": 0.0,
            "evidence": ["every source these metrics depend on had arrived for the hours of the incident" if not held else
                         f"{len(held)} hour(s) are still waiting for data and are left out of every comparison above; "
                         "the incident rests on the hours that have arrived"]})
        split = self.mix(cells, segment) if cells is not None else None
        if split is not None:
            hypotheses.append({
                "cause": f"a shift in the mix of segments inside {seg_text(segment)}, with unchanged rates", "kind": "metric",
                "status": "rejected" if split["share"] < MIX_MAX_SHARE else "plausible", "score": round(0.5 * split["share"], 2),
                "evidence": [f"{primary.metric}: the cells' own rates account for {split['rate']:+.4f}, "
                             f"the changed weights of the cells for {split['mix']:+.4f} ({split['share']:.0%} of the change)"]})

        rank = {"supported": 0, "plausible": 1, "rejected": 2}
        hypotheses.sort(key=lambda h: (rank[h["status"]], -h["score"], h.get("_lag", 0)))
        for h in hypotheses:
            h.pop("_ts", None)
            h.pop("_lag", None)
        lead = hypotheses[0]
        second = hypotheses[1] if len(hypotheses) > 1 else None
        trail.append({"action": "compare", "target": "explanations", "result": (
            f"{len(hypotheses)} tested: " + "; ".join(f"{h['cause']} - {h['status']} ({h['score']:.2f})" for h in hypotheses[:6])
            + ("; ..." if len(hypotheses) > 6 else ""))})
        standing = [h for h in hypotheses[1:] if h["status"] != "rejected"]
        rival = max(standing, key=lambda h: h["score"]) if standing else None
        if lead["status"] != "supported":
            verdict = "no explanation is supported yet: every candidate was tested, the leading one stays a candidate"
        elif rival is None:
            verdict = "the leading explanation is supported and every other one was rejected"
        elif lead["score"] > rival["score"]:
            verdict = f"the leading explanation is supported and ahead of the next one standing by {lead['score'] - rival['score']:.2f}"
        else:
            verdict = (f"the leading explanation is supported, but {rival['cause']} ({rival['status']}) scores "
                       f"{rival['score']:.2f} against {lead['score']:.2f}")
        trail.append({"action": "stop", "target": "investigation", "result": verdict})

        # 5. what stays open
        if (primary.observed - primary.expected) * (primary.episode_observed - primary.expected) < 0:
            uncertain.append(f"{primary.metric} changed direction during the incident; the effect so far nets both phases.")
        if primary.hours < 2 and inc.status == "open":
            uncertain.append("Only one hour of evidence so far.")
        if sum(h["status"] == "supported" for h in hypotheses) > 1:
            uncertain.append("More than one explanation fits in system, place and time.")
        if cost is not None:
            uncertain.append("Traffic is taken as given: only rates are judged, so a change in sessions is not part of the effect.")

        body["hypotheses"] = hypotheses
        body["trail"] = trail
        body["uncertain"] = uncertain
        body["report"] = (
            f"{primary.metric} {'fell' if obs < exp else 'rose'} to {num(obs)} from an expected {num(exp)} in "
            f"{where}, starting {when(inc.onset)}.{loss} "
            + (f"Most likely cause: {lead['cause']}." if lead["status"] == "supported" else "No cause is confirmed yet.")
            + (f" {sum(h['status'] == 'rejected' for h in hypotheses)} other explanation(s) were tested and rejected."
               if any(h["status"] == "rejected" for h in hypotheses) else ""))
        return body

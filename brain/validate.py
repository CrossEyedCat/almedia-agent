"""Validation of the graph against the data, run when the graph is loaded.

Checks per metric:
  coverage     the metric has finite values in the window
  identity     its investigative formula reproduces it, hourly, in total and per segment
               (skipped when the formula *is* its definition: true by construction)
  definition   a metric with both a column/SQL and a formula: the two agree
  additivity   an additive metric's segments sum to its total, for every dimension
  bounds       hourly values stay inside the declared range
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from brain.data import Store
from brain.evaluator import Evaluator
from brain.graph import MetricGraph
from brain.metric_spec import DIMENSIONS

EPS = 1e-9


@dataclass
class Check:
    metric: str
    check: str
    status: str  # pass | fail | skip
    detail: str
    worst: float | None = None


def rel_error(a: pd.Series, b: pd.Series) -> tuple[float, int]:
    """Largest relative difference over rows where both are finite, and how many rows were compared."""
    a = pd.to_numeric(a, errors="coerce").to_numpy(dtype=float)
    b = pd.to_numeric(b, errors="coerce").to_numpy(dtype=float)
    mask = np.isfinite(a) & np.isfinite(b)
    if not mask.any():
        return float("nan"), 0
    denom = np.maximum(np.maximum(np.abs(a[mask]), np.abs(b[mask])), EPS)
    return float(np.max(np.abs(a[mask] - b[mask]) / denom)), int(mask.sum())


def validate_graph(graph: MetricGraph, store: Store, days: int = 7, start=None, end=None) -> list[Check]:
    ev = Evaluator(graph, store)
    if end is None:
        end = store.time_range()[1] + pd.Timedelta(hours=1)
    if start is None:
        start = pd.Timestamp(end) - pd.Timedelta(days=days)

    checks: list[Check] = []
    computable = []
    for m in graph.specs:
        if ev.computable(m):
            computable.append(m)
        else:
            checks.append(Check(m, "coverage", "skip", "not computable from columns yet (SQL-only metric)"))

    total = ev.frame(computable, start, end, by_hour=True)
    per_dim = {d: ev.frame(computable, start, end, group_by=(d,), by_hour=True) for d in DIMENSIONS}
    levels = [("total", total)] + [(f"by {d}", f) for d, f in per_dim.items()]

    for m in computable:
        s = graph.specs[m]

        # coverage
        finite = np.isfinite(pd.to_numeric(total[m], errors="coerce")).sum()
        checks.append(Check(m, "coverage", "pass" if finite else "fail",
                            f"{finite} of {len(total)} hours have a value"))

        # identity along the investigative formula
        inv = s.investigative_formula
        if inv is not None:
            if s.formula is not None and inv.text == s.formula.text and not s.column:
                checks.append(Check(m, "identity", "skip", f"{inv.text} is its definition"))
            else:
                worst, compared, where = 0.0, 0, ""
                for label, f in levels:
                    expr = inv.evaluate({n: f[n] for n in inv.names})
                    err, n = rel_error(f[m], expr)
                    compared += n
                    if n and err > worst:
                        worst, where = err, label
                ok = compared > 0 and worst <= s.tolerance
                checks.append(Check(m, "identity", "pass" if ok else "fail",
                                    f"{m} = {inv.text}: max relative error {worst:.2e}"
                                    + (f" ({where})" if where else "") + f" over {compared} rows",
                                    worst))
                for e in graph.out_edges(m, "formula"):
                    graph.status[e.id] = "validated" if ok else "failed"

        # column/SQL vs formula
        if s.formula is not None and s.column and (inv is None or inv.text != s.formula.text):
            expr = s.formula.evaluate({n: total[n] for n in s.formula.names})
            err, n = rel_error(total[m], expr)
            ok = n > 0 and err <= s.tolerance
            checks.append(Check(m, "definition", "pass" if ok else "fail",
                                f"column {s.column} vs {s.formula.text}: max relative error {err:.2e}", err))

        # additivity
        if graph.additive.get(m):
            worst, bad_dim = 0.0, ""
            for d in s.dimensions:
                summed = per_dim[d].groupby("ts")[m].sum(min_count=1)
                aligned = total.set_index("ts")[m].reindex(summed.index)
                err, n = rel_error(aligned, summed)
                if n and err > worst:
                    worst, bad_dim = err, d
            ok = worst <= max(s.tolerance, 1e-9)
            checks.append(Check(m, "additivity", "pass" if ok else "fail",
                                "segments sum to the total for " + ", ".join(s.dimensions)
                                if ok else f"segments by {bad_dim} differ from the total by {worst:.2e}", worst))

        # bounds
        if s.bounds is not None:
            lo, hi = s.bounds
            n_bad, n_all = 0, 0
            for _, f in levels:
                v = pd.to_numeric(f[m], errors="coerce")
                v = v[np.isfinite(v)]
                n_all += len(v)
                if lo is not None:
                    n_bad += int((v < lo - 1e-12).sum())
                if hi is not None:
                    n_bad += int((v > hi + 1e-12).sum())
            checks.append(Check(m, "bounds", "pass" if n_bad == 0 else "fail",
                                f"{n_bad} of {n_all} hourly values outside [{lo}, {hi}]"))

    failed = {c.metric for c in checks if c.status == "fail"}
    for m in graph.specs:
        if m in failed:
            graph.status[m] = "failed"
        elif m in computable:
            graph.status[m] = "validated"
    return checks


def summarize(checks: list[Check]) -> str:
    by_status = {s: [c for c in checks if c.status == s] for s in ("pass", "fail", "skip")}
    metrics = sorted({c.metric for c in checks})
    lines = [f"Validation: {len(by_status['pass'])} passed, {len(by_status['fail'])} failed, "
             f"{len(by_status['skip'])} skipped across {len(metrics)} metrics"]
    for c in by_status["fail"]:
        lines.append(f"  ✗ {c.metric} [{c.check}] {c.detail}")
    for c in by_status["skip"]:
        if c.check != "identity":
            lines.append(f"  – {c.metric} [{c.check}] {c.detail}")
    return "\n".join(lines)

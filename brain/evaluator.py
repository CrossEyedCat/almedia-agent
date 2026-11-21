"""Compute any metric for any segment, window and grouping.

Column metrics are summed in SQL; derived metrics are evaluated on those sums, so a ratio is
always a ratio of sums (never an average of ratios) at whatever level it is asked for.
"""
from __future__ import annotations

import pandas as pd

from brain.data import Store
from brain.formula import Formula
from brain.graph import MetricGraph


class NotComputable(ValueError):
    pass


class _LazyEnv:
    def __init__(self, compute):
        self._compute = compute

    def __getitem__(self, name):
        return self._compute(name)


class Evaluator:
    def __init__(self, graph: MetricGraph, store: Store):
        self.graph = graph
        self.store = store

    def base_columns(self, metric: str, _stack: tuple[str, ...] = ()) -> set[str]:
        if metric in _stack:
            raise NotComputable(f"definition cycle: {' -> '.join(_stack + (metric,))}")
        s = self.graph.specs[metric]
        if s.column:
            if s.column not in self.store.measures:
                raise NotComputable(f"{metric}: column {s.column} is not in the data")
            return {s.column}
        if s.formula is not None:
            cols: set[str] = set()
            for n in s.formula.names:
                cols |= self.base_columns(n, _stack + (metric,))
            return cols
        raise NotComputable(f"{metric}: defined only by SQL; needs the extension agent to infer a formula")

    def computable(self, metric: str) -> bool:
        try:
            self.base_columns(metric)
            return True
        except NotComputable:
            return False

    def frame(self, metrics, start=None, end=None, segment: dict | None = None,
              group_by=(), by_hour: bool = False) -> pd.DataFrame:
        """One row per (hour?, group) with a column per requested metric."""
        metrics = list(dict.fromkeys(metrics))
        cols: set[str] = set()
        for m in metrics:
            cols |= self.base_columns(m)
        raw = self.store.sums(sorted(cols), start, end, segment, group_by, by_hour)
        keys = (["ts"] if by_hour else []) + list(group_by)
        out = raw[keys].copy()
        cache: dict[str, pd.Series] = {}

        def compute(name: str):
            if name not in cache:
                s = self.graph.specs[name]
                if s.column:
                    cache[name] = raw[s.column]
                else:
                    cache[name] = s.formula.evaluate(_LazyEnv(compute))
            return cache[name]

        for m in metrics:
            out[m] = compute(m)
        return out

    def expression(self, formula: Formula, start=None, end=None, segment=None,
                   group_by=(), by_hour: bool = False) -> pd.DataFrame:
        """Evaluate an arbitrary formula over metrics; the result is in column `value`."""
        f = self.frame(formula.names, start, end, segment, group_by, by_hour)
        f["value"] = formula.evaluate({n: f[n] for n in formula.names})
        return f

    # ------------------------------------------------------------ shortcuts
    def value(self, metric: str, start=None, end=None, segment=None) -> float:
        f = self.frame([metric], start, end, segment)
        return float(f[metric].iloc[0]) if len(f) else float("nan")

    def series(self, metric: str, start=None, end=None, segment=None) -> pd.Series:
        f = self.frame([metric], start, end, segment, by_hour=True)
        return f.set_index("ts")[metric]

    def breakdown(self, metric: str, dim: str, start=None, end=None, segment=None) -> pd.Series:
        f = self.frame([metric], start, end, segment, group_by=(dim,))
        return f.set_index(dim)[metric]

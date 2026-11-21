"""The metric graph: how the business works, as metrics and typed relationships.

Edge kinds, each supporting a different kind of reasoning:
  formula     parent -> component of an exact identity used for decomposition
              (gmv = orders * aov). Must be acyclic.
  definition  derived metric -> an input of its computing formula, when that formula is not
              the investigative identity (take_rate = marketplace_fees / gmv).
  breakdown   metric -> metric@dimension (orders by country); drill-down.
  may_affect  possible cause -> effect, a hypothesis with a prior (stockouts -> add_to_cart_rate).
  context     release:<system> / freshness:<source> / calendar -> metric; evidence sources.

Run `python -m brain.graph --tree` for the tree view.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from brain.metric_spec import DIMENSIONS, MetricSpec, load_specs

DEFAULT_METRICS_DIR = Path(__file__).parent / "metrics"
EDGE_KINDS = ("formula", "definition", "breakdown", "may_affect", "context")
DEMAND_SYSTEM = "demand"  # metrics tagged with this system are linked to the promo calendar


class GraphError(ValueError):
    pass


@dataclass
class Node:
    id: str
    kind: str  # metric | breakdown | context
    label: str
    attrs: dict = field(default_factory=dict)


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    attrs: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.src}->{self.dst}"


@dataclass
class TreeNode:
    metric: str
    role: str | None  # role in the parent's identity: factor, term+, term-, ...
    children: list["TreeNode"] = field(default_factory=list)
    repeated: bool = False  # already shown elsewhere in the tree


class MetricGraph:
    def __init__(self) -> None:
        self.specs: dict[str, MetricSpec] = {}
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.status: dict[str, str] = {}  # metric or edge id -> validated / failed / assumed

    # ----------------------------------------------------------------- build
    @classmethod
    def from_specs(cls, specs: list[MetricSpec]) -> "MetricGraph":
        g = cls()
        for s in specs:
            if s.name in g.specs:
                raise GraphError(f"duplicate metric {s.name}")
            g.specs[s.name] = s
        g._check_references()
        g._infer_additivity()
        g._build()
        g._check_formula_cycles()
        return g

    @classmethod
    def load(cls, metrics_dir: str | Path = DEFAULT_METRICS_DIR) -> "MetricGraph":
        return cls.from_specs(load_specs(metrics_dir))

    def _check_references(self) -> None:
        for s in self.specs.values():
            for label, f in (("formula", s.formula), ("decomposition", s.decomposition)):
                if f is None:
                    continue
                missing = [n for n in f.names if n not in self.specs]
                if missing:
                    raise GraphError(f"{s.name}: {label} uses unknown metrics {missing}")
            for link in s.may_affect:
                if link.metric not in self.specs:
                    raise GraphError(f"{s.name}: may_affect points to unknown metric {link.metric}")
                if link.metric == s.name:
                    raise GraphError(f"{s.name}: may_affect points to itself")

    def _infer_additivity(self) -> None:
        """Column metrics are additive; a sum of additive metrics is additive; others are not."""
        resolved: dict[str, bool] = {}

        def additive(name: str, stack: tuple[str, ...] = ()) -> bool:
            if name in resolved:
                return resolved[name]
            s = self.specs[name]
            if s.additive is not None:
                val = bool(s.additive)
            elif s.column:
                val = True
            elif s.formula is not None and s.formula.kind in ("sum", "name") and name not in stack:
                val = all(additive(n, stack + (name,)) for n in s.formula.names)
            else:
                val = False
            resolved[name] = val
            return val

        for name in self.specs:
            additive(name)
        self.additive = resolved

    def _build(self) -> None:
        for s in self.specs.values():
            self.nodes[s.name] = Node(
                id=s.name, kind="metric", label=s.name,
                attrs={"unit": s.unit, "description": s.description, "origin": s.origin,
                       "additive": self.additive[s.name], "dimensions": list(s.dimensions),
                       "direction": s.direction, "systems": list(s.systems), "source": s.source,
                       "formula": s.formula.text if s.formula else None,
                       "decomposition": s.investigative_formula.text if s.investigative_formula else None,
                       "column": s.column},
            )
            self.status.setdefault(s.name, "unvalidated")

        for s in self.specs.values():
            inv = s.investigative_formula
            if inv is not None:
                for name, role in inv.roles().items():
                    self._add_edge(s.name, name, "formula", formula=inv.text, role=role)
            if s.formula is not None and (inv is None or inv.text != s.formula.text):
                for name, role in s.formula.roles().items():
                    self._add_edge(s.name, name, "definition", formula=s.formula.text, role=role)
            for dim in s.dimensions:
                bid = f"{s.name}@{dim}"
                self.nodes[bid] = Node(id=bid, kind="breakdown", label=f"{s.name} by {dim}",
                                       attrs={"metric": s.name, "dimension": dim})
                self._add_edge(s.name, bid, "breakdown", dimension=dim,
                               aggregation="sum" if self.additive[s.name] else "ratio_of_sums")
            for link in s.may_affect:
                e = self._add_edge(s.name, link.metric, "may_affect", prior=link.prior, note=link.note)
                self.status[e.id] = "assumed"
            for system in s.systems:
                cid = f"release:{system}"
                self.nodes.setdefault(cid, Node(id=cid, kind="context", label=f"releases of {system}",
                                                attrs={"table": "releases", "system": system}))
                self._add_edge(cid, s.name, "context", via="releases.system")
                if system == DEMAND_SYSTEM:
                    self.nodes.setdefault("calendar", Node(id="calendar", kind="context",
                                                           label="promo and holiday calendar",
                                                           attrs={"table": "calendar"}))
                    self._add_edge("calendar", s.name, "context", via="calendar.country")
            if s.source:
                cid = f"freshness:{s.source}"
                self.nodes.setdefault(cid, Node(id=cid, kind="context", label=f"freshness of {s.source}",
                                                attrs={"table": "ingestion_log", "source": s.source}))
                self._add_edge(cid, s.name, "context", via="ingestion_log.source")

    def _add_edge(self, src: str, dst: str, kind: str, **attrs) -> Edge:
        e = Edge(src, dst, kind, attrs)
        self.edges.append(e)
        return e

    def _check_formula_cycles(self) -> None:
        children = {m: [e.dst for e in self.out_edges(m, "formula")] for m in self.specs}
        color: dict[str, int] = {}

        def visit(m: str, path: list[str]) -> None:
            color[m] = 1
            for c in children.get(m, []):
                if color.get(c) == 1:
                    cycle = path[path.index(c):] + [c] if c in path else [m, c]
                    raise GraphError("formula cycle: " + " -> ".join(cycle)
                                     + " (mark one of these metrics `atomic: true`)")
                if color.get(c) is None:
                    visit(c, path + [c])
            color[m] = 2

        for m in self.specs:
            if m not in color:
                visit(m, [m])

    # --------------------------------------------------------------- queries
    def out_edges(self, node: str, kind: str | None = None) -> list[Edge]:
        return [e for e in self.edges if e.src == node and (kind is None or e.kind == kind)]

    def in_edges(self, node: str, kind: str | None = None) -> list[Edge]:
        return [e for e in self.edges if e.dst == node and (kind is None or e.kind == kind)]

    def formula_children(self, metric: str) -> list[tuple[str, str]]:
        return [(e.dst, e.attrs.get("role", "")) for e in self.out_edges(metric, "formula")]

    def formula_parents(self, metric: str) -> list[str]:
        return [e.src for e in self.in_edges(metric, "formula")]

    def possible_causes(self, metric: str) -> list[Edge]:
        return self.in_edges(metric, "may_affect")

    def context_of(self, metric: str) -> list[Node]:
        return [self.nodes[e.src] for e in self.in_edges(metric, "context")]

    def top_outcomes(self) -> list[str]:
        """Metrics that nothing decomposes into, but that decompose into others."""
        return [m for m in self.specs if not self.formula_parents(m) and self.formula_children(m)]

    def outside_tree(self) -> list[str]:
        reachable: set[str] = set()
        for root in self.top_outcomes():
            stack = [root]
            while stack:
                m = stack.pop()
                if m in reachable:
                    continue
                reachable.add(m)
                stack.extend(c for c, _ in self.formula_children(m))
        return [m for m in self.specs if m not in reachable]

    # ------------------------------------------------------------------ tree
    def tree(self, root: str) -> TreeNode:
        if root not in self.specs:
            raise GraphError(f"unknown metric {root}")
        seen: set[str] = set()

        def build(m: str, role: str | None) -> TreeNode:
            if m in seen:
                return TreeNode(m, role, repeated=True)
            seen.add(m)
            node = TreeNode(m, role)
            node.children = [build(c, r) for c, r in self.formula_children(m)]
            return node

        return build(root, None)

    def render_tree(self, root: str | None = None) -> str:
        roots = [root] if root else self.top_outcomes()
        lines: list[str] = []

        def label(t: TreeNode) -> str:
            s = self.specs[t.metric]
            role = {"term+": "+ ", "term-": "− ", "factor": "× ", "numerator": "÷num ",
                    "denominator": "÷den ", "alias": "= "}.get(t.role or "", "")
            unit = f" [{s.unit}]" if s.unit else ""
            if t.repeated:
                return f"{role}{t.metric}{unit}  (shown above)"
            inv = s.investigative_formula
            rule = f"  = {inv.text}" if inv is not None else ""
            state = self.status.get(t.metric, "")
            mark = {"validated": "  ✓", "failed": "  ✗ FAILED"}.get(state, "")
            return f"{role}{t.metric}{unit}{rule}{mark}"

        def walk(t: TreeNode, prefix: str, last: bool, top: bool) -> None:
            if top:
                lines.append(label(t))
            else:
                lines.append(prefix + ("└─ " if last else "├─ ") + label(t))
            child_prefix = prefix if top else prefix + ("   " if last else "│  ")
            for i, c in enumerate(t.children):
                walk(c, child_prefix, i == len(t.children) - 1, False)

        for r in roots:
            walk(self.tree(r), "", True, True)
            lines.append("")
        rest = self.outside_tree()
        if rest and root is None:
            lines.append("Outside the outcome tree (linked by definition, may_affect or context):")
            for m in rest:
                links = [f"{e.kind}→{e.dst}" for e in self.out_edges(m) if e.kind in ("may_affect",)]
                links += [f"defines {e.src}" for e in self.in_edges(m, "definition")]
                lines.append(f"  · {m}" + (f"  ({', '.join(links)})" if links else ""))
        dims = sorted({e.attrs['dimension'] for e in self.edges if e.kind == "breakdown"})
        lines.append(f"Breakdowns available: {', '.join(dims)}")
        return "\n".join(lines)

    # ---------------------------------------------------------------- export
    def to_dict(self) -> dict:
        def tree_dict(t: TreeNode) -> dict:
            return {"metric": t.metric, "role": t.role, "repeated": t.repeated,
                    "children": [tree_dict(c) for c in t.children]}

        return {
            "nodes": [{"id": n.id, "kind": n.kind, "label": n.label,
                       "status": self.status.get(n.id, "") if n.kind == "metric" else "",
                       **n.attrs} for n in self.nodes.values()],
            "edges": [{"id": e.id, "source": e.src, "target": e.dst, "kind": e.kind,
                       "status": self.status.get(e.id, ""), **e.attrs} for e in self.edges],
            "top_outcomes": self.top_outcomes(),
            "trees": [tree_dict(self.tree(r)) for r in self.top_outcomes()],
            "dimensions": list(DIMENSIONS),
        }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m brain.graph", description="Metric graph tools")
    p.add_argument("--metrics", default=str(DEFAULT_METRICS_DIR), help="metrics directory or YAML file")
    p.add_argument("--db", default=os.environ.get("MARKET_DB", "data/market.db"))
    p.add_argument("--tree", nargs="?", const="", metavar="ROOT", help="print the tree (optionally from ROOT)")
    p.add_argument("--validate", action="store_true", help="check identities, additivity and bounds on the data")
    p.add_argument("--days", type=int, default=7, help="days of recent data to validate on")
    p.add_argument("--export", metavar="PATH", help="write nodes, edges and trees as JSON")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    graph = MetricGraph.load(a.metrics)
    if a.validate:
        from brain.data import Store
        from brain.validate import summarize, validate_graph
        store = Store(a.db)
        checks = validate_graph(graph, store, days=a.days)
        print(summarize(checks))
        print()
    if a.tree is not None or not (a.validate or a.export):
        print(graph.render_tree(a.tree or None))
    if a.export:
        Path(a.export).parent.mkdir(parents=True, exist_ok=True)
        Path(a.export).write_text(json.dumps(graph.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {a.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

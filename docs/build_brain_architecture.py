"""Build docs/brain-architecture.html from its template and the metric graph the brain loads.

    python docs/build_brain_architecture.py

The graph figure is drawn from MetricGraph.load(), so it cannot drift from the code: a metric that
enters the outcome tree without a position here stops the build. Open the result in a browser.
"""
from __future__ import annotations

import argparse
import html
import pathlib
import sys
from collections import Counter
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from brain.graph import MetricGraph  # noqa: E402

# Refresh by hand when they change: the number of brain tests, and the two numbers printed by
# `python -m brain.graph --validate --days 28` on a 28-day simulator database.
BRAIN_TESTS = 102
DATA_CHECKS = 52
DEFINITION_CHECKS = 28

g = MetricGraph.load()
esc = html.escape


# ------------------------------------------------------------------ small SVG helpers
def card(x, y, w, h, title, lines, cls="box", line_cls="tm", step=15):
    out = [f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="6"/>',
           f'<text class="tt" x="{x + 12}" y="{y + 21}">{esc(title)}</text>']
    for i, ln in enumerate(lines):
        c = line_cls
        if isinstance(ln, tuple):
            ln, c = ln
        out.append(f'<text class="{c}" x="{x + 12}" y="{y + 39 + i * step}">{esc(ln)}</text>')
    return "\n".join(out)


def arrow(x1, y1, x2, y2, cls="edge", marker="ar"):
    return f'<line class="{cls}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" marker-end="url(#{marker})"/>'


def elbow(points, cls="edge", marker="ar"):
    d = "M" + " L".join(f"{x},{y}" for x, y in points)
    return f'<path class="{cls}" d="{d}" marker-end="url(#{marker})"/>'


def defs(prefix):
    return (f'<defs>'
            f'<marker id="{prefix}ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="mk"/></marker>'
            f'<marker id="{prefix}am" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="mk-m"/></marker>'
            f'<marker id="{prefix}ac" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="mk-c"/></marker>'
            f'</defs>')


# ------------------------------------------------------------------ figure 1: from data to incident
def fig_pipeline():
    counts = Counter(e.kind for e in g.edges)
    s = [defs("p-")]
    A = lambda *a, **k: arrow(*a, marker="p-ar", **k)
    # row 1: extension
    s.append(card(250, 16, 150, 54, "new metric", ["YAML + SQL"]))
    s.append(card(430, 16, 455, 54, "extension agent",
                  ["planned · M4: run the SQL · find the formula · spot aliases · find the place"], cls="box-o"))
    s.append(A(400, 43, 427, 43))
    s.append(A(730, 70, 730, 103))
    s.append('<text class="lbl" x="738" y="92">adds a placed, checked metric</text>')
    # row 2: the model
    s.append(card(250, 106, 150, 74, "metrics/*.yaml", [f"{len(g.specs)} metric definitions", "formulas and causes"]))
    s.append(card(430, 106, 150, 74, "metric_spec.py", ["specs checked on load", "formula.py: parser"]))
    s.append(card(610, 106, 275, 74, "graph.py · MetricGraph",
                  [f"{len(g.specs)} metrics, {len(g.edges)} typed edges", "declared outcome: contribution"], cls="box-a"))
    s.append(card(905, 106, 130, 74, "validate.py", [f"{DATA_CHECKS} checks on data", f"{DEFINITION_CHECKS} by definition"]))
    s.append(A(400, 143, 427, 143))
    s.append(A(580, 143, 607, 143))
    s.append(A(885, 143, 902, 143))
    # row 3: the data path
    s.append(card(16, 216, 204, 124, "simulator database",
                  [("facts_hourly", "mono"), ("ingestion_log", "mono"), ("releases", "mono"), ("calendar", "mono"),
                   ("ground_truth: not readable", "tw")]))
    s.append(card(250, 226, 150, 104, "data.py · Store", ["read-only connection", "tables whitelisted", "NULL stays visible"]))
    s.append(card(430, 226, 150, 104, "evaluator.py", ["sums of base columns", "derive any metric", "rate = ratio of sums"]))
    s.append(card(610, 226, 126, 104, "monitor", ["planned · M2", "expected state", "rates in cells", "completeness gate"], cls="box-o"))
    s.append(card(748, 226, 140, 104, "investigator", ["planned · M3", "attribution on DAG", "scan all segments", "context, hypotheses"], cls="box-o"))
    s.append(card(900, 226, 135, 104, "reporter", ["planned · M3", "incident JSON", "lifecycle, dedup"], cls="box-o"))
    s.append(A(220, 278, 247, 278))
    s.append(A(400, 278, 427, 278))
    s.append(A(580, 278, 607, 278))
    s.append(A(736, 278, 745, 278))
    s.append(A(888, 278, 897, 278))
    # the graph feeds the data path
    s.append(elbow([(640, 180), (640, 204), (505, 204), (505, 223)], marker="p-ar"))
    s.append('<text class="lbl" x="512" y="199">definitions</text>')
    s.append(A(675, 180, 675, 223))
    s.append('<text class="lbl" x="681" y="212">what to watch</text>')
    s.append(A(820, 180, 820, 223))
    s.append('<text class="lbl" x="826" y="212">edges to follow</text>')
    # output
    s.append(card(900, 356, 135, 44, "incidents/*.json", ["read by the UI"]))
    s.append(A(967, 330, 967, 353))
    # legend
    s.append('<rect class="box" x="16" y="372" width="22" height="14" rx="3"/><text class="tm" x="46" y="383">built and tested</text>')
    s.append('<rect class="box-o" x="160" y="372" width="22" height="14" rx="3"/><text class="tm" x="190" y="383">planned</text>')
    label = ("Metric definitions become a typed graph; the store and the evaluator turn the simulator's tables into metric "
             "values; the planned monitor, investigator and reporter turn those into incidents; the planned extension "
             "agent adds new metrics to the graph.")
    return f'<svg class="d" viewBox="0 0 1050 412" role="img" aria-label="{esc(label)}">' + "\n".join(s) + "</svg>", counts


# ------------------------------------------------------------------ figure 2: the metric graph
W, H = 158, 30
POS = {
    "contribution": (446, 16),
    "ad_spend": (120, 86), "marketplace_fees": (446, 86), "discounts": (780, 86),
    "ad_cpc": (16, 156), "ad_clicks": (190, 156), "take_rate": (400, 156), "gmv": (613, 156), "discount_rate": (876, 156),
    "ad_ctr": (60, 226), "ad_impressions": (234, 226), "orders": (530, 226), "aov": (704, 226),
    "ad_load": (150, 296), "sessions": (382, 296), "conversion_rate": (620, 296),
    "views_per_session": (16, 366), "add_to_cart_rate": (188, 366), "checkout_rate": (360, 366),
    "payment_attempt_rate": (532, 366), "payment_success_rate": (704, 366), "order_rate": (876, 366),
}
CAUSE_Y = 456
CAUSES = {"search_ctr": 16, "feed_ctr": 188, "stockouts": 396}
OPS = {"term+": "+", "term-": "−", "factor": "×", "numerator": "÷", "denominator": "÷"}
SIGN = {1: "+", -1: "−", 0: "?"}


def node(x, y, label, cls="n", w=W, text_cls="nt"):
    return (f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{H}" rx="5"/>'
            f'<text class="{text_cls}" x="{x + w / 2}" y="{y + 19.5}" text-anchor="middle">{esc(label)}</text>')


def fig_graph():
    s = [defs("g-")]
    formula_edges = [e for e in g.edges if e.kind == "formula"]
    missing = {m for e in formula_edges for m in (e.src, e.dst)} - set(POS)
    assert not missing, f"no position for {missing}"
    parents = {}
    for e in formula_edges:
        parents.setdefault(e.dst, []).append(e.src)
    shared = {m for m, ps in parents.items() if len(ps) > 1}

    # formula edges first, so boxes are drawn over their ends
    for e in formula_edges:
        px, py = POS[e.src]
        cx, cy = POS[e.dst]
        ps = sorted(parents[e.dst], key=lambda p: POS[p][0])
        off = (ps.index(e.src) - (len(ps) - 1) / 2) * 48
        x1, y1, x2, y2 = px + W / 2, py + H, cx + W / 2 + off, cy
        s.append(f'<path class="e-f" d="M{x1},{y1} C{x1},{y1 + 22} {x2},{y2 - 22} {x2},{y2}"/>')
        s.append(f'<text class="op" x="{x2 + 5}" y="{y2 - 5}">{OPS.get(e.attrs.get("role", ""), "")}</text>')

    for m, (x, y) in POS.items():
        s.append(node(x, y, m, "n-shared" if m in shared else ("n-root" if g.specs[m].outcome else "n")))

    # possible causes
    for m, x in CAUSES.items():
        s.append(node(x, CAUSE_Y, m, "n-cause"))
    s.append(node(560, CAUSE_Y, "discount_rate", "n-ref"))
    s.append(f'<text class="tm" x="{560 + W / 2}" y="{CAUSE_Y + 44}" text-anchor="middle">same node as in the tree</text>')

    def bottom(metric, dx):
        x, y = POS[metric]
        return x + W / 2 + dx, y + H + 3

    def cause_edge(x1, y1, target, dx, sign):
        x2, y2 = bottom(target, dx)
        s.append(f'<line class="e-m" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" marker-end="url(#g-am)"/>')
        lx, ly = x1 + (x2 - x1) * 0.3, y1 + (y2 - y1) * 0.3
        s.append(f'<text class="sg" x="{lx + 6}" y="{ly + 4}">{SIGN[sign]}</text>')

    signs = {(e.src, e.dst): e.attrs.get("sign", 0) for e in g.edges if e.kind == "may_affect"}
    cause_edge(80, CAUSE_Y, "views_per_session", -15, signs[("search_ctr", "views_per_session")])
    cause_edge(215, CAUSE_Y, "views_per_session", 30, signs[("feed_ctr", "views_per_session")])
    cause_edge(290, CAUSE_Y, "add_to_cart_rate", -25, signs[("feed_ctr", "add_to_cart_rate")])
    cause_edge(460, CAUSE_Y, "add_to_cart_rate", 15, signs[("stockouts", "add_to_cart_rate")])
    cause_edge(640, CAUSE_Y, "add_to_cart_rate", 50, signs[("discount_rate", "add_to_cart_rate")])
    s.append(f'<line class="e-m" x1="396" y1="{CAUSE_Y + 15}" x2="349" y2="{CAUSE_Y + 15}" marker-end="url(#g-am)"/>')
    s.append(f'<text class="sg" x="368" y="{CAUSE_Y + 9}">{SIGN[signs[("stockouts", "feed_ctr")]]}</text>')
    assert len(signs) == 6, signs

    # context examples
    s.append(node(728, CAUSE_Y, "release: payments-sdk", "n-ctx", w=160, text_cls="nt nt-s"))
    s.append(node(898, CAUSE_Y, "freshness: payments", "n-ctx", w=140, text_cls="nt nt-s"))
    x2, y2 = bottom("payment_success_rate", 10)
    s.append(f'<line class="e-c" x1="808" y1="{CAUSE_Y}" x2="{x2}" y2="{y2}" marker-end="url(#g-ac)"/>')
    x2, y2 = bottom("payment_success_rate", 55)
    s.append(f'<line class="e-c" x1="968" y1="{CAUSE_Y}" x2="{x2}" y2="{y2}" marker-end="url(#g-ac)"/>')
    s.append(node(406, 226, "calendar", "n-ctx", w=110))
    sx, sy = POS["sessions"]
    s.append(f'<line class="e-c" x1="461" y1="{226 + H}" x2="{sx + W / 2}" y2="{sy - 3}" marker-end="url(#g-ac)"/>')

    # legend
    y = 536
    s.append(f'<line class="e-f" x1="16" y1="{y}" x2="52" y2="{y}"/><text class="tm" x="60" y="{y + 4}">formula: exact identity (+ − × show the role)</text>')
    s.append(f'<rect class="n-shared" x="330" y="{y - 8}" width="26" height="16" rx="4"/><text class="tm" x="364" y="{y + 4}">feeds two parents: the graph is a DAG</text>')
    s.append(f'<line class="e-m" x1="600" y1="{y}" x2="636" y2="{y}" marker-end="url(#g-am)"/><text class="tm" x="646" y="{y + 4}">possible cause, with sign</text>')
    s.append(f'<line class="e-c" x1="820" y1="{y}" x2="856" y2="{y}" marker-end="url(#g-ac)"/><text class="tm" x="866" y="{y + 4}">context (3 of {sum(1 for e in g.edges if e.kind == "context")} shown)</text>')
    label = ("The metric graph as loaded from the YAML files: contribution splits into marketplace fees plus ad spend minus "
             "discounts and further down to the six step rates of the funnel; gmv and sessions feed two parents; possible "
             "causes point at step rates; releases, data freshness and the calendar are context.")
    return f'<svg class="d" viewBox="0 0 1050 556" role="img" aria-label="{esc(label)}">' + "\n".join(s) + "</svg>"


# ------------------------------------------------------------------ figure 3: one tick of the planned loop
def fig_tick():
    steps = [
        ("1 · clock and gate", ["clock from the contract", "an hour is judged only", "when its sources arrived"]),
        ("2 · expected state", ["same hour of the week,", "3 weeks, base columns;", "metrics derived from it"]),
        ("3 · detect", ["step rates and sessions", "in 12 country × platform", "cells; binomial test"]),
        ("4 · attribute", ["walk the formula DAG,", "sum over all paths,", "amounts in EUR"]),
        ("5 · localize", ["score all 479 segments", "for one common shift;", "best must beat second"]),
        ("6 · explain", ["freshness, releases,", "calendar; test cause", "links by their sign"]),
    ]
    s = [defs("t-")]
    for i, (title, lines) in enumerate(steps):
        x = 16 + i * 174
        s.append(card(x, 16, 162, 92, title, lines, cls="box-o", line_cls="ts"))
        if i:
            s.append(arrow(x - 12, 62, x - 2, 62, marker="t-ar"))
    label = ("One tick of the planned loop: clock and completeness gate, expected state, detection on step rates in cells, "
             "attribution over the formula graph, localization over all segments, explanation with context and cause links.")
    return f'<svg class="d" viewBox="0 0 1062 124" role="img" aria-label="{esc(label)}">' + "\n".join(s) + "</svg>"


# ------------------------------------------------------------------ module table
MODULES = [
    ("formula.py", "Safe parser and evaluator for metric formulas; knows whether a formula is a sum, a product or a ratio",
     "parse, Formula.kind, roles, evaluate", "test_formula"),
    ("metric_spec.py", "Metric definitions from YAML, checked field by field", "MetricSpec, CauseLink, load_specs", "test_graph"),
    ("graph.py", "The typed graph: five edge kinds, declared outcome, tree view, JSON export for the UI",
     "MetricGraph, top_outcomes, unplaced_roots, sources", "test_graph, test_plan_fixes"),
    ("signature.py", "Numeric signature of a definition: tells an identity that holds by construction from one the data must confirm",
     "signatures, holds_by_construction", "test_plan_fixes"),
    ("data.py", "Read-only store; SQLite itself refuses any table outside the contract; strict timestamps; NULL never summed as zero",
     "Store.sums, read_sql, time_range", "test_evaluator, test_review_fixes"),
    ("evaluator.py", "Any metric for any segment, window and grouping, always from sums; metrics from an expected table of sums",
     "Evaluator.sums, derive, frame, series", "test_evaluator, test_plan_fixes"),
    ("validate.py", "Checks on load: coverage, identity, SQL against formula, additivity, bounds; each marked data or definition",
     "validate_graph, summarize", "test_validate, test_review_fixes"),
    ("inspect_db.py", "Prints tables, row counts and the time range of a database", "python -m brain.inspect_db", "—"),
]


def module_rows():
    rows = []
    for name, job, api, tests in MODULES:
        n = len((ROOT / "brain" / name).read_text(encoding="utf-8").splitlines())
        rows.append(f"<tr><td><code>{name}</code></td><td class=\"num\">{n}</td><td>{esc(job)}</td>"
                    f"<td><code>{esc(api)}</code></td><td><code>{esc(tests)}</code></td></tr>")
    yaml_lines = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in (ROOT / "brain" / "metrics").glob("*.yaml"))
    rows.append(f"<tr><td><code>metrics/*.yaml</code></td><td class=\"num\">{yaml_lines}</td>"
                f"<td>{len(g.specs)} metric definitions in four files: money, funnel, traffic, ads and inventory</td>"
                f"<td><code>name, formula, decomposition, may_affect, systems, source</code></td><td><code>test_validate</code></td></tr>")
    total = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in (ROOT / "brain").glob("*.py"))
    return "\n".join(rows), total


# ------------------------------------------------------------------ assemble
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--fragment", help="also write the page without the document wrapper to this path")
args = ap.parse_args()

fig1, counts = fig_pipeline()
rows, total_lines = module_rows()
page = (HERE / "brain-architecture.template.html").read_text(encoding="utf-8")
values = {
    "STATE": datetime.now().strftime("%d %b %Y, %H:%M"), "N_TESTS": BRAIN_TESTS,
    "N_DATA_CHECKS": DATA_CHECKS, "N_DEF_CHECKS": DEFINITION_CHECKS,
    "FIG_PIPELINE": fig1, "FIG_GRAPH": fig_graph(), "FIG_TICK": fig_tick(), "MODULE_ROWS": rows,
    "N_METRICS": len(g.specs), "N_EDGES": len(g.edges), "N_LINES": total_lines,
    "N_FORMULA": counts["formula"], "N_DEFINITION": counts["definition"], "N_BREAKDOWN": counts["breakdown"],
    "N_MAY": counts["may_affect"], "N_CONTEXT": counts["context"],
}
for k, v in values.items():
    assert "{{" + k + "}}" in page, k
    page = page.replace("{{" + k + "}}", str(v))
assert "{{" not in page, page[page.index("{{"):][:60]
head, body = page.split('<main class="wrap">', 1)
document = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n' + head
            + '</head>\n<body style="margin:0">\n<main class="wrap">' + body + "</body>\n</html>\n")
(HERE / "brain-architecture.html").write_text(document, encoding="utf-8", newline="\n")
if args.fragment:
    pathlib.Path(args.fragment).write_text(page, encoding="utf-8", newline="\n")
print("wrote", HERE / "brain-architecture.html", len(document), "chars;", dict(counts))

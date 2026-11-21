"""Build docs/system-overview.html: one page that shows how the whole system fits together.

    python docs/build_system_overview.py

The facts on the page (segments, columns, fault kinds, domains, module sizes, test counts) are read from the code, so
the page does not go stale when the code changes: run this script again. The monitor itself (brain/) has its own page,
docs/brain-architecture.html, written by B; this page links to it instead of repeating it.
"""
from __future__ import annotations

import html
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sim import inject, world                      # noqa: E402
from sim.schema import DIMENSIONS, MEASURES, SOURCES   # noqa: E402
from ui.domains import DOMAINS                     # noqa: E402

OUT = ROOT / "docs" / "system-overview.html"
esc = html.escape


# --------------------------------------------------------------------------- facts read from the code
def lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def tests_in(path: Path) -> int:
    return len(re.findall(r"^def test_", path.read_text(encoding="utf-8"), flags=re.M))


def first_line(fn) -> str:
    return (inspect.getdoc(fn) or "").split("\n")[0].strip()


def facts() -> dict:
    countries, platforms = world.COUNTRIES, world.PLATFORMS
    cats, chans = world.CATEGORIES, world.CHANNELS
    segments = len(countries) * len(platforms) * len(cats) * len(chans)
    modules = [
        ("sim/world.py", "One hour of the marketplace for every segment: sessions to orders, money, ads, stockouts; seasonality and noise; faults and promos applied"),
        ("sim/schema.py", "The tables and columns (the contract, printed as docs/contracts/schema.sql)"),
        ("sim/db.py", "SQLite access, write or rewrite hours with every injection on record"),
        ("sim/run.py", "Backfill a clean history (28 days in about 3 s)"),
        ("sim/live.py", "Move the simulated clock forward, one hour per tick"),
        ("sim/inject.py", "Faults, promos, late data, and the rollback; each one is recorded in ground_truth"),
        ("sim/score.py", "Random scenarios and the scoring of what the monitor reported"),
        ("ui/app.py", "The console's API: graph, incidents, state, injection, rollback, data browser"),
        ("ui/domains.py", "Which metric belongs to which business domain"),
        ("ui/static/index.html", "Console: domain selector, graph, incidents, report, DEMO controls"),
        ("ui/static/data.html", "Read-only browser of the simulator tables"),
        ("ui/static/console.css", "One style for both pages, light and dark"),
    ]
    mods = [(name, job, lines(ROOT / name)) for name, job in modules]
    tests = [(p.name, tests_in(p)) for p in sorted((ROOT / "tests").glob("test_*.py"))]
    return {
        "countries": countries, "platforms": platforms, "cats": cats, "chans": chans, "segments": segments,
        "measures": MEASURES, "dims": DIMENSIONS, "sources": SOURCES, "domains": DOMAINS, "modules": mods, "tests": tests,
        "hours": 28 * 24, "rows": 28 * 24 * segments,
        "inject_docs": {n: first_line(getattr(inject, n)) for n in
                        ("payment_failure", "stockout_surge", "search_regression", "ad_budget_exhausted", "promo", "data_delay", "end_injection")},
    }


# the faults: what changes in the data, what is left as context, where a monitor should end up
SCENARIOS = [
    ("payment_failure", "Fault", "Payment success rate falls in one country and platform", "release: payments-sdk",
     "orders, payment success rate, the country and platform, the release"),
    ("stockout_surge", "Fault", "More views land on out-of-stock listings in one category; add-to-cart rate falls, feed clicks rise", "none",
     "orders, add-to-cart rate, the category, stockout rate"),
    ("search_regression", "Fault", "Clicks per search fall in one country, so fewer product views", "release: search",
     "orders, views per session, search clicks per search, the country, the release"),
    ("ad_budget_exhausted", "Fault", "Sponsored impressions per session jump, then collapse; traffic unchanged", "release: pacing",
     "ad spend, ad load (not sessions), the country"),
    ("promo", "Innocent", "Traffic and orders rise in one country", "calendar: promo",
     "explained by the calendar, no alert"),
    ("data_delay", "Innocent", "One source arrives late; the facts stay correct", "ingestion_log only",
     "held as pending, no alert"),
]


# --------------------------------------------------------------------------- the picture
def box(x, y, w, h, title, sub, dashed=False, strong=False):
    cls = "box dashed" if dashed else "box strong" if strong else "box"
    out = [f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>',
           f'<text class="t" x="{x + 12}" y="{y + 22}">{esc(title)}</text>']
    for i, line in enumerate(sub):
        out.append(f'<text class="s" x="{x + 12}" y="{y + 42 + 16 * i}">{esc(line)}</text>')
    return "\n".join(out)


def arrow(x1, y1, x2, y2, label="", dashed=False, lx=None, ly=None):
    cls = "arrow dashed" if dashed else "arrow"
    out = f'<line class="{cls}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" marker-end="url(#ah)"/>'
    if label:
        x = lx if lx is not None else (x1 + x2) / 2
        y = ly if ly is not None else (y1 + y2) / 2 - 6
        for i, line in enumerate(label.split("\n")):
            out += f'<text class="l" x="{x}" y="{y + 13 * i}">{esc(line)}</text>'
    return out


def diagram(f) -> str:
    seg = f["segments"]
    parts = [
        '<svg viewBox="0 0 1000 420" role="img" aria-label="How the parts fit together">',
        '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9 z" class="head"/></marker></defs>',
        # row 1: things that write the database
        box(20, 30, 210, 78, "inject", ["faults, promo, late data,", "rollback (sim/inject.py)"]),
        box(20, 150, 210, 78, "world", [f"{seg} segments x 24 h", "funnel, seasonality, noise"], strong=True),
        box(20, 270, 210, 78, "live clock", ["one simulated hour per tick", "(sim/live.py)"]),
        # the database
        box(310, 150, 230, 130, "database  data/market.db", ["facts_hourly", "ingestion_log   releases   calendar", "ground_truth  (the hidden answer)", "sim_meta"], strong=True),
        # the brain
        box(620, 30, 360, 90, "brain/  (B, own page)", ["solid: graph, evaluator, validation", "planned: monitor, investigator,", "reporter, extension agent"], dashed=True),
        box(620, 160, 170, 60, "incidents/*.json", ["written by the brain"], dashed=True),
        box(620, 270, 360, 78, "console  ui/", ["domains, graph, incident report, data browser", "DEMO controls (inject, rollback)"], strong=True),
        box(310, 320, 230, 78, "score  sim/score.py", ["compares incidents with", "ground_truth, writes results.md"]),
        # arrows
        arrow(125, 108, 125, 150, "writes truth", lx=135, ly=134),
        arrow(230, 74, 330, 150),
        arrow(230, 189, 310, 189, "hours"),
        arrow(125, 270, 125, 228, "next hour", lx=135, ly=254),
        arrow(540, 170, 620, 85, "read only,\n4 tables", dashed=True, lx=560, ly=140),
        arrow(705, 120, 705, 160, "", dashed=True),
        arrow(790, 190, 880, 270, "reports", dashed=True, lx=860, ly=226),
        arrow(540, 250, 620, 300, "state, data", lx=555, ly=290),
        arrow(425, 280, 425, 320, "answer", lx=435, ly=306),
        arrow(620, 200, 540, 345, "", dashed=True),
        '</svg>',
    ]
    return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------- the page
def table(headers, rows):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class=\"tw\"><table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table></div>"


def build() -> str:
    f = facts()
    css = (ROOT / "ui" / "static" / "console.css").read_text(encoding="utf-8")
    total_tests = sum(n for _, n in f["tests"])
    dims = ", ".join(f"{d} ({n})" for d, n in zip(f["dims"], (len(f["countries"]), len(f["platforms"]), len(f["cats"]), len(f["chans"]))))
    scen = table(["Name", "Kind", "What changes in the data", "Left as context", "Where a monitor should end up"],
                 [(a, b, c, d, e) for a, b, c, d, e in SCENARIOS])
    mods = table(["Module", "Job", "Lines"], [(n, j, l) for n, j, l in f["modules"]])
    tests = table(["Test file", "Tests"], list(f["tests"]) + [("total", total_tests)])
    domains = table(["Domain", "Metrics"], [(label, ", ".join(ms)) for _, label, ms in f["domains"]])
    page_css = """
    .page { max-width: 1100px; }
    h2 { font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); margin: 48px 0 16px; }
    p { max-width: 72ch; margin: 0 0 16px; }
    .lead { font-size: 16px; max-width: 70ch; }
    .meta { color: var(--muted); font-size: 12px; letter-spacing: .04em; margin-bottom: 8px; }
    .stats { display: flex; flex-wrap: wrap; gap: 8px 32px; margin: 16px 0 0; }
    .stats b { font-size: 20px; font-weight: 700; margin-right: 8px; }
    .tw { background: var(--block); border-radius: 8px; overflow-x: auto; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { padding: 8px 16px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; font-weight: 700; letter-spacing: .04em; }
    .dia { background: var(--block); border-radius: 8px; padding: 16px; overflow-x: auto; }
    svg { width: 100%; min-width: 720px; height: auto; }
    .box { fill: var(--bg); stroke: var(--edge); stroke-width: 1.3; } .box.strong { stroke: var(--ink); stroke-width: 1.6; }
    .box.dashed { stroke-dasharray: 5 4; }
    .t { fill: var(--ink); font: 700 13px var(--ui); } .s { fill: var(--muted); font: 400 12px var(--ui); } .l { fill: var(--muted); font: 400 11px var(--ui); }
    .arrow { stroke: var(--muted); stroke-width: 1.4; } .arrow.dashed { stroke-dasharray: 4 3; } .head { fill: var(--muted); }
    .note { color: var(--muted); font-size: 12px; margin-top: 8px; }
    code { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
    """
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>System overview</title>
<script>var t = new URLSearchParams(location.search).get('theme'); if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;</script>
<style>
{css}
{page_css}
</style>
</head>
<body>
<div class="page">
<header class="top"><div>
  <div class="meta">EYE OF THE SYSTEM · HACKLAB #2 BERLIN · ALMEDIA TRACK</div>
  <h1>How the whole system fits together</h1>
</div></header>
<p class="lead">A simulated marketplace produces hourly numbers. A monitor reads them, notices a change, traces it to a cause and
reports it. A console shows what the monitor found. Around them: a way to put faults into the world, and a script that
scores the monitor against the truth. This page covers the world, the console and the scoring; the monitor has its own page:
<a href="brain-architecture.html">brain-architecture.html</a>.</p>
<div class="stats"><span><b>{f['segments']}</b>segments per hour</span><span><b>{f['rows']:,}</b>rows in 28 days</span>
<span><b>{len(SCENARIOS)}</b>scenarios</span><span><b>{total_tests}</b>test functions</span></div>

<h2>The parts</h2>
<div class="dia">
{diagram(f)}
</div>
<p class="note">Solid boxes exist and are tested. Dashed boxes belong to the monitor (B) and are not finished; until the monitor writes
incidents, the console shows the example incident from <code>docs/contracts/incident_example.json</code>, marked as an example.
The database is read-only for the monitor and the table <code>ground_truth</code> is refused to it.</p>

<h2>What the world contains</h2>
<p>One row per hour and segment. A segment is a combination of {esc(dims)}: {f['segments']} segments.
The purchase funnel is built from counts and rates, so the graph's formulas hold exactly in the data:
sessions, product views, add to cart, checkouts, payment attempts, payment successes, orders, then money.</p>
<p>Columns ({len(f['measures'])}): {esc(', '.join(f['measures']))}. Sources that report data: {esc(', '.join(f['sources']))}.</p>
<p>Daily and weekly rhythm, a slow trend, one shared shock per country and hour, and the noise of small counts.
At the row level 91% of rows have fewer than 5 payment attempts, so a single row can show a rate of 0.0 or 1.0 by chance;
see <a href="data-granularity.md">data-granularity.md</a> for what each level of aggregation can and cannot show.</p>

<h2>Faults and innocent changes</h2>
{scen}
<p class="note">Every injection is written to <code>ground_truth</code> with its full settings, so it can be replayed. A rollback sets the end of
an injection and writes a rollback release. The same fault with the same settings cannot be recorded twice.</p>

<h2>Why one change moves nothing else</h2>
<p>Every value in an hour gets its own uniform random number, drawn up front, and is turned into a count through the inverse
distribution. A change to one segment's rate therefore changes only that segment's values: the random numbers of every other
segment stay where they were. Backfill, live mode and replaying an injection give identical rows wherever the injection does not
reach. The tests compare whole tables row by row to prove it.</p>

<h2>The console</h2>
<p>Top bar: the domain selector. The graph shows the formula tree, coloured by incident status; picking a domain keeps that domain's boxes
and their path to the top. The right side lists the incidents of the domain, then a report, or the details of a clicked metric.
The folded DEMO bar holds the controls that write simulated faults. The Data page browses the tables read-only.</p>
{domains}
<p class="note">The domain table lives in <code>ui/domains.py</code>. A <code>domain</code> field on each metric in <code>brain/metrics</code> would remove the second copy.</p>

<h2>Scoring</h2>
<p><code>python -m sim.score generate</code> builds a database with random faults, promos and late data in separate slots after a clean week.
<code>python -m sim.score score</code> compares the monitor's <code>incidents/*.json</code> with <code>ground_truth</code>:
detection rate, delay, localization, the right cause, false alarms per day, and how many innocent events stayed silent.
An incident counts for a fault only inside the fault's window (up to 6 hours after its end), and for one fault only.</p>

<h2>Modules</h2>
{mods}

<h2>Tests</h2>
{tests}
<p class="note">The tests of <code>brain/</code> are B's. The totals above count every test file in the repository.</p>
<p class="note">Generated by <code>docs/build_system_overview.py</code> from the code. All data is simulated.</p>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {OUT.stat().st_size} bytes")

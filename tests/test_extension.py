"""The extension agent (README 3.3, issue 3, demo scenario S4): a metric arrives as a definition file,
the brain checks it against the data, finds its place in the graph and answers next to the file."""
import importlib
import json
import re
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from brain.data import Store
from brain.extension import Extension
from brain.formula import parse
from brain.graph import MetricGraph
from brain.metric_spec import SpecError, spec_from_dict
from brain.monitor import Config, Monitor, watch_list
from brain.run import main as run_main
from sim.run import backfill

ROOT = Path(__file__).resolve().parents[1]
GRAIN = "ts, country, platform, category, channel"


def page_yaml() -> str:
    """The definition the console's "Add refund_rate" button posts, taken from the page itself."""
    html = (ROOT / "ui/static/index.html").read_text(encoding="utf-8")
    return re.search(r"var y = '([^']+)';", html).group(1).replace("\\n", "\n")


def definition(name: str, value_sql: str | None = None, where: str = "", **fields) -> str:
    lines = [f"name: {name}"] + [f"{k}: {v}" for k, v in fields.items()]
    if value_sql:
        lines += ["sql: >", f"  SELECT {GRAIN}, {value_sql} AS value", f"  FROM facts_hourly {where} GROUP BY {GRAIN}"]
    return "\n".join(lines) + "\n"


def drop(folder: Path, stem: str, text: str, crlf: bool = True) -> Path:
    """Put a definition into the folder the way the console does on Windows: CRLF line ends."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem}.yaml"
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    return path


def look(ext: Extension, times: int = 2) -> list[dict]:
    """A file is taken once it is the same on two looks in a row."""
    out = []
    for _ in range(times):
        out += ext.poll()
    return out


def answer(ext: Extension, stem: str) -> dict:
    return json.loads((ext.incoming / f"{stem}.result.json").read_text(encoding="utf-8"))


def by_check(result: dict) -> dict:
    return {c["check"]: c for c in result["checks"]}


def tree_metrics(tree: dict) -> list[str]:
    return [tree["metric"]] + [m for c in tree["children"] for m in tree_metrics(c)]


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("extension") / "market.db"), 240)   # 10 days, seed 42


@pytest.fixture(scope="module")
def counts(market):
    con = sqlite3.connect(market)
    try:
        rows, with_orders = con.execute("SELECT COUNT(*), SUM(orders > 0) FROM facts_hourly").fetchone()
    finally:
        con.close()
    return {"rows": rows, "with_orders": with_orders}


@pytest.fixture
def ext(market, tmp_path):
    store = Store(market)
    yield Extension(MetricGraph.load(), store, tmp_path / "incoming", graph_out=tmp_path / "graph.json", quiet=True)
    store.conn.close()


# ------------------------------------------------------------------------------- the demo's definition
def test_the_pages_own_definition_with_crlf_is_accepted_and_validated(ext, counts):
    text = page_yaml()
    path = drop(ext.incoming, "refund_rate", text)
    assert b"\r\n" in path.read_bytes() and "refund_rate" in text
    [r] = look(ext)
    assert r == answer(ext, "refund_rate")
    assert r["name"] == "refund_rate" and r["status"] == "validated", r
    assert {"name", "status", "placed", "links_proposed", "checks", "message"} <= set(r)
    assert isinstance(r["links_proposed"], list) and r["links_proposed"]
    assert all(set(c) == {"check", "status", "detail"} for c in r["checks"])
    assert all(c["status"] in ("pass", "skip") for c in r["checks"]), r["checks"]
    for sentence in (r["placed"], r["message"]):
        assert sentence and "\n" not in sentence
    assert r["message"].startswith("refund_rate")
    assert r["formula"] == "refunds / orders" and r["formula_source"] == "given"
    checks = by_check(r)
    # the SQL was compared with the formula on the rows that have a value, and the number is in the answer
    assert checks["definition"]["status"] == "pass" and str(counts["with_orders"]) in checks["definition"]["detail"]
    assert checks["rows"]["status"] == "pass" and str(counts["rows"]) in checks["rows"]["detail"]
    assert checks["rollup"]["status"] == "pass" and re.search(r"\d", checks["rollup"]["detail"])
    assert checks["alias"]["status"] == "pass"
    spec = ext.graph.specs["refund_rate"]
    assert spec.origin == "contributed" and ext.graph.status["refund_rate"] == "validated"


def test_it_finds_its_place_as_a_factor_of_an_existing_metric(ext):
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    identities = [l.get("identity") for l in r["links_proposed"]]
    assert "refunds = orders * refund_rate" in identities
    g = ext.graph
    assert {e.dst for e in g.out_edges("refunds", "formula")} == {"orders", "refund_rate"}
    assert {e.dst for e in g.out_edges("refund_rate", "definition")} == {"refunds", "orders"}
    assert {e.attrs["dimension"] for e in g.out_edges("refund_rate", "breakdown")} == \
        {"country", "platform", "category", "channel"}
    assert "freshness:orders" in {n.id for n in g.context_of("refund_rate")}
    assert "refunds" in r["placed"] and "contribution" in r["placed"]      # it says the outcome tree does not reach it
    assert g.top_outcomes() == ["contribution"]
    assert "sessions" in {c for c, _ in g.formula_children("orders")}      # the core decomposition is untouched


@pytest.mark.parametrize("name, value_sql, kind, names", [
    ("refund_rate", "SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0)", "ratio", ["refunds", "orders"]),
    ("platform_revenue", "SUM(marketplace_fees) + SUM(ad_spend)", "sum", ["ad_spend", "marketplace_fees"]),
    ("failed_payments", "SUM(payment_attempts) - SUM(payment_successes)", "sum", ["payment_attempts", "payment_successes"]),
    ("view_volume", "SUM(sessions) * 1.0 * SUM(product_views)", "product", ["product_views", "sessions"]),
])
def test_a_definition_with_sql_only_gets_its_formula_found(ext, name, value_sql, kind, names):
    drop(ext.incoming, name, definition(name, value_sql, unit="ratio"))
    [r] = look(ext)
    assert r["status"] in ("validated", "tentative"), r
    assert r["formula_source"] == "inferred"
    found = parse(r["formula"])
    assert found.kind == kind and sorted(found.names) == sorted(names)
    if name == "refund_rate":
        assert r["formula"] == "refunds / orders" and r["status"] == "validated"
    if name == "failed_payments":
        assert dict((n, s) for s, n in found.terms()) == {"payment_attempts": 1, "payment_successes": -1}
    detail = by_check(r)["formula"]["detail"]
    assert re.search(r"\d+ candidates", detail), detail
    assert ext.graph.specs[name].formula.text == r["formula"]


def test_an_alias_of_an_existing_metric_is_reported_and_not_added(ext):
    drop(ext.incoming, "basket_value", definition("basket_value", formula="gmv / orders", unit="eur"))
    drop(ext.incoming, "money_per_order", definition("money_per_order", "SUM(gmv) / NULLIF(SUM(orders), 0)"))
    results = {r["name"]: r for r in look(ext)}
    for name in ("basket_value", "money_per_order"):
        r = results[name]
        assert r["alias_of"] == "aov" and r["status"] == "rejected"
        assert "alias" in r["message"] and "aov" in r["message"]
        assert by_check(r)["alias"]["status"] == "fail"
        assert name not in ext.graph.specs


# ------------------------------------------------------------------------------------------- rejections
REJECTED = {
    "bad_name": ("name: Refund Rate\nformula: refunds / orders\n", "bad metric name"),
    "core_name": ("name: orders\nformula: refunds / sessions\n", "already exists"),
    "unknown_input": ("name: return_rate\nformula: returns / orders\n", "unknown metrics"),
    "no_definition": ("name: mystery\ndescription: nobody said how to compute it\n", "needs a formula or a SQL query"),
    "hidden_table": ("name: peek\nsql: SELECT injected_at AS ts, 1.0 AS value FROM ground_truth\n", "contract tables"),
    "broken_sql": ("name: typo_rate\nsql: SELECT ts, SUM(refundz) AS value FROM facts_hourly GROUP BY ts\n", "SQL failed"),
    "wrong_formula": (definition("refund_share", "SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0)",
                                 formula="refunds / sessions"), "disagree"),
    "no_formula": (definition("odd_number", "SUM(refunds * refunds) + 0.5"), "no formula"),
    "wrong_grain": ("name: by_country\nformula: refunds / orders\ndimensions: [country, platform]\n"
                    "sql: SELECT ts, country, SUM(refunds) * 1.0 / SUM(orders) AS value FROM facts_hourly "
                    "GROUP BY ts, country\n", "declares dimensions"),
    "not_a_metric": ("- refunds\n- orders\n", "mapping"),
    "several": ("metrics:\n  - name: one\n    formula: refunds / orders\n", "one metric per file"),
}


def test_each_rejection_has_its_own_message_and_the_loop_goes_on(ext):
    for stem, (text, _) in REJECTED.items():
        drop(ext.incoming, stem, text)
    drop(ext.incoming, "refund_rate", page_yaml())
    results = look(ext)
    assert len(results) == len(REJECTED) + 1
    for stem, (_, fragment) in REJECTED.items():
        r = answer(ext, stem)
        assert r["status"] == "rejected", (stem, r)
        assert fragment in r["message"], (stem, r["message"])
        assert "\n" not in r["message"] and r["placed"] and r["links_proposed"] == []
        assert any(c["status"] == "fail" for c in r["checks"]), (stem, r["checks"])
    messages = [answer(ext, stem)["message"] for stem in REJECTED]
    assert len(set(messages)) == len(messages)
    assert "refundz" in answer(ext, "broken_sql")["message"]
    assert "returns" in answer(ext, "unknown_input")["message"]
    # the bad files did not stop the good one, and none of them reached the graph
    assert answer(ext, "refund_rate")["status"] == "validated"
    contributed = [m for m, s in ext.graph.specs.items() if s.origin != "core"]
    assert contributed == ["refund_rate"]


def test_a_query_that_never_ends_is_stopped_and_rejected(market, tmp_path):
    endless = "FROM facts_hourly a, facts_hourly b, facts_hourly c"         # 43,200 ** 3 rows to walk through
    store = Store(market)
    with pytest.raises(TimeoutError):
        store.read_sql(f"SELECT COUNT(*) AS n {endless}", timeout=0.2)
    assert len(store.read_sql("SELECT ts FROM facts_hourly LIMIT 3", timeout=5)) == 3     # the limit is per query
    with pytest.raises(OverflowError):
        store.read_sql("SELECT ts FROM facts_hourly", max_rows=1000)
    assert len(store.read_sql("SELECT ts FROM facts_hourly LIMIT 7", max_rows=1000)) == 7
    ext = Extension(MetricGraph.load(), store, tmp_path / "incoming", quiet=True, sql_timeout=0.2)
    drop(ext.incoming, "endless", f"name: endless\nsql: SELECT a.ts AS ts, COUNT(*) AS value {endless} GROUP BY a.ts\n")
    [r] = look(ext)
    assert r["status"] == "rejected" and "longer than" in r["message"]
    store.conn.close()


# ------------------------------------------------------------------------ files that are not ready yet
def test_an_empty_or_half_written_file_is_tried_again_never_rejected(ext):
    text = page_yaml().replace("\n", "\r\n")
    path = ext.incoming / "refund_rate.yaml"
    ext.incoming.mkdir(parents=True)
    path.write_bytes(b"")                                   # the console has opened the file and written nothing yet
    assert look(ext, 3) == []
    for cut in (3, text.index("description") + 5, text.rindex("category, channel]")):
        path.write_bytes(text[:cut].encode())               # cut inside a key or inside a list: not a definition yet
        assert look(ext, 3) == []
    assert not (ext.incoming / "refund_rate.result.json").exists()
    # cut at a line end after the first lines of the query: readable YAML with half a query. One look sees it,
    # the next look sees the whole file; the half is never judged
    cut = text.index("SUM(refunds)")
    path.write_bytes(text[:text.rindex("\r\n", 0, cut) + 2].encode())
    assert ext.poll() == []
    path.write_bytes(text.encode())
    results = look(ext, 3)
    assert [r["status"] for r in results] == ["validated"]
    assert "refund_rate" in ext.graph.specs


def test_a_second_identical_click_does_nothing_and_a_changed_file_is_taken_again(ext):
    path = drop(ext.incoming, "refund_rate", page_yaml())
    assert len(look(ext)) == 1
    result_path, graph_path = ext.incoming / "refund_rate.result.json", Path(ext.graph_out)
    before = (result_path.stat().st_mtime_ns, graph_path.stat().st_mtime_ns, result_path.read_bytes())
    path.write_bytes(b"")                                                  # the second click: truncated, ...
    assert ext.poll() == []
    drop(ext.incoming, "refund_rate", page_yaml())                         # ... then the same text again
    assert look(ext, 4) == []
    assert (result_path.stat().st_mtime_ns, graph_path.stat().st_mtime_ns, result_path.read_bytes()) == before

    drop(ext.incoming, "refund_rate", page_yaml().replace("Share of orders refunded", "Refunded orders per order"))
    assert ext.poll() == []
    assert not result_path.exists()            # the old answer is not shown for the new text
    [r] = look(ext)
    assert r["status"] == "validated" and ext.graph.specs["refund_rate"].description == "Refunded orders per order"


def test_a_file_changed_into_a_bad_definition_withdraws_its_metric(ext):
    drop(ext.incoming, "refund_rate", page_yaml())
    look(ext)
    assert "refund_rate" in ext.graph.specs
    drop(ext.incoming, "refund_rate", page_yaml().replace("refunds / orders", "refunds / sessions"))
    [r] = look(ext)
    assert r["status"] == "rejected" and "disagree" in r["message"]
    assert "refund_rate" not in ext.graph.specs and ext.graph.out_edges("refunds", "formula") == []
    exported = json.loads(Path(ext.graph_out).read_text(encoding="utf-8"))
    assert "refund_rate" not in {n["id"] for n in exported["nodes"]}
    assert [t["metric"] for t in exported["trees"]] == ["contribution"]


def test_a_metric_built_on_a_withdrawn_one_is_judged_again(ext):
    drop(ext.incoming, "refund_rate", page_yaml())
    drop(ext.incoming, "refunds_per_view", definition("refunds_per_view", formula="refund_rate * conversion_rate"))
    look(ext, 4)
    assert answer(ext, "refunds_per_view")["status"] == "validated"
    drop(ext.incoming, "refund_rate", "name: refund_rate\ndescription: the formula was taken out\n")
    look(ext, 6)
    assert answer(ext, "refund_rate")["status"] == "rejected"
    r = answer(ext, "refunds_per_view")
    assert r["status"] == "rejected" and "unknown metrics ['refund_rate']" in r["message"]
    assert [m for m, s in ext.graph.specs.items() if s.origin != "core"] == []


def test_an_error_of_ours_is_tried_again_then_answered_and_the_loop_goes_on(ext, monkeypatch):
    calls = []

    def broken(path, raw):
        calls.append(path.name)
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ext, "_judge", broken)
    drop(ext.incoming, "refund_rate", page_yaml())
    assert look(ext, 3) == [] and len(calls) == 2            # the second and the third look tried it
    [r] = look(ext, 1)
    assert r["status"] == "rejected" and "could not be checked" in r["message"] and "database is locked" in r["message"]
    assert look(ext, 3) == [] and len(calls) == 3            # answered: not tried on every poll for ever


def test_two_files_cannot_define_the_same_name(ext):
    drop(ext.incoming, "refund_rate", page_yaml())
    drop(ext.incoming, "zz_copy", page_yaml().replace("Share of orders refunded", "The same name again"))
    look(ext)
    assert answer(ext, "refund_rate")["status"] == "validated"
    r = answer(ext, "zz_copy")
    assert r["status"] == "rejected" and "already exists" in r["message"] and "refund_rate.yaml" in r["message"]


# -------------------------------------------------------------------- what the SQL really defines
def test_a_query_that_returns_part_of_the_rows_is_tentative(ext, counts):
    drop(ext.incoming, "refund_rate_de", definition("refund_rate_de", "SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0)",
                                                    where="WHERE country = 'DE'", formula="refunds / orders"))
    [r] = look(ext)
    rows = by_check(r)["rows"]
    assert r["status"] == "tentative" and rows["status"] == "warn"
    assert str(counts["rows"]) in rows["detail"] and "part" in r["message"]
    assert ext.graph.status["refund_rate_de"] == "tentative"
    # a tentative metric does not change how another metric decomposes: the identity stays a proposal
    assert ext.graph.out_edges("refunds", "formula") == []
    assert [l["status"] for l in r["links_proposed"] if l.get("identity")] == ["proposed"]
    exported = json.loads(Path(ext.graph_out).read_text(encoding="utf-8"))
    assert [n["status"] for n in exported["nodes"] if n["id"] == "refund_rate_de"] == ["tentative"]


def test_an_ambiguous_place_is_said_so(ext):
    drop(ext.incoming, "failed_payments", definition("failed_payments", formula="payment_attempts - payment_successes",
                                                     unit="count"))
    [r] = look(ext)
    assert r["status"] == "tentative" and "ambiguous" in r["message"] and "ambiguous" in r["placed"]
    identities = {l["identity"] for l in r["links_proposed"] if l.get("identity")}
    assert {"payment_attempts = payment_successes + failed_payments",
            "payment_successes = payment_attempts - failed_payments"} <= identities
    # neither is applied: the two core metrics keep no decomposition
    assert ext.graph.out_edges("payment_attempts", "formula") == []
    assert ext.graph.out_edges("payment_successes", "formula") == []
    assert by_check(r)["placement"]["status"] == "warn"


def test_a_place_inside_the_outcome_tree_is_proposed_not_applied(ext):
    """sessions is a leaf of the outcome tree: giving it a decomposition would change how the outcome is explained."""
    drop(ext.incoming, "sessions_per_search", definition("sessions_per_search", formula="sessions / searches"))
    [r] = look(ext)
    assert r["status"] == "validated"
    [link] = [l for l in r["links_proposed"] if l.get("identity") == "sessions = searches * sessions_per_search"]
    assert link["status"] == "proposed"
    assert ext.graph.out_edges("sessions", "formula") == []
    assert "proposed" in r["placed"]
    # it is still drawn: a tree of its own under the outcome's, with what it is defined from
    exported = json.loads(Path(ext.graph_out).read_text(encoding="utf-8"))
    assert [t["metric"] for t in exported["trees"]] == ["contribution", "sessions_per_search"]
    assert [(c["metric"], c["role"], c["repeated"]) for c in exported["trees"][1]["children"]] == \
        [("sessions", "numerator", True), ("searches", "denominator", False)]
    assert exported["trees"][0] == MetricGraph.load().to_dict()["trees"][0]      # the outcome's tree is as it was


# -------------------------------------------------------------------------------------- the export
def test_the_export_keeps_the_outcome_first_and_carries_the_new_node(ext):
    drop(ext.incoming, "refund_rate", page_yaml())
    look(ext)
    path = Path(ext.graph_out)
    exported = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda name: pytest.fail(name))
    assert exported["top_outcomes"] == ["contribution"]
    assert exported["trees"][0]["metric"] == "contribution" and len(exported["trees"]) == 2
    assert "refund_rate" not in tree_metrics(exported["trees"][0])
    second = exported["trees"][1]
    assert "refund_rate" in tree_metrics(second)
    assert len(tree_metrics(second)) <= 4            # what the outcome tree already shows is not drawn again
    [node] = [n for n in exported["nodes"] if n["id"] == "refund_rate"]
    assert node["status"] == "validated" and node["origin"] == "contributed" and node["kind"] == "metric"
    assert node["formula"] == "refunds / orders" and node["domain"] is None
    core = [n for n in exported["nodes"] if n["kind"] == "metric" and n["id"] != "refund_rate"]
    assert core and all(n["origin"] == "core" and n["status"] == "validated" for n in core)
    ids = {n["id"] for n in exported["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in exported["edges"])
    assert {e["target"] for e in exported["edges"] if e["source"] == "refund_rate" and e["kind"] == "breakdown"} == \
        {f"refund_rate@{d}" for d in exported["dimensions"]}
    assert not list(path.parent.glob("*.tmp")) and not list(ext.incoming.glob("*.tmp"))
    assert b"\r" not in path.read_bytes()


def test_a_rejected_definition_does_not_rewrite_the_export(ext):
    drop(ext.incoming, "bad", "name: orders\nformula: refunds / sessions\n")
    look(ext)
    assert answer(ext, "bad")["status"] == "rejected"
    assert not Path(ext.graph_out).exists()


def test_a_domain_given_with_a_metric_reaches_its_node(ext, graph):
    assert spec_from_dict({"name": "a", "column": "a", "domain": "orders"}).domain == "orders"
    with pytest.raises(SpecError, match="domain"):
        spec_from_dict({"name": "a", "column": "a", "domain": ["orders"]})
    core = [n for n in graph.to_dict()["nodes"] if n["kind"] == "metric"]
    assert core and all(n["domain"] is None for n in core)             # not filled in for the core metrics
    drop(ext.incoming, "refund_rate", page_yaml() + "domain: orders\n")
    [r] = look(ext)
    assert r["status"] == "validated"
    exported = json.loads(Path(ext.graph_out).read_text(encoding="utf-8"))
    assert [n["domain"] for n in exported["nodes"] if n["id"] == "refund_rate"] == ["orders"]


# ------------------------------------------------------------------------------------- the console
@pytest.fixture
def console(market, tmp_path, monkeypatch):
    """The console the way a second process sees it: its folders come from the environment when it is imported."""
    from fastapi.testclient import TestClient
    for key, value in (("MARKET_DB", market), ("INCOMING_METRICS_DIR", tmp_path / "incoming"),
                       ("GRAPH_JSON", tmp_path / "graph.json"), ("INCIDENTS_DIR", tmp_path / "incidents")):
        monkeypatch.setenv(key, str(value))
    import ui.app
    module = importlib.reload(ui.app)
    try:
        yield TestClient(module.app)
    finally:
        monkeypatch.undo()
        importlib.reload(ui.app)          # other tests get the console with its default folders back


def test_the_console_returns_the_result_file_and_the_new_graph(console, market, tmp_path):
    assert console.get("/api/metric/refund_rate").status_code == 404
    posted = console.post("/api/metric", json={"yaml_text": page_yaml()})
    assert posted.status_code == 200 and posted.json()["name"] == "refund_rate"
    store = Store(market)
    ext = Extension(MetricGraph.load(), store, tmp_path / "incoming", graph_out=tmp_path / "graph.json", quiet=True)
    [r] = look(ext)
    store.conn.close()
    got = console.get("/api/metric/refund_rate")
    assert got.status_code == 200 and got.json() == r == answer(ext, "refund_rate")
    assert got.json()["status"] == "validated" and got.json()["message"]
    g = console.get("/api/graph").json()
    assert g["trees"][0]["metric"] == "contribution"
    assert [(n["status"], n["origin"]) for n in g["nodes"] if n["id"] == "refund_rate"] == [("validated", "contributed")]


# ------------------------------------------------------------------------------ the monitor's loop
def test_fresh_removes_old_answers_and_keeps_the_definitions(market, tmp_path):
    incoming = tmp_path / "incoming"
    drop(incoming, "refund_rate", page_yaml())
    (incoming / "refund_rate.result.json").write_text('{"status": "validated", "message": "from the last run"}')
    (incoming / "gone.result.json").write_text('{"status": "rejected", "message": "from the last run"}')
    store = Store(market)
    Extension(MetricGraph.load(), store, incoming, quiet=True)                       # not fresh: nothing is removed
    assert (incoming / "gone.result.json").exists()
    Extension(MetricGraph.load(), store, incoming, quiet=True, fresh=True)
    store.conn.close()
    assert sorted(p.name for p in incoming.iterdir()) == ["refund_rate.yaml"]


def test_live_mode_looks_at_the_incoming_folder_on_every_poll(market, tmp_path):
    incoming, graph_out = tmp_path / "incoming", tmp_path / "graph.json"
    drop(incoming, "refund_rate", page_yaml())
    (incoming / "gone.result.json").write_text('{"status": "rejected", "message": "from the last run"}')
    code = run_main(["--db", str(market), "--incidents", str(tmp_path / "incidents"), "--incoming", str(incoming),
                     "--graph-out", str(graph_out), "--fresh", "--max-ticks", "2", "--poll", "0.01", "--quiet"])
    assert code == 0
    assert not (incoming / "gone.result.json").exists() and (incoming / "refund_rate.yaml").exists()
    r = json.loads((incoming / "refund_rate.result.json").read_text(encoding="utf-8"))
    assert r["status"] == "validated"
    exported = json.loads(graph_out.read_text(encoding="utf-8"))
    assert "refund_rate" in {n["id"] for n in exported["nodes"]} and exported["trees"][0]["metric"] == "contribution"


def test_replay_does_not_touch_the_incoming_folder(market, tmp_path, monkeypatch):
    incoming = tmp_path / "incoming"
    drop(incoming, "refund_rate", page_yaml())
    monkeypatch.setenv("INCOMING_METRICS_DIR", str(incoming))
    assert run_main(["--replay", "--db", str(market), "--incidents", str(tmp_path / "incidents"), "--fresh", "--quiet",
                     "--from", "2026-11-03T00:00:00Z", "--to", "2026-11-03T02:00:00Z"]) == 0
    assert sorted(p.name for p in incoming.iterdir()) == ["refund_rate.yaml"]


class RewatchingMonitor(Monitor):
    """brain/monitor.py plus the change the extension needs from it (see the hand-over report): take up the rates
    that joined the graph after the start. Kept here until that change is in the monitor itself."""

    def rewatch(self) -> list[str]:
        known = {w.metric for w in self.watched}
        self.watched = watch_list(self.graph)
        self.sources = sorted({s for w in self.watched for s in w.sources})
        cubes = list(self.cubes.values()) + ([self.leaf] if hasattr(self, "leaf") else [])
        missing = sorted({c for w in self.watched for c in (w.numerator, w.denominator)} - set(cubes[0].columns))
        for cube in cubes:
            for c in missing:
                cube.columns.append(c)
                cube.data[c] = np.full((cube.rows, len(cube.keys)), np.nan)
        if missing:
            self._loaded_until = None      # the new columns have no history yet: the next tick loads the lookback
        return [w.metric for w in self.watched if w.metric not in known]


def test_an_accepted_rate_is_ready_for_the_monitor_and_the_answer_says_whether_it_is_watched(market, tmp_path):
    store, graph = Store(market), MetricGraph.load()
    monitor = Monitor(graph, store, Config())
    ext = Extension(graph, store, tmp_path / "incoming", monitor=monitor, quiet=True)
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    [w] = [w for w in watch_list(graph) if w.metric == "refund_rate"]         # the graph the monitor holds has it
    assert (w.numerator, w.denominator, w.sources, w.counts) == ("refunds", "orders", ("orders",), True)
    assert "registered, not watched yet" in r["message"]                      # this monitor fixes its list at start
    assert by_check(r)["monitoring"]["status"] == "skip"
    assert "refund_rate" not in {w.metric for w in monitor.watched}
    store.conn.close()


def test_with_the_proposed_monitor_change_the_new_rate_is_judged_from_the_next_tick(market, tmp_path):
    store, graph = Store(market), MetricGraph.load()
    monitor = RewatchingMonitor(graph, store, Config())
    judged_before = monitor.tick().judged_series
    ext = Extension(graph, store, tmp_path / "incoming", monitor=monitor, quiet=True)
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    assert "watched from the next tick" in r["message"] and "not watched" not in r["message"]
    assert by_check(r)["monitoring"]["status"] == "pass"
    tick = monitor.tick()
    assert tick.judged_series > judged_before
    assert monitor.expected_rate("refund_rate", {}) == pytest.approx(0.03, abs=0.01)
    assert monitor.expected_rate("payment_success_rate", {}) is not None      # the rates it had are still judged
    assert not [a for a in tick.alerts if a.metric == "refund_rate"]           # clean data: nothing to report
    store.conn.close()

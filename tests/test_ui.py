"""Tests for the showcase UI's API."""
import json

import pytest
from fastapi.testclient import TestClient

from sim.run import backfill
import ui.app as ui

START = "2026-10-25T00:00:00Z"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = backfill(str(tmp_path / "market.db"), 48, START, seed=1)
    monkeypatch.setattr(ui, "MARKET_DB", db_path)
    monkeypatch.setattr(ui, "INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(ui, "INCOMING_DIR", tmp_path / "incoming")
    return TestClient(ui.app)


def test_index_and_graph_are_served(client):
    assert "Eye of the System" in client.get("/").text
    g = client.get("/api/graph").json()
    assert g["nodes"] and g["trees"]


def test_graph_missing_is_a_clear_404(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "GRAPH_JSON", tmp_path / "nope.json")
    r = client.get("/api/graph")
    assert r.status_code == 404 and "graph export not found" in r.json()["detail"]


def test_state_reports_the_clock_and_no_database(client, monkeypatch, tmp_path):
    s = client.get("/api/state").json()
    assert s["ready"] and s["last_hour"] == "2026-10-26T23:00:00Z" and s["injected"] == []
    monkeypatch.setattr(ui, "MARKET_DB", tmp_path / "missing.db")
    assert client.get("/api/state").json()["ready"] is False


def test_incidents_fall_back_to_the_marked_example_then_show_real_ones(client, tmp_path):
    r = client.get("/api/incidents").json()
    assert r["example"] is True and r["incidents"][0]["id"] == "inc-0001"
    d = tmp_path / "incidents"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"id": "inc-A", "last_seen": "2026-10-26T10:00:00Z", "metric": "orders"}))
    (d / "b.json").write_text(json.dumps({"id": "inc-B", "last_seen": "2026-10-26T12:00:00Z", "metric": "gmv"}))
    (d / "half.json").write_text('{"id": "inc-C", "last_se')           # a file being written: skipped, no crash
    r = client.get("/api/incidents").json()
    assert r["example"] is False and [i["id"] for i in r["incidents"]] == ["inc-B", "inc-A"]


def test_inject_works_and_is_recorded(client):
    r = client.post("/api/inject", json={"kind": "payment_failure", "country": "DE", "platform": "Android", "severity": 0.4})
    assert r.status_code == 200 and r.json()["start"] == "2026-10-26T23:00:00Z"      # the latest hour: it shows at once
    kinds = [i["kind"] for i in client.get("/api/state").json()["injected"]]
    assert kinds == ["payment_failure"]
    assert client.post("/api/inject", json={"kind": "promo", "country": "FR", "hours": 6}).status_code == 200
    assert client.post("/api/inject", json={"kind": "data_delay", "source": "payments"}).status_code == 200


@pytest.mark.parametrize("body", [
    {"kind": "payment_failure", "country": "XX", "platform": "Android"},
    {"kind": "payment_failure", "country": "DE", "platform": "Android", "severity": 1.5},
    {"kind": "promo", "country": "XX"},
    {"kind": "data_delay", "source": "nothing"},
    {"kind": "explode"},
])
def test_inject_rejects_bad_input_and_changes_nothing(client, body):
    assert client.post("/api/inject", json=body).status_code == 422
    assert client.get("/api/state").json()["injected"] == []


def test_inject_without_a_database_is_a_409(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "MARKET_DB", tmp_path / "missing.db")
    assert client.post("/api/inject", json={"kind": "promo", "country": "FR"}).status_code == 409


def test_new_metric_is_handed_over_as_a_file(client, tmp_path):
    y = "name: refund_rate\nformula: refunds / orders\nunit: ratio\n"
    r = client.post("/api/metric", json={"yaml_text": y})
    assert r.status_code == 200 and (tmp_path / "incoming" / "refund_rate.yaml").read_text() == y


@pytest.mark.parametrize("text", ["name: [unclosed", "just a string", "description: no name\nformula: a / b", "name: x\n"])
def test_new_metric_rejects_bad_definitions(client, tmp_path, text):
    assert client.post("/api/metric", json={"yaml_text": text}).status_code == 422
    assert not (tmp_path / "incoming").exists()


def test_metric_name_cannot_escape_the_folder(client, tmp_path):
    r = client.post("/api/metric", json={"yaml_text": "name: ../../evil\nformula: a / b\n"})
    assert r.status_code == 200 and r.json()["name"] == "evil"
    assert (tmp_path / "incoming" / "evil.yaml").exists()


def test_clicking_the_same_button_twice_does_not_stack(client):
    body = {"kind": "payment_failure", "country": "DE", "platform": "Web", "severity": 0.4}
    assert client.post("/api/inject", json=body).status_code == 200
    r = client.post("/api/inject", json=body)
    assert r.status_code == 409 and "already scheduled" in r.json()["detail"]
    assert len(client.get("/api/state").json()["injected"]) == 1


def test_the_page_marks_the_controls_as_demo(client):
    html = client.get("/").text
    assert 'class="demo-tag"' in html and "not part of the monitor" in html


# ------------------------------------------------------------- data browser
def test_data_browser_lists_tables_and_pages_through_rows(client):
    tables = {t["name"]: t for t in client.get("/api/db/tables").json()}
    assert tables["facts_hourly"]["rows"] == 48 * 180 and "payment_successes" in tables["facts_hourly"]["columns"]
    r = client.get("/api/db/rows", params={"table": "facts_hourly", "limit": 5}).json()
    assert len(r["rows"]) == 5 and r["total"] == 48 * 180 and r["rows"][0][0] == "2026-10-26T23:00:00Z"   # newest first
    r = client.get("/api/db/rows", params={"table": "facts_hourly", "country": "DE", "platform": "iOS", "category": "fashion",
                                           "channel": "organic", "ts_from": "2026-10-26T20:00:00Z", "limit": 50}).json()
    assert r["total"] == 4 and {row[1] for row in r["rows"]} == {"DE"}


def test_data_browser_refuses_unknown_tables_and_cannot_be_used_for_sql(client):
    assert client.get("/api/db/rows", params={"table": "sqlite_master"}).status_code == 422
    assert client.get("/api/db/rows", params={"table": "facts_hourly; DROP TABLE facts_hourly"}).status_code == 422
    r = client.get("/api/db/rows", params={"table": "facts_hourly", "country": "DE' OR '1'='1"}).json()
    assert r["total"] == 0                                              # a value, never SQL
    assert client.get("/api/db/rows", params={"table": "facts_hourly", "limit": 100000}).json()["rows"].__len__() == 500
    assert "Simulator data" in client.get("/data").text


def test_data_browser_shows_the_answer_table_but_cannot_change_anything(client):
    client.post("/api/inject", json={"kind": "promo", "country": "FR", "hours": 6})
    r = client.get("/api/db/rows", params={"table": "ground_truth"}).json()
    assert [row[1] for row in r["rows"]] == ["promo"]
    assert client.post("/api/db/rows", json={}).status_code == 405


def test_the_three_other_fault_kinds_can_be_injected_and_checked(client):
    ok = [{"kind": "stockout_surge", "category": "electronics"},
          {"kind": "search_regression", "country": "DE"},
          {"kind": "ad_budget_exhausted", "country": "FR", "hours": 12}]
    for body in ok:
        assert client.post("/api/inject", json=body).status_code == 200, body
    assert [i["kind"] for i in client.get("/api/state").json()["injected"]] == [b["kind"] for b in ok]
    for bad in ({"kind": "stockout_surge", "category": "cars"}, {"kind": "search_regression", "country": "XX"},
                {"kind": "ad_budget_exhausted"}):
        assert client.post("/api/inject", json=bad).status_code == 422, bad
    assert client.post("/api/inject", json=ok[1]).status_code == 409           # the same click twice


def test_the_page_offers_the_fault_kinds(client):
    html = client.get("/").text
    for word in ("stockout_surge", "search_regression", "ad_budget_exhausted", "Inject fault"):
        assert word in html


# ------------------------------------------------------------------ domains
def test_domains_list_real_metrics_once_each(client):
    domains = client.get("/api/domains").json()
    graph = client.get("/api/graph").json()
    known = {n["id"] for n in graph["nodes"] if n["kind"] == "metric"}
    listed = [m for d in domains for m in d["metrics"]]
    assert len({d["id"] for d in domains}) == len(domains)
    assert not [m for m in listed if m not in known], "a domain lists a metric the graph does not have"
    assert len(listed) == len(set(listed)), "a metric is in two domains"
    assert {"payments", "ads", "inventory", "search"} <= {d["id"] for d in domains}


def test_the_page_has_the_domain_selector_and_keeps_the_demo_controls_folded(client):
    html = client.get("/").text
    assert 'id="domains"' in html and "Business domain" in html
    assert '<details id="demo-controls">' in html and "open" not in html.split('<details id="demo-controls"')[1][:3]


# ------------------------------------------------------------- visual style
def _tokens(block):
    import re
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9A-Fa-f]{6})", block))


def _contrast(a, b):
    def lum(h):
        h = h.lstrip("#")
        r, g, bl = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(bl)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_console_colours_meet_contrast_in_both_themes():
    css = (ui.Path(ui.__file__).parent / "static" / "console.css").read_text(encoding="utf-8")
    light = _tokens(css.split("@media")[0])
    dark = _tokens(css.split(':root[data-theme="dark"] {')[1].split("}")[0])
    text_pairs = [("ink", "bg"), ("ink", "block"), ("muted", "bg"), ("muted", "block"), ("sel-fg", "sel-bg"),
                  ("hot", "block"), ("hot", "hot-bg"), ("amber", "block"), ("amber", "amber-bg"), ("ok", "block"), ("ok", "ok-bg")]
    for name, t in (("light", light), ("dark", dark)):
        for a, b in text_pairs:
            assert _contrast(t[a], t[b]) >= 4.5, f"{name}: {a} on {b}"
        for a, b in (("accent", "bg"), ("accent", "block"), ("edge", "block")):
            assert _contrast(t[a], t[b]) >= 1.8 if a == "edge" else _contrast(t[a], t[b]) >= 3, f"{name}: {a} on {b}"


def test_style_stays_flat_and_both_pages_share_it(client):
    import re
    css = (ui.Path(ui.__file__).parent / "static" / "console.css").read_text(encoding="utf-8")
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)                  # comments may talk about gradients
    assert "gradient" not in rules and "box-shadow" not in rules and "text-shadow" not in rules
    for page in ("/", "/data"):
        html = client.get(page).text
        assert "/static/console.css" in html and "gradient" not in html and "box-shadow" not in html
        assert "prefers-color-scheme" not in html                      # themes live in one place
    assert client.get("/static/console.css").status_code == 200


def test_title_and_domain_selector_stay_at_the_top_when_scrolling(client):
    html = client.get("/").text
    head, body = html.split("</head>")
    assert ".sticky-top { position: sticky; top: 0;" in head
    block = body.split('<div class="sticky-top">')[1].split("<main>")[0]
    assert 'id="domains"' in block and "Eye of the System" in block          # both sit inside the sticky container
    assert 'id="demo-controls"' not in block.split('id="domains"')[0]


def test_the_graph_card_stays_in_view_next_to_a_long_report(client):
    html = client.get("/").text
    assert 'id="graph-card"' in html and "#graph-card { position: sticky;" in html
    assert "--top-h" in html and "setTop" in html                      # the offset follows the real height of the top bar


# ----------------------------------------------------------------- rollback
def test_a_fault_can_be_ended_from_the_page_and_stops_being_active(client):
    assert client.post("/api/inject", json={"kind": "payment_failure", "country": "DE", "platform": "Android"}).status_code == 200
    injected = client.get("/api/state").json()["injected"]
    assert injected[0]["active"] is True and isinstance(injected[0]["id"], int)
    r = client.post("/api/end", json={"id": injected[0]["id"]})
    assert r.status_code == 200 and r.json()["ended_from"] == "2026-10-26T23:00:00Z"
    assert client.get("/api/state").json()["injected"][0]["active"] is False
    assert client.post("/api/end", json={"id": injected[0]["id"]}).status_code == 409      # already ended
    assert client.post("/api/end", json={"id": 999}).status_code == 404
    # and the same fault can be started again
    assert client.post("/api/inject", json={"kind": "payment_failure", "country": "DE", "platform": "Android"}).status_code == 200


def test_the_page_has_the_end_control_next_to_the_other_demo_controls(client):
    html = client.get("/").text
    assert 'id="end-grp"' in html and "End it (rollback)" in html and "/api/end" in html
    assert html.index('id="end-grp"') < html.index('id="demo-controls"') + 4000        # inside the folded demo bar


def test_static_files_are_not_cached_so_a_changed_style_shows_at_once(client):
    r = client.get("/static/console.css")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert "[hidden] { display: none !important; }" in r.text


def test_pages_link_the_stylesheet_with_its_modification_time(client):
    import re
    for page in ("/", "/data"):
        r = client.get(page)
        assert re.search(r'/static/console\.css\?v=\d+"', r.text), page
        assert r.headers["cache-control"] == "no-cache"


# ------------------------------------------------------- one odd file must not stop the feed (issue 4)
def test_odd_incident_files_are_skipped_and_counted_not_fatal(client, tmp_path):
    d = tmp_path / "incidents"
    d.mkdir()
    good = {"id": "inc-0001", "metric": "orders", "status": "open", "last_seen": "2026-10-26T12:00:00Z"}
    (d / "good.json").write_text(json.dumps(good))
    (d / "later.json").write_text(json.dumps(dict(good, id="inc-0002", last_seen="2026-10-26T15:00:00Z")))
    (d / "null_seen.json").write_text(json.dumps(dict(good, id="inc-0003", last_seen=None)))         # kept, sorted last
    (d / "number_seen.json").write_text(json.dumps(dict(good, id="inc-0004", last_seen=12345)))
    (d / "a_list.json").write_text("[1, 2]")
    (d / "null.json").write_text("null")
    (d / "nan.json").write_text('{"id": "inc-0005", "metric": "orders", "change": {"z": NaN}}')      # json.dumps writes NaN
    (d / "infinity.json").write_text('{"id": "inc-0006", "metric": "orders", "z": Infinity}')
    (d / "state.json").write_text(json.dumps({"tick": 7}))                                              # not an incident
    (d / "number_id.json").write_text(json.dumps({"id": 7, "metric": "orders"}))
    (d / "no_metric.json").write_text(json.dumps({"id": "inc-0007"}))
    (d / "half.json").write_text('{"id": "inc-0008", "metr')
    r = client.get("/api/incidents")
    assert r.status_code == 200
    body = r.json()
    assert body["example"] is False
    assert [i["id"] for i in body["incidents"]][:2] == ["inc-0002", "inc-0001"] and {i["id"] for i in body["incidents"]} == {"inc-0001", "inc-0002", "inc-0003", "inc-0004"}
    assert body["skipped"] == 8


def test_the_example_shows_only_while_the_incident_folder_does_not_exist(client, tmp_path):
    assert client.get("/api/incidents").json()["example"] is True                 # no folder yet: the monitor has not started
    (tmp_path / "incidents").mkdir()
    r = client.get("/api/incidents").json()
    assert r["example"] is False and r["incidents"] == []                          # an empty folder means nothing found


def test_notes_and_other_subfolders_are_ignored(client, tmp_path):
    d = tmp_path / "incidents"
    (d / "notes").mkdir(parents=True)
    (d / "notes" / "inc-0009.json").write_text(json.dumps({"id": "inc-0009", "metric": "orders", "status": "explained"}))
    assert client.get("/api/incidents").json()["incidents"] == []


# ------------------------------------------------------------- the answer to adding a metric (issue 3)
def test_the_result_of_a_new_metric_is_read_from_its_result_file(client, tmp_path):
    assert client.get("/api/metric/refund_rate").status_code == 404                # not answered yet
    client.post("/api/metric", json={"yaml_text": "name: refund_rate\nformula: refunds / orders\n"})
    assert client.get("/api/metric/refund_rate").status_code == 404
    (tmp_path / "incoming" / "refund_rate.result.json").write_text(json.dumps({"status": "validated", "message": "placed under orders"}))
    r = client.get("/api/metric/refund_rate")
    assert r.status_code == 200 and r.json()["message"] == "placed under orders"
    (tmp_path / "incoming" / "half.result.json").write_text('{"status": ')
    assert client.get("/api/metric/half").status_code == 404                       # half written: try again later
    assert client.get("/api/metric/..%2Fsecret").status_code in (404, 422)


def test_the_page_reads_the_typed_links_and_survives_odd_fields(client):
    html = client.get("/").text
    for word in ("may_affect", "Breaks down by", "May be affected by", "Context", "Defined from", "Status", "Origin",
                 "Where in the segments", "/api/metric/", "function str(", "function list("):
        assert word in html, word
    assert "explained" in client.get("/static/console.css").text                   # grey, not counted


def test_page_metric_yaml_is_accepted_by_brain(client, tmp_path):
    import re
    from brain.metric_spec import load_specs
    html = client.get('/').text
    literal = re.search(r"var y = '([^']+)';", html).group(1)
    yaml_text = literal.replace('\\n', '\n')
    response = client.post('/api/metric', json={'yaml_text': yaml_text})
    assert response.status_code == 200
    specs = load_specs(tmp_path / 'incoming' / 'refund_rate.yaml')
    assert len(specs) == 1 and specs[0].name == 'refund_rate'

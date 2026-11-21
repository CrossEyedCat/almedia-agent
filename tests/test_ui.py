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
    assert r.status_code == 200 and r.json()["start"] == "2026-10-27T00:00:00Z"
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

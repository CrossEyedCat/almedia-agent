"""Showcase UI: the metric graph, the incident feed, and buttons that inject faults into the simulator.

    uvicorn ui.app:app --port 8000        then open http://127.0.0.1:8000

Reads: the graph export (docs/contracts/graph.json), incidents/*.json written by brain/, and the simulator
database. It never writes to brain/. The buttons call the same functions as `python -m sim.inject`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sim import db, inject
from sim.world import CHANNELS, COUNTRIES, PLATFORMS
from sim.schema import SOURCES

ROOT = Path(__file__).resolve().parent.parent
GRAPH_JSON = Path(os.environ.get("GRAPH_JSON", ROOT / "docs/contracts/graph.json"))
INCIDENTS_DIR = Path(os.environ.get("INCIDENTS_DIR", ROOT / "incidents"))
INCOMING_DIR = Path(os.environ.get("INCOMING_METRICS_DIR", ROOT / "incoming_metrics"))
EXAMPLE_INCIDENT = ROOT / "docs/contracts/incident_example.json"
MARKET_DB = Path(os.environ.get("MARKET_DB", ROOT / "data/market.db"))

app = FastAPI(title="Eye of the System")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/graph")
def graph():
    if not GRAPH_JSON.exists():
        raise HTTPException(404, f"graph export not found: {GRAPH_JSON} (python -m brain.graph --export PATH)")
    return json.loads(GRAPH_JSON.read_text(encoding="utf-8"))


def _incidents() -> tuple[list[dict], bool]:
    items = []
    if INCIDENTS_DIR.exists():
        for p in sorted(INCIDENTS_DIR.glob("*.json")):
            try:
                items.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue  # a file that is being written right now
    if items:
        return sorted(items, key=lambda i: i.get("last_seen", ""), reverse=True), False
    if EXAMPLE_INCIDENT.exists():  # nothing detected yet: show the contract example, clearly marked
        return [json.loads(EXAMPLE_INCIDENT.read_text(encoding="utf-8"))], True
    return [], False


@app.get("/api/incidents")
def incidents():
    items, is_example = _incidents()
    return {"example": is_example, "incidents": items}


@app.get("/api/state")
def state():
    if not MARKET_DB.exists():
        return {"ready": False, "error": f"no database at {MARKET_DB}; run python -m sim.run --backfill 28d"}
    con = db.connect(MARKET_DB)
    try:
        last = db.last_hour(con)
        meta = db.read_meta(con)
        injected = [{"kind": k, "at": at, "scope": json.loads(sc), "description": d}
                    for at, k, sc, d in con.execute("SELECT injected_at, kind, scope, description FROM ground_truth ORDER BY injected_at")]
        return {"ready": True, "last_hour": last.strftime("%Y-%m-%dT%H:00:00Z"), "start": meta["start"].strftime("%Y-%m-%dT%H:00:00Z"),
                "countries": list(meta["countries"]), "injected": injected}
    finally:
        con.close()


class Inject(BaseModel):
    kind: str
    country: str | None = None
    platform: str | None = None
    source: str | None = None
    severity: float = 0.35
    hours: int = 12
    delay_hours: float = 2
    when: str = "now"  # now = from the next simulated hour; past = 6 hours before the end of the data


@app.post("/api/inject")
def do_inject(req: Inject):
    if not MARKET_DB.exists():
        raise HTTPException(409, "no database yet")
    con = db.connect(MARKET_DB)
    try:
        last = db.last_hour(con)
        start = last + pd.Timedelta(hours=1) if req.when == "now" else last - pd.Timedelta(hours=5)
        end = start + pd.Timedelta(hours=req.hours)
        if req.kind == "payment_failure":
            if req.country not in COUNTRIES or req.platform not in PLATFORMS:
                raise HTTPException(422, "country and platform must be valid")
            if not 0 < req.severity < 1:
                raise HTTPException(422, "severity must be between 0 and 1")
            n = inject.payment_failure(con, req.country, req.platform, start, req.severity)
        elif req.kind == "promo":
            if req.country not in COUNTRIES:
                raise HTTPException(422, "country must be valid")
            n = inject.promo(con, req.country, start, end)
        elif req.kind == "data_delay":
            if req.source not in SOURCES:
                raise HTTPException(422, f"source must be one of {SOURCES}")
            n = inject.data_delay(con, req.source, start, end, req.delay_hours)
        else:
            raise HTTPException(422, "unknown kind")
        return {"ok": True, "kind": req.kind, "start": start.strftime("%Y-%m-%dT%H:00:00Z"), "replayed_hours": n}
    finally:
        con.close()


class NewMetric(BaseModel):
    yaml_text: str


@app.post("/api/metric")
def new_metric(req: NewMetric):
    """Hand a new metric definition to the brain: saved as a file that the extension agent picks up."""
    try:
        spec = yaml.safe_load(req.yaml_text)
    except yaml.YAMLError as exc:
        raise HTTPException(422, f"not valid YAML: {exc}")
    if not isinstance(spec, dict) or not spec.get("name") or not (spec.get("sql") or spec.get("formula")):
        raise HTTPException(422, "a metric needs a name and a sql or a formula")
    name = "".join(ch for ch in str(spec["name"]) if ch.isalnum() or ch == "_")
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    path = INCOMING_DIR / f"{name}.yaml"
    path.write_text(req.yaml_text, encoding="utf-8")
    try:
        saved = path.relative_to(ROOT).as_posix()
    except ValueError:                      # the folder was configured outside the repository
        saved = path.as_posix()
    return {"ok": True, "saved": saved, "name": name}

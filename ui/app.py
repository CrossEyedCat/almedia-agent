"""Showcase UI: the metric graph, the incident feed, and buttons that inject faults into the simulator.

    uvicorn ui.app:app --port 8000        then open http://127.0.0.1:8000

Reads: the graph export (docs/contracts/graph.json), incidents/*.json written by brain/, and the simulator
database. It never writes to brain/. The buttons call the same functions as `python -m sim.inject`.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sim import db, inject
from ui import domains as domain_map
from sim.inject import AlreadyEnded, AlreadyInjected, NoSuchInjection
from sim.world import CATEGORIES, CHANNELS, COUNTRIES, PLATFORMS
from sim.schema import SOURCES

ROOT = Path(__file__).resolve().parent.parent
GRAPH_JSON = Path(os.environ.get("GRAPH_JSON", ROOT / "docs/contracts/graph.json"))
INCIDENTS_DIR = Path(os.environ.get("INCIDENTS_DIR", ROOT / "incidents"))
INCOMING_DIR = Path(os.environ.get("INCOMING_METRICS_DIR", ROOT / "incoming_metrics"))
EXAMPLE_INCIDENT = ROOT / "docs/contracts/incident_example.json"
MARKET_DB = Path(os.environ.get("MARKET_DB", ROOT / "data/market.db"))

app = FastAPI(title="Eye of the System")
class NoCacheStatic(StaticFiles):
    """The console is a local demo: a changed style or script must show up on the next reload."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", NoCacheStatic(directory=Path(__file__).parent / "static"), name="static")


STATIC = Path(__file__).parent / "static"


def _page(name: str) -> HTMLResponse:
    """A page whose stylesheet link carries the file's modification time, so a changed style is fetched again
    even by a browser that cached the old one."""
    html = (STATIC / name).read_text(encoding="utf-8")
    version = int((STATIC / "console.css").stat().st_mtime)
    return HTMLResponse(html.replace("/static/console.css", f"/static/console.css?v={version}"),
                        headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return _page("index.html")


@app.get("/api/domains")
def domains():
    return domain_map.as_json()


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
        injected = [{"id": i["id"], "kind": i["kind"], "at": con.execute("SELECT injected_at FROM ground_truth WHERE rowid = ?", (i["id"],)).fetchone()[0],
                     "scope": i["scope"], "description": i["description"], "active": i["active"]}
                    for i in inject.list_injections(con, last + pd.Timedelta(hours=1))]
        return {"ready": True, "last_hour": last.strftime("%Y-%m-%dT%H:00:00Z"), "start": meta["start"].strftime("%Y-%m-%dT%H:00:00Z"),
                "countries": list(meta["countries"]), "injected": injected}
    finally:
        con.close()


class Inject(BaseModel):
    kind: str
    country: str | None = None
    platform: str | None = None
    category: str | None = None
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
        return _inject(con, req)
    except AlreadyInjected as exc:
        raise HTTPException(409, str(exc))
    finally:
        con.close()


def _inject(con, req: "Inject"):
    last = db.last_hour(con)
    start = last + pd.Timedelta(hours=1) if req.when == "now" else last - pd.Timedelta(hours=5)
    end = start + pd.Timedelta(hours=req.hours)
    if req.kind == "payment_failure":
        if req.country not in COUNTRIES or req.platform not in PLATFORMS:
            raise HTTPException(422, "country and platform must be valid")
        if not 0 < req.severity < 1:
            raise HTTPException(422, "severity must be between 0 and 1")
        n = inject.payment_failure(con, req.country, req.platform, start, req.severity)
    elif req.kind == "stockout_surge":
        if req.category not in CATEGORIES:
            raise HTTPException(422, f"category must be one of {CATEGORIES}")
        n = inject.stockout_surge(con, req.category, start, None, country=req.country if req.country in COUNTRIES else None)
    elif req.kind == "search_regression":
        if req.country not in COUNTRIES:
            raise HTTPException(422, "country must be valid")
        n = inject.search_regression(con, req.country, start, 0.5)
    elif req.kind == "ad_budget_exhausted":
        if req.country not in COUNTRIES:
            raise HTTPException(422, "country must be valid")
        n = inject.ad_budget_exhausted(con, req.country, start, end, boost_hours=max(1, req.hours // 3))
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


class End(BaseModel):
    id: int


@app.post("/api/end")
def end_fault(req: End):
    """Demo: someone rolls the change back. The injection stops from the next simulated hour."""
    if not MARKET_DB.exists():
        raise HTTPException(409, "no database yet")
    con = db.connect(MARKET_DB)
    try:
        at = db.last_hour(con) + pd.Timedelta(hours=1)
        inject.end_injection(con, req.id, at)
        return {"ok": True, "id": req.id, "ended_from": at.strftime("%Y-%m-%dT%H:00:00Z")}
    except NoSuchInjection as exc:
        raise HTTPException(404, str(exc))
    except AlreadyEnded as exc:
        raise HTTPException(409, str(exc))
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

# ---------------------------------------------------------------- data browser (read only)
BROWSABLE = ("facts_hourly", "ingestion_log", "releases", "calendar", "ground_truth", "sim_meta")
FILTERABLE = ("country", "platform", "category", "channel", "source", "kind")


def _ro() -> sqlite3.Connection:
    if not MARKET_DB.exists():
        raise HTTPException(409, "no database yet")
    return sqlite3.connect(f"file:{Path(MARKET_DB).resolve().as_posix()}?mode=ro", uri=True)


@app.get("/data")
def data_page():
    return _page("data.html")


@app.get("/api/db/tables")
def db_tables():
    con = _ro()
    try:
        out = []
        for t in BROWSABLE:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
            if cols:
                out.append({"name": t, "columns": cols, "rows": con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]})
        return out
    finally:
        con.close()


@app.get("/api/db/rows")
def db_rows(table: str, limit: int = 50, offset: int = 0, newest_first: bool = True, ts_from: str | None = None,
            ts_to: str | None = None, country: str | None = None, platform: str | None = None,
            category: str | None = None, channel: str | None = None, source: str | None = None, kind: str | None = None):
    if table not in BROWSABLE:
        raise HTTPException(422, f"table must be one of {BROWSABLE}")
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    con = _ro()
    try:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        where, args = [], []
        for name, value in (("country", country), ("platform", platform), ("category", category),
                            ("channel", channel), ("source", source), ("kind", kind)):
            if value and name in cols:
                where.append(f"{name} = ?")
                args.append(value)
        time_col = "ts" if "ts" in cols else "start_ts" if "start_ts" in cols else "injected_at" if "injected_at" in cols else None
        if time_col and ts_from:
            where.append(f"{time_col} >= ?")
            args.append(ts_from)
        if time_col and ts_to:
            where.append(f"{time_col} < ?")
            args.append(ts_to)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        order = f" ORDER BY {time_col} {'DESC' if newest_first else 'ASC'}" if time_col else ""
        total = con.execute(f"SELECT COUNT(*) FROM {table}{clause}", args).fetchone()[0]
        rows = con.execute(f"SELECT * FROM {table}{clause}{order} LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        return {"table": table, "columns": cols, "total": total, "rows": rows}
    finally:
        con.close()

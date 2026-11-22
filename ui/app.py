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
from pydantic import BaseModel, Field

from sim import db, inject
from sim.clock import sync_current_hour, wall_now
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

CLOCK_MODE = os.environ.get("MARKET_CLOCK", "replay")
if CLOCK_MODE not in ("wall", "replay"):
    raise ValueError("MARKET_CLOCK must be wall or replay")


def current_hour(con):
    if CLOCK_MODE == "wall":
        try:
            return sync_current_hour(con, wall_now())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return db.last_hour(con)


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


def _no_constant(name: str):
    raise ValueError(f"{name} is not valid JSON")


def _read_incident(path: Path) -> dict | None:
    """One incident file, or None when it is not an incident: unreadable, half written, NaN or Infinity (Python's
    json.dumps writes them, browsers refuse them), not an object, or without a text id and metric."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=_no_constant)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return None
    # explained and pending records carry no outcome metric; notes/ is still read for older runs.
    if not isinstance(data.get("metric"), str):
        if data.get("status") not in ("pending", "explained"):
            return None
        data["metric"] = "data_arrival" if data["status"] == "pending" else "calendar"
    return data


FEED_STATUS = {"open": 0, "ongoing": 0, "resolved": 1, "explained": 2, "pending": 3}
SEVERITIES = ("high", "medium", "low")


def feed_order(record: dict) -> tuple:
    """The feed's order, the same as brain.incidents.feed_rank: live first, then resolved, explained, pending;
    within a status the severe ones first, then the newest."""
    try:
        newest_first = -pd.Timestamp(record.get("last_seen")).value
    except (TypeError, ValueError):
        newest_first = 0
    sev = record.get("severity")
    return (FEED_STATUS.get(record.get("status"), 4), SEVERITIES.index(sev) if sev in SEVERITIES else len(SEVERITIES),
            newest_first)


def _incidents() -> tuple[list[dict], bool, int]:
    """(incidents, is_example, number of files ignored). The contract example is shown only when the folder does not
    exist; once the monitor has made it, an empty folder means "nothing found". Non-alert status notes are included."""
    if INCIDENTS_DIR.is_dir():
        items, skipped = [], 0
        paths = sorted(INCIDENTS_DIR.glob("*.json"))
        known = {p.stem for p in paths}
        paths += [p for p in sorted((INCIDENTS_DIR / "notes").glob("*.json")) if p.stem not in known]
        for p in paths:
            d = _read_incident(p)
            if d is None:
                skipped += 1
            elif p.parent == INCIDENTS_DIR or d.get("status") in ("explained", "pending"):
                items.append(d)
        return sorted(items, key=feed_order), False, skipped
    if EXAMPLE_INCIDENT.exists():
        return [json.loads(EXAMPLE_INCIDENT.read_text(encoding="utf-8"))], True, 0
    return [], False, 0


@app.get("/api/incidents")
def incidents():
    items, is_example, skipped = _incidents()
    return {"example": is_example, "incidents": items, "skipped": skipped}


@app.get("/api/state")
def state():
    if not MARKET_DB.exists():
        return {"ready": False, "error": f"no database at {MARKET_DB}; run python -m sim.run --backfill 28d"}
    con = db.connect(MARKET_DB)
    try:
        last = current_hour(con)
        meta = db.read_meta(con)
        injected = [{"id": i["id"], "kind": i["kind"], "at": con.execute("SELECT injected_at FROM ground_truth WHERE rowid = ?", (i["id"],)).fetchone()[0],
                     "scope": i["scope"], "description": i["description"], "active": i["active"]}
                    for i in inject.list_injections(con, last + pd.Timedelta(hours=1))]
        return {"ready": True, "last_hour": last.strftime("%Y-%m-%dT%H:00:00Z"), "start": meta["start"].strftime("%Y-%m-%dT%H:00:00Z"),
                "countries": list(meta["countries"]), "injected": injected,
                "clock_mode": CLOCK_MODE, "display_timezone": "Europe/Berlin",
                "server_now": wall_now().isoformat()}
    finally:
        con.close()


class Inject(BaseModel):
    kind: str
    country: str | None = None
    platform: str | None = None
    category: str | None = None
    source: str | None = None
    severity: float | None = None   # payment_failure: 0.35 when not given; traffic_drop: 0.5
    share: float = 0.5              # mix_shift: the favoured category's share of sessions
    lift: float = 0.6               # discount_push: relative rise of the discount per euro
    hours: int = Field(default=12, ge=1)
    delay_hours: float = 2
    when: str = "now"  # now = current data hour; wall mode synchronizes it to the real current hour


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
    last = current_hour(con)
    # "now" is the latest hour of data: the effect shows at once and carries on as the clock moves
    start = last if req.when == "now" else last - pd.Timedelta(hours=5)
    end = start + pd.Timedelta(hours=req.hours)
    if req.kind in ("payment_failure", "traffic_drop"):
        if req.country not in COUNTRIES or req.platform not in PLATFORMS:
            raise HTTPException(422, "country and platform must be valid")
        severity = req.severity if req.severity is not None else (0.35 if req.kind == "payment_failure" else 0.5)
        if not 0 < severity < 1:
            raise HTTPException(422, "severity must be between 0 and 1")
        if req.kind == "payment_failure":
            n = inject.payment_failure(con, req.country, req.platform, start, severity, end=end)
        else:
            n = inject.traffic_drop(con, req.country, req.platform, start, end, severity)
    elif req.kind == "feed_unavailable":
        n = inject.feed_unavailable(con, start, end)
    elif req.kind == "mix_shift":
        if req.category not in CATEGORIES:
            raise HTTPException(422, f"category must be one of {CATEGORIES}")
        if not 0 < req.share < 1:
            raise HTTPException(422, "share must be between 0 and 1")
        n = inject.mix_shift(con, req.category, start, end, req.share)
    elif req.kind == "discount_push":
        if not 0 < req.lift <= 5:
            raise HTTPException(422, "lift must be above 0 and at most 5")
        n = inject.discount_push(con, start, end, req.lift)
    elif req.kind == "stockout_surge":
        if req.category not in CATEGORIES:
            raise HTTPException(422, f"category must be one of {CATEGORIES}")
        n = inject.stockout_surge(con, req.category, start, end, country=req.country if req.country in COUNTRIES else None)
    elif req.kind == "search_regression":
        if req.country not in COUNTRIES:
            raise HTTPException(422, "country must be valid")
        n = inject.search_regression(con, req.country, start, 0.5, end=end)
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
    """Demo: someone rolls the change back. The injection stops from the latest hour, so the effect shows at once."""
    if not MARKET_DB.exists():
        raise HTTPException(409, "no database yet")
    con = db.connect(MARKET_DB)
    try:
        at = current_hour(con)
        inject.end_injection(con, req.id, at)
        return {"ok": True, "id": req.id, "ended_from": at.strftime("%Y-%m-%dT%H:00:00Z")}
    except NoSuchInjection as exc:
        raise HTTPException(404, str(exc))
    except AlreadyEnded as exc:
        raise HTTPException(409, str(exc))
    finally:
        con.close()


@app.get("/api/metric/{name}")
def metric_result(name: str):
    """What the monitor did with a contributed metric: <name>.result.json next to the YAML (404 until it exists)."""
    safe = "".join(ch for ch in name if ch.isalnum() or ch == "_")
    path = INCOMING_DIR / f"{safe}.result.json"
    if not safe or not path.is_file():
        raise HTTPException(404, "no result yet")
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_no_constant)
    except (OSError, ValueError):
        raise HTTPException(404, "the result is not readable yet")


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

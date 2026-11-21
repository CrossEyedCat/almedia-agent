"""Read-only access to the simulator's SQLite database (README §3.1 data contract).

Timestamps are UTC hour starts in the form 2026-11-01T13:00:00Z; windows are [start, end).
Column and dimension names are checked against the schema before they reach SQL.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd

from brain.metric_spec import DIMENSIONS

FACTS = "facts_hourly"
# The only tables the brain may read (guardrail 2: it never sees the scoring table).
READABLE_TABLES = (FACTS, "ingestion_log", "releases", "calendar")
_TABLE_REF = re.compile(r'\b(?:from|join)\s+(?:"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))',
                        re.IGNORECASE)
TS_FORMAT = "%Y-%m-%dT%H:00:00Z"


def iso_hour(ts) -> str:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.floor("h").strftime(TS_FORMAT)


def to_ts(values) -> pd.Series | pd.Timestamp:
    return pd.to_datetime(values, utc=True)


class Store:
    def __init__(self, path: str | Path):
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"database not found: {p} (run the simulator, or set MARKET_DB)")
        self.path = p
        # read-only, so the brain can never damage the simulator's data; the simulator
        # should use WAL mode so reads do not block its writes
        self.conn = sqlite3.connect(f"file:{p.resolve().as_posix()}?mode=ro", uri=True,
                                    check_same_thread=False)
        cols = [r[1] for r in self.conn.execute(f"PRAGMA table_info({FACTS})")]
        if not cols:
            raise ValueError(f"{p}: table {FACTS} is missing")
        missing_dims = [d for d in ("ts",) + DIMENSIONS if d not in cols]
        if missing_dims:
            raise ValueError(f"{p}: {FACTS} lacks columns {missing_dims}")
        self.columns = cols
        self.measures = [c for c in cols if c not in ("ts",) + DIMENSIONS]

    # --------------------------------------------------------------- tables
    def tables(self) -> list[str]:
        """Readable contract tables present in the database."""
        present = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return [t for t in READABLE_TABLES if t in present]

    def row_counts(self) -> dict[str, int]:
        return {t: self.conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in self.tables()}

    def time_range(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        lo, hi = self.conn.execute(f"SELECT MIN(ts), MAX(ts) FROM {FACTS}").fetchone()
        if lo is None:
            raise ValueError(f"{self.path}: {FACTS} is empty")
        return to_ts(lo), to_ts(hi)

    def read_sql(self, sql: str, params=()) -> pd.DataFrame:
        """Run a query that reads only the contract tables (e.g. a contributed metric's SQL)."""
        refs = {next(g for g in m.groups() if g) for m in _TABLE_REF.finditer(sql)}
        lowered = sql.lower()
        if "sqlite_master" in lowered or "attach" in lowered or "pragma" in lowered:
            raise PermissionError("query touches the schema or other databases")
        denied = sorted(r for r in refs if r not in READABLE_TABLES)
        if denied:
            raise PermissionError(f"query reads tables outside the contract: {denied}")
        return pd.read_sql_query(sql, self.conn, params=params)

    # ---------------------------------------------------------------- sums
    def sums(self, columns, start=None, end=None, segment: dict | None = None,
             group_by=(), by_hour: bool = False) -> pd.DataFrame:
        """SUM of each measure column, grouped by the given dimensions (and hour)."""
        columns = list(dict.fromkeys(columns))
        bad = [c for c in columns if c not in self.measures]
        if bad:
            raise KeyError(f"unknown measure columns {bad}")
        group_by = list(group_by)
        bad = [d for d in group_by if d not in DIMENSIONS]
        if bad:
            raise KeyError(f"unknown dimensions {bad}")
        keys = (["ts"] if by_hour else []) + group_by

        where, params = [], []
        if start is not None:
            where.append("ts >= ?")
            params.append(iso_hour(start))
        if end is not None:
            where.append("ts < ?")
            params.append(iso_hour(end))
        for dim, val in (segment or {}).items():
            if dim not in DIMENSIONS:
                raise KeyError(f"unknown dimension {dim}")
            vals = list(val) if isinstance(val, (list, tuple, set)) else [val]
            where.append(f'"{dim}" IN ({",".join("?" * len(vals))})')
            params.extend(str(v) for v in vals)

        select = [f'"{k}"' for k in keys] + [f'SUM("{c}") AS "{c}"' for c in columns]
        sql = f"SELECT {', '.join(select)} FROM {FACTS}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        if keys:
            sql += " GROUP BY " + ", ".join(f'"{k}"' for k in keys) + " ORDER BY " + ", ".join(f'"{k}"' for k in keys)
        df = pd.read_sql_query(sql, self.conn, params=params)
        for c in columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
        if by_hour:
            df["ts"] = to_ts(df["ts"])
        return df

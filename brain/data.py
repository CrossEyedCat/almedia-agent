"""Read-only access to the simulator's SQLite database (README §3.1 data contract).

Timestamps are UTC hour starts in the form 2026-11-01T13:00:00Z; windows are [start, end).
Guardrail 2 is enforced inside SQLite: an authorizer lets this connection read only the
contract tables, so no query text - core or contributed - can reach the scoring table.
A missing value is never summed as zero: a group with any NULL comes back as NaN.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from brain.metric_spec import DIMENSIONS

FACTS = "facts_hourly"
READABLE_TABLES = (FACTS, "ingestion_log", "releases", "calendar")
TS_FORMAT = "%Y-%m-%dT%H:00:00Z"
TS_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:00:00Z"
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                    getattr(sqlite3, "SQLITE_RECURSIVE", 33)}


class ContractError(ValueError):
    """The database does not follow the data contract."""


def iso_hour(ts) -> str:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.floor("h").strftime(TS_FORMAT)


def to_ts(values) -> pd.Series | pd.Timestamp:
    return pd.to_datetime(values, utc=True)


def _authorize(action, arg1, arg2, dbname, source):
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and (arg1 or "").lower() not in READABLE_TABLES:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class Store:
    def __init__(self, path: str | Path):
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"database not found: {p} (run the simulator, or set MARKET_DB)")
        self.path = p
        # read-only URI (as_uri escapes '#', '?' and spaces); the simulator should use WAL mode
        self.conn = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        cols = [r[1] for r in self.conn.execute(f"PRAGMA table_info({FACTS})")]
        if not cols:
            raise ContractError(f"{p}: table {FACTS} is missing")
        missing_dims = [d for d in ("ts",) + DIMENSIONS if d not in cols]
        if missing_dims:
            raise ContractError(f"{p}: {FACTS} lacks columns {missing_dims}")
        present = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self._tables = [t for t in READABLE_TABLES if t in present]
        bad, example = self.conn.execute(
            f"SELECT COUNT(*), MIN(ts) FROM {FACTS} WHERE ts IS NULL OR ts NOT GLOB ?", (TS_GLOB,)).fetchone()
        if bad:
            raise ContractError(f"{p}: {bad} rows of {FACTS} have ts like {example!r}; "
                                f"the contract is UTC hour starts like 2026-11-01T13:00:00Z")
        stamps = [r[0] for r in self.conn.execute(f"SELECT DISTINCT ts FROM {FACTS}")]
        parsed = pd.to_datetime(pd.Series(stamps, dtype="object"), format=TS_FORMAT, errors="coerce", utc=True)
        if parsed.isna().any():
            raise ContractError(f"{p}: {FACTS} has impossible timestamps such as "
                                f"{stamps[int(parsed.isna().to_numpy().argmax())]!r}")
        self.columns = cols
        self.measures = [c for c in cols if c not in ("ts",) + DIMENSIONS]
        self.conn.set_authorizer(_authorize)

    # --------------------------------------------------------------- tables
    def tables(self) -> list[str]:
        """Readable contract tables present in the database."""
        return list(self._tables)

    def row_counts(self) -> dict[str, int]:
        return {t: self.conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in self._tables}

    def time_range(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        lo, hi = self.conn.execute(f"SELECT MIN(ts), MAX(ts) FROM {FACTS}").fetchone()
        if lo is None:
            raise ContractError(f"{self.path}: {FACTS} is empty")
        return to_ts(lo), to_ts(hi)

    def distinct(self, table: str, column: str) -> list:
        """Distinct values of a column in a readable table; [] when the table or column is absent."""
        if table not in self._tables:
            return []
        try:
            return [r[0] for r in self.conn.execute(f'SELECT DISTINCT "{column}" FROM "{table}"')]
        except sqlite3.DatabaseError:
            return []

    def read_sql(self, sql: str, params=()) -> pd.DataFrame:
        """Run a read-only query; anything outside the contract tables is refused by SQLite."""
        try:
            return pd.read_sql_query(sql, self.conn, params=params)
        except Exception as e:  # pandas wraps sqlite3 errors in its own DatabaseError
            if "not authorized" in str(e) or "is prohibited" in str(e):
                raise PermissionError(f"query reads outside the contract tables {READABLE_TABLES}") from e
            raise

    # ---------------------------------------------------------------- sums
    def sums(self, columns, start=None, end=None, segment: dict | None = None,
             group_by=(), by_hour: bool = False) -> pd.DataFrame:
        """SUM of each measure column, grouped by the given dimensions (and hour).
        A group containing any NULL for a column returns NaN for that column, never a partial sum."""
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

        select = [f'"{k}"' for k in keys] + [
            f'CASE WHEN COUNT("{c}") < COUNT(*) THEN NULL ELSE SUM("{c}") END AS "{c}"' for c in columns]
        sql = f"SELECT {', '.join(select)} FROM {FACTS}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        if keys:
            sql += " GROUP BY " + ", ".join(f'"{k}"' for k in keys) + " ORDER BY " + ", ".join(f'"{k}"' for k in keys)
        df = pd.read_sql_query(sql, self.conn, params=params)
        if not keys and not self._any_rows(where, params):
            df = df.iloc[0:0]  # no rows in the window: an empty result, not a row of NULLs
        for c in columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
        if by_hour:
            df["ts"] = to_ts(df["ts"])
        return df

    def _any_rows(self, where: list[str], params: list) -> bool:
        sql = f"SELECT 1 FROM {FACTS}" + (" WHERE " + " AND ".join(where) if where else "") + " LIMIT 1"
        return self.conn.execute(sql, params).fetchone() is not None

"""Inject a fault or an innocent event into an existing database, and replay the affected hours.

    python -m sim.inject payment_failure --country DE --platform Android
    python -m sim.inject stockout_surge --category electronics
    python -m sim.inject search_regression --country DE
    python -m sim.inject ad_budget_exhausted --country FR --start 2026-11-20T23:00:00Z
    python -m sim.inject end --id 3            # roll back: the fault stops from the latest hour
    python -m sim.inject promo --country FR --start 2026-11-20T08:00:00Z --end 2026-11-20T20:00:00Z
    python -m sim.inject data_delay --source payments --start 2026-11-21T06:00:00Z --end 2026-11-21T09:00:00Z

Each injection is written to ground_truth (hidden from brain/) with its full parameters. A payment
failure also writes a release, a promo writes a calendar event; a data delay is visible only in
ingestion_log. Rows of segments that the injection does not touch stay exactly as they were.
"""
from __future__ import annotations

import argparse
import json
import sqlite3

import pandas as pd

from sim import db, world
from sim.schema import SOURCES, TS_FORMAT
from sim.world import Injection


def _fmt(ts: pd.Timestamp) -> str:
    return ts.strftime(TS_FORMAT)


def utc(ts) -> pd.Timestamp:
    """Any time as a UTC timestamp: a time without a zone is taken as UTC (2026-11-20T08:00:00 works like ...Z)."""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _check(country=None, platform=None, category=None, source=None) -> None:
    """Refuse a value the simulator does not know before anything is written (a lower-case de used to be accepted,
    recorded in ground_truth and a release, and then changed no row)."""
    for name, value, allowed in (("country", country, world.COUNTRIES), ("platform", platform, world.PLATFORMS),
                                 ("category", category, world.CATEGORIES), ("source", source, SOURCES)):
        if value is not None and value not in allowed:
            raise ValueError(f"unknown {name} {value!r}; use one of {', '.join(allowed)}")


def replay(con: sqlite3.Connection, start: pd.Timestamp) -> int:
    """Regenerate every hour from `start` to the last hour on record. Returns the number of hours."""
    end = db.last_hour(con)
    if end is None or start > end:
        return 0
    first = db.hour_index(con, start)
    count = db.hour_index(con, end) - first + 1
    db.write_hours(con, first, count)
    return count


class AlreadyEnded(ValueError):
    """The injection has already ended at or before that hour."""


class NoSuchInjection(LookupError):
    pass


RELEASE_SYSTEM = {"payment_failure": "payments-sdk", "search_regression": "search", "ad_budget_exhausted": "pacing"}


class AlreadyInjected(ValueError):
    """The same injection (kind and parameters, same start) is already on record. Repeating it would stack."""


TARGET_KEYS = ("country", "platform", "category", "channel", "source")


def _target(params: dict) -> dict:
    return {k: params[k] for k in TARGET_KEYS if params.get(k)}


def _overlaps(a: Injection, b: Injection) -> bool:
    return (a.end is None or b.start < a.end) and (b.end is None or a.start < b.end)


def _record(con, inj: Injection, description: str) -> None:
    if con.execute("SELECT 1 FROM ground_truth WHERE kind = ? AND scope = ?", (inj.kind, inj.to_scope_json())).fetchone():
        raise AlreadyInjected(f"{inj.kind} with the same settings and start is already scheduled")
    for kind, scope in con.execute("SELECT kind, scope FROM ground_truth WHERE kind = ?", (inj.kind,)).fetchall():
        other = Injection.from_row(kind, scope)
        if _target(other.params) == _target(inj.params) and _overlaps(other, inj):
            # a second copy on the same target would stack on the first one
            raise AlreadyInjected(f"{inj.kind} is already in effect on the same target; end it first")
    con.execute("INSERT INTO ground_truth VALUES (?, ?, ?, ?)", (_fmt(inj.start), inj.kind, inj.to_scope_json(), description))
    con.commit()


def payment_failure(con, country: str, platform: str, start: pd.Timestamp, severity: float = 0.35,
                    end: pd.Timestamp | None = None, replay_now: bool = True) -> int:
    """A payment SDK release breaks payments for one country and platform (scenario S1)."""
    _check(country=country, platform=platform)
    start, end = utc(start), None if end is None else utc(end)
    inj = Injection("payment_failure", start, end, {"country": country, "platform": platform, "severity": severity})
    _record(con, inj, f"payment success rate drops {severity:.0%} for {country} {platform} after a payments-sdk release")
    con.execute("INSERT INTO releases VALUES (?, ?, ?, ?, ?)",
                (_fmt(start), "payments-sdk", country, platform, f"payments-sdk 4.2 rollout ({country}, {platform})"))
    con.commit()
    return replay(con, start) if replay_now else 0


def promo(con, country: str, start: pd.Timestamp, end: pd.Timestamp, traffic_lift: float = 0.4,
          conversion_lift: float = 0.2, extra_discount: float = 0.03, replay_now: bool = True) -> int:
    """A planned promotion: more traffic and orders, listed in the calendar (scenario S2)."""
    _check(country=country)
    start, end = utc(start), utc(end)
    inj = Injection("promo", start, end, {"country": country, "traffic_lift": traffic_lift,
                                          "conversion_lift": conversion_lift, "extra_discount": extra_discount})
    _record(con, inj, f"planned promo in {country}: harmless uplift in traffic and orders")
    con.execute("INSERT INTO calendar VALUES (?, ?, ?, ?, ?)", (_fmt(start), _fmt(end), country, "promo", f"{country} promo"))
    con.commit()
    return replay(con, start) if replay_now else 0


def data_delay(con, source: str, start: pd.Timestamp, end: pd.Timestamp, delay_hours: float = 2,
               replay_now: bool = True) -> int:
    """One source arrives late; the facts stay correct, only ingestion_log shows it (scenario S3)."""
    _check(source=source)
    start, end = utc(start), utc(end)
    inj = Injection("data_delay", start, end, {"source": source, "delay_hours": delay_hours})
    _record(con, inj, f"{source} data arrives {delay_hours:g} h late")
    return replay(con, start) if replay_now else 0


def stockout_surge(con, category: str, start: pd.Timestamp, end: pd.Timestamp | None = None, multiplier: float = 12.0,
                   country: str | None = None, replay_now: bool = True) -> int:
    """Popular listings in one category run out (scenario S5): more views land on unavailable products, so the
    add-to-cart rate falls while feed clicks stay up or rise. No release explains it."""
    _check(category=category, country=country)
    start, end = utc(start), None if end is None else utc(end)
    params = {"category": category, "multiplier": multiplier, "feed_ctr_lift": 0.25}
    if country:
        params["country"] = country
    inj = Injection("stockout_surge", start, end, params)
    where = f"{category} in {country}" if country else category
    _record(con, inj, f"out-of-stock listings x{multiplier:g} for {where}: add-to-cart rate falls, feed clicks rise")
    return replay(con, start) if replay_now else 0


def search_regression(con, country: str, start: pd.Timestamp, severity: float = 0.4, end: pd.Timestamp | None = None,
                      replay_now: bool = True) -> int:
    """A search release makes results less relevant in one country: fewer clicks per search, fewer product views."""
    _check(country=country)
    start, end = utc(start), None if end is None else utc(end)
    inj = Injection("search_regression", start, end, {"country": country, "severity": severity})
    _record(con, inj, f"search clicks per search drop {severity:.0%} in {country} after a search release")
    con.execute("INSERT INTO releases VALUES (?, ?, ?, ?, ?)",
                (_fmt(start), "search", country, None, f"search ranking v2 rollout ({country})"))
    con.commit()
    return replay(con, start) if replay_now else 0


def ad_budget_exhausted(con, country: str, start: pd.Timestamp, end: pd.Timestamp, boost: float = 3.0,
                        boost_hours: int = 10, after: float = 0.05, replay_now: bool = True) -> int:
    """The pacing controller ignores its schedule (scenario S6): sponsored impressions per session jump for the first
    `boost_hours` of the window, then the budget is spent and they almost stop. Traffic itself does not change.
    Start the window at local midnight (23:00 UTC) and use 10 hours to get "the budget is gone by 10:00"."""
    _check(country=country)
    start, end = utc(start), utc(end)
    inj = Injection("ad_budget_exhausted", start, end, {"country": country, "boost": boost, "boost_hours": boost_hours, "after": after})
    _record(con, inj, f"ad impressions per session x{boost:g} for {boost_hours} h, then budget spent, in {country}")
    con.execute("INSERT INTO releases VALUES (?, ?, ?, ?, ?)",
                (_fmt(start), "pacing", country, None, f"pacing controller 2.3 rollout ({country})"))
    con.commit()
    return replay(con, start) if replay_now else 0


def list_injections(con, after: pd.Timestamp) -> list[dict]:
    """Every injection on record, with its id and whether it is still in effect after hour `after`."""
    out = []
    for rowid, kind, scope, description in con.execute("SELECT rowid, kind, scope, description FROM ground_truth ORDER BY injected_at, rowid"):
        inj = Injection.from_row(kind, scope)
        out.append({"id": rowid, "kind": kind, "scope": json.loads(scope), "description": description,
                    "active": inj.end is None or inj.end > after})
    return out


def end_injection(con, rowid: int, at: pd.Timestamp, replay_now: bool = True) -> int:
    """Stop an injection from hour `at` on (someone rolled the change back) and regenerate the hours from `at`.
    A release or fault that wrote a release gets a rollback release. Returns the number of hours replayed."""
    at = utc(at)
    row = con.execute("SELECT kind, scope FROM ground_truth WHERE rowid = ?", (rowid,)).fetchone()
    if row is None:
        raise NoSuchInjection(f"no injection with id {rowid}")
    inj = Injection.from_row(*row)
    if inj.end is not None and inj.end <= at:
        raise AlreadyEnded(f"{inj.kind} already ended at {_fmt(inj.end)}")
    ended = Injection(inj.kind, inj.start, at, inj.params)
    con.execute("UPDATE ground_truth SET scope = ? WHERE rowid = ?", (ended.to_scope_json(), rowid))
    system = RELEASE_SYSTEM.get(inj.kind)
    if system and at >= inj.start:
        where = ", ".join(str(inj.params[d]) for d in ("country", "platform") if inj.params.get(d))
        con.execute("INSERT INTO releases VALUES (?, ?, ?, ?, ?)",
                    (_fmt(at), system, inj.params.get("country"), inj.params.get("platform"), f"{system} rollback ({where})"))
    con.commit()
    return replay(con, at) if replay_now else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["payment_failure", "stockout_surge", "search_regression", "ad_budget_exhausted", "promo", "data_delay", "end"])
    ap.add_argument("--id", type=int, help="end: the injection to stop (see ground_truth)")
    ap.add_argument("--category", choices=world.CATEGORIES)
    ap.add_argument("--multiplier", type=float, default=12.0)
    ap.add_argument("--boost", type=float, default=3.0)
    ap.add_argument("--boost-hours", type=int, default=10)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--country", choices=world.COUNTRIES)
    ap.add_argument("--platform", choices=world.PLATFORMS)
    ap.add_argument("--source", choices=SOURCES)
    ap.add_argument("--start", help="UTC hour; default: 6 hours before the end of the data")
    ap.add_argument("--now", action="store_true", help="start with the current (latest) hour, so the effect shows at once")
    ap.add_argument("--end", help="UTC hour; default: until the end of the simulation")
    ap.add_argument("--severity", type=float, default=0.35)
    ap.add_argument("--delay-hours", type=float, default=2)
    a = ap.parse_args(argv)

    con = db.connect(a.db)
    try:
        last = db.last_hour(con)
        if a.start:
            start = utc(a.start)
        elif a.now:
            start = last
        else:
            start = last - pd.Timedelta(hours=5)
        end = utc(a.end) if a.end else None
        if a.kind == "end":
            if a.id is None:
                ap.error("end needs --id")
            at = utc(a.start) if a.start else last
            n = end_injection(con, a.id, at)
            print(f"injection {a.id} ended from {_fmt(at)}, {n} hours replayed")
            return 0
        if a.kind == "payment_failure":
            if not (a.country and a.platform):
                ap.error("payment_failure needs --country and --platform")
            n = payment_failure(con, a.country, a.platform, start, a.severity, end)
        elif a.kind == "stockout_surge":
            if not a.category:
                ap.error("stockout_surge needs --category")
            n = stockout_surge(con, a.category, start, end, a.multiplier, a.country)
        elif a.kind == "search_regression":
            if not a.country:
                ap.error("search_regression needs --country")
            n = search_regression(con, a.country, start, a.severity, end)
        elif a.kind == "ad_budget_exhausted":
            if not a.country:
                ap.error("ad_budget_exhausted needs --country")
            n = ad_budget_exhausted(con, a.country, start, end or start + pd.Timedelta(hours=24), a.boost, a.boost_hours)
        elif a.kind == "promo":
            if not (a.country and end):
                ap.error("promo needs --country and --end")
            n = promo(con, a.country, start, end)
        else:
            if not (a.source and end):
                ap.error("data_delay needs --source and --end")
            n = data_delay(con, a.source, start, end, a.delay_hours)
        print(f"{a.kind}: from {_fmt(start)}, {n} hours replayed")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

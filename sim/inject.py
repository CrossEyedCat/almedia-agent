"""Inject a fault or an innocent event into an existing database, and replay the affected hours.

    python -m sim.inject payment_failure --country DE --platform Android
    python -m sim.inject promo --country FR --start 2026-11-20T08:00:00Z --end 2026-11-20T20:00:00Z
    python -m sim.inject data_delay --source payments --start 2026-11-21T06:00:00Z --end 2026-11-21T09:00:00Z

Each injection is written to ground_truth (hidden from brain/) with its full parameters. A payment
failure also writes a release, a promo writes a calendar event; a data delay is visible only in
ingestion_log. Rows of segments that the injection does not touch stay exactly as they were.
"""
from __future__ import annotations

import argparse
import sqlite3

import pandas as pd

from sim import db
from sim.schema import TS_FORMAT
from sim.world import Injection


def _fmt(ts: pd.Timestamp) -> str:
    return ts.strftime(TS_FORMAT)


def replay(con: sqlite3.Connection, start: pd.Timestamp) -> int:
    """Regenerate every hour from `start` to the last hour on record. Returns the number of hours."""
    end = db.last_hour(con)
    if end is None or start > end:
        return 0
    first = db.hour_index(con, start)
    count = db.hour_index(con, end) - first + 1
    db.write_hours(con, first, count)
    return count


def _record(con, inj: Injection, description: str) -> None:
    con.execute("INSERT INTO ground_truth VALUES (?, ?, ?, ?)", (_fmt(inj.start), inj.kind, inj.to_scope_json(), description))
    con.commit()


def payment_failure(con, country: str, platform: str, start: pd.Timestamp, severity: float = 0.35,
                    end: pd.Timestamp | None = None) -> int:
    """A payment SDK release breaks payments for one country and platform (scenario S1)."""
    inj = Injection("payment_failure", start, end, {"country": country, "platform": platform, "severity": severity})
    _record(con, inj, f"payment success rate drops {severity:.0%} for {country} {platform} after a payments-sdk release")
    con.execute("INSERT INTO releases VALUES (?, ?, ?, ?, ?)",
                (_fmt(start), "payments-sdk", country, platform, f"payments-sdk 4.2 rollout ({country}, {platform})"))
    con.commit()
    return replay(con, start)


def promo(con, country: str, start: pd.Timestamp, end: pd.Timestamp, traffic_lift: float = 0.4,
          conversion_lift: float = 0.2, extra_discount: float = 0.03) -> int:
    """A planned promotion: more traffic and orders, listed in the calendar (scenario S2)."""
    inj = Injection("promo", start, end, {"country": country, "traffic_lift": traffic_lift,
                                          "conversion_lift": conversion_lift, "extra_discount": extra_discount})
    _record(con, inj, f"planned promo in {country}: harmless uplift in traffic and orders")
    con.execute("INSERT INTO calendar VALUES (?, ?, ?, ?, ?)", (_fmt(start), _fmt(end), country, "promo", f"{country} promo"))
    con.commit()
    return replay(con, start)


def data_delay(con, source: str, start: pd.Timestamp, end: pd.Timestamp, delay_hours: float = 2) -> int:
    """One source arrives late; the facts stay correct, only ingestion_log shows it (scenario S3)."""
    inj = Injection("data_delay", start, end, {"source": source, "delay_hours": delay_hours})
    _record(con, inj, f"{source} data arrives {delay_hours:g} h late")
    return replay(con, start)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["payment_failure", "promo", "data_delay"])
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--country")
    ap.add_argument("--platform")
    ap.add_argument("--source")
    ap.add_argument("--start", help="UTC hour; default: 6 hours before the end of the data")
    ap.add_argument("--end", help="UTC hour; default: until the end of the simulation")
    ap.add_argument("--severity", type=float, default=0.35)
    ap.add_argument("--delay-hours", type=float, default=2)
    a = ap.parse_args(argv)

    con = db.connect(a.db)
    try:
        last = db.last_hour(con)
        start = pd.Timestamp(a.start) if a.start else last - pd.Timedelta(hours=5)
        end = pd.Timestamp(a.end) if a.end else None
        if a.kind == "payment_failure":
            if not (a.country and a.platform):
                ap.error("payment_failure needs --country and --platform")
            n = payment_failure(con, a.country, a.platform, start, a.severity, end)
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

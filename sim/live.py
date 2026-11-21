"""Run the simulated clock forward: one new hour of data every few seconds.

    python -m sim.live --speed 2          # one simulated hour every 2 s
    python -m sim.live --speed 0 --hours 6  # as fast as possible, 6 hours

Injections (python -m sim.inject ... --now) are picked up when the clock reaches their start hour,
because every hour is generated with the injections on record at that moment.
"""
from __future__ import annotations

import argparse
import time

from sim import db


def run_live(path: str, speed: float = 2.0, hours: int | None = None, on_hour=None) -> int:
    con = db.connect(path)
    done = 0
    try:
        while hours is None or done < hours:
            last = db.last_hour(con)
            idx = db.hour_index(con, last) + 1
            db.write_hours(con, idx, 1)
            done += 1
            if on_hour:
                on_hour(idx)
            if speed > 0 and (hours is None or done < hours):
                time.sleep(speed)
    finally:
        con.close()
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--speed", type=float, default=2.0, help="seconds per simulated hour; 0 = no waiting")
    ap.add_argument("--hours", type=int, help="stop after this many hours (default: run until stopped)")
    a = ap.parse_args(argv)

    def show(idx):
        con = db.connect(a.db)
        print("hour", idx, db.last_hour(con).strftime("%Y-%m-%dT%H:00:00Z"), flush=True)
        con.close()

    try:
        run_live(a.db, a.speed, a.hours, show)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

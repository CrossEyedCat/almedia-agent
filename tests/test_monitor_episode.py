"""An episode covers the hours that are bad on their own evidence, not the normal hours a summed
window drags along after a fault has ended (risk 4: reports that halve the change and a recovery
time two hours late)."""
import shutil
import sqlite3

import pandas as pd
import pytest

from brain.data import Store
from brain.graph import MetricGraph
from brain.monitor import Monitor
from sim import db as simdb
from sim import inject
from sim.run import backfill
from sim.schema import MEASURES

T = pd.Timestamp
H = pd.Timedelta(hours=1)
START = "2026-10-25T00:00:00Z"
FAULT = T("2026-11-03T12:00:00Z")   # day 9, midday
DROP = T("2026-11-16T12:00:00Z")    # day 23, for the volumes (three earlier weeks)
PLACE = {"country": "DE", "platform": "Android"}


def _db(tmp_path_factory, days):
    return backfill(str(tmp_path_factory.mktemp(f"ep{days}") / "clean.db"), days * 24, START, seed=11)


@pytest.fixture(scope="module")
def clean10(tmp_path_factory):
    return _db(tmp_path_factory, 10)


@pytest.fixture(scope="module")
def clean24(tmp_path_factory):
    return _db(tmp_path_factory, 24)


def _monitor(clean, tmp_path, *steps):
    path = tmp_path / "s.db"
    shutil.copy(clean, path)
    con = simdb.connect(path)
    try:
        for step in steps:
            step(con)
    finally:
        con.close()
    return Monitor(MetricGraph.load(), Store(path)), path


def _sum(path, columns, start, end):
    con = sqlite3.connect(path)
    try:
        q = f"SELECT {', '.join(f'SUM({c})' for c in columns)} FROM facts_hourly " \
            "WHERE country = 'DE' AND platform = 'Android' AND ts >= ? AND ts < ?"
        return con.execute(q, (start.strftime("%Y-%m-%dT%H:00:00Z"), end.strftime("%Y-%m-%dT%H:00:00Z"))).fetchone()
    finally:
        con.close()


def _alert(tick, metric):
    return next((a for a in tick.alerts if a.metric == metric and a.segment == PLACE), None)


def test_a_six_hour_step_fault_ends_at_its_last_faulty_hour(clean10, tmp_path):
    monitor, path = _monitor(clean10, tmp_path,
                             lambda con: inject.payment_failure(con, "DE", "Android", FAULT, 0.4, end=FAULT + 6 * H))
    ticks = {k: monitor.tick(as_of=FAULT + k * H) for k in range(0, 9)}
    found = _alert(ticks[8], "payment_success_rate")   # two normal hours after the fault are in
    assert found is not None
    assert found.onset == FAULT
    assert found.last_bad == FAULT + 5 * H               # not FAULT + 7 h
    assert found.hours == 6
    s, a = _sum(path, ["payment_successes", "payment_attempts"], FAULT + 3 * H, FAULT + 6 * H)
    assert found.observed == pytest.approx(s / a)        # the newest bad hours are fault hours only
    assert (found.numerator, found.denominator) == (pytest.approx(s), pytest.approx(a))
    s, a = _sum(path, ["payment_successes", "payment_attempts"], FAULT, FAULT + 6 * H)
    assert found.episode_observed == pytest.approx(s / a)


def test_the_normal_hours_after_a_fault_are_history_again(clean10, tmp_path):
    # they were flagged as bad before, and so left out of every later baseline
    monitor, _ = _monitor(clean10, tmp_path,
                          lambda con: inject.payment_failure(con, "DE", "Android", FAULT, 0.4, end=FAULT + 6 * H))
    for k in range(0, 9):
        monitor.tick(as_of=FAULT + k * H)
    cube = monitor.cubes[("country", "platform")]
    col = monitor._flags[("payment_success_rate", ("country", "platform"))][:, cube._kidx[("DE", "Android")]]
    flagged = [cube.hour(r) for r in range(len(col)) if col[r]]
    assert flagged == [FAULT + k * H for k in range(6)]


def test_a_volume_episode_ends_at_its_last_low_hour(clean24, tmp_path):
    def halve(con):
        sets = ", ".join(f"{m} = {m} * 0.5" for m in MEASURES)
        con.execute(f"UPDATE facts_hourly SET {sets} WHERE country = 'DE' AND platform = 'Android' "
                    "AND ts >= ? AND ts < ?", (DROP.strftime("%Y-%m-%dT%H:00:00Z"),
                                               (DROP + 6 * H).strftime("%Y-%m-%dT%H:00:00Z")))
        con.commit()
    monitor, path = _monitor(clean24, tmp_path, halve)
    ticks = {k: monitor.tick(as_of=DROP + k * H) for k in range(0, 9)}
    found = _alert(ticks[8], "sessions")
    assert found is not None and found.kind == "volume"
    assert found.onset == DROP and found.last_bad == DROP + 5 * H
    (sessions,) = _sum(path, ["sessions"], DROP + 3 * H, DROP + 6 * H)
    assert found.numerator == pytest.approx(sessions)
    assert found.observed == pytest.approx(sessions / 3)
    (sessions,) = _sum(path, ["sessions"], DROP, DROP + 6 * H)
    assert found.episode_observed == pytest.approx(sessions / 6)

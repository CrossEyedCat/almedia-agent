"""Tests for the simulator: contract, internal consistency, seasonality, and the injected scenarios."""
import re
import sqlite3
import time

import numpy as np
import pandas as pd
import pytest

from sim import db, inject
from sim.run import backfill
from sim.schema import DIMENSIONS, MEASURES
from tests.fixture_db import make_frame

START = "2026-10-25T00:00:00Z"
FAULT_START = pd.Timestamp("2026-10-30T08:00:00Z")


def facts(path) -> pd.DataFrame:
    con = sqlite3.connect(path)
    try:
        return pd.read_sql_query("SELECT * FROM facts_hourly", con)
    finally:
        con.close()


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("sim") / "clean.db"), 7 * 24, START, seed=7)


def fresh_copy(tmp_path, hours=7 * 24, seed=7):
    return backfill(str(tmp_path / "x.db"), hours, START, seed=seed)


def key(df):
    return df.sort_values(["ts", *DIMENSIONS]).reset_index(drop=True)


# ----------------------------------------------------------------- contract
def test_columns_match_the_brain_fixture(clean_db):
    assert list(facts(clean_db).columns) == list(make_frame(days=1).columns)


def test_timestamps_are_utc_hour_starts(clean_db):
    ts = facts(clean_db)["ts"].unique()
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:00:00Z", t) for t in ts)
    assert len(ts) == 7 * 24


def test_brain_can_read_it(clean_db):
    from brain.data import Store
    counts = Store(clean_db).row_counts()
    assert counts["facts_hourly"] > 0 and counts["ingestion_log"] == 7 * 24 * 7
    assert "ground_truth" not in counts  # the brain never reads the answer


# ------------------------------------------------------------- consistency
def test_funnel_is_consistent_and_non_negative(clean_db):
    f = facts(clean_db)
    assert (f[list(MEASURES)] >= 0).all().all()
    assert (f.orders <= f.payment_successes).all()
    assert (f.payment_successes <= f.payment_attempts).all()
    assert (f.checkouts <= f.payment_attempts).all()
    assert (f.add_to_cart <= f.product_views).all()
    assert (f.checkouts <= f.add_to_cart).all()
    assert (f.refunds <= f.orders).all()
    assert (f.product_views >= f.search_clicks + f.feed_clicks).all()
    assert not f.isna().any().any()


def test_money_follows_orders(clean_db):
    f = facts(clean_db)
    assert (f.loc[f.orders == 0, "gmv"] == 0).all()
    take = f.marketplace_fees.sum() / f.gmv.sum()
    assert 0.08 < take < 0.16
    assert (f.discounts <= f.gmv).all()


def test_same_seed_gives_identical_data(tmp_path):
    a = facts(backfill(str(tmp_path / "a.db"), 48, START, seed=3))
    b = facts(backfill(str(tmp_path / "b.db"), 48, START, seed=3))
    c = facts(backfill(str(tmp_path / "c.db"), 48, START, seed=4))
    pd.testing.assert_frame_equal(key(a), key(b))
    assert not key(a).equals(key(c))


def test_full_backfill_is_fast(tmp_path):
    t0 = time.time()
    backfill(str(tmp_path / "big.db"), 28 * 24, START)
    assert time.time() - t0 < 30


# ------------------------------------------------------------- seasonality
def test_evening_peak_and_night_dip(clean_db):
    f = facts(clean_db)
    f["hour"] = f.ts.str[11:13].astype(int)
    by_hour = f.groupby("hour").sessions.sum()
    assert by_hour.loc[19] > 3 * by_hour.loc[3]      # 20:00 local peak vs 04:00 local
    assert by_hour.loc[11] > by_hour.loc[5]          # lunch bump vs early morning


def test_weekend_is_busier_than_midweek(clean_db):
    f = facts(clean_db)
    day = pd.to_datetime(f.ts).dt.day_of_week
    per_day = f.groupby(day).sessions.sum()
    assert per_day.loc[6] > per_day.loc[1]            # Sunday vs Tuesday


# ------------------------------------------------------------------ S1
def test_payment_failure_hits_only_its_segment(clean_db, tmp_path):
    clean = key(facts(clean_db))
    path = fresh_copy(tmp_path)
    con = db.connect(path)
    inject.payment_failure(con, "DE", "Android", FAULT_START, severity=0.35)
    con.close()
    faulty = key(facts(path))

    target = (faulty.country == "DE") & (faulty.platform == "Android")
    after = pd.to_datetime(faulty.ts) >= FAULT_START
    # every other row is exactly as before, and so is the target before the fault
    pd.testing.assert_frame_equal(faulty[~(target & after)], clean[~(target & after)])

    def psr(df):
        sub = df[target & after]
        return sub.payment_successes.sum() / sub.payment_attempts.sum()
    assert psr(faulty) < 0.75 * psr(clean)
    assert faulty[target & after].orders.sum() < 0.8 * clean[target & after].orders.sum()


def test_payment_failure_leaves_a_release_and_the_answer(tmp_path):
    path = fresh_copy(tmp_path, hours=3 * 24)
    con = db.connect(path)
    inject.payment_failure(con, "FR", "iOS", pd.Timestamp("2026-10-26T10:00:00Z"))
    release = con.execute("SELECT ts, system, country, platform FROM releases").fetchall()
    truth = con.execute("SELECT injected_at, kind, scope FROM ground_truth").fetchall()
    con.close()
    assert release == [("2026-10-26T10:00:00Z", "payments-sdk", "FR", "iOS")]
    assert truth[0][1] == "payment_failure" and '"country": "FR"' in truth[0][2]


def test_replay_equals_a_run_that_knew_the_fault_from_the_start(tmp_path):
    a = fresh_copy(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path, hours=4 * 24)
    con = db.connect(a)
    inject.payment_failure(con, "DE", "Web", pd.Timestamp("2026-10-27T06:00:00Z"), severity=0.5)
    con.close()
    # second database: record the fault first, then generate all hours once
    (tmp_path / "b").mkdir()
    b = str(tmp_path / "b" / "x.db")
    con = db.connect(b)
    db.init_db(con, 7, pd.Timestamp(START), ("DE", "FR", "ES", "PL"))
    from sim.world import Injection
    inj = Injection("payment_failure", pd.Timestamp("2026-10-27T06:00:00Z"), None,
                    {"country": "DE", "platform": "Web", "severity": 0.5})
    con.execute("INSERT INTO ground_truth VALUES (?, ?, ?, ?)", ("2026-10-27T06:00:00Z", inj.kind, inj.to_scope_json(), ""))
    con.commit()
    db.write_hours(con, 0, 4 * 24)
    con.close()
    pd.testing.assert_frame_equal(key(facts(a)), key(facts(b)))


# ------------------------------------------------------------------ S2
def test_promo_lifts_orders_in_its_country_and_is_in_the_calendar(clean_db, tmp_path):
    clean = key(facts(clean_db))
    path = fresh_copy(tmp_path)
    s, e = pd.Timestamp("2026-10-28T08:00:00Z"), pd.Timestamp("2026-10-28T20:00:00Z")
    con = db.connect(path)
    inject.promo(con, "FR", s, e)
    cal = con.execute("SELECT start_ts, end_ts, country, kind FROM calendar").fetchall()
    con.close()
    promo = key(facts(path))
    ts = pd.to_datetime(promo.ts)
    window = (ts >= s) & (ts < e)
    fr = promo.country == "FR"
    assert promo[fr & window].orders.sum() > 1.3 * clean[fr & window].orders.sum()
    pd.testing.assert_frame_equal(promo[~(fr & window)], clean[~(fr & window)])
    assert cal == [("2026-10-28T08:00:00Z", "2026-10-28T20:00:00Z", "FR", "promo")]


# ------------------------------------------------------------------ S3
def test_data_delay_changes_only_the_ingestion_log(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    s, e = pd.Timestamp("2026-10-29T06:00:00Z"), pd.Timestamp("2026-10-29T09:00:00Z")
    con = db.connect(path)
    inject.data_delay(con, "payments", s, e, delay_hours=2)
    rows = con.execute("SELECT ts, arrived_at FROM ingestion_log WHERE source='payments' AND ts >= ? AND ts < ?",
                       (s.strftime("%Y-%m-%dT%H:00:00Z"), e.strftime("%Y-%m-%dT%H:00:00Z"))).fetchall()
    other = con.execute("SELECT ts, arrived_at FROM ingestion_log WHERE source='orders' AND ts = ?",
                        (s.strftime("%Y-%m-%dT%H:00:00Z"),)).fetchone()
    con.close()
    pd.testing.assert_frame_equal(key(facts(path)), key(facts(clean_db)))
    for ts, arrived in rows:
        lag = pd.Timestamp(arrived) - pd.Timestamp(ts)
        assert pd.Timedelta(hours=3) <= lag <= pd.Timedelta(hours=3, minutes=10)
    assert pd.Timestamp(other[1]) - pd.Timestamp(other[0]) < pd.Timedelta(hours=1, minutes=10)

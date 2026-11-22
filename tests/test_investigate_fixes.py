"""The investigator after the brief audit: the top-down check of the order chain (V1), volume alerts (V2),
innocent explanations (V3), the stock-out and ad-pacing wording (V4) and the recommendations system (V5)."""
import pandas as pd
import pytest

from brain.data import Store
from brain.graph import MetricGraph
from brain.incidents import Book, Incident
from brain.investigate import Investigator
from brain.monitor import Alert, Monitor
from sim import db as simdb
from sim import inject
from tests.test_investigate import DAYS, FAULT, H, START, clean_db, payment, release, run  # noqa: F401  (fixtures)

CHAIN = ("sessions", "views_per_session", "add_to_cart_rate", "checkout_rate", "payment_attempt_rate",
         "payment_success_rate", "order_rate")


def step_of(inc, action, target):
    return next(s for s in inc["trail"] if s["action"] == action and s["target"] == target)


# ------------------------------------------------------------------ V1: the order chain, top-down
def test_the_trail_checks_every_factor_of_orders_in_the_place(run):
    (inc,) = run(payment)
    check = step_of(inc, "check", "factors of orders")
    text = check["result"]
    assert "DE x Android" in text
    for m in CHAIN:
        assert m in text
    parts = {p.split(" ")[0]: p for p in text.split(": ", 1)[1].split("; ")}
    assert "moved -" in parts["payment_success_rate"]
    for m in CHAIN:
        if m != "payment_success_rate":
            assert parts[m].endswith("normal"), parts[m]
    actions = [s["action"] for s in inc["trail"]]
    assert actions.index("drill") < inc["trail"].index(check)


def test_the_report_says_which_factor_moved_and_that_traffic_stayed_normal(run):
    (inc,) = run(payment)
    assert "payment_success_rate moved" in inc["report"]
    assert "traffic (sessions) included, stayed normal" in inc["report"]
    assert not any("Traffic is taken as given" in u for u in inc["uncertain"])


# ------------------------------------------------------------------ V2: volume alerts
def ts(t):
    return t.strftime("%Y-%m-%dT%H:00:00Z")


def investigate_by_hand(db, alerts, as_of):
    """A tick of the monitor, then the investigator on an incident built from the given alerts."""
    monitor = Monitor(MetricGraph.load(), Store(db))
    tick = monitor.tick(as_of=as_of)
    inc = Incident(id="inc-0001", first_seen=max(a.last_bad for a in alerts), last_seen=max(a.last_bad for a in alerts),
                   onset=min(a.onset for a in alerts), segment=dict(alerts[0].segment),
                   metrics={a.metric: a for a in alerts})
    return monitor, tick, Investigator(monitor).report(inc, tick)


@pytest.fixture
def traffic_drop(clean_db, tmp_path):
    """Every count of DE x Android halved from FAULT on: sessions fall, every rate stays the same."""
    from sim.schema import MEASURES
    path = tmp_path / "drop.db"
    import shutil
    shutil.copy(clean_db, path)
    con = simdb.connect(path)
    sets = ", ".join(f'"{c}" = ROUND("{c}" * 0.5)' for c in MEASURES)
    con.execute(f"UPDATE facts_hourly SET {sets} WHERE country = 'DE' AND platform = 'Android' AND ts >= ?", (ts(FAULT),))
    con.commit()
    con.close()
    return path


def volume_alert(db, metric, last_bad):
    """The alert the monitor raises for a fallen count (interface 1): counts per hour over the newest bad hours."""
    monitor = Monitor(MetricGraph.load(), Store(db))
    monitor.tick(as_of=last_bad + H)
    seg = {"country": "DE", "platform": "Android"}
    newest = Investigator(monitor).volume(metric, seg, last_bad - 2 * H, last_bad + H)
    episode = Investigator(monitor).volume(metric, seg, FAULT, last_bad + H)
    return Alert(metric=metric, kind="volume", segment=seg, onset=FAULT, last_bad=last_bad, hours=5,
                 observed=newest["observed"] / newest["hours"], expected=newest["expected"] / newest["hours"],
                 numerator=newest["observed"], denominator=newest["expected"],
                 episode_observed=episode["observed"] / episode["hours"], score=9.0, z=-6.0)


@pytest.mark.parametrize("metric", ["sessions", "orders"])
def test_a_traffic_drop_is_named_as_traffic_with_no_confirmed_cause(traffic_drop, metric):
    alert = volume_alert(traffic_drop, metric, FAULT + 4 * H)
    assert alert.observed < 0.7 * alert.expected     # small counts round up: orders fall a little less than half
    _, _, body = investigate_by_hand(traffic_drop, [alert], FAULT + 5 * H)
    check = step_of(body, "check", "factors of orders")["result"]
    parts = {p.split(" ")[0]: p for p in check.split(": ", 1)[1].split("; ")}
    assert "moved -" in parts["sessions"]
    for m in CHAIN[1:]:
        assert parts[m].endswith("normal"), parts[m]
    assert body["contributions"][0]["node"] == "sessions" and body["contributions"][0]["delta"] < 0
    assert body["impact"]["orders"] < 0
    assert "No cause is confirmed: traffic fell in DE x Android; no release, event or late source fits" in body["report"]
    lead = body["hypotheses"][0]
    assert lead["status"] == "plausible" and lead["cause"].startswith("traffic fell in DE x Android")
    assert any("tracking" in u and "app store" in u and "outage" in u for u in body["uncertain"])
    watch = body["trail"][0]["result"]
    assert "per hour" in watch and "same hours of earlier weeks" in watch


def test_a_volume_alert_gets_the_same_release_check(traffic_drop):
    from tests.test_investigate import release as add_release
    con = simdb.connect(traffic_drop)
    add_release(FAULT, "demand", "DE", "Android", "app store listing removed (DE, Android)")(con)
    con.close()
    alert = volume_alert(traffic_drop, "sessions", FAULT + 4 * H)
    _, _, body = investigate_by_hand(traffic_drop, [alert], FAULT + 5 * H)
    lead = body["hypotheses"][0]
    assert lead["kind"] == "release" and lead["status"] == "supported" and "app store" in lead["cause"]

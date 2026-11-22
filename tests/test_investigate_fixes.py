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

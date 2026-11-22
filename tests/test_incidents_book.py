"""The incident book (brain/incidents.py): one incident per fault when the fault spans sibling series,
innocent explanations, recurrences and severity."""
import json
import shutil
import sqlite3

import pandas as pd
import pytest

from brain.data import Store, iso_hour
from brain.graph import MetricGraph
from brain.incidents import Book
from brain.monitor import Alert, Monitor, Tick
from sim.run import backfill

T = pd.Timestamp
H = pd.Timedelta(hours=1)
START = "2026-10-25T00:00:00Z"
DAYS = 10
FAULT = T("2026-11-03T12:00:00Z")


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("book") / "clean.db"), DAYS * 24, START, seed=11)


def records(folder):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def alerts_in(folder):
    return [r for r in records(folder) if r["status"] in ("open", "ongoing", "resolved")]


# ------------------------------------------------------------------ a book with a stand-in investigator
def key(segment: dict) -> tuple:
    return tuple(sorted(segment.items()))


def alert(metric, segment, score, hour=FAULT, observed=0.2, expected=0.1):
    return Alert(metric=metric, kind="ratio", segment=dict(segment), onset=hour, last_bad=hour, hours=1,
                 observed=observed, expected=expected, numerator=observed * 1000, denominator=1000.0,
                 episode_observed=observed, score=score, z=score / 10)


class StubInvestigator:
    """What the investigation would conclude, per monitor series: the localized place, the lead cause and any
    extra fields (an innocent explanation, an impact)."""

    def __init__(self, places=None, causes=None, extra=None):
        self.places, self.causes, self.extra = places or {}, causes or {}, extra or {}

    def report(self, inc, tick):
        lead = max(inc.metrics.values(), key=lambda a: a.score)
        k = key(inc.segment)
        cause = self.causes.get(k, f"{lead.metric} moved in {k} without a matching release")
        body = {"id": inc.id, "status": inc.status, "first_seen": iso_hour(inc.first_seen),
                "last_seen": iso_hour(inc.last_seen), "metric": lead.metric,
                "segment": dict(self.places.get(k, inc.segment)),
                "fingerprint": f"{lead.metric}|{k}",
                "change": {"observed": lead.observed, "expected": lead.expected, "delta_pct": 100 * lead.effect,
                           "z": lead.z, "window": {"start": iso_hour(inc.onset), "end": iso_hour(inc.last_seen + H)}},
                "drivers": [{"metric": a.metric, "segment": a.segment, "observed": a.observed, "expected": a.expected,
                             "delta_pct": 100 * a.effect, "z": a.z} for a in inc.metrics.values()],
                "hypotheses": [{"cause": cause, "kind": "release", "status": "supported", "score": 0.9,
                                "evidence": ["stub"]}],
                "trail": [{"action": "watch", "target": lead.metric, "result": "stub"}],
                "uncertain": [], "report": f"{lead.metric} moved."}
        body.update(self.extra.get(k, {}))
        return body


@pytest.fixture
def stub_book(clean_db, tmp_path):
    def make(**kw):
        book = Book(Monitor(MetricGraph.load(), Store(clean_db)), tmp_path / "incidents", fresh=True)
        book.investigator = StubInvestigator(**kw)
        return book
    return make


def feed(book, *alerts, hour=FAULT):
    return book.update(Tick(now=hour + H, newest=hour, alerts=list(alerts)))


DE_EL = {"country": "DE", "category": "electronics"}
FR_EL = {"country": "FR", "category": "electronics"}
EL = {"category": "electronics"}


# ------------------------------------------------------------------ I1: one incident per fault across sibling series
def test_sibling_series_of_one_fault_join_through_the_localized_place(stub_book):
    book = stub_book(places={key(DE_EL): EL, key(FR_EL): EL})
    feed(book, alert("discount_rate", DE_EL, 50), alert("discount_rate", FR_EL, 40))
    feed(book, alert("discount_rate", DE_EL, 60), alert("discount_rate", FR_EL, 70), hour=FAULT + H)
    (inc,) = alerts_in(book.folder)
    assert inc["id"] == "inc-0001" and inc["segment"] == EL
    assert book._next == 2     # no number was spent on the duplicate


def test_sibling_series_join_when_the_newer_one_localizes_into_the_older(stub_book):
    # the first series' investigation keeps its own cell; the second one's points at the whole category
    book = stub_book(places={key(FR_EL): EL})
    feed(book, alert("discount_rate", DE_EL, 50))
    feed(book, alert("discount_rate", DE_EL, 55), alert("discount_rate", FR_EL, 40), hour=FAULT + H)
    assert [i["id"] for i in alerts_in(book.folder)] == ["inc-0001"]


def test_sibling_series_with_the_same_supported_cause_are_one_incident(stub_book):
    cause = "release pricing: pricing 7.1 rollout (electronics)"
    book = stub_book(causes={key(DE_EL): cause, key(FR_EL): cause})
    feed(book, alert("discount_rate", DE_EL, 50), alert("discount_rate", FR_EL, 40))
    feed(book, alert("discount_rate", DE_EL, 60), alert("discount_rate", FR_EL, 45), hour=FAULT + H)
    (inc,) = alerts_in(book.folder)
    assert inc["hypotheses"][0]["cause"] == cause


def test_sibling_series_with_their_own_places_and_causes_stay_apart(stub_book):
    book = stub_book(causes={key(DE_EL): "release a: x (DE)", key(FR_EL): "release b: y (FR)"})
    feed(book, alert("discount_rate", DE_EL, 50), alert("discount_rate", FR_EL, 40))
    assert len(alerts_in(book.folder)) == 2


def test_an_unrelated_metric_in_the_localized_place_is_its_own_incident(stub_book):
    book = stub_book(places={key(DE_EL): EL})
    feed(book, alert("discount_rate", DE_EL, 50))
    feed(book, alert("search_ctr", FR_EL, 40, observed=0.05), hour=FAULT + H)
    assert len(alerts_in(book.folder)) == 2


def test_a_discount_bug_across_a_country_is_one_incident(clean_db, tmp_path):
    # the strongest series are the country's category cells (discount_rate is judged on its spread, and the
    # cells are steadier than the country): before the fix this gave one incident per category
    path = tmp_path / "d.db"
    shutil.copy(clean_db, path)
    con = sqlite3.connect(path)
    con.execute("UPDATE facts_hourly SET discounts = ROUND(discounts * 2.5, 2) WHERE country = 'FR' AND ts >= ? AND ts < ?",
                (iso_hour(FAULT), iso_hour(FAULT + 8 * H)))
    con.commit()
    con.close()
    monitor = Monitor(MetricGraph.load(), Store(path))
    book = Book(monitor, tmp_path / "incidents", fresh=True)
    for k in range(0, 6):
        book.update(monitor.tick(as_of=FAULT + k * H))
    got = alerts_in(book.folder)
    assert len(got) == 1, [(i["id"], i["fingerprint"]) for i in got]
    (inc,) = got
    assert inc["segment"] == {"country": "FR"}
    assert {d["metric"] for d in inc["drivers"]} >= {"discount_rate"}

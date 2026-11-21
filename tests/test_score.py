"""Tests for the scoring harness, using made-up incidents against a generated truth."""
import json

import pandas as pd
import pytest

from sim import score as sc


@pytest.fixture(scope="module")
def truth(tmp_path_factory):
    path = sc.generate(tmp_path_factory.mktemp("eval") / "eval.db", faults=4, innocents=4, seed=3)
    events, start, end = sc.load_truth(path)
    return path, events, start, end, (end - start) / pd.Timedelta(days=1)


def faults_of(events):
    return [e for e in events if e.kind == "payment_failure"]


def incident(n, f, hours_after=2, segment=None, supported=True, cause=None):
    seg = {"country": f.scope["country"], "platform": f.scope["platform"]} if segment is None else segment
    text = cause or f"release payments-sdk 4.2 for {f.scope['platform']} in {f.scope['country']}"
    return {"id": f"inc-{n}", "first_seen": (f.start + pd.Timedelta(hours=hours_after)).strftime("%Y-%m-%dT%H:00:00Z"),
            "segment": seg, "hypotheses": [{"cause": text, "kind": "release", "status": "supported" if supported else "rejected"}]}


def test_generated_events_fit_their_slots_and_do_not_overlap(truth):
    _, events, start, end, _ = truth
    assert len(events) == 8 and len(faults_of(events)) == 4
    ordered = sorted(events, key=lambda e: e.start)
    for a, b in zip(ordered, ordered[1:]):
        assert a.end <= b.start
    assert ordered[0].start >= start + pd.Timedelta(days=7)         # a clean week first
    assert all(e.end <= end for e in events)


def test_generate_is_reproducible(tmp_path):
    a = sc.load_truth(sc.generate(tmp_path / "a.db", faults=3, innocents=2, seed=9))[0]
    b = sc.load_truth(sc.generate(tmp_path / "b.db", faults=3, innocents=2, seed=9))[0]
    assert [(e.kind, e.start, e.scope) for e in a] == [(e.kind, e.start, e.scope) for e in b]


def test_a_perfect_brain_scores_perfectly(truth):
    _, events, _, end, days = truth
    incs = [incident(n, f, hours_after=2) for n, f in enumerate(faults_of(events))]
    r = sc.score(events, incs, end, days)
    assert (r["detection_rate"], r["localized_rate"], r["correct_cause_rate"]) == (1, 1, 1)
    assert r["mean_delay_h"] == 2 and r["false_alarms"] == 0 and r["innocent_silent_rate"] == 1


def test_a_silent_brain_detects_nothing_but_raises_no_alarm(truth):
    _, events, _, end, days = truth
    r = sc.score(events, [], end, days)
    assert r["detection_rate"] == 0 and r["mean_delay_h"] is None and r["false_alarms"] == 0
    assert r["innocent_silent_rate"] == 1 and r["localized_rate"] is None


def test_country_only_incident_is_detected_but_not_localized(truth):
    _, events, _, end, days = truth
    f = faults_of(events)[0]
    r = sc.score(events, [incident(1, f, segment={"country": f.scope["country"]})], end, days)
    assert r["detected"] == 1 and r["localized_rate"] == 0


def test_wrong_cause_is_not_counted_as_correct(truth):
    _, events, _, end, days = truth
    f = faults_of(events)[0]
    r = sc.score(events, [incident(1, f, supported=False), ], end, days)
    assert r["detected"] == 1 and r["correct_cause_rate"] == 0
    r = sc.score(events, [incident(1, f, cause="traffic drop")], end, days)
    assert r["correct_cause_rate"] == 0


def test_an_incident_before_the_fault_does_not_count_as_detection(truth):
    _, events, _, end, days = truth
    f = faults_of(events)[0]
    r = sc.score(events, [incident(1, f, hours_after=-3)], end, days)
    assert r["detected"] == 0 and r["false_alarms"] == 1


def test_alarm_during_a_promo_in_its_country_breaks_silence(truth):
    _, events, _, end, days = truth
    promo = next(e for e in events if e.kind == "promo")
    noisy = {"id": "inc-x", "first_seen": (promo.start + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z"),
             "segment": {"country": promo.scope["country"]}, "hypotheses": []}
    r = sc.score(events, [noisy], end, days)
    assert r["false_alarms"] == 1 and r["innocents_silent"] == r["innocents"] - 1


def test_cli_writes_the_report(truth, tmp_path):
    path, events, *_ = truth
    folder = tmp_path / "incidents"
    folder.mkdir()
    for n, f in enumerate(faults_of(events)):
        (folder / f"{n}.json").write_text(json.dumps(incident(n, f)))
    out = tmp_path / "results.md"
    assert sc.main(["score", "--db", str(path), "--incidents", str(folder), "--out", str(out)]) == 0
    text = out.read_text()
    assert "Faults detected | 4 of 4 (100%)" in text and "The data is simulated" in text

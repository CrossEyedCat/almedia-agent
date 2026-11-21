"""docs/results.md is built from scored runs. Seeds the brain never saw are kept apart from the seeds it was
built on, and the page says which is which. Every failure is listed: a miss, a wrong place, a cause that was
not confirmed, a second cause left standing beside the right one, a false alarm."""
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import summarize_evaluation as se
from sim import score as sc

ROOT = Path(__file__).resolve().parents[1]
AT = "2026-11-20T10:00:00Z"


def fault(kind, detected=True, delay=0.0, localized=True, cause=True, supported=1, incident="inc-1", **scope):
    p = {"kind": kind, "fault": {**scope, "start": AT}, "detected": detected}
    if detected:
        p.update(delay_h=delay, localized=localized, correct_cause=cause, incident=incident, supported_causes=supported)
    return p


def run(seed, per_fault, false_alarms=0, innocents=8, silent=8, days=49.0, alarms=None):
    r = dict(seed=seed, days=days, faults=len(per_fault), detected=sum(p["detected"] for p in per_fault),
             false_alarms=false_alarms, innocents=innocents, innocents_silent=silent, per_fault=per_fault)
    if alarms is not None:
        r["false_alarm_incidents"] = alarms
    return r


@pytest.fixture
def reports():
    """Seven faults in three runs: one missed, one in the wrong place, one without its cause, one tie."""
    return [
        run(4, [fault("payment_failure", country="DE", platform="Android"),
                fault("stockout_surge", cause=False, supported=0, category="sports"),
                fault("search_regression", delay=2.0, localized=False, country="FR")]),
        run(101, [fault("payment_failure", country="FR", platform="iOS"),
                  fault("search_regression", delay=1.0, country="ES")]),
        run(102, [fault("ad_budget_exhausted", detected=False, country="DE"),
                  fault("payment_failure", supported=2, incident="inc-7", country="PL", platform="Web")],
            false_alarms=1, silent=7),
    ]


def page(reports, **kw):
    return "\n".join(se.render(reports, **kw))


def section(text, title):
    return text.split(f"## {title}")[1].split("\n## ")[0]


# ------------------------------------------------------------------------------------ the summary table
def test_holdout_seeds_get_their_own_line_and_the_totals_add_up(reports):
    text = page(reports, holdout={101, 102}, commit="abc1234")
    assert "| Holdout (run once) | 2 | 3/4 | 3/3 | 3/3 | 1 | 15/16 | 0.33 |" in text
    assert "| Used while the brain was built | 1 | 3/3 | 2/3 | 2/3 | 0 | 8/8 | 0.67 |" in text
    assert "| All | 3 | 6/7 | 5/6 | 5/6 | 1 | 23/24 | 0.50 |" in text
    assert "brain at commit `abc1234`" in text
    assert "Holdout seeds (101, 102)" in text and "The other seeds (4)" in text


def test_without_holdout_seeds_the_page_claims_no_holdout(reports):
    text = page(reports)
    assert "| Holdout" not in text and "| Used while" not in text
    assert "None of these seeds is a holdout" in text
    assert "| All | 3 | 6/7 | 5/6 | 5/6 | 1 | 23/24 | 0.50 |" in text


def test_the_false_alarm_rate_is_per_day_of_data_unless_the_warm_up_is_given(reports):
    plain = page(reports, holdout={101, 102})
    assert "False alarms. holdout: 1 over 98 simulated days (0.010/day); all runs: 1 over 147 simulated days (0.007/day)." in plain
    watched = page(reports, holdout={101, 102}, warmup_hours=80)
    # 49 days less 80 hours is 45.67 watched days per run
    assert ("False alarms. holdout: 1 in 91 watched days (0.011/day); all runs: 1 in 137 watched days (0.007/day). "
            "Watched days leave out the first 80 hours of each run, before the replay's first judgement "
            "(98 and 147 days of data).") in watched


# ------------------------------------------------------------------------------------ the other tables
def test_fault_kinds_are_tallied_over_all_runs(reports):
    text = section(page(reports, holdout={101, 102}), "By fault kind (all runs)")
    assert "| payment_failure | 3/3 | 3/3 | 3/3 | 0.00 |" in text
    assert "| stockout_surge | 1/1 | 1/1 | 0/1 | 0.00 |" in text
    assert "| search_regression | 2/2 | 1/2 | 2/2 | 1.50 |" in text     # found twice, once in the wrong place
    assert "| ad_budget_exhausted | 0/1 | 0/0 | 0/0 | n/a |" in text


def test_every_run_has_its_row_and_holdout_runs_are_marked(reports):
    text = section(page(reports, holdout={101, 102}), "Per run")
    assert "| Seed 4, 49 days | 3/3 | 2/3 detected | 0 | 8/8 |" in text
    assert "| Seed 102, 49 days, holdout | 1/2 | 1/1 detected | 1 | 7/8 |" in text


# ------------------------------------------------------------------------------------ the lists of failures
def test_misses_wrong_places_and_unsupported_causes_are_listed_with_their_seed(reports):
    text = section(page(reports, holdout={101, 102}), "Misses and unsupported causes")
    assert f"- Seed 102 (holdout): ad_budget_exhausted at {AT}, country=DE: not detected (no matching monitor incident)." in text
    assert f"- Seed 4: stockout_surge at {AT}, category=sports: detected and localized; cause not confirmed." in text
    assert f"- Seed 4: search_regression at {AT}, country=FR: detected, not localized to the injected scope." in text
    assert "Seed 101" not in text and text.count("- Seed") == 3


def test_a_fault_with_nothing_right_but_the_detection_says_both(reports):
    text = page([run(7, [fault("payment_failure", localized=False, cause=False, supported=0, country="DE", platform="iOS")])])
    assert (f"- Seed 7: payment_failure at {AT}, country=DE, platform=iOS: detected, not localized to the injected scope; "
            "cause not confirmed.") in text


def test_a_report_that_supports_a_second_cause_is_counted_and_listed(reports):
    text = page(reports, holdout={101, 102})
    assert "In 1 of the 5 reports with the correct cause a second cause is supported beside it (listed below)." in text
    ties = section(text, "Reports that support more than one cause")
    assert (f"- Seed 102 (holdout): payment_failure at {AT}, country=PL, platform=Web, incident inc-7: the right cause is "
            "supported, and 1 other beside it.") in ties
    assert ties.count("- Seed") == 1
    clean = page(reports[:2])
    assert "None." in section(clean, "Reports that support more than one cause") and "a second cause is supported" not in clean


def test_supported_causes_that_are_all_wrong_are_said_to_be_wrong_and_are_not_a_tie():
    alarms = [{"id": "inc-8", "first_seen": AT, "metric": "ad_ctr", "segment": {}, "status": "resolved"},
              {"id": "inc-9", "first_seen": AT, "metric": "take_rate", "segment": {}, "status": "open"}]
    reports = [run(1, [fault("payment_failure", cause=False, supported=2, incident="inc-1", country="DE", platform="iOS"),
                       fault("payment_failure", supported=3, incident="inc-2", country="PL", platform="Web")],
                   false_alarms=2, alarms=alarms)]
    text = page(reports)
    assert "In 1 of the 1 reports with the correct cause" in text
    ties = section(text, "Reports that support more than one cause")
    assert ties.count("- Seed") == 1 and "incident inc-2: the right cause is supported, and 2 other beside it." in ties
    misses = section(text, "Misses and unsupported causes")
    assert (f"- Seed 1: payment_failure at {AT}, country=DE, platform=iOS: detected and localized; "
            "2 cause(s) supported, none of them the injected one.") in misses
    assert section(text, "False alarms").count("- Seed 1") == 2


def test_false_alarms_are_listed_with_what_the_incident_said(reports):
    reports[2]["false_alarm_incidents"] = [{"id": "inc-0001", "first_seen": "2026-11-05T20:00:00Z", "metric": "ad_ctr",
                                            "segment": {"country": "PL", "platform": "Web"}, "status": "resolved"}]
    text = section(page(reports, holdout={101, 102}), "False alarms")
    assert "- Seed 102 (holdout): inc-0001, ad_ctr in country=PL, platform=Web, first seen 2026-11-05T20:00:00Z (resolved)." in text


def test_a_false_alarm_the_scorer_did_not_itemise_is_still_counted_on_the_page(reports):
    text = section(page(reports), "False alarms")            # seed 102 has one, without details
    assert "- Seed 102: 1 false alarm(s), not itemised by the scorer." in text
    quiet = section(page(reports[:2]), "False alarms")
    assert "None." in quiet and "Seed" not in quiet


def test_the_limits_say_what_a_holdout_of_the_same_simulator_does_not_test(reports):
    limits = section(page(reports, holdout={101, 102}), "Limits")
    assert "new random seeds of the same simulator" in limits and "does not test other kinds of fault" in limits
    assert "for every fault" not in limits                 # routine deployments are not near every fault
    assert "only move the arrival time of a source" in limits and "Data that never arrives is not tested" in limits
    assert "does not check which release of that kind" in limits


# ------------------------------------------------------------------------------------ files and arguments
def test_the_page_is_written_as_utf8_with_lf_line_ends(reports, tmp_path):
    out = tmp_path / "docs" / "results.md"
    meta = dict(holdout=[101], commit="abc1234", warmup_hours=80)
    se.write(out, se.render(reports, **meta), reports, meta)
    raw = out.read_bytes()
    assert b"\r\n" not in raw and "Results — simulated marketplace" in raw.decode("utf-8")
    assert b"\r\n" not in out.with_suffix(".json").read_bytes()
    saved = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert [r["seed"] for r in saved["runs"]] == [4, 101, 102]
    assert (saved["holdout"], saved["commit"], saved["warmup_hours"]) == ([101], "abc1234", 80)


def test_the_page_can_be_rebuilt_from_its_json(reports, tmp_path):
    out = tmp_path / "results.md"
    meta = dict(holdout=[101, 102], commit="abc1234", warmup_hours=80)
    se.write(out, se.render(reports, **meta), reports, meta)
    assert se.from_json(out.with_suffix(".json")) == out.read_text(encoding="utf-8").splitlines()


def test_the_tracked_page_is_what_the_script_makes_of_the_tracked_numbers():
    """docs/results.md is never edited by hand: it is the script's rendering of docs/results.json."""
    assert se.from_json(ROOT / "docs" / "results.json") == (ROOT / "docs" / "results.md").read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("args, message", [
    (["--seeds", "4", "--holdout", "5"], "holdout seed 5 is not among the runs"),
    (["--seeds", "4,4"], "seeds must be distinct"),
    (["--seeds", ""], "no seeds given"),
    (["--seeds", "4,x"], "seeds must be whole numbers"),
    (["--seeds", "4"], "missing database"),
    (["--seeds", "4", "--warmup-hours=-1"], "warm-up must be a number of hours from 0"),
    (["--seeds", "4", "--warmup-hours", "nan"], "warm-up must be a number of hours from 0"),
    (["--seeds", "4", "--out", "page.json"], "the page cannot be a .json file"),
])
def test_arguments_that_cannot_give_a_page_are_refused_and_nothing_is_written(tmp_path, capsys, args, message):
    out = tmp_path / "r.md"
    args = [str(tmp_path / a) if a == "page.json" else a for a in args]
    with pytest.raises(SystemExit) as stop:
        se.main(["--runs", str(tmp_path), "--out", str(out)] + args)       # a later --out in args wins
    assert stop.value.code == 2 and message in capsys.readouterr().err
    assert not out.exists() and not list(tmp_path.glob("*.json"))


def test_a_warm_up_as_long_as_a_run_is_refused(tmp_path, capsys):
    runs, out = tmp_path / "runs", tmp_path / "r.md"
    sc.generate(runs / "seed3.db", faults=4, innocents=4, seed=3)
    (runs / "seed3-incidents").mkdir()
    (runs / "seed3-incidents" / "inc-1.json").write_text(json.dumps(
        {"id": "inc-1", "first_seen": "2026-11-01T00:00:00Z", "segment": {}, "hypotheses": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        se.main(["--runs", str(runs), "--seeds", "3", "--warmup-hours", "2000", "--out", str(out)])
    assert stop.value.code == 2 and "warm-up is not shorter than run 3" in capsys.readouterr().err and not out.exists()


def test_a_page_that_cannot_be_completed_leaves_the_old_page_and_its_json_alone(reports, tmp_path, monkeypatch):
    out = tmp_path / "results.md"
    se.write(out, se.render(reports), reports)
    before = out.read_bytes(), out.with_suffix(".json").read_bytes()

    def locked(*a):
        raise PermissionError("locked")
    monkeypatch.setattr(se.os, "replace", locked)
    with pytest.raises(PermissionError):
        se.write(out, se.render(reports[:1]), reports[:1])
    assert (out.read_bytes(), out.with_suffix(".json").read_bytes()) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["results.json", "results.md"]     # no temporary file is left


def test_two_runs_are_scored_from_their_own_files_and_the_json_rebuilds_the_page(tmp_path):
    runs = tmp_path / "runs"
    for seed, hits in ((3, 4), (5, 2)):
        path = sc.generate(runs / f"seed{seed}.db", faults=4, innocents=4, seed=seed)
        events, _, _ = sc.load_truth(path)
        folder = runs / f"seed{seed}-incidents"
        folder.mkdir()
        faults = [e for e in events if e.kind in sc.FAULT_KINDS]
        for n, f in enumerate(faults[:hits]):
            body = {"id": f"inc-{n}", "first_seen": (f.start + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z"),
                    "segment": {d: f.scope[d] for d in sc.DIMS if d in f.scope}, "hypotheses": []}
            (folder / f"inc-{n}.json").write_text(json.dumps(body), encoding="utf-8")
    out = tmp_path / "results.md"
    assert se.main(["--runs", str(runs), "--seeds", "3,5", "--holdout", "5", "--commit", "abc1234", "--warmup-hours", "80",
                    "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| Seed 3, 49 days | 4/4 | 0/4 detected | 0 |" in text
    assert "| Seed 5, 49 days, holdout | 2/4 | 0/2 detected | 0 |" in text
    saved = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert (saved["holdout"], saved["commit"], saved["warmup_hours"]) == ([5], "abc1234", 80)
    assert se.from_json(out.with_suffix(".json")) == text.splitlines()


def test_a_run_without_incident_files_is_an_error_not_a_measured_zero(tmp_path, capsys):
    sc.generate(tmp_path / "seed3.db", faults=4, innocents=4, seed=3)
    out = tmp_path / "r.md"
    with pytest.raises(SystemExit) as stop:
        se.main(["--runs", str(tmp_path), "--seeds", "3", "--out", str(out)])
    assert stop.value.code == 2 and "incidents folder does not exist" in capsys.readouterr().err and not out.exists()


def test_real_files_are_scored_and_summarized(tmp_path):
    """One generated truth and made-up incidents for its faults, through the command line."""
    runs = tmp_path / "runs"
    path = sc.generate(runs / "seed3.db", faults=4, innocents=4, seed=3)
    events, _, _ = sc.load_truth(path)
    folder = runs / "seed3-incidents"
    folder.mkdir()
    faults = [e for e in events if e.kind in sc.FAULT_KINDS]
    for n, f in enumerate(faults):
        body = {"id": f"inc-{n}", "first_seen": (f.start + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z"),
                "segment": {d: f.scope[d] for d in sc.DIMS if d in f.scope},
                "hypotheses": [{"cause": "nothing that names a cause", "kind": "metric", "status": "plausible"}]}
        (folder / f"inc-{n}.json").write_text(json.dumps(body), encoding="utf-8")
    # a stray incident on a clean day; its own top-level metric is the outcome, its driver is what moved
    stray = {"id": "inc-stray", "first_seen": (faults[0].start - pd.Timedelta(days=5)).strftime("%Y-%m-%dT%H:00:00Z"),
             "segment": {"country": "PL"}, "status": "resolved", "metric": "contribution",
             "drivers": [{"metric": "ad_ctr"}], "hypotheses": []}
    (folder / "inc-stray.json").write_text(json.dumps(stray), encoding="utf-8")
    out = tmp_path / "results.md"
    assert se.main(["--runs", str(runs), "--seeds", "3", "--holdout", "3", "--commit", "abc1234", "--warmup-hours", "80",
                    "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| Holdout (run once) | 1 | 4/4 | 4/4 | 0/4 | 1 | 4/4 | 1.00 |" in text
    assert "| Used while" not in text and "The other seeds" not in text       # every seed here is a holdout
    assert text.count("cause not confirmed") == 4
    assert f"- Seed 3 (holdout): inc-stray, ad_ctr in country=PL, first seen {stray['first_seen']} (resolved)." in text
    assert "False alarms. all runs: 1 in 46 watched days (0.022/day)." in text

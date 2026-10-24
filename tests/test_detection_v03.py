import pandas as pd

from analytics.detection.common import add_artefact_flag_pandas
from analytics.detection.evaluate import evaluate
from analytics.detection.gold_input import load_detector_input


def _series(completeness):
    n = len(completeness)
    return pd.DataFrame({"district_code": "01", "disease": "dengue",
                         "week_start_date": pd.date_range("2022-01-03", periods=n, freq="7D"),
                         "reporting_completeness": completeness})


def test_completeness_flags_only_first_full_week_after_silence():
    df = add_artefact_flag_pandas(_series([None, 1.0, 0.5, 0.5, 1.0, 1.0]))
    assert df["reporting_artefact_flag"].tolist() == [False, False, False, False, True, True]
    assert df["_under"].tolist() == [False, False, True, True, False, False]


def test_null_completeness_is_not_a_silence():
    df = add_artefact_flag_pandas(_series([None, None, 1.0]))
    assert not df["reporting_artefact_flag"].any() and not df["_under"].any()


def test_legacy_facility_counts_still_work():
    df = _series([1.0] * 4).drop(columns="reporting_completeness")
    df["expected_facilities"] = 10
    df["reporting_facilities"] = [10, 5, 10, 10]
    assert add_artefact_flag_pandas(df)["reporting_artefact_flag"].tolist() == [False, False, True, True]


def test_window_grace_counts_late_report_week_as_in_window():
    truth = pd.DataFrame([dict(outbreak_id="OB1", district_code="01", disease="dengue",
                               start_date=pd.Timestamp("2022-02-14"), end_date=pd.Timestamp("2022-03-06"))])
    weeks = pd.date_range("2022-01-03", periods=14, freq="7D")
    alerts = pd.DataFrame({"district_code": "01", "disease": "dengue", "week_start_date": weeks,
                           "alert_flag": [w == pd.Timestamp("2022-03-14") for w in weeks]})
    assert evaluate(alerts, truth)["false_alert_episodes"] == 1
    m = evaluate(alerts, truth, window_grace_days=14)
    assert m["false_alert_episodes"] == 0 and m["sensitivity"] == 1.0


def test_loader_renames_and_joins_status(tmp_path):
    pd.DataFrame({"district_code": ["1"], "disease": ["dengue"], "report_week_start_date": ["2022-01-03"],
                  "reported_cases": [7], "late_cases_ge8d": [1], "report_epi_year": [2022],
                  "report_epi_week": [1]}).to_csv(tmp_path / "gold_weekly_reports.csv", index=False)
    pd.DataFrame({"district_code": ["01"], "report_epi_year": [2022], "report_epi_week": [1],
                  "reporting_completeness": [0.9]}).to_csv(tmp_path / "gold_reporting_status.csv", index=False)
    out = load_detector_input(tmp_path)
    assert out.loc[0, "district_code"] == "01" and out.loc[0, "cases"] == 7
    assert out.loc[0, "reporting_completeness"] == 0.9


from analytics.detection.evaluate import artefact_check, per_outbreak_context, threshold_sweep


def _scored(scores, cases, start="2022-01-03"):
    n = len(scores)
    return pd.DataFrame({"district_code": "01", "disease": "dengue",
                         "week_start_date": pd.date_range(start, periods=n, freq="7D"),
                         "score": scores, "cases": cases})


def _truth():
    return pd.DataFrame([dict(outbreak_id="OB1", district_code="01", disease="dengue",
                              start_date=pd.Timestamp("2022-03-07"), end_date=pd.Timestamp("2022-03-20"))])


def test_count_floor_removes_alerts_from_tiny_counts():
    s = _scored([0, 0, 6, 0, 0, 0, 0, 0, 0, 0], [1, 1, 2, 1, 1, 1, 1, 1, 1, 1])
    assert threshold_sweep(s, _truth(), [3.0])["false_alert_episodes"].iloc[0] == 1
    swept = threshold_sweep(s, _truth(), [3.0], min_cases=5)
    assert swept["false_alert_episodes"].iloc[0] == 0 and swept["min_cases"].iloc[0] == 5


def test_floor_needs_cases_column():
    import pytest
    with pytest.raises(ValueError):
        threshold_sweep(_scored([0, 0], [1, 1]).drop(columns="cases"), _truth(), [1.0], min_cases=3)


def test_artefact_check_counts_raw_and_effective_alerts():
    weeks = pd.date_range("2022-01-03", periods=6, freq="7D")
    alerts = pd.DataFrame({"district_code": "01", "disease": "dengue", "week_start_date": weeks,
                           "alert_flag": [False, False, False, False, True, False],
                           "reporting_artefact_flag": [False, False, False, False, True, False]})
    art = pd.DataFrame([dict(district_code="01", disease="dengue", silent_start=weeks[2],
                             silent_end=weeks[3], backfill_week_start=weeks[4])])
    out = artefact_check(alerts, art).iloc[0]
    assert out["raw_alert_weeks"] == 1 and out["effective_alert_weeks"] == 0
    assert bool(out["backfill_week_flagged"])


def test_context_shows_alert_already_firing_before_outbreak():
    weeks = pd.date_range("2022-01-03", periods=12, freq="7D")
    flag = [w in (pd.Timestamp("2022-02-21"), pd.Timestamp("2022-02-28")) for w in weeks] 
    alerts = pd.DataFrame({"district_code": "01", "disease": "dengue", "week_start_date": weeks,
                           "alert_flag": flag})
    out = per_outbreak_context(alerts, _truth()).iloc[0]
    assert out["pre_alert_weeks"] == 2


def test_backfill_weeks_parameter_and_default():
    s = [None, 1.0, 0.5, 0.5, 1.0, 1.0]
    one = add_artefact_flag_pandas(_series(s), backfill_weeks=1)["reporting_artefact_flag"].tolist()
    assert one == [False, False, False, False, True, False]
    two = add_artefact_flag_pandas(_series(s))["reporting_artefact_flag"].tolist()
    assert two == [False, False, False, False, True, True]

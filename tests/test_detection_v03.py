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
    assert df["reporting_artefact_flag"].tolist() == [False, False, False, False, True, False]
    assert df["_under"].tolist() == [False, False, True, True, False, False]


def test_null_completeness_is_not_a_silence():
    df = add_artefact_flag_pandas(_series([None, None, 1.0]))
    assert not df["reporting_artefact_flag"].any() and not df["_under"].any()


def test_legacy_facility_counts_still_work():
    df = _series([1.0] * 4).drop(columns="reporting_completeness")
    df["expected_facilities"] = 10
    df["reporting_facilities"] = [10, 5, 10, 10]
    assert add_artefact_flag_pandas(df)["reporting_artefact_flag"].tolist() == [False, False, True, False]


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

"""Tests for EARS C2, CUSUM and the reporting-artefact rule, on the tests/fixtures data."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.detection.common import add_artefact_flag_pandas
from analytics.detection.cusum import cusum_pandas, cusum_scores
from analytics.detection.ears_c2 import ears_c2_pandas
from analytics.detection.evaluate import evaluate, load_truth

FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def cases():
    return pd.read_csv(FIX / "gold_weekly_cases_fixture.csv", dtype={"district_code": str})


@pytest.fixture(scope="module")
def truth():
    return load_truth(FIX / "ground_truth_fixture.csv")


@pytest.fixture(scope="module")
def artefact():
    return pd.read_csv(FIX / "artefact_fixture.csv", dtype={"district_code": str}).iloc[0]


def _artefact_row(alerts, art):
    return alerts[(alerts.district_code == art.district_code) & (alerts.disease == art.disease)
                  & (alerts.week_start_date == art.backfill_week_start)].iloc[0]


def test_artefact_flag_is_set_only_on_the_backfill_week(cases, artefact):
    d = add_artefact_flag_pandas(cases, backfill_weeks=1)
    flagged = d[d.reporting_artefact_flag]
    assert len(flagged) == 1
    r = flagged.iloc[0]
    assert (r.district_code, r.disease, r.week_start_date) == (
        artefact.district_code, artefact.disease, artefact.backfill_week_start)


@pytest.mark.parametrize("fn", [ears_c2_pandas, cusum_pandas])
def test_backfill_spike_is_raised_but_not_counted(cases, artefact, fn):
    row = _artefact_row(fn(cases), artefact)
    assert row.alert_flag and row.reporting_artefact_flag


def test_naive_detector_counts_the_artefact_robust_does_not(cases, truth, artefact):
    robust = ears_c2_pandas(cases, mask=True)
    naive = ears_c2_pandas(cases, mask=False).assign(reporting_artefact_flag=False)
    r_naive = _artefact_row(naive, artefact)
    r_robust = _artefact_row(robust, artefact)
    assert r_naive.alert_flag and not r_naive.reporting_artefact_flag      # counted: false alarm
    assert r_robust.alert_flag and r_robust.reporting_artefact_flag        # suppressed
    assert evaluate(robust, truth)["false_alert_episodes"] < evaluate(naive, truth)["false_alert_episodes"]


@pytest.mark.parametrize("fn", [ears_c2_pandas, cusum_pandas])
def test_all_injected_outbreaks_are_detected(cases, truth, fn):
    assert evaluate(fn(cases), truth)["sensitivity"] == 1.0


def test_output_has_gold_alerts_columns(cases):
    a = ears_c2_pandas(cases)
    for c in ["district_code", "disease", "epi_year", "epi_week", "detector", "score", "threshold",
              "alert_flag", "severity", "reporting_artefact_flag", "run_id"]:
        assert c in a.columns
    assert set(a.detector) == {"ears_c2"}
    assert a.loc[~a.alert_flag, "severity"].isna().all()


def test_early_weeks_have_no_score(cases):
    a = ears_c2_pandas(cases)
    first = a.sort_values("week_start_date").groupby(["district_code", "disease"]).head(5)
    assert first.score.isna().all() and not first.alert_flag.any()


def test_cusum_recursion_and_cap():
    s = cusum_scores(np.array([2.0, 2.0, -5.0, np.nan, 100.0]), k=1.0, z_cap=6.0)
    assert s.tolist() == [1.0, 2.0, 0.0, 0.0, 5.0]


def test_spark_matches_pandas(cases):
    """Needs pyspark + Java + HADOOP_HOME. Skipped where Spark is not available."""
    pytest.importorskip("pyspark")
    from pipeline.spark_session import get_spark
    from analytics.detection.cusum import cusum_spark
    from analytics.detection.ears_c2 import ears_c2_spark
    try:
        spark = get_spark()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Spark unavailable: {e}")
    cols = ["district_code", "disease", "epi_year", "epi_week", "week_start_date", "cases",
            "reporting_facilities", "expected_facilities"]
    pdf = cases[cols].copy()
    pdf["week_start_date"] = pd.to_datetime(pdf["week_start_date"]).dt.date
    sdf = spark.createDataFrame(pdf)
    keys = ["district_code", "disease", "week_start_date"]
    for spark_fn, pandas_fn in [(ears_c2_spark, ears_c2_pandas), (cusum_spark, cusum_pandas)]:
        got = spark_fn(sdf).toPandas()
        want = pandas_fn(cases)
        got["week_start_date"] = pd.to_datetime(got["week_start_date"])
        want["week_start_date"] = pd.to_datetime(want["week_start_date"])
        m = got.merge(want, on=keys, suffixes=("_s", "_p"))
        assert len(m) == len(want)
        assert np.allclose(m.score_s.astype(float), m.score_p.astype(float), equal_nan=True, atol=1e-6)
        assert (m.alert_flag_s == m.alert_flag_p).all()
        assert (m.reporting_artefact_flag_s == m.reporting_artefact_flag_p).all()
    spark.stop()

"""Spark and pandas must agree for the 'last 7 usable weeks' EARS C2 baseline (AN-1, AN-3).

Synthetic series with under-reporting stretches (so weeks are masked), missing completeness values and
random spikes. Needs pyspark + Java; skipped where Spark is not available.
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from analytics.detection.variants import ears_c2_usable_pandas


def _series():
    rng = np.random.default_rng(1)
    rows = []
    for dcode in ["01", "02", "03"]:
        for dis in ["dengue", "ili"]:
            comp = np.ones(80)
            for s in rng.integers(10, 70, size=3):
                comp[s:s + rng.integers(1, 5)] = 0.5          # silent stretch, then back-fill
            cases = rng.poisson(20, 80) + (rng.random(80) < 0.05) * 60
            for i in range(80):
                wk = dt.date(2024, 1, 1) + dt.timedelta(weeks=i)
                rows.append(dict(district_code=dcode, disease=dis, epi_year=wk.year, epi_week=i % 52 + 1,
                                 week_start_date=wk, cases=int(cases[i]),
                                 reporting_completeness=float(comp[i])))
    df = pd.DataFrame(rows)
    df.loc[df.sample(5, random_state=0).index, "reporting_completeness"] = np.nan
    return df


@pytest.fixture(scope="module")
def df():
    return _series()


def test_usable_spark_matches_pandas(df):
    pytest.importorskip("pyspark")
    from pipeline.spark_session import get_spark
    from analytics.detection.variants import ears_c2_usable_spark
    try:
        spark = get_spark()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Spark unavailable: {e}")
    got = ears_c2_usable_spark(spark.createDataFrame(df), threshold=3.0).toPandas()
    want = ears_c2_usable_pandas(df, threshold=3.0)
    keys = ["district_code", "disease", "week_start_date"]
    got["week_start_date"] = pd.to_datetime(got["week_start_date"])
    want["week_start_date"] = pd.to_datetime(want["week_start_date"])
    m = got.merge(want, on=keys, suffixes=("_s", "_p"))
    assert len(m) == len(want)
    assert (m.score_s.isna() == m.score_p.isna()).all()
    assert np.allclose(m.score_s.astype(float), m.score_p.astype(float), equal_nan=True, atol=1e-6)
    assert (m.alert_flag_s == m.alert_flag_p).all()
    assert (m.reporting_artefact_flag_s == m.reporting_artefact_flag_p).all()
    assert (m.severity_s.fillna("-") == m.severity_p.fillna("-")).all()
    assert m.alert_flag_p.sum() > 0 and m.reporting_artefact_flag_p.sum() > 0   # the test is not vacuous
    spark.stop()


def test_only_warm_up_weeks_are_unscored(df):
    """Masked stretches are bridged: here every unscored week is one of the first 7 of its series."""
    a = ears_c2_usable_pandas(df).sort_values(["district_code", "disease", "week_start_date"])
    a["pos"] = a.groupby(["district_code", "disease"]).cumcount()
    assert (a.loc[a.score.isna(), "pos"] < 7).all()
    assert a.loc[a.pos >= 7, "score"].notna().all()

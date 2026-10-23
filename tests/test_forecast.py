"""Tests for analytics/forecast/metrics.py and forecast.py (synthetic data, no repo data files needed)."""
import numpy as np
import pandas as pd
import pytest

from analytics.forecast.forecast import (OUT_COLS, _nb_forecast, fit_nb, forecast_series, make_frame,
                                         run_pandas, score_rows, summarise, to_eval_metrics)
from analytics.forecast.metrics import covered, interval_score, nb_quantiles, wis


def test_wis_inside_interval():
    assert wis(10, 10, 5, 15) == pytest.approx((0 + 0.1 * 10) / 1.5)


def test_wis_outside_interval_is_penalised():
    # IS = 10 + (2/0.2)*(20-15) = 60 ; WIS = (0.5*10 + 0.1*60)/1.5
    assert interval_score(20, 5, 15) == pytest.approx(60.0)
    assert wis(20, 10, 5, 15) == pytest.approx(11 / 1.5)


def test_coverage_and_quantiles():
    assert covered([1, 5, 9], [2, 2, 2], [8, 8, 8]).tolist() == [False, True, False]
    q10, q50, q90 = nb_quantiles(50.0, 0.2)
    assert q10 < q50 < q90 and 40 <= q50 <= 60


def test_fit_nb_recovers_coefficients():
    rng = np.random.default_rng(1)
    n = 3000
    X = np.column_stack([np.ones(n), rng.normal(size=n), rng.normal(size=n)])
    beta_true, alpha_true = np.array([2.0, 0.5, -0.3]), 0.2
    mu = np.exp(X @ beta_true)
    r = 1 / alpha_true
    y = rng.negative_binomial(r, r / (r + mu)).astype(float)
    beta, alpha = fit_nb(X, y)
    assert np.allclose(beta, beta_true, atol=0.08)
    assert abs(alpha - alpha_true) < 0.07


def _series(weeks=260, planted=True, seed=3, code="01", disease="dengue"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2019-01-07", periods=weeks, freq="7D")
    df = pd.DataFrame(dict(district_code=code, disease=disease, week_start_date=idx,
                           cases=1.0, precip_sum_mm=rng.gamma(2.0, 30.0, weeks),
                           temp_mean_c=26 + 3 * np.sin(2 * np.pi * np.arange(weeks) / 52) + rng.normal(0, .5, weeks)))
    f, _ = make_frame(df)
    rain = f["rain"].to_numpy()[:weeks]
    z = (rain - np.nanmean(rain)) / np.nanstd(rain)
    z = np.nan_to_num(z)
    mu = np.exp(3.0 + (0.7 * z if planted else 0.0))
    r = 15.0
    df["cases"] = rng.negative_binomial(r, r / (r + mu)).astype(float)
    return df


def test_output_columns_and_row_counts():
    fc = forecast_series(_series())
    assert list(fc.columns) == OUT_COLS
    live = fc[~fc.is_backtest]
    assert set(live.horizon_weeks) == {1, 2, 3, 4} and live.actual_cases.isna().all()
    back = fc[fc.is_backtest]
    assert back.groupby(["model", "horizon_weeks"]).size().eq(52).all()
    assert (fc.q10 <= fc.q50).all() and (fc.q50 <= fc.q90).all()


def test_nb_beats_seasonal_naive_when_weather_drives_cases():
    fc = forecast_series(_series(planted=True))
    s = summarise(fc)
    row = s[(s.horizon_weeks == "all")].set_index("model")
    assert row.loc["nb_lagged_weather", "wis"] < row.loc["seasonal_naive", "wis"]
    assert row.loc["nb_lagged_weather", "mae"] < row.loc["seasonal_naive", "mae"]
    assert 0.6 <= row.loc["nb_lagged_weather", "coverage_80"] <= 0.97


def test_no_leakage_from_after_the_origin():
    df = _series()
    f, n = make_frame(df)
    o, h = n - 20, 3
    base = _nb_forecast(f, f["cases"].to_numpy(), o, h, 52)
    df2 = df.copy()
    df2.loc[df2.index > o, "cases"] = 9999.0           # change everything observed after the origin
    df2.loc[df2.index > o, "precip_sum_mm"] = 9999.0
    f2, _ = make_frame(df2)
    again = _nb_forecast(f2, f2["cases"].to_numpy(), o, h, 52)
    assert base[0] == pytest.approx(again[0])


def test_run_pandas_and_eval_metrics_format():
    table = pd.concat([_series(code="01"), _series(code="02", seed=4, disease="diarrhoeal")])
    fc = run_pandas(table)
    assert set(fc.district_code) == {"01", "02"}
    rows = score_rows(fc)
    assert rows.groupby(["district_code", "disease", "origin_epi_year", "origin_epi_week",
                         "horizon_weeks"])["model"].nunique().eq(2).all()
    m = to_eval_metrics(summarise(fc), "run1")
    assert set(m.columns) == {"run_id", "component", "metric", "value", "model_or_detector", "threshold"}
    assert {"mae", "coverage_80", "wis", "wis_h1"} <= set(m.metric)


def test_non_contiguous_weeks_raise():
    df = _series().drop(index=10)
    with pytest.raises(ValueError):
        make_frame(df)


def test_spark_matches_pandas():
    """Needs pyspark + Java + HADOOP_HOME. Skipped where Spark is not available."""
    pytest.importorskip("pyspark")
    from pipeline.spark_session import get_spark
    from analytics.forecast.forecast import run_spark
    try:
        spark = get_spark()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Spark unavailable: {e}")
    table = _series(weeks=200)
    table["week_start_date"] = pd.to_datetime(table["week_start_date"]).dt.date
    want = run_pandas(table, n_backtest=8)
    got = run_spark(spark.createDataFrame(table), n_backtest=8).toPandas()
    key = ["district_code", "disease", "origin_epi_year", "origin_epi_week", "horizon_weeks", "model"]
    m = got.merge(want, on=key, suffixes=("_s", "_p"))
    assert len(m) == len(want) == len(got)
    assert np.allclose(m.pred_mean_s, m.pred_mean_p, rtol=1e-6)
    spark.stop()

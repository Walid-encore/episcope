"""AN-4 / ENG-4: a forecast made at origin o must not change when data after o changes.

Run:  python -m pytest tests/test_forecast_leakage.py -q
"""
import numpy as np
import pandas as pd
import pytest

from analytics.forecast.forecast import (ABLATION_MODELS, DEFAULT_MODELS, _runners, forecast_series,
                                         make_frame, summarise)

N = 150
ORIGIN = 110


def _series(seed=0, n=N):
    rng = np.random.default_rng(seed)
    wk = pd.date_range("2021-01-04", periods=n, freq="7D")
    rain = rng.gamma(2.0, 20.0, n)
    temp = 26 + 4 * np.sin(2 * np.pi * np.arange(n) / 52) + rng.normal(0, 0.5, n)
    mu = 8 + 6 * np.sin(2 * np.pi * np.arange(n) / 52) + 0.05 * np.roll(rain, 5)
    cases = rng.negative_binomial(5, 5 / (5 + np.maximum(mu, 1)))
    return pd.DataFrame(dict(district_code="01", disease="dengue", week_start_date=wk, cases=cases,
                             precip_sum_mm=rain, temp_mean_c=temp))


def _all_forecasts(s, o):
    f, _ = make_frame(s)
    run = _runners(f, f["cases"].to_numpy(), 52)
    return {(m, h): run[m](o, h) for m in ABLATION_MODELS for h in (1, 2, 3, 4)}


def test_future_cases_do_not_change_forecast():
    s = _series()
    base = _all_forecasts(s, ORIGIN)
    s2 = s.copy()
    s2.loc[s2.index > ORIGIN, "cases"] = 999          # corrupt everything after the origin week
    alt = _all_forecasts(s2, ORIGIN)
    for k, v in base.items():
        assert v is not None, k
        assert np.allclose(v, alt[k]), f"leak in {k}"


def test_future_weather_does_not_change_forecast():
    s = _series()
    base = _all_forecasts(s, ORIGIN)
    s2 = s.copy()
    s2.loc[s2.index > ORIGIN, ["precip_sum_mm", "temp_mean_c"]] = 999.0
    alt = _all_forecasts(s2, ORIGIN)
    for k, v in base.items():
        assert np.allclose(v, alt[k]), f"weather leak in {k}"


def test_backtest_rows_and_summary():
    fc = forecast_series(_series(), n_backtest=20, models=ABLATION_MODELS)
    back = fc[fc["is_backtest"]]
    assert set(back["model"]) == set(ABLATION_MODELS)
    assert back.groupby("model").size().nunique() == 1          # every model scored on every target
    s = summarise(fc)
    assert {"wis_ratio_vs_naive", "wis_ratio_vs_persistence"} <= set(s.columns)
    assert (fc["q10"] <= fc["q90"]).all()


def test_default_models_unchanged():
    fc = forecast_series(_series(), n_backtest=10)
    assert set(fc["model"]) == set(DEFAULT_MODELS)
    assert "wis_ratio_vs_persistence" not in summarise(fc).columns


def test_unknown_model_rejected():
    with pytest.raises(ValueError):
        forecast_series(_series(), models=("nope",))


def test_asof_cases_ignore_unreported():
    from analytics.forecast.forecast import _asof_cases
    n, K = 30, 3
    final = np.arange(n, dtype=float) + 10
    cum = np.stack([final - 3, final - 2, final - 1, final], axis=1)   # reported within 0,1,2,3 weeks
    cases = np.concatenate([final, np.full(4, np.nan)])
    c = _asof_cases(cases, cum, o=20)
    assert c[20] == cum[20, 0] and c[19] == cum[19, 1] and c[18] == cum[18, 2] and c[17] == final[17]
    cum2 = cum.copy()
    cum2[20, 1:], cum2[19, 2:], cum2[21:, :] = 999, 999, 999           # reports that had not arrived yet
    assert np.array_equal(_asof_cases(cases, cum2, o=20)[:21], c[:21])


def test_asof_mode_runs_and_scores_final_counts():
    s = _series()
    rng = np.random.default_rng(3)
    cases = s["cases"].to_numpy()
    lag = rng.multinomial(1, [0.5, 0.25, 0.15, 0.1], size=len(s)).argmax(1)   # crude per-week split
    parts = np.stack([np.floor(cases * w) for w in (0.5, 0.25, 0.15)], axis=1)
    parts = np.concatenate([parts, (cases - parts.sum(1))[:, None]], axis=1)
    cum = np.cumsum(parts, axis=1)
    for k in range(4):
        s[f"cum_lag{k}"] = cum[:, k]
    fc_asof = forecast_series(s, n_backtest=15, models=("persistence", "nb_lagged_weather"))
    fc_final = forecast_series(s.drop(columns=[f"cum_lag{k}" for k in range(4)]), n_backtest=15,
                               models=("persistence", "nb_lagged_weather"))
    a, b = fc_asof[fc_asof.is_backtest], fc_final[fc_final.is_backtest]
    assert np.array_equal(a["actual_cases"].to_numpy(), b["actual_cases"].to_numpy())   # same final targets
    assert not np.allclose(a["pred_mean"], b["pred_mean"])                              # inputs differ

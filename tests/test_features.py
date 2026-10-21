"""Tests for analytics/forecast/features.py using small synthetic daily weather."""
import numpy as np
import pandas as pd
import pytest

from analytics.forecast.features import (add_lags, build_model_table, build_weekly_weather,
                                         daily_to_weekly, lag_correlation)


def _daily(start="2020-12-30", end="2021-03-31", codes=("01", "02")):
    days = pd.date_range(start, end)
    rows = []
    for c in codes:
        for i, d in enumerate(days):
            rows.append(dict(district_code=c, date=d.date().isoformat(), precip_mm=float(i % 5),
                             temp_mean_c=25.0 + (i % 3), humidity_pct=80.0))
    return pd.DataFrame(rows)


def test_partial_weeks_dropped_and_iso_year_used():
    w = daily_to_weekly(_daily())
    first = w[w.district_code == "01"].iloc[0]
    # 2020-12-30 is a Wednesday, so ISO 2020-W53 is partial and dropped; first full week starts 2021-01-04
    assert first.week_start_date == pd.Timestamp("2021-01-04") and (first.epi_year, first.epi_week) == (2021, 1)
    assert (w.week_start_date.dt.weekday == 0).all()


def test_iso_year_for_early_january_week():
    daily = _daily(start="2020-12-28", end="2021-01-10", codes=("01",))
    w = daily_to_weekly(daily)
    assert (w.epi_year.iloc[0], w.epi_week.iloc[0]) == (2020, 53)
    assert w.week_start_date.iloc[0] == pd.Timestamp("2020-12-28")


def test_weekly_aggregates():
    daily = _daily(start="2021-01-04", end="2021-01-10", codes=("01",))
    w = daily_to_weekly(daily).iloc[0]
    assert w.precip_sum_mm == daily.precip_mm.sum()
    assert w.precip_max_daily_mm == daily.precip_mm.max()
    assert w.temp_mean_c == pytest.approx(daily.temp_mean_c.mean())
    assert w.humidity_mean_pct == 80.0


def test_lags_are_whole_week_shifts_within_district():
    w = build_weekly_weather(_daily())
    d1 = w[w.district_code == "01"].reset_index(drop=True)
    assert d1["precip_sum_mm_lag3"].iloc[:3].isna().all()
    assert d1["precip_sum_mm_lag3"].iloc[5] == d1["precip_sum_mm"].iloc[2]
    d2_first = w[w.district_code == "02"].iloc[0]
    assert np.isnan(d2_first["temp_mean_c_lag1"])      # lags never leak across districts
    assert "precip_sum_mm_lag8" in w.columns and "temp_mean_c_lag8" in w.columns


def test_gap_in_weeks_raises():
    w = daily_to_weekly(_daily())
    w = w.drop(index=w[(w.district_code == "01")].index[4])
    with pytest.raises(ValueError):
        add_lags(w)


def test_model_table_adds_population_and_incidence():
    w = build_weekly_weather(_daily())
    cases = pd.DataFrame(dict(district_code=["01", "01"], disease=["dengue", "dengue"],
                              epi_year=[2021, 2021], epi_week=[2, 3], cases=[10, 20]))
    districts = pd.DataFrame(dict(district_code=["01", "02"], population=[1_000_000, 500_000]))
    m = build_model_table(cases, w, districts)
    assert m.incidence_per_100k.tolist() == [1.0, 2.0]
    assert m["precip_sum_mm"].notna().all()
    with pytest.raises(ValueError):
        build_model_table(cases, w)                      # no population source


def test_lag_correlation_finds_planted_lag():
    rng = np.random.default_rng(0)
    w = build_weekly_weather(_daily(start="2020-01-06", end="2023-12-31", codes=("01", "02", "03")))
    w["precip_sum_mm"] = rng.gamma(2.0, 20.0, len(w))   # random rain
    w = add_lags(w)
    d = w.copy()
    rain_l6 = d.groupby("district_code")["precip_sum_mm"].shift(6)
    d["cases"] = np.expm1(1 + 0.02 * rain_l6.fillna(rain_l6.mean())) * 10
    d["disease"] = "dengue"
    d["population"] = 1_000_000
    d["incidence_per_100k"] = d["cases"] / d["population"] * 1e5
    lc = lag_correlation(d, "dengue")
    assert lc.loc[lc.correlation.idxmax(), "lag_weeks"] == 6

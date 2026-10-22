import numpy as np
import pandas as pd

from analytics.calendar.preparedness import season_window, seasonal_index, shift_week


def _series(peak=30, years=5, amp=1.0, seed=0):
    rng = np.random.default_rng(seed)
    weeks = pd.date_range("2021-01-04", periods=52 * years, freq="7D")
    w = np.arange(len(weeks)) % 52 + 1
    mu = 10 * (1 + amp * np.cos(2 * np.pi * (w - peak) / 52))
    return pd.Series(rng.poisson(mu).astype(float), index=weeks)


def test_peak_and_window_recovered():
    idx, strength = seasonal_index(_series(peak=30))
    w = season_window(idx)
    assert w["clear"] and abs(w["peak"] - 30) <= 2
    assert w["onset"] < w["peak"] < w["end"] and strength > 0.5


def test_flat_series_has_no_clear_season():
    idx, _ = seasonal_index(_series(amp=0.0))
    assert not season_window(idx)["clear"]


def test_window_wraps_around_year_end():
    idx, _ = seasonal_index(_series(peak=51))
    w = season_window(idx)
    assert w["clear"] and (w["onset"] > w["end"] or w["onset"] > 40)


def test_shift_week_wraps():
    assert shift_week(3, -4) == 51 and shift_week(50, 4) == 2 and shift_week(10, -8) == 2

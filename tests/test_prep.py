import pandas as pd

from analytics.forecast.prep import prepare_cases


def _table(n=10):
    w = pd.date_range("2020-12-28", periods=n, freq="7D")
    return w, pd.DataFrame({"week_start_date": w, "cases": 1})


def test_prepare_cases_drops_edges_and_clips_to_weather():
    w, c = _table()
    out = prepare_cases(c, weather_end=w[5], trim_last_weeks=3)
    assert out["week_start_date"].min() == w[1]
    assert out["week_start_date"].max() == w[5]   # weather end beats trim (w[6])


def test_prepare_cases_trim_wins_when_weather_is_longer():
    w, c = _table()
    out = prepare_cases(c, weather_end=w[9], trim_last_weeks=3)
    assert out["week_start_date"].max() == w[6]


def test_prepare_cases_without_weather_only_trims():
    w, c = _table()
    out = prepare_cases(c, trim_last_weeks=2)
    assert out["week_start_date"].min() == w[1]
    assert out["week_start_date"].max() == w[7]

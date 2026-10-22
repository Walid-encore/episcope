"""Shared preparation of gold_weekly_cases for forecasting, demand and the calendar.

One rule, stated once: keep complete weeks only, then trim the last k weeks.
  * the first week is dropped (its onset window opens mid-week);
  * the last `trim_last_weeks` weeks are dropped (partial week + late reports, up to 21 days);
  * the table never ends later than the last week that has weather, because live
    horizons up to 4 need weather up to the origin week (lags 4..8).
Document the chosen k in docs/assumptions.md.
"""
import pandas as pd


def prepare_cases(cases, weather_end=None, trim_last_weeks=3, verbose=True):
    c = cases.copy()
    c["week_start_date"] = pd.to_datetime(c["week_start_date"])
    c = c[c["week_start_date"] > c["week_start_date"].min()]
    last = c["week_start_date"].max() - pd.Timedelta(weeks=trim_last_weeks)
    if weather_end is not None:
        w_end = pd.to_datetime(weather_end)
        if w_end < last:
            if verbose:
                print(f"prepare_cases: clipping to last weather week {w_end.date()} "
                      f"(trim alone would end {last.date()})")
            last = w_end
    return c[c["week_start_date"] <= last]

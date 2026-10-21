"""Weekly weather features with lags (AN-4 prep). Output follows gold_weekly_weather in docs/gold_contract.md.

Daily weather (data/reference/weather.csv: district_code, date, precip_mm, temp_mean_c, humidity_pct)
-> one row per district x ISO week:
    temp_mean_c        weekly mean of daily temp_mean_c
    precip_sum_mm      weekly sum of daily precip_mm
    humidity_mean_pct  weekly mean of daily humidity_pct
    precip_max_daily_mm  wettest day of the week (extra column; diarrhoeal disease reacts to extreme rain)
    temp_mean_c_lag1..8, precip_sum_mm_lag1..8   value from L weeks earlier, same district
    week_start_date    Monday (extra column, same as the other gold tables)

Rules
  * ISO year/week from the date (2021-01-01 belongs to ISO 2020-W53). Never the calendar year.
  * Only complete 7-day weeks are kept, so the partial first/last weeks of the download are dropped.
  * Lags are shifts by whole weeks, so weeks must be contiguous per district; if not, this raises
    instead of silently shifting by the wrong amount.
  * Pandas is fine here: this is small reference data (64 districts x ~300 weeks).

Run from the repo root:  python -m analytics.forecast.features
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LAGS = range(1, 9)
LAG_COLS = ("temp_mean_c", "precip_sum_mm")


def daily_to_weekly(daily):
    d = daily.copy()
    d["district_code"] = d["district_code"].astype(str).str.zfill(2)
    d["date"] = pd.to_datetime(d["date"])
    iso = d["date"].dt.isocalendar()
    d["epi_year"] = iso["year"].astype(int)
    d["epi_week"] = iso["week"].astype(int)
    d["week_start_date"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    w = (d.groupby(["district_code", "epi_year", "epi_week", "week_start_date"], as_index=False)
           .agg(temp_mean_c=("temp_mean_c", "mean"),
                precip_sum_mm=("precip_mm", "sum"),
                precip_max_daily_mm=("precip_mm", "max"),
                humidity_mean_pct=("humidity_pct", "mean"),
                n_days=("date", "nunique")))
    w = w[w["n_days"] == 7].drop(columns="n_days")
    return w.sort_values(["district_code", "week_start_date"]).reset_index(drop=True)


def add_lags(w, cols=LAG_COLS, lags=LAGS):
    w = w.sort_values(["district_code", "week_start_date"]).reset_index(drop=True)
    gaps = w.groupby("district_code")["week_start_date"].diff().dropna()
    if (gaps != pd.Timedelta(days=7)).any():
        bad = w.loc[gaps[gaps != pd.Timedelta(days=7)].index, ["district_code", "week_start_date"]]
        raise ValueError(f"weekly weather is not contiguous, cannot lag safely. First gaps:\n{bad.head()}")
    g = w.groupby("district_code")
    for c in cols:
        for L in lags:
            w[f"{c}_lag{L}"] = g[c].shift(L)
    return w


def build_weekly_weather(daily):
    return add_lags(daily_to_weekly(daily))


def build_model_table(cases, weekly_weather, districts=None, require_lags=False):
    """Join weekly cases (gold_weekly_cases columns) with weekly weather on district x ISO week.
    Adds population and incidence_per_100k from `districts` when the cases table lacks them."""
    c = cases.copy()
    c["district_code"] = c["district_code"].astype(str).str.zfill(2)
    wx = weekly_weather.drop(columns=["week_start_date"], errors="ignore")
    m = c.merge(wx, on=["district_code", "epi_year", "epi_week"], how="left", validate="many_to_one")
    if "population" not in m.columns:
        if districts is None:
            raise ValueError("cases has no population column; pass districts (districts.csv)")
        dd = districts[["district_code", "population"]].copy()
        dd["district_code"] = dd["district_code"].astype(str).str.zfill(2)
        m = m.merge(dd, on="district_code", how="left", validate="many_to_one")
    if "incidence_per_100k" not in m.columns:
        m["incidence_per_100k"] = m["cases"] / m["population"] * 1e5
    if require_lags:
        m = m.dropna(subset=[f"{c}_lag{L}" for c in LAG_COLS for L in LAGS])
    return m


def lag_correlation(model, disease, predictor="precip_sum_mm", lags=LAGS):
    """Within-district correlation of log1p(incidence) with the weather predictor L weeks earlier.
    Both series are centred per district, so differences between districts do not drive the result.
    Use it to justify the lag range in the report (dengue should peak around lags 4-8)."""
    d = model[model["disease"] == disease].copy()
    d["y"] = np.log1p(d["incidence_per_100k"])
    d["y"] -= d.groupby("district_code")["y"].transform("mean")
    rows = []
    for L in lags:
        x = d[f"{predictor}_lag{L}"]
        d["x"] = x - x.groupby(d["district_code"]).transform("mean")
        ok = d[["x", "y"]].dropna()
        rows.append(dict(lag_weeks=L, correlation=float(ok["x"].corr(ok["y"])) if len(ok) > 2 else np.nan,
                         n=len(ok)))
    return pd.DataFrame(rows)


def main():
    daily = pd.read_csv(ROOT / "data" / "reference" / "weather.csv", dtype={"district_code": str})
    w = build_weekly_weather(daily)
    out = ROOT / "data" / "analytics"
    out.mkdir(parents=True, exist_ok=True)
    w.to_parquet(out / "gold_weekly_weather.parquet", index=False)
    print(f"{len(w)} district-weeks, {w.district_code.nunique()} districts, "
          f"{w.week_start_date.min().date()} .. {w.week_start_date.max().date()}")
    print(f"wrote {out / 'gold_weekly_weather.parquet'}")


if __name__ == "__main__":
    main()

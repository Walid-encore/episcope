"""AN-5: rolling-origin backtest on AS-OF counts (what was actually known at each forecast origin).

    python -m analytics.forecast.asof_backtest [--cases data/gold/gold_weekly_cases]
            [--by-lag data/gold/gold_weekly_cases_by_lag] [--weather data/reference/weather.csv]
            [--trim-last-weeks 1] [--districts 0] [--workers 6]

gold_weekly_cases_by_lag holds cases_new per onset week and report lag, so the cumulative sum over lags <= k is
the count of that week as known k weeks after it. At origin o the newest weeks are therefore incomplete (up to
the maximum report lag), exactly as in live operation. Predictions are scored against the FINAL counts.
Both bases are run on the same series so the difference isolates the effect of late reporting:
  final  = the usual backtest (every week complete at every origin)
  as_of  = the realistic backtest (only reports received by the origin)
Writes docs/experiments/forecast_backtest_asof.csv and forecast_backtest_asof_by_disease.csv. Reads no truth.
"""
import argparse
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from analytics.forecast.ablation import load_table
from analytics.forecast.forecast import KEYS, forecast_series, summarise

ROOT = Path(__file__).resolve().parents[2]
MODELS = ("seasonal_naive", "persistence", "nb_lagged_weather")


def cumulative_by_lag(path):
    p = Path(path)
    bl = pd.read_parquet(p) if p.is_dir() or p.suffix.lower() == ".parquet" else pd.read_csv(p)
    bl["district_code"] = bl["district_code"].astype(str).str.zfill(2)
    bl["disease"] = bl["disease"].astype(str)
    bl["week_start_date"] = pd.to_datetime(bl["week_start_date"]).dt.round("D")
    bl["report_lag_weeks"] = bl["report_lag_weeks"].astype(int)
    wide = bl.pivot_table(index=KEYS + ["week_start_date"], columns="report_lag_weeks", values="cases_new",
                          aggfunc="sum", fill_value=0, observed=True)
    wide = wide.reindex(sorted(wide.columns), axis=1).cumsum(axis=1)
    wide.columns = [f"cum_lag{int(c)}" for c in wide.columns]
    return wide.reset_index()


def _one(g):
    cum = [c for c in g.columns if c.startswith("cum_lag")]
    a = forecast_series(g, models=MODELS).assign(basis="as_of")
    b = forecast_series(g.drop(columns=cum), models=MODELS).assign(basis="final")
    return pd.concat([a, b], ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(ROOT / "data" / "gold" / "gold_weekly_cases"))
    ap.add_argument("--by-lag", default=str(ROOT / "data" / "gold" / "gold_weekly_cases_by_lag"))
    ap.add_argument("--weather", default=str(ROOT / "data" / "reference" / "weather.csv"))
    ap.add_argument("--trim-last-weeks", type=int, default=1)
    ap.add_argument("--districts", type=int, default=0)
    ap.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 2))
    a = ap.parse_args()

    table = load_table(a.cases, a.weather, a.trim_last_weeks)
    cum = cumulative_by_lag(a.by_lag)
    table = table.merge(cum, on=KEYS + ["week_start_date"], how="left")
    cols = [c for c in table.columns if c.startswith("cum_lag")]
    if table[cols].isna().any().any():
        raise SystemExit("gold_weekly_cases_by_lag does not cover every case week")
    last = cols[-1]
    mism = float((table["cases"] != table[last]).mean())
    print(f"max report lag {len(cols) - 1} weeks; final count equals gold cases in {1 - mism:.2%} of series-weeks")
    if mism > 0.01:
        print("WARNING: by-lag totals differ from gold_weekly_cases; as-of counts are approximate")
    if a.districts:
        keep = sorted(table["district_code"].unique())[: a.districts]
        table = table[table["district_code"].isin(keep)]
    groups = [g for _, g in table.groupby(KEYS)]
    print(f"{len(groups)} series, {a.workers} workers", flush=True)
    with ProcessPoolExecutor(a.workers) as ex:
        fc = pd.concat(ex.map(_one, groups, chunksize=4), ignore_index=True)

    exp = ROOT / "docs" / "experiments"
    exp.mkdir(parents=True, exist_ok=True)
    overall, by_dis = [], []
    for basis, g in fc.groupby("basis"):
        s = summarise(g)
        s.insert(0, "basis", basis)
        overall.append(s)
        for dis, gd in g.groupby("disease"):
            sd = summarise(gd)
            sd.insert(0, "disease", dis)
            sd.insert(0, "basis", basis)
            by_dis.append(sd)
    overall, by_dis = pd.concat(overall), pd.concat(by_dis)
    overall.to_csv(exp / "forecast_backtest_asof.csv", index=False)
    by_dis.to_csv(exp / "forecast_backtest_asof_by_disease.csv", index=False)

    pd.set_option("display.width", 200)
    allh = overall[overall["horizon_weeks"] == "all"].drop(columns="horizon_weeks")
    print("\nAll diseases, all horizons:")
    print(allh.round(3).to_string(index=False))
    w = by_dis[by_dis["horizon_weeks"] == "all"].pivot_table(index=["disease", "model"], columns="basis", values="wis")
    w["as_of_vs_final"] = w["as_of"] / w["final"]
    print("\nWIS by disease (as_of_vs_final > 1 means late reports hurt):")
    print(w.round(3).to_string())
    print(f"\nwrote {exp / 'forecast_backtest_asof.csv'} and forecast_backtest_asof_by_disease.csv")


if __name__ == "__main__":
    main()

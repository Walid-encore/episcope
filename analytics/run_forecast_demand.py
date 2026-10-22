"""Forecast + backtest + demand conversion on the stand-in gold tables (AN-4, AN-5, AN-6).

Run from the repo root:
    python -m analytics.run_forecast_demand [--limit 6]     # --limit = quick test on 6 districts

Reads   data/standin_gold/gold_weekly_cases.csv, data/analytics/gold_weekly_weather.parquet
Writes  data/analytics/gold_forecast.csv                 all forecast rows (live + backtest)
        docs/experiments/forecast_backtest_standin.csv   MAE / 80% coverage / WIS vs seasonal-naive
        data/outputs/demand_weekly.csv, data/outputs/planning_totals.csv   (input of run_allocation)

Edge weeks are trimmed because onset-week counts are incomplete there: the first week starts
before 2021-01-01, and the last weeks are still waiting for late reports (up to 21 days).
Proper as-of backtests will use gold_weekly_cases_by_lag instead.
"""
import argparse
from pathlib import Path

import pandas as pd

from analytics.forecast.forecast import run_pandas, summarise
from analytics.optimise.demand import admission_rates, compute_demand, planning_totals

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "standin_gold"))
    ap.add_argument("--limit", type=int, default=0, help="use only the first N districts (quick test)")
    ap.add_argument("--trim-last-weeks", type=int, default=3)
    a = ap.parse_args()

    cases = pd.read_csv(Path(a.gold) / "gold_weekly_cases.csv", dtype={"district_code": str})
    cases["week_start_date"] = pd.to_datetime(cases["week_start_date"])
    from analytics.forecast.prep import prepare_cases
    _wend = pd.read_parquet(ROOT / "data" / "analytics" / "gold_weekly_weather.parquet",
                            columns=["week_start_date"])["week_start_date"].max()
    cases = prepare_cases(cases, _wend, a.trim_last_weeks)
    if a.limit:
        keep = sorted(cases["district_code"].unique())[: a.limit]
        cases = cases[cases["district_code"].isin(keep)]

    wx = pd.read_parquet(ROOT / "data" / "analytics" / "gold_weekly_weather.parquet")
    wx["district_code"] = wx["district_code"].astype(str)
    wx["week_start_date"] = pd.to_datetime(wx["week_start_date"])
    wx = wx[["district_code", "week_start_date", "precip_sum_mm", "temp_mean_c"]]
    table = cases.merge(wx, on=["district_code", "week_start_date"], how="left")
    missing = int(table["precip_sum_mm"].isna().sum())
    print(f"series: {table.groupby(['district_code', 'disease']).ngroups}, weeks "
          f"{table['week_start_date'].min().date()}..{table['week_start_date'].max().date()}, "
          f"rows with missing weather: {missing}")
    if missing:
        raise SystemExit("weather does not cover every case week; extend weather or trim the cases")

    fc = run_pandas(table)
    out = ROOT / "data" / "analytics"
    out.mkdir(parents=True, exist_ok=True)
    fc.to_csv(out / "gold_forecast.csv", index=False)

    summary = summarise(fc)
    pd.set_option("display.width", 200)
    print(summary.round(3).to_string(index=False))
    exp = ROOT / "docs" / "experiments"
    exp.mkdir(parents=True, exist_ok=True)
    summary.to_csv(exp / "forecast_backtest_standin.csv", index=False)

    rates = admission_rates(cases)
    demand = compute_demand(fc, rates)
    totals = planning_totals(demand)
    res = ROOT / "data" / "outputs"
    res.mkdir(parents=True, exist_ok=True)
    demand.to_csv(res / "demand_weekly.csv", index=False)
    totals.to_csv(res / "planning_totals.csv", index=False)
    print(totals.groupby("resource")[["demand", "demand_q90"]].sum().round(0))
    print(f"wrote {res / 'planning_totals.csv'}")


if __name__ == "__main__":
    main()

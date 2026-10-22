"""AN-4 / AN-6 glue: gold_weekly_cases -> gold_forecast -> gold_demand -> planning_totals.

Usage:
    python -m analytics.optimise.run_demand --cases <csv | parquet file | parquet dir>

--cases must hold gold_weekly_cases (district_code, disease, week_start_date, cases,
admitted_cases, ...). No dates or file names are hard-coded.
"""
import argparse
from pathlib import Path

import pandas as pd

from analytics.forecast.features import build_weekly_weather
from analytics.forecast.prep import prepare_cases
from analytics.forecast.forecast import run_pandas
from analytics.optimise.demand import admission_rates, compute_demand, planning_totals

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analytics"


def load_cases(path):
    p = Path(path)
    if p.suffix == ".csv":
        df = pd.read_csv(p, dtype={"district_code": str})
    else:
        df = pd.read_parquet(p)
    df["district_code"] = df["district_code"].astype(str).str.zfill(2)
    df["week_start_date"] = pd.to_datetime(df["week_start_date"])
    need = {"district_code", "disease", "week_start_date", "cases", "admitted_cases"}
    missing = need - set(df.columns)
    if missing:
        raise SystemExit(f"cases table is missing columns: {sorted(missing)}")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--weather", default=str(ROOT / "data" / "reference" / "weather.csv"))
    ap.add_argument("--trim-last-weeks", type=int, default=3,
                    help="drop the newest k weeks (partial week + late reports)")
    a = ap.parse_args()

    cases = load_cases(a.cases)
    wx = build_weekly_weather(pd.read_csv(a.weather, dtype={"district_code": str}))
    wx = wx[["district_code", "week_start_date", "precip_sum_mm", "temp_mean_c"]]
    cases = prepare_cases(cases, wx["week_start_date"].max(), a.trim_last_weeks)
    table = cases.merge(wx, on=["district_code", "week_start_date"], how="left")

    fc = run_pandas(table)
    live = fc[~fc["is_backtest"].astype(bool)]
    if live.empty:
        raise SystemExit("forecast has no live (non-backtest) rows; compute_demand needs them")

    rates = admission_rates(cases)
    demand = compute_demand(fc, rates)
    totals = planning_totals(demand)

    OUT.mkdir(parents=True, exist_ok=True)
    fc.to_parquet(OUT / "gold_forecast.parquet", index=False)
    demand.to_parquet(OUT / "gold_demand.parquet", index=False)
    totals.to_parquet(OUT / "planning_totals.parquet", index=False)
    totals.to_csv(OUT / "planning_totals.csv", index=False)  # run_allocation reads CSV

    print(f"forecast rows: {len(fc):,} ({len(live):,} live); "
          f"districts with demand: {totals['district_code'].nunique()}")
    print(totals.groupby("resource")[["demand", "demand_q90"]].sum().round(1))
    print(f"wrote gold_forecast, gold_demand, planning_totals to {OUT}")


if __name__ == "__main__":
    main()

"""AN-4 / AN-5 evidence: weather ablation, persistence baseline, lag-window comparison, interval calibration.

    python -m analytics.forecast.ablation [--cases data/gold/gold_weekly_cases] [--weather data/reference/weather.csv]
                                          [--trim-last-weeks 1] [--districts 0] [--workers 6]

Runs the 52-week rolling-origin backtest (horizons 1-4) for six models on the same rows:
  seasonal_naive, persistence                       baselines
  nb_lagged_weather                                 production model (weather lags 4-8 weeks)
  nb_no_weather                                     ablation: seasonal terms + last count only
  nb_weather_short                                  weather lags h..h+3 (shortest that exist at the origin)
  nb_lagged_weather_cal                             production model with calibrated interval width
This is an offline evaluation experiment (one pandas process per series); the production forecast stays on
Spark (run_demand). It writes NOTHING to the lake and does not touch gold_forecast.

Writes docs/experiments/forecast_ablation.csv and forecast_ablation_by_disease.csv, and prints
  weather skill = 1 - WIS(nb_lagged_weather) / WIS(nb_no_weather)   (> 0: the weather terms help)
  lag verdict   = which weather window has the lowest WIS, per disease.
Reads only gold cases and weather, never data/truth.
"""
import argparse
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from analytics.forecast.features import build_weekly_weather
from analytics.forecast.forecast import ABLATION_MODELS, KEYS, forecast_series, summarise
from analytics.forecast.prep import prepare_cases

ROOT = Path(__file__).resolve().parents[2]


def _one(group):
    return forecast_series(group, models=ABLATION_MODELS)


def load_table(cases_path, weather_path, trim):
    p = Path(cases_path)
    cs = pd.read_parquet(p) if p.is_dir() or p.suffix.lower() == ".parquet" else pd.read_csv(p)
    cs["district_code"] = cs["district_code"].astype(str).str.zfill(2)
    cs["disease"] = cs["disease"].astype(str)
    cs["week_start_date"] = pd.to_datetime(cs["week_start_date"]).dt.round("D")
    cs = cs.groupby(KEYS + ["week_start_date"], as_index=False, observed=True)["cases"].sum()
    wx = build_weekly_weather(pd.read_csv(weather_path, dtype={"district_code": str}))
    wx = wx[["district_code", "week_start_date", "precip_sum_mm", "temp_mean_c"]]
    cs = prepare_cases(cs, wx["week_start_date"].max(), trim)
    table = cs.merge(wx, on=["district_code", "week_start_date"], how="left")
    if table["precip_sum_mm"].isna().any():
        raise SystemExit("weather does not cover every case week")
    return table


def verdicts(by_dis):
    allh = by_dis[by_dis["horizon_weeks"] == "all"]
    rows = []
    for dis, g in allh.groupby("disease"):
        w = g.set_index("model")["wis"]
        rows.append(dict(
            disease=dis,
            weather_skill=1 - w["nb_lagged_weather"] / w["nb_no_weather"],
            short_vs_lag48=1 - w["nb_weather_short"] / w["nb_lagged_weather"],
            best_weather_window=("none" if w["nb_no_weather"] <= min(w["nb_lagged_weather"], w["nb_weather_short"])
                                 else "lags 4-8" if w["nb_lagged_weather"] <= w["nb_weather_short"]
                                 else "lags h..h+3"),
            coverage_uncal=g.set_index("model").loc["nb_lagged_weather", "coverage_80"],
            coverage_cal=g.set_index("model").loc["nb_lagged_weather_cal", "coverage_80"],
            wis_best_nb=w[["nb_lagged_weather", "nb_no_weather", "nb_weather_short", "nb_lagged_weather_cal"]].min(),
            wis_persistence=w["persistence"], wis_seasonal_naive=w["seasonal_naive"]))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(ROOT / "data" / "gold" / "gold_weekly_cases"))
    ap.add_argument("--weather", default=str(ROOT / "data" / "reference" / "weather.csv"))
    ap.add_argument("--trim-last-weeks", type=int, default=1)
    ap.add_argument("--districts", type=int, default=0, help="only the first N districts (quick test)")
    ap.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 2))
    a = ap.parse_args()

    table = load_table(a.cases, a.weather, a.trim_last_weeks)
    if a.districts:
        keep = sorted(table["district_code"].unique())[: a.districts]
        table = table[table["district_code"].isin(keep)]
    groups = [g for _, g in table.groupby(KEYS)]
    print(f"{len(groups)} series, weeks {table['week_start_date'].min().date()}.."
          f"{table['week_start_date'].max().date()}, {len(ABLATION_MODELS)} models, {a.workers} workers",
          flush=True)
    with ProcessPoolExecutor(a.workers) as ex:
        parts = []
        for i, part in enumerate(ex.map(_one, groups, chunksize=4), 1):
            parts.append(part)
            if i % 32 == 0:
                print(f"  {i}/{len(groups)} series", flush=True)
    fc = pd.concat(parts, ignore_index=True)

    exp = ROOT / "docs" / "experiments"
    exp.mkdir(parents=True, exist_ok=True)
    overall = summarise(fc)
    overall.to_csv(exp / "forecast_ablation.csv", index=False)
    pieces = []
    for dis, g in fc.groupby("disease"):
        s = summarise(g)
        s.insert(0, "disease", dis)
        pieces.append(s)
    by_dis = pd.concat(pieces, ignore_index=True)
    by_dis.to_csv(exp / "forecast_ablation_by_disease.csv", index=False)

    pd.set_option("display.width", 220)
    print("\nAll diseases, all horizons (same rows for every model):")
    print(overall[overall["horizon_weeks"] == "all"].drop(columns="horizon_weeks").round(3).to_string(index=False))
    print("\nPer disease (WIS, lower is better):")
    print(by_dis[by_dis["horizon_weeks"] == "all"].pivot(index="disease", columns="model", values="wis")
          .round(3).to_string())
    v = verdicts(by_dis)
    print("\nVerdicts:")
    print(v.round(3).to_string(index=False))
    print(f"\nwrote {exp / 'forecast_ablation.csv'} and forecast_ablation_by_disease.csv")


if __name__ == "__main__":
    main()

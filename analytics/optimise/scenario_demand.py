"""Planning-demand file for a PAST forecast origin (default: the origin with the highest cost-weighted q90 demand).

    python -m analytics.optimise.scenario_demand [--origin 202534] [--analytics data/analytics]

Why: at the latest origin (a low season) stock covers almost all demand, so the allocation problem is trivial.
This builds planning_totals for a backtest origin, with the admission rates of the last pipeline run, and prints the
stock date to pass to run_allocation --as-of. Forecast quantiles come from the rolling-origin backtest rows.
Writes <analytics>/planning_totals_peak.csv and planning_totals_peak.json (origin and as-of date).
"""
import argparse
import json
from pathlib import Path

import pandas as pd

from analytics.io_util import load_table
from analytics.optimise.demand import ROOT, compute_demand, implied_admission_rates, planning_totals

COSTS = {"iv_saline_units": 1.0, "ns1_kits": 3.0, "ors_sachets": 0.2}   # same relative units as run_allocation


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analytics", default=str(ROOT / "data" / "analytics"))
    ap.add_argument("--model", default="nb_lagged_weather")
    ap.add_argument("--origin", type=int, default=None, help="year*100+week; default = peak cost-weighted q90 demand")
    a = ap.parse_args()
    fc = load_table(a.analytics, "gold_forecast")
    dem = load_table(a.analytics, "gold_demand")
    rates = implied_admission_rates(fc, dem, a.model)

    bt = fc[(fc["model"] == a.model) & fc["is_backtest"].astype(bool)].copy()
    bt["_o"] = bt["origin_epi_year"] * 100 + bt["origin_epi_week"]
    # edge origins of a rolling backtest only have some horizons (the others have no actuals yet / any more)
    full = bt.groupby(["_o", "district_code", "disease"])["horizon_weeks"].nunique().groupby("_o").min()
    origins = sorted(full[full >= 4].index)
    print(f"{len(origins)} of {bt['_o'].nunique()} backtest origins have all 4 horizons for every series")
    if a.origin is not None and a.origin not in origins:
        raise SystemExit(f"origin {a.origin} is not a complete backtest origin; choose one of {origins[0]}..{origins[-1]}")
    cost = {}
    for o in origins:
        t = planning_totals(compute_demand(fc, rates, model=a.model, origin=o))
        t = t[t["resource"].isin(COSTS)]
        cost[o] = float((t["demand_q90"] * t["resource"].map(COSTS)).sum())
    cost = pd.Series(cost)
    origin = a.origin if a.origin is not None else int(cost.idxmax())
    print("top origins by cost-weighted q90 demand:")
    print(cost.sort_values(ascending=False).head(5).round(1).to_string())

    d = compute_demand(fc, rates, model=a.model, origin=origin)
    first = pd.to_datetime(d.loc[d["horizon_weeks"] == 1, "target_week_start_date"]).min()
    as_of = (first - pd.Timedelta(days=1)).date()
    t = planning_totals(d)
    out = Path(a.analytics) / "planning_totals_peak.csv"
    t.to_csv(out, index=False)
    Path(a.analytics, "planning_totals_peak.json").write_text(
        json.dumps({"origin": origin, "as_of": str(as_of), "model": a.model}, indent=2))
    print(f"\norigin {origin}, stock as of {as_of}; national planning demand:")
    print(t.groupby("resource")[["demand", "demand_q90"]].sum().round(1).to_string())
    print(f"\nwrote {out}\nnext:\n  python -m analytics.optimise.run_allocation --demand {out} --as-of {as_of} "
          f"--cases data\\gold_csv\\gold_weekly_cases.csv --out data\\analytics\\allocation_peak")


if __name__ == "__main__":
    main()

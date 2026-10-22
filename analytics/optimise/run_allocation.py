"""Produce the allocation plan, efficiency-equity curve and recommended alpha (AN-7, AN-8).

Inputs
  --demand      CSV saved from demand.planning_totals(...): district_code, resource, demand, demand_q90
  --stock-dir   data/incoming/stock (JSON lines; latest snapshot per facility is used)
  --facilities  data/reference/facilities.csv   (ASSUMES columns facility_id, district_code)
  --districts   data/reference/districts.csv    (district_code, division, population)
Outputs (in --out): allocation_plan.csv, efficiency_equity_curve.csv, recommendation.json

Planning uses demand_q90 (upper prediction quantile, Section 6.3). Beds are not a shippable
stock, so only the three consumables are allocated. UNIT_COSTS are relative cost units and
are ASSUMPTIONS: document them in docs/assumptions.md and test sensitivity to them.
"""
import argparse
import json
from pathlib import Path

import pandas as pd

from analytics.optimise.allocate import (efficiency_equity_curve, recommend_alpha,
                                         solve_allocation, metrics, proportional_baseline)

STOCK_COLS = {"iv_saline_units": "iv_saline_units", "ns1_kits": "ns1_kits",
              "ors_sachets": "ors_sachets"}
UNIT_COSTS = {"iv_saline_units": 1.0, "ns1_kits": 3.0, "ors_sachets": 0.2}


def latest_stock_by_district(stock_dir, facilities):
    files = sorted(Path(stock_dir).rglob("*"))
    files = [f for f in files if f.is_file() and f.suffix in (".json", ".jsonl", ".txt", "")]
    snap = pd.concat((pd.read_json(f, lines=True, dtype={"facility_id": str}) for f in files),
                     ignore_index=True)
    snap["snapshot_date"] = pd.to_datetime(snap["snapshot_date"])
    last = snap.sort_values("snapshot_date").drop_duplicates("facility_id", keep="last")
    last = last.merge(facilities[["facility_id", "district_code"]], on="facility_id")
    long = last.melt(id_vars="district_code", value_vars=list(STOCK_COLS), var_name="resource",
                     value_name="stock")
    return long.groupby(["district_code", "resource"], as_index=False)["stock"].sum()


def build_input(demand, stock, districts):
    d = demand[demand["resource"].isin(UNIT_COSTS)].copy()
    d["district_code"] = d["district_code"].astype(str).str.zfill(2)
    d = d.rename(columns={"demand": "demand_central", "demand_q90": "demand"})
    d = d.merge(stock, on=["district_code", "resource"], how="left")
    d["stock"] = d["stock"].fillna(0.0)
    return d.merge(districts[["district_code", "division"]], on="district_code")[
        ["district_code", "division", "resource", "demand", "stock"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demand", required=True)
    ap.add_argument("--stock-dir", default="data/incoming/stock")
    ap.add_argument("--facilities", default="data/reference/facilities.csv")
    ap.add_argument("--districts", default="data/reference/districts.csv")
    ap.add_argument("--out", default="data/outputs/allocation")
    ap.add_argument("--budget-frac", type=float, default=0.5,
                    help="national budget as a share of the cost of all unmet need")
    ap.add_argument("--tolerance", type=float, default=0.05)
    a = ap.parse_args()

    fac = pd.read_csv(a.facilities, dtype={"district_code": str, "facility_id": str})
    if "district_code" not in fac.columns:
        raise SystemExit(f"facilities.csv has no district_code; columns: {list(fac.columns)}")
    fac["district_code"] = fac["district_code"].str.zfill(2)
    dis = pd.read_csv(a.districts, dtype={"district_code": str})
    pop = dis.set_index("district_code")["population"]
    demand = pd.read_csv(a.demand, dtype={"district_code": str})
    inp = build_input(demand, latest_stock_by_district(a.stock_dir, fac), dis)

    curve = efficiency_equity_curve(inp, pop, UNIT_COSTS)
    alpha = recommend_alpha(curve, a.tolerance)
    need = float(((inp["demand"] - inp["stock"]).clip(lower=0) * inp["resource"].map(UNIT_COSTS)).sum())
    budget = a.budget_frac * need
    plan = solve_allocation(inp, pop, UNIT_COSTS, budget, alpha)
    base = proportional_baseline(inp, pop, UNIT_COSTS, budget)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    plan.to_csv(out / "allocation_plan.csv", index=False)
    curve.to_csv(out / "efficiency_equity_curve.csv", index=False)
    summary = {"budget": budget, "budget_frac": a.budget_frac, "recommended_alpha": alpha,
               "tolerance": a.tolerance, "optimised": metrics(plan, UNIT_COSTS),
               "proportional_baseline": metrics(base, UNIT_COSTS)}
    (out / "recommendation.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

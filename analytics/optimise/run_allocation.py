"""Produce the allocation plan, efficiency-equity curve and recommended alpha (AN-7, AN-8).

Inputs
  --demand      CSV saved from demand.planning_totals(...): district_code, resource, demand, demand_q90
  --stock-dir   data/incoming/stock (.json/.jsonl lines; latest snapshot per facility is used)
  --facilities  data/reference/facilities.csv   (ASSUMES columns facility_id, district_code)
  --districts   data/reference/districts.csv    (district_code, division, population)
  --cases       gold_weekly_cases (csv/parquet) used for severity weights w_d (optional; omit
                for equal weights, which is the sensitivity case)
Outputs (in --out): allocation_plan.csv, efficiency_equity_curve.csv, recommendation.json

Planning uses demand_q90 (upper prediction quantile, Section 6.3). Beds are not a shippable
stock, so only the three consumables are allocated. UNIT_COSTS are relative cost units and
are ASSUMPTIONS: document them in docs/assumptions.md and test sensitivity to them.
"""
import argparse
import io
import json
from pathlib import Path

import pandas as pd

from analytics.optimise.allocate import (efficiency_equity_curve, recommend_alpha,
                                         solve_allocation, metrics, proportional_baseline)
from analytics.optimise.weights import severity_weights, describe

STOCK_COLS = {"iv_saline_units": "iv_saline_units", "ns1_kits": "ns1_kits",
              "ors_sachets": "ors_sachets"}
UNIT_COSTS = {"iv_saline_units": 1.0, "ns1_kits": 3.0, "ors_sachets": 0.2}


def latest_stock_by_district(stock_dir, facilities):
    files = sorted(f for f in Path(stock_dir).rglob("*")
                   if f.is_file() and f.suffix.lower() in (".json", ".jsonl"))
    if not files:
        raise SystemExit(f"no .json/.jsonl stock files under {stock_dir}")
    # utf-8-sig strips a BOM if the feed has one
    frames = (pd.read_json(io.StringIO(f.read_text(encoding="utf-8-sig")), lines=True,
                           dtype={"facility_id": str}) for f in files)
    snap = pd.concat(frames, ignore_index=True)
    snap["facility_id"] = snap["facility_id"].astype(str)
    snap["snapshot_date"] = pd.to_datetime(snap["snapshot_date"])
    last = snap.sort_values("snapshot_date").drop_duplicates("facility_id", keep="last")

    n_before = len(last)
    last = last.merge(facilities[["facility_id", "district_code"]], on="facility_id")
    dropped = n_before - len(last)
    newest, oldest = snap["snapshot_date"].max(), last["snapshot_date"].min()
    print(f"stock: {len(files)} files, {n_before} facilities with a snapshot, "
          f"{dropped} dropped (facility_id not in registry)")
    print(f"stock: latest snapshot {newest.date()}, oldest snapshot used {oldest.date()} "
          f"({(newest - oldest).days} days older)")
    if (newest - oldest).days > 14:
        print("WARNING: some facilities' stock is over 14 days stale; it is treated as current")

    long = last.melt(id_vars="district_code", value_vars=list(STOCK_COLS), var_name="resource",
                     value_name="stock")
    return long.groupby(["district_code", "resource"], as_index=False)["stock"].sum()


def build_input(demand, stock, districts):
    d = demand[demand["resource"].isin(UNIT_COSTS)].copy()
    d["district_code"] = d["district_code"].astype(str).str.zfill(2)
    d = d.rename(columns={"demand": "demand_central", "demand_q90": "demand"})
    d = d.merge(stock, on=["district_code", "resource"], how="left")
    n_nostock = int(d["stock"].isna().sum())
    if n_nostock:
        print(f"WARNING: {n_nostock} district-resource rows have no stock record; stock set to 0")
    d["stock"] = d["stock"].fillna(0.0)
    n_before = len(d)
    out = d.merge(districts[["district_code", "division"]], on="district_code")[
        ["district_code", "division", "resource", "demand", "stock"]]
    if len(out) < n_before:
        print(f"WARNING: {n_before - len(out)} demand rows dropped (district_code not in districts.csv)")
    return out


def load_weights(path, district_codes):
    """Severity weights from gold_weekly_cases, or None (equal weights) when no path is given."""
    if not path:
        return None
    cs = (pd.read_parquet(path) if str(path).endswith(".parquet")
          else pd.read_csv(path, dtype={"district_code": str}, encoding="utf-8-sig"))
    return severity_weights(cs, district_codes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demand", default="data/analytics/planning_totals.csv")
    ap.add_argument("--stock-dir", default="data/incoming/stock")
    ap.add_argument("--facilities", default="data/reference/facilities.csv")
    ap.add_argument("--districts", default="data/reference/districts.csv")
    ap.add_argument("--out", default="data/analytics/allocation")
    ap.add_argument("--cases", default=None,
                    help="gold_weekly_cases (csv/parquet) for severity weights; omit for equal weights")
    ap.add_argument("--budget-frac", type=float, default=0.5,
                    help="national budget as a share of the cost of all unmet need")
    ap.add_argument("--tolerance", type=float, default=0.05)
    ap.add_argument("--rule", choices=["relative", "need"], default="relative",
                    help="how efficiency loss is measured when recommending alpha (see allocate.recommend_alpha)")
    a = ap.parse_args()

    fac = pd.read_csv(a.facilities, dtype={"district_code": str, "facility_id": str},
                      encoding="utf-8-sig")
    if "district_code" not in fac.columns:
        raise SystemExit(f"facilities.csv has no district_code; columns: {list(fac.columns)}")
    fac["district_code"] = fac["district_code"].str.zfill(2)
    dis = pd.read_csv(a.districts, dtype={"district_code": str}, encoding="utf-8-sig")
    dis["district_code"] = dis["district_code"].str.zfill(2)
    pop = dis.set_index("district_code")["population"]
    demand = pd.read_csv(a.demand, dtype={"district_code": str}, encoding="utf-8-sig")
    inp = build_input(demand, latest_stock_by_district(a.stock_dir, fac), dis)

    weights = load_weights(a.cases, sorted(inp["district_code"].unique()))
    print("severity weights:", describe(weights) if weights is not None else "equal (no --cases)")

    curve = efficiency_equity_curve(inp, pop, UNIT_COSTS, weights=weights)
    alpha = recommend_alpha(curve, a.tolerance, a.rule)
    need = float(((inp["demand"] - inp["stock"]).clip(lower=0) * inp["resource"].map(UNIT_COSTS)).sum())
    budget = a.budget_frac * need
    plan = solve_allocation(inp, pop, UNIT_COSTS, budget, alpha, weights=weights)
    base = proportional_baseline(inp, pop, UNIT_COSTS, budget)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    plan.to_csv(out / "allocation_plan.csv", index=False)
    curve.to_csv(out / "efficiency_equity_curve.csv", index=False)
    summary = {"budget": budget, "budget_frac": a.budget_frac, "recommended_alpha": alpha,
               "tolerance": a.tolerance, "rule": a.rule,
               "weights": "severity (from --cases)" if weights is not None else "equal",
               "optimised": metrics(plan, UNIT_COSTS, weights),
               "proportional_baseline": metrics(base, UNIT_COSTS, weights)}
    (out / "recommendation.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

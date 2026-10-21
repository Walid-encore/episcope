"""AN-7 / AN-8 adapter: planning totals + stock + districts -> gold_allocation.

Usage:
    python -m analytics.optimise.run_allocation --planning data/analytics/planning_totals.parquet

--planning must hold the output of demand.planning_totals():
    district_code, resource, demand, demand_q90

No dates or file names are hard-coded; stock is read from whatever snapshots exist.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from analytics.optimise.allocate import (solve_allocation, proportional_baseline,
                                         efficiency_equity_curve, recommend_alpha)

REF = Path("data/reference")
STOCK_DIR = Path("data/incoming/stock")
OUT = Path("data/analytics")
EXP = Path("docs/experiments")

RESOURCES = ["beds", "iv_saline_units", "ns1_kits", "ors_sachets"]
# PLACEHOLDER unit costs: replace with the values in docs/assumptions.md.
COSTS = {"beds": 50.0, "iv_saline_units": 2.0, "ns1_kits": 5.0, "ors_sachets": 0.3}
ALPHAS = (0.0, 0.2, 0.4, 0.6, 0.8)
BUDGET_FRACS = (0.2, 0.4, 0.6, 0.8, 1.0)


def load_stock(stock_dir=STOCK_DIR, facilities=None, as_of=None):
    """Latest snapshot per facility, summed to district. Returns long: district_code, resource, stock."""
    files = [p for p in Path(stock_dir).rglob("*") if p.is_file()]
    if not files:
        raise FileNotFoundError(f"no stock snapshots under {stock_dir}")
    snap = pd.concat((pd.read_json(p, lines=True, dtype={"facility_id": str}) for p in files),
                     ignore_index=True)
    snap["snapshot_date"] = pd.to_datetime(snap["snapshot_date"])
    if as_of is not None:
        snap = snap[snap["snapshot_date"] <= pd.Timestamp(as_of)]
    snap = (snap.sort_values("snapshot_date")
                .drop_duplicates("facility_id", keep="last"))
    fac = facilities if facilities is not None else pd.read_csv(
        REF / "facilities.csv", dtype={"district_code": str, "facility_id": str})
    m = snap.merge(fac[["facility_id", "district_code", "beds"]], on="facility_id", how="inner")
    m["free_beds"] = (m["beds"] - m["occupied_beds"]).clip(lower=0)
    g = m.groupby("district_code").agg(
        beds=("free_beds", "sum"), iv_saline_units=("iv_saline_units", "sum"),
        ns1_kits=("ns1_kits", "sum"), ors_sachets=("ors_sachets", "sum"))
    return (g.reset_index().melt("district_code", var_name="resource", value_name="stock"),
            snap["snapshot_date"].max())


def build_inputs(planning, districts, stock):
    planning = planning.copy()
    planning["district_code"] = planning["district_code"].astype(str).str.zfill(2)
    grid = pd.MultiIndex.from_product([districts["district_code"], RESOURCES],
                                      names=["district_code", "resource"]).to_frame(index=False)
    d = (grid.merge(planning[["district_code", "resource", "demand_q90"]],
                    on=["district_code", "resource"], how="left")
             .merge(stock, on=["district_code", "resource"], how="left")
             .merge(districts[["district_code", "division"]], on="district_code"))
    d = d.rename(columns={"demand_q90": "demand"})
    d[["demand", "stock"]] = d[["demand", "stock"]].fillna(0.0)
    pop = districts.set_index("district_code")["population"].astype(float)
    return d, pop


def _tag(res, d, strategy, alpha, frac, budget):
    r = res.drop(columns=["demand", "stock"], errors="ignore").merge(
        d[["district_code", "resource", "demand", "stock"]],
                  on=["district_code", "resource"], how="left")
    r["unmet_demand"] = (r["demand"] - r["stock"] - r["x"]).clip(lower=0)
    return pd.DataFrame({
        "budget_level": frac, "budget_units": budget, "alpha": alpha, "strategy": strategy,
        "district_code": r["district_code"], "resource": r["resource"],
        "allocated_units": r["x"], "unmet_demand": r["unmet_demand"]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--planning", required=True)
    ap.add_argument("--as-of", default=None, help="ignore stock snapshots after this date")
    ap.add_argument("--vol", default=None, help="JSON {resource: transport volume per unit}")
    ap.add_argument("--caps", default=None, help="JSON {division: transport capacity}")
    a = ap.parse_args()

    districts = pd.read_csv(REF / "districts.csv", dtype={"district_code": str})
    p = Path(a.planning)
    planning = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, dtype={"district_code": str})
    stock, stock_date = load_stock(as_of=a.as_of)
    d, pop = build_inputs(planning, districts, stock)
    vol = json.loads(a.vol) if a.vol else None
    caps = json.loads(a.caps) if a.caps else None

    need = (d["demand"] - d["stock"]).clip(lower=0) * d["resource"].map(COSTS)
    full_budget = float(need.sum())
    if full_budget <= 0:
        raise SystemExit("no unmet need after stock; nothing to allocate")

    frames = []
    for frac in BUDGET_FRACS:
        B = frac * full_budget
        base = proportional_baseline(d, pop, COSTS, B)
        frames.append(_tag(base, d, "population_proportional", np.nan, frac, B))
        for alpha in ALPHAS:
            res = solve_allocation(d, pop, COSTS, B, alpha, vol=vol, caps=caps)
            frames.append(_tag(res, d, "optimised", alpha, frac, B))
    alloc = pd.concat(frames, ignore_index=True)

    curve = efficiency_equity_curve(d, pop, COSTS)
    rec = float(recommend_alpha(curve))
    alloc["is_recommended"] = (alloc["strategy"] == "optimised") & np.isclose(alloc["alpha"], rec, atol=1e-9)

    OUT.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)
    alloc.to_parquet(OUT / "gold_allocation.parquet", index=False)
    curve.to_csv(EXP / "allocation_curve.csv", index=False)
    (OUT / "allocation_recommendation.json").write_text(json.dumps({
        "recommended_alpha": rec, "full_budget_units": full_budget,
        "stock_as_of": str(stock_date.date()), "costs": COSTS}, indent=2))

    print(f"stock as of {stock_date.date()}; full-need budget = {full_budget:,.0f}")
    print(f"recommended alpha = {rec}")
    print(f"wrote gold_allocation.parquet ({len(alloc):,} rows), allocation_curve.csv")


if __name__ == "__main__":
    main()

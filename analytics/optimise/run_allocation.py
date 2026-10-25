"""Produce the allocation plan, efficiency-equity curve and recommended alpha (AN-7, AN-8).

Inputs
  --demand      CSV saved from demand.planning_totals(...): district_code, resource, demand, demand_q90
  --stock-dir   data/incoming/stock (.json/.jsonl lines; latest snapshot per facility is used)
  --facilities  data/reference/facilities.csv   (ASSUMES columns facility_id, district_code)
  --districts   data/reference/districts.csv    (district_code, division, population)
  --cases       gold_weekly_cases used for severity weights w_d (optional; omit for equal
                weights, which is the sensitivity case). Accepts a parquet directory
                (e.g. data/gold/gold_weekly_cases), a single .parquet file, or a CSV.
Outputs (in --out): allocation_plan.csv, efficiency_equity_curve.csv, recommendation.json

Planning uses demand_q90 (upper prediction quantile, Section 6.3). Beds are not a shippable
stock, so only the three consumables are allocated. UNIT_COSTS are relative cost units and
are ASSUMPTIONS: document them in docs/assumptions.md and test sensitivity to them.

Stock input handling (DQ note for the report)
  The stock feed is read from the raw incoming snapshots, not from a silver table, and this
  script applies its own referential-integrity filter: snapshot rows whose facility_id is not
  in the registry (the ~0.5% injected orphans) are dropped BEFORE the latest snapshot per
  facility is chosen. The filter is reported per ROW, not per distinct id (orphan ids are
  mostly unique, so a distinct-id count looks far larger than the real data loss). The run
  stops if the orphan row share exceeds --max-orphan-share, or if a registry facility has no
  stock at all, because either would point to an ID mismatch rather than injected noise.
  The stock quality numbers are written to recommendation.json under "stock_input".
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
# Transport volume per unit (litres of vehicle space). ASSUMPTIONS, illustrative: no logistics data is available.
BUDGET_FRACS = (0.1, 0.2, 0.4, 0.6, 0.8, 1.0)   # efficiency-equity curve and what-if grid
ALPHAS = (0.0, 0.2, 0.4, 0.6, 0.8)
VOLUME = {"iv_saline_units": 1.0, "ns1_kits": 0.05, "ors_sachets": 0.02}


def latest_stock_by_district(stock_dir, facilities, max_orphan_share=0.05, as_of=None):
    """Latest valid snapshot per registry facility, summed to district x resource.
    as_of (a date) ignores snapshots after that date, for planning scenarios at a past date.

    Returns (stock, quality) where quality is a dict of input-quality counts."""
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
    if as_of is not None:
        cut = pd.Timestamp(as_of)
        n_all = len(snap)
        snap = snap[snap["snapshot_date"] <= cut]
        print(f"stock: as of {cut.date()}: using {len(snap):,} of {n_all:,} snapshot rows")
        if snap.empty:
            raise SystemExit(f"no stock snapshots on or before {cut.date()}")

    # referential integrity on ROWS, before choosing the latest snapshot
    registry = set(facilities["facility_id"])
    is_orphan = ~snap["facility_id"].isin(registry)
    n_rows, n_orphan_rows = len(snap), int(is_orphan.sum())
    orphan_share = n_orphan_rows / n_rows if n_rows else 0.0
    n_orphan_ids = int(snap.loc[is_orphan, "facility_id"].nunique())
    valid = snap[~is_orphan]

    covered = registry & set(valid["facility_id"])
    missing = len(registry) - len(covered)
    print(f"stock: {len(files)} files, {n_rows:,} snapshot rows; "
          f"{n_orphan_rows:,} rows ({orphan_share:.2%}) have a facility_id not in the registry "
          f"and are excluded ({n_orphan_ids:,} distinct orphan ids)")
    print(f"stock: {len(covered)} of {len(registry)} registry facilities have a snapshot")
    if orphan_share > max_orphan_share:
        raise SystemExit(f"orphan share {orphan_share:.2%} exceeds --max-orphan-share "
                         f"{max_orphan_share:.0%}: likely a facility_id format mismatch, not noise")
    if missing:
        raise SystemExit(f"{missing} registry facilities have no stock snapshot: check facility_id "
                         f"format between the stock feed and {len(registry)} registry rows")

    last = valid.sort_values("snapshot_date").drop_duplicates("facility_id", keep="last")
    last = last.merge(facilities[["facility_id", "district_code"]], on="facility_id")
    newest, oldest = valid["snapshot_date"].max(), last["snapshot_date"].min()
    stale_days = int((newest - oldest).days)
    print(f"stock: latest snapshot {newest.date()}, oldest snapshot used {oldest.date()} "
          f"({stale_days} days older)")
    if stale_days > 14:
        print("WARNING: some facilities' stock is over 14 days stale; it is treated as current")

    quality = {"snapshot_files": len(files), "snapshot_rows": int(n_rows),
               "orphan_rows": n_orphan_rows, "orphan_row_share": round(orphan_share, 5),
               "distinct_orphan_ids": n_orphan_ids,
               "registry_facilities": len(registry), "registry_facilities_with_stock": len(covered),
               "latest_snapshot": str(newest.date()), "oldest_snapshot_used": str(oldest.date()),
               "max_staleness_days": stale_days}

    long = last.melt(id_vars="district_code", value_vars=list(STOCK_COLS), var_name="resource",
                     value_name="stock")
    stock = long.groupby(["district_code", "resource"], as_index=False)["stock"].sum()
    return stock, quality


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
    """Severity weights from gold_weekly_cases, or None (equal weights) when no path is given.

    path may be a Spark-written parquet directory (partitioned by epi_year), a single
    .parquet file, or a CSV export."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"--cases path not found: {p}")
    cs = (pd.read_parquet(p) if p.is_dir() or p.suffix.lower() == ".parquet"
          else pd.read_csv(p, dtype={"district_code": str}, encoding="utf-8-sig"))
    needed = {"district_code", "cases", "admitted_cases", "deaths"}
    missing = needed - set(cs.columns)
    if missing:
        raise SystemExit(f"--cases table is missing columns {sorted(missing)}; "
                         f"found: {list(cs.columns)}")
    return severity_weights(cs, district_codes)


def resolve_as_of(value):
    """--as-of may be a date or a .json file with an "as_of" field (written by scenario_demand)."""
    if value and str(value).lower().endswith(".json"):
        return json.loads(Path(value).read_text(encoding="utf-8-sig"))["as_of"]
    return value


def add_input_args(ap):
    ap.add_argument("--demand", default="data/analytics/planning_totals.csv")
    ap.add_argument("--stock-dir", default="data/incoming/stock")
    ap.add_argument("--facilities", default="data/reference/facilities.csv")
    ap.add_argument("--districts", default="data/reference/districts.csv")
    ap.add_argument("--cases", default=None,
                    help="gold_weekly_cases (parquet dir/file or csv) for severity weights; "
                         "omit for equal weights")
    ap.add_argument("--as-of", default=None,
                    help="date (YYYY-MM-DD) or a .json file with an as_of field: use only stock snapshots up to "
                         "that date (scenario at a past date; pair with a --demand file built for the same origin)")
    ap.add_argument("--max-orphan-share", type=float, default=0.05,
                    help="stop if more than this share of stock snapshot ROWS has an unknown "
                         "facility_id (injected noise is about 0.5%%)")


def load_scenario(a):
    """Everything the optimiser needs: input frame, population, severity weights (or None), stock-quality numbers."""
    fac = pd.read_csv(a.facilities, dtype={"district_code": str, "facility_id": str}, encoding="utf-8-sig")
    if "district_code" not in fac.columns:
        raise SystemExit(f"facilities.csv has no district_code; columns: {list(fac.columns)}")
    fac["district_code"] = fac["district_code"].str.zfill(2)
    dis = pd.read_csv(a.districts, dtype={"district_code": str}, encoding="utf-8-sig")
    dis["district_code"] = dis["district_code"].str.zfill(2)
    pop = dis.set_index("district_code")["population"]
    demand = pd.read_csv(a.demand, dtype={"district_code": str}, encoding="utf-8-sig")
    stock, stock_quality = latest_stock_by_district(a.stock_dir, fac, a.max_orphan_share, resolve_as_of(a.as_of))
    inp = build_input(demand, stock, dis)
    weights = load_weights(a.cases, sorted(inp["district_code"].unique()))
    return inp, pop, weights, stock_quality


def allocation_grid(inp, pop, weights, vol=None, caps=None, min_cover=0.0):
    """Per-district allocation for every budget level x alpha, plus the proportional baseline at each budget
    (serving layer: what-if panel, VIS-5). One row per policy x budget_frac x alpha x district x resource."""
    full = float(((inp["demand"] - inp["stock"]).clip(lower=0) * inp["resource"].map(UNIT_COSTS)).sum())
    parts = []

    def add(res, frac, alpha, policy):
        part = res[["district_code", "resource", "x", "unmet"]].copy()
        part.insert(0, "policy", policy)
        part.insert(0, "alpha", alpha)
        part.insert(0, "budget_frac", frac)
        parts.append(part)

    for f in BUDGET_FRACS:
        budget = f * full
        add(proportional_baseline(inp, pop, UNIT_COSTS, budget), f, float("nan"), "proportional")
        for al in ALPHAS:
            add(solve_allocation(inp, pop, UNIT_COSTS, budget, al, weights=weights, vol=vol, caps=caps,
                                 min_cover=min_cover), f, al, "optimised")
    grid = pd.concat(parts, ignore_index=True)
    grid[["x", "unmet"]] = grid[["x", "unmet"]].round(3)
    return grid.sort_values(["policy", "budget_frac", "alpha", "district_code", "resource"]
                            ).reset_index(drop=True)


def division_caps(inp, frac):
    """Transport capacity T_k per division = frac x the transport volume of that division's own unmet need.
    An ASSUMPTION used to test whether the transport constraint of Section 6.3 ever binds."""
    need = (inp["demand"] - inp["stock"]).clip(lower=0) * inp["resource"].map(VOLUME)
    return {k: float(frac * v) for k, v in need.groupby(inp["division"]).sum().items()}


def transport_report(plan, vol, caps):
    if not caps:
        return None
    used = (plan["x"] * plan["resource"].map(vol)).groupby(plan["division"]).sum()
    util = {k: float(used.get(k, 0.0) / c) for k, c in caps.items() if c > 0}
    return {"binding_divisions": sorted(k for k, u in util.items() if u >= 0.999),
            "max_utilisation": max(util.values()) if util else 0.0}


def main():
    ap = argparse.ArgumentParser()
    add_input_args(ap)
    ap.add_argument("--out", default="data/analytics/allocation")
    ap.add_argument("--budget-frac", type=float, default=0.5,
                    help="national budget as a share of the cost of all unmet need")
    ap.add_argument("--tolerance", type=float, default=0.05)
    ap.add_argument("--rule", choices=["relative", "need"], default="relative",
                    help="how efficiency loss is measured when recommending alpha (see allocate.recommend_alpha)")
    ap.add_argument("--min-cover", type=float, default=0.0,
                    help="coverage floor: share of its OWN need every district is guaranteed (default 0 = off)")
    ap.add_argument("--transport-frac", type=float, default=None,
                    help="switch on the transport-capacity constraint: each division can move this share of the "
                         "volume of its own unmet need (VOLUME assumptions); omit for no transport limit")
    a = ap.parse_args()
    as_of = resolve_as_of(a.as_of)

    inp, pop, weights, stock_quality = load_scenario(a)
    print("severity weights:", describe(weights) if weights is not None else "equal (no --cases)")
    vol, caps = (VOLUME, division_caps(inp, a.transport_frac)) if a.transport_frac else (None, None)

    curve = efficiency_equity_curve(inp, pop, UNIT_COSTS, budget_fracs=BUDGET_FRACS, alphas=ALPHAS,
                                    weights=weights, vol=vol, caps=caps, min_cover=a.min_cover)
    alpha = recommend_alpha(curve, a.tolerance, a.rule)
    need = float(((inp["demand"] - inp["stock"]).clip(lower=0) * inp["resource"].map(UNIT_COSTS)).sum())
    budget = a.budget_frac * need
    plan = solve_allocation(inp, pop, UNIT_COSTS, budget, alpha, weights=weights, vol=vol, caps=caps,
                            min_cover=a.min_cover)
    base = proportional_baseline(inp, pop, UNIT_COSTS, budget)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    plan.to_csv(out / "allocation_plan.csv", index=False)
    curve.to_csv(out / "efficiency_equity_curve.csv", index=False)
    grid = allocation_grid(inp, pop, weights, vol, caps, a.min_cover)
    grid.to_csv(out / "allocation_grid.csv", index=False)
    print(f"wrote allocation_grid.csv: {len(grid):,} rows")
    summary = {"budget": budget, "budget_frac": a.budget_frac, "recommended_alpha": alpha,
               "tolerance": a.tolerance, "rule": a.rule, "as_of": as_of or (stock_quality or {}).get("latest_snapshot"), "min_cover": a.min_cover,
               "min_cover_used": plan.attrs.get("min_cover_used", 0.0),
               "transport_frac": a.transport_frac, "transport": transport_report(plan, vol, caps),
               "weights": "severity (from --cases)" if weights is not None else "equal",
               "stock_input": stock_quality,
               "optimised": metrics(plan, UNIT_COSTS, weights),
               "proportional_baseline": metrics(base, UNIT_COSTS, weights)}
    (out / "recommendation.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

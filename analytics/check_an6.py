"""AN-6 acceptance check on the REAL pipeline outputs.

    python -m analytics.check_an6 [--analytics data/analytics]

Recomputes gold_demand from gold_forecast with the numbers in ASSUMPTIONS, independently of compute_demand,
and checks it covers what the brief asks for (beds, IV saline, test kits, with stated assumptions and uncertainty).
Exit code 1 if anything fails.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from analytics.io_util import load_table
from analytics.optimise.demand import (ASSUMPTIONS as A, DISEASES, KEYS, RESOURCES, ROOT, planning_totals)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analytics", default=str(ROOT / "data" / "analytics"))
    ap.add_argument("--model", default="nb_lagged_weather")
    args = ap.parse_args()
    fc = load_table(args.analytics, "gold_forecast")
    dem = load_table(args.analytics, "gold_demand")
    plan = load_table(args.analytics, "planning_totals")

    f = fc[(fc["model"] == args.model) & (~fc["is_backtest"].astype(bool))].copy()
    f["district_code"] = f["district_code"].astype(str).str.zfill(2)
    f["_o"] = f["origin_epi_year"] * 100 + f["origin_epi_week"]
    f = f[f["_o"] == f.groupby(KEYS)["_o"].transform("max")]
    dem = dem.copy()
    dem["district_code"] = dem["district_code"].astype(str).str.zfill(2)

    # coverage
    check("every live forecast series has all 4 horizons", (f.groupby(KEYS)["horizon_weeks"].nunique() == 4).all())
    check("demand has one row per live forecast row", len(dem) == len(f), f"{len(dem)} vs {len(f)}")
    check("all four diseases are converted", set(dem["disease"]) == set(DISEASES), str(sorted(set(dem['disease']))))
    check("districts covered", dem["district_code"].nunique() == f["district_code"].nunique(),
          f"{dem['district_code'].nunique()} districts")
    num = [c for c in dem.columns if c.endswith(("_needed", "_needed_q90", "_needed_q10"))]
    check("no missing or negative demand values", dem[num].notna().all().all() and (dem[num] >= 0).all().all())

    # independent recomputation of the per-case resources
    m = dem.merge(f[KEYS + ["horizon_weeks", "pred_mean", "q90"]], on=KEYS + ["horizon_weeks"], validate="1:1")
    for col, key in (("pred_mean", ""), ("q90", "_q90")):
        v = m[col] if col == "pred_mean" else np.maximum(m[col], m["pred_mean"])  # q90 is floored at the mean
        want = {
            "iv_saline_units": v * m["disease"].map(A["saline_units_per_case"]),
            "ns1_kits": v * m["disease"].map(A["ns1_tests_per_case"]) * (1 + A["ns1_wastage"]),
            "ors_sachets": v * m["disease"].map(A["ors_sachets_per_case"]),
        }
        for res, w in want.items():
            check(f"{res}{key} = forecast x coefficient", np.allclose(m[f"{res}_needed{key}"], w, atol=1e-6),
                  f"max abs diff {np.abs(m[f'{res}_needed{key}'] - w).max():.2e}")

    # beds: implied admission rate must be a valid, stable rate
    pos = m[m["pred_mean"] > 0].copy()
    pos["rate"] = pos["beds_needed"] * 7 / (pos["disease"].map(A["los_days"]) * pos["pred_mean"])
    check("implied admission rate in (0, 1]", ((pos["rate"] > 0) & (pos["rate"] <= 1 + 1e-9)).all(),
          f"min {pos['rate'].min():.3f} max {pos['rate'].max():.3f}")
    spread = pos.groupby(KEYS)["rate"].agg(lambda s: s.max() - s.min())
    check("admission rate constant across horizons per series", (spread < 1e-9).all())
    by_d = pos.groupby("disease")["rate"].median().round(3).to_dict()
    print("    median implied admission rate by disease:", by_d)

    # uncertainty
    for r in RESOURCES:
        check(f"{r}: q90 >= mean", (dem[f"{r}_needed_q90"] >= dem[f"{r}_needed"] - 1e-9).all())
        if f"{r}_needed_q10" in dem:
            check(f"{r}: q10 <= mean", (dem[f"{r}_needed_q10"] <= dem[f"{r}_needed"] + 1e-9).all())
    print("    lower quantile (q10) columns:", "present" if "beds_needed_q10" in dem.columns
          else "absent (optional: add q10 to gold_forecast to show a full band)")
    zero_q90 = int(((m["q90"] == 0) & (m["pred_mean"] > 0)).sum())
    print(f"    rows with q90 = 0 but mean > 0: {zero_q90} (integer quantiles; q90 planning is conservative on sums)")

    # planning totals handed to the optimiser
    want = planning_totals(dem)
    got = plan.copy()
    got["district_code"] = got["district_code"].astype(str).str.zfill(2)
    j = want.merge(got, on=["district_code", "resource"], suffixes=("", "_stored"), validate="1:1")
    check("planning_totals matches gold_demand",
          len(j) == len(want) == len(got) and np.allclose(j["demand"], j["demand_stored"])
          and np.allclose(j["demand_q90"], j["demand_q90_stored"]))
    tot = got.groupby("resource")[["demand", "demand_q90"]].sum().round(1)
    print(tot.to_string())

    # documentation agrees with code
    doc = Path(ROOT / "docs" / "demand_assumptions.md")
    txt = doc.read_text(encoding="utf-8") if doc.exists() else ""
    check("docs/demand_assumptions.md exists and has no 'placeholder' wording", bool(txt) and "placeholder" not in txt.lower())
    check("doc lists every coefficient in code",
          all(str(v) in txt for k in ("saline_units_per_case", "los_days", "ors_sachets_per_case") for v in A[k].values()))

    bad = RESULTS.count(False)
    print(f"\n{len(RESULTS) - bad}/{len(RESULTS)} checks passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

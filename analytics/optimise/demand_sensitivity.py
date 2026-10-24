"""AN-6 sensitivity: how much do the national planning totals move if each conversion assumption is +/-25%?

    python -m analytics.optimise.demand_sensitivity [--analytics data/analytics]

Admission rates are recovered from gold_demand (beds x 7 / (stay x forecast mean)), so the run uses exactly the rates
of the last pipeline run. Writes data/analytics/demand_sensitivity.csv and docs/demand_sensitivity.md.
"""
import argparse

import numpy as np
import pandas as pd

from analytics.io_util import load_table
from analytics.optimise.demand import ASSUMPTIONS as A, KEYS, ROOT, assumption_sensitivity


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analytics", default=str(ROOT / "data" / "analytics"))
    ap.add_argument("--model", default="nb_lagged_weather")
    args = ap.parse_args()
    fc = load_table(args.analytics, "gold_forecast")
    dem = load_table(args.analytics, "gold_demand")
    dem["district_code"] = dem["district_code"].astype(str).str.zfill(2)

    f = fc[(fc["model"] == args.model) & (~fc["is_backtest"].astype(bool))].copy()
    f["district_code"] = f["district_code"].astype(str).str.zfill(2)
    m = dem.merge(f[KEYS + ["horizon_weeks", "pred_mean"]], on=KEYS + ["horizon_weeks"])
    m = m[m["pred_mean"] > 0]
    m["admission_rate"] = m["beds_needed"] * 7 / (m["disease"].map(A["los_days"]) * m["pred_mean"])
    rates = m.groupby(KEYS, as_index=False)["admission_rate"].median()

    s = assumption_sensitivity(fc, rates, model=args.model)
    out_csv = ROOT / "data" / "analytics" / "demand_sensitivity.csv"
    s.to_csv(out_csv, index=False)
    s = s.sort_values(["resource", "assumption", "factor"])
    print(s.round(2).to_string(index=False))

    lines = ["# AN-6 sensitivity of national planning demand", "",
             "Each conversion value is scaled by 0.75 and 1.25 with everything else fixed. "
             "Totals are the 4-week national planning demand (mean and q90).", "",
             "| Resource | Assumption | Factor | Demand | Change % | Demand q90 | Change % q90 |",
             "|---|---|---|---|---|---|---|"]
    for r in s.itertuples():
        lines.append(f"| {r.resource} | {r.assumption} | {r.factor} | {r.national_demand:,.1f} | "
                     f"{r.change_pct:+.1f} | {r.national_demand_q90:,.1f} | {r.change_pct_q90:+.1f} |")
    lines += ["", "Demand is linear in every value except the admission rate, so a 25% change in a coefficient moves "
              "the matching resource by 25%. Beds respond only to length of stay; saline only to saline per case."]
    doc = ROOT / "docs" / "demand_sensitivity.md"
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {out_csv} and {doc}")


if __name__ == "__main__":
    main()

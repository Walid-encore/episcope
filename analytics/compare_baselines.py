"""Sensitivity analysis of the EARS baseline (AN-2, AN-3, conflict C1). Reads ground truth, so it lives in
analytics/ and NOT in analytics/detection/.

    python -m analytics.compare_baselines [--gold data/standin_gold] [--max-fa 0.5] [--grace 14]

Why: the current baseline (7 weeks ending 3 weeks before the scored week, at least 5 usable weeks) leaves
weeks unscored where reporting is patchy. This compares ways of closing that gap at the same evaluation
rules as run_detection.py: unscored share after warm-up, and the operating point (highest sensitivity with
false alerts per district-year <= max-fa).

Read the result with care: 21 outbreaks, one data set. A difference of one or two detected outbreaks is not
evidence. Choose on principle (no blind spots) unless a variant clearly hurts the false-alert rate, and
report the table as a sensitivity analysis, not as tuning.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from analytics.detection.ears_c2 import ears_c2_pandas
from analytics.detection.evaluate import load_truth, threshold_sweep
from analytics.detection.gold_input import load_detector_input
from analytics.detection.run_detection import FLOORS, GRIDS, SCORED, pick_operating_point
from analytics.detection.variants import ears_c2_usable_pandas

ROOT = Path(__file__).resolve().parents[1]
KEYS3 = ["district_code", "disease", "week_start_date"]
COMMON_WARM_UP = 9      # weeks skipped per series when counting gaps, same for every variant

VARIANTS = {
    "current (7 wk, min 5)":        lambda d, t: ears_c2_pandas(d, threshold=t),
    "min_n 3":                      lambda d, t: ears_c2_pandas(d, threshold=t, min_n=3),
    "baseline 10 wk":               lambda d, t: ears_c2_pandas(d, threshold=t, baseline=10),
    "baseline 12 wk":               lambda d, t: ears_c2_pandas(d, threshold=t, baseline=12),
    "last 7 usable weeks":          lambda d, t: ears_c2_usable_pandas(d, threshold=t),
}


def unscored_after_warm_up(scored):
    s = scored.sort_values(["district_code", "disease", "week_start_date"])
    pos = s.groupby(["district_code", "disease"]).cumcount()
    after = s[pos >= COMMON_WARM_UP]
    return int(after["score"].isna().sum()), len(after)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold"))
    ap.add_argument("--truth", default=str(ROOT / "data" / "truth" / "ground_truth_outbreaks.csv"))
    ap.add_argument("--max-fa", type=float, default=0.5)
    ap.add_argument("--grace", type=int, default=14)
    a = ap.parse_args()

    data = load_detector_input(a.gold)
    truth = load_truth(a.truth)
    truth = truth[truth["disease"].isin(SCORED)].reset_index(drop=True)
    grid = GRIDS["ears_c2"]
    rows = []
    for name, fn in VARIANTS.items():
        scored = fn(data, float(grid[0]))
        miss, total = unscored_after_warm_up(scored)
        scored = scored.merge(data[KEYS3 + ["cases"]], on=KEYS3, how="left")
        curve = pd.concat([threshold_sweep(scored, truth, grid, window_grace_days=a.grace, min_cases=f)
                           for f in FLOORS], ignore_index=True)
        op, feasible = pick_operating_point(curve, a.max_fa)
        rows.append(dict(variant=name, unscored_weeks=miss, unscored_share=round(miss / total, 4),
                         feasible=feasible, threshold=op["threshold"], min_cases=int(op["min_cases"]),
                         sensitivity=round(float(op["sensitivity"]), 3), n_detected=int(op["n_detected"]),
                         n_outbreaks=int(op["n_outbreaks"]),
                         false_per_district_year=round(float(op["false_alerts_per_district_year"]), 3),
                         median_days_to_detect=op["median_days_to_detect"]))
        print(f"done: {name}", flush=True)
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(f"\nEARS C2 baseline variants (feasible = false alerts per district-year <= {a.max_fa}):")
    print(out.to_string(index=False))
    dest = ROOT / "docs" / "experiments" / "detection_baseline_variants.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest, index=False)
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()

"""Detection evaluation on the report-week gold tables (AN-1, AN-2, AN-3, conflict C1).

    python -m analytics.detection.run_detection [--gold data/standin_gold] [--max-fa 0.5]

Runs EARS C2 and CUSUM once (scores do not depend on the threshold), sweeps the threshold,
compares against ground truth (only this script and evaluate.py read data/truth), and shows
the artefact rule's effect (robust vs naive = artefact suppression switched off).

Evaluation choices (state them in the report):
  * scored diseases: dengue and diarrhoeal only (the gold marts).
  * alerts are on REPORT weeks, outbreaks on ONSET dates, so a window grace of --grace days
    (default 14) lets late-arriving signals count as in-window.
  * operating point = the threshold with the highest sensitivity whose false alerts per
    district-year stay <= --max-fa (ties: fewest median days to detect).
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .cusum import cusum_pandas
from .ears_c2 import ears_c2_pandas
from .evaluate import evaluate, load_truth, threshold_sweep
from .gold_input import load_detector_input

ROOT = Path(__file__).resolve().parents[2]
SCORED = ["dengue", "diarrhoeal"]
GRIDS = {"ears_c2": np.arange(1.0, 6.01, 0.5), "cusum": np.arange(1.0, 10.01, 0.5)}


def pick_operating_point(curve, max_fa):
    ok = curve[curve["false_alerts_per_district_year"] <= max_fa]
    if ok.empty:
        return curve.sort_values("false_alerts_per_district_year").iloc[0]
    return ok.sort_values(["sensitivity", "median_days_to_detect"], ascending=[False, True]).iloc[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "standin_gold"))
    ap.add_argument("--truth", default=str(ROOT / "data" / "truth" / "ground_truth_outbreaks.csv"))
    ap.add_argument("--max-fa", type=float, default=0.5)
    ap.add_argument("--grace", type=int, default=14)
    a = ap.parse_args()

    data = load_detector_input(a.gold)
    truth = load_truth(a.truth)
    truth = truth[truth["disease"].isin(SCORED)].reset_index(drop=True)
    print(f"series: {data.groupby(['district_code', 'disease']).ngroups}, scored outbreaks: {len(truth)}")

    runs = {"ears_c2": ears_c2_pandas, "cusum": cusum_pandas}
    exp = ROOT / "docs" / "experiments"
    exp.mkdir(parents=True, exist_ok=True)
    summary = []
    for name, fn in runs.items():
        scored = fn(data, threshold=float(GRIDS[name][0]))
        curve = threshold_sweep(scored, truth, GRIDS[name], window_grace_days=a.grace)
        curve.insert(0, "detector", name)
        curve.to_csv(exp / f"detection_sweep_{name}.csv", index=False)
        op = pick_operating_point(curve, a.max_fa)
        # artefact rule: robust (suppression on) vs naive (suppression off) at the chosen threshold
        at = scored.copy()
        at["alert_flag"] = at["score"] >= op["threshold"]
        robust = evaluate(at, truth, a.grace)
        naive = evaluate(at.drop(columns=["reporting_artefact_flag"]), truth, a.grace)
        summary.append(dict(detector=name, threshold=op["threshold"],
                            sensitivity=robust["sensitivity"],
                            false_per_district_year=robust["false_alerts_per_district_year"],
                            median_days_to_detect=robust["median_days_to_detect"],
                            false_per_district_year_naive=naive["false_alerts_per_district_year"],
                            false_episodes_robust=robust["false_alert_episodes"],
                            false_episodes_naive=naive["false_alert_episodes"]))
        print(f"\n{name}: trade-off curve")
        print(curve.round(3).to_string(index=False))
    s = pd.DataFrame(summary)
    pd.set_option("display.width", 220)
    print(f"\nOperating points (false alerts per district-year <= {a.max_fa}):")
    print(s.round(3).to_string(index=False))
    s.to_csv(exp / "detection_operating_points.csv", index=False)


if __name__ == "__main__":
    main()

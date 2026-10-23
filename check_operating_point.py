"""EVALUATION ONLY (reads ground truth, so it lives outside analytics/detection).

    python check_operating_point.py [--gold data/gold_csv]

Compares EARS C2 operating points and shows what the reporting-artefact rule costs: the same alerts
scored with the artefact flag (robust) and without it (naive).
"""
import argparse
from pathlib import Path

from analytics.detection.ears_c2 import ears_c2_pandas
from analytics.detection.evaluate import evaluate, load_truth
from analytics.detection.gold_input import load_detector_input
from analytics.detection.run_detection import SCORED

KEYS = ["district_code", "disease", "week_start_date"]
POINTS = [(6.5, 15), (6.5, 0), (7.5, 0), (7.5, 15), (9.0, 0)]
GRACE = 14


def short(r):
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()
            if k in ("sensitivity", "false_alerts_per_district_year", "median_days_to_detect",
                     "n_detected", "n_outbreaks")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="data/gold_csv")
    ap.add_argument("--truth", default="data/truth/ground_truth_outbreaks.csv")
    a = ap.parse_args()
    data = load_detector_input(a.gold)
    truth = load_truth(a.truth)
    truth = truth[truth["disease"].isin(SCORED)].reset_index(drop=True)
    scored = ears_c2_pandas(data, threshold=1.0)
    scored = scored.merge(data[KEYS + ["cases"]], on=KEYS, how="left")
    for thr, floor in POINTS:
        at = scored.copy()
        at["alert_flag"] = (at["score"] >= thr) & (at["cases"] >= floor)
        print(f"\nEARS C2 threshold {thr}, count floor {floor}")
        print("  with artefact rule   :", short(evaluate(at, truth, GRACE)))
        print("  without artefact rule:", short(evaluate(at.drop(columns=["reporting_artefact_flag"]), truth, GRACE)))


if __name__ == "__main__":
    main()

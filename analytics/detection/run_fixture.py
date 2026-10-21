"""Run EARS C2 and CUSUM (naive vs robust) on the fixture and print the comparison.

From the repo root:   python -m analytics.detection.run_fixture
Writes docs/experiments/detection_sweep_fixture.csv (threshold trade-off curve data for conflict C1).
This script reads the fixture truth file because it is EVALUATION code, not a detector.
"""
from pathlib import Path

import pandas as pd

from .cusum import cusum_pandas
from .ears_c2 import ears_c2_pandas
from .evaluate import evaluate, load_truth, threshold_sweep

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures"
SWEEPS = {"ears_c2": [2, 2.5, 3, 3.5, 4, 5, 6, 8], "cusum": [2, 3, 4, 5, 6, 8, 10, 14]}


def naive(alerts):
    a = alerts.copy()
    a["reporting_artefact_flag"] = False
    return a


def main():
    cases = pd.read_csv(FIX / "gold_weekly_cases_fixture.csv", dtype={"district_code": str})
    truth = load_truth(FIX / "ground_truth_fixture.csv")
    art = pd.read_csv(FIX / "artefact_fixture.csv", dtype={"district_code": str}).iloc[0]
    fns = {"ears_c2": ears_c2_pandas, "cusum": cusum_pandas}

    rows, sweeps = [], []
    for name, fn in fns.items():
        for variant, mask in [("naive", False), ("robust", True)]:
            a = fn(cases, mask=mask)
            if variant == "naive":
                a = naive(a)
            m = evaluate(a, truth)
            hit = a[(a.district_code == art.district_code) & (a.disease == art.disease)
                    & (a.week_start_date == art.backfill_week_start)].iloc[0]
            m.update(detector=name, variant=variant,
                     artefact_week_alert_raised=bool(hit.alert_flag),
                     artefact_week_alert_counted=bool(hit.alert_flag and not hit.reporting_artefact_flag))
            rows.append(m)
        s = threshold_sweep(fn(cases, mask=True), truth, SWEEPS[name])
        s.insert(0, "detector", name)
        sweeps.append(s)

    cols = ["detector", "variant", "sensitivity", "false_alerts_per_district_year",
            "median_days_to_detect", "artefact_week_alert_raised", "artefact_week_alert_counted"]
    pd.set_option("display.width", 200)
    print(pd.DataFrame(rows)[cols].round(2).to_string(index=False))
    sweep = pd.concat(sweeps)
    print("\nThreshold sweep (robust variant):")
    print(sweep.round(2).to_string(index=False))
    out = ROOT / "docs" / "experiments"
    out.mkdir(parents=True, exist_ok=True)
    sweep.to_csv(out / "detection_sweep_fixture.csv", index=False)
    print(f"\nwrote {out / 'detection_sweep_fixture.csv'}")


if __name__ == "__main__":
    main()

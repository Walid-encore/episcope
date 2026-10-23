"""gold_alerts (docs/gold_contract.md): EARS C2 scores and alerts per district x disease x REPORT week.

    python -m analytics.detection.make_alerts [--gold data/standin_gold] [--out data/analytics]
                                              [--engine spark|pandas]

Runs EARS C2 at a FIXED operating point and writes gold_alerts.parquet. One row per scored
district x disease x report week, so the dashboard can re-threshold (what-if panel) from the scores.

Why the operating point is a constant and is not re-tuned here: choosing it needs ground truth,
which only run_detection.py / evaluate.py may read, and the hidden 14-day drop has none.
(threshold 6.5, count floor 15) is the point run_detection.py selects: highest sensitivity with
false alerts per district-year <= 0.5. If you change it, re-run run_detection.py and update the
constants below together.

Columns (contract): district_code, disease, epi_year, epi_week (REPORT week), detector, score,
threshold, alert_flag, severity, reporting_artefact_flag, run_id.
Extra columns (allowed, additive): week_start_date, min_cases.
  alert_flag               score >= threshold AND cases >= min_cases AND NOT inside a reporting artefact
  reporting_artefact_flag  the week is inside a silent-facility / back-fill artefact (alert suppressed)
  severity                 alert weeks only: score / threshold < SEV_MEDIUM -> low, < SEV_HIGH -> medium,
                           otherwise high (ASSUMPTION, document in docs/assumptions.md); null otherwise
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from analytics.engine import on_spark, timed

from .ears_c2 import ears_c2_pandas, ears_c2_spark
from .gold_input import load_detector_input

ROOT = Path(__file__).resolve().parents[2]
DETECTOR = "ears_c2"
THRESHOLD = 6.5     # EARS C2 score
MIN_CASES = 15      # count floor: no alert on fewer cases than this in the week
SEV_MEDIUM = 1.5    # score / threshold ratios for the severity label
SEV_HIGH = 2.0
KEYS = ["district_code", "disease", "week_start_date"]
COLUMNS = ["district_code", "disease", "epi_year", "epi_week", "detector", "score", "threshold",
           "alert_flag", "severity", "reporting_artefact_flag", "run_id", "week_start_date", "min_cases"]


def build_alerts(data, threshold=THRESHOLD, min_cases=MIN_CASES, run_id="local", engine="pandas"):
    series = data.groupby(["district_code", "disease"]).ngroups
    with timed("detection_ears_c2", engine, len(data), series):
        if engine == "spark":
            scored = on_spark(lambda s: ears_c2_spark(s, threshold=threshold, run_id=run_id), data,
                              "episcope-detection")
        else:
            scored = ears_c2_pandas(data, threshold=threshold, run_id=run_id)
    scored = (scored.drop(columns=["cases", "epi_year", "epi_week"], errors="ignore")
                    .merge(data[KEYS + ["cases", "epi_year", "epi_week"]], on=KEYS, how="left"))
    scored = scored[scored["score"].notna()].copy()
    if "reporting_artefact_flag" in scored.columns:
        art = scored["reporting_artefact_flag"].fillna(False).astype(bool)
    else:
        art = pd.Series(False, index=scored.index)
    over = (scored["score"] >= threshold) & (scored["cases"] >= min_cases)
    alert = over & ~art
    ratio = scored["score"] / threshold
    sev = np.where(ratio < SEV_MEDIUM, "low", np.where(ratio < SEV_HIGH, "medium", "high"))
    out = pd.DataFrame({
        "district_code": scored["district_code"].astype(str),
        "disease": scored["disease"],
        "epi_year": scored["epi_year"].astype(int),
        "epi_week": scored["epi_week"].astype(int),
        "detector": DETECTOR,
        "score": scored["score"].astype(float).round(3),
        "threshold": float(threshold),
        "alert_flag": alert.astype(bool),
        "severity": pd.Series(np.where(alert, sev, None), index=scored.index, dtype="object"),
        "reporting_artefact_flag": art.astype(bool),
        "run_id": run_id,
        "week_start_date": pd.to_datetime(scored["week_start_date"]),
        "min_cases": int(min_cases),
    })
    return (out.sort_values(["week_start_date", "district_code", "disease"])
               .reset_index(drop=True)[COLUMNS])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "standin_gold"))
    ap.add_argument("--out", default=str(ROOT / "data" / "analytics"))
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--min-cases", type=int, default=MIN_CASES)
    ap.add_argument("--run-id", default="local")
    ap.add_argument("--engine", choices=["spark", "pandas"], default="spark")
    a = ap.parse_args()

    data = load_detector_input(a.gold)
    alerts = build_alerts(data, a.threshold, a.min_cases, a.run_id, a.engine)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    alerts.to_parquet(out / "gold_alerts.parquet", index=False)

    n_alert = int(alerts["alert_flag"].sum())
    n_supp = int((alerts["reporting_artefact_flag"] & (alerts["score"] >= a.threshold)).sum())
    last = alerts["week_start_date"].max()
    now = alerts[(alerts["week_start_date"] == last) & alerts["alert_flag"]]
    print(f"{DETECTOR} threshold {a.threshold} floor {a.min_cases}: {len(alerts):,} scored weeks, "
          f"{n_alert} alerts, {n_supp} above-threshold weeks suppressed as reporting artefacts")
    print(f"latest report week {pd.Timestamp(last).date()}: {len(now)} active alerts")
    print(f"wrote {out / 'gold_alerts.parquet'}")


if __name__ == "__main__":
    main()

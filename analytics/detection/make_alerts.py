"""gold_alerts (docs/gold_contract.md): EARS C2 scores and alerts per district x disease x REPORT week.

    python -m analytics.detection.make_alerts [--gold data/standin_gold] [--out data/analytics]
                                              [--engine spark|pandas]

Runs EARS C2 at a FIXED operating point and writes gold_alerts.parquet. ONE ROW PER district x disease x
report week, scored or not, so the dashboard can re-threshold (what-if panel) from the scores AND can tell
"no alert" from "not assessed". A week gets no score when fewer than MIN_N usable baseline weeks exist
(series warm-up, or the baseline weeks were masked as under-reporting / back-fill). Those weeks are kept
with score = null, assessed = False and a reason; they never raise an alert.

Why the operating point is a constant and is not re-tuned here: choosing it needs ground truth,
which only run_detection.py / evaluate.py may read, and the hidden 14-day drop has none.
(threshold 7.5, count floor 0 on the current gold) is the point run_detection.py selects: highest sensitivity with
false alerts per district-year <= 0.5. If you change it, re-run run_detection.py and update the
constants below together.

Columns (contract): district_code, disease, epi_year, epi_week (REPORT week), detector, score,
threshold, alert_flag, severity, reporting_artefact_flag, run_id.
Extra columns (allowed, additive): week_start_date, min_cases, assessed, not_assessed_reason.
  alert_flag               score >= threshold AND cases >= min_cases AND NOT inside a reporting artefact
  reporting_artefact_flag  the week is inside a silent-facility / back-fill artefact (alert suppressed)
  assessed                 False when score is null (the detector could not judge this week)
  not_assessed_reason      "warm_up" (first GUARD + MIN_N weeks of a series) or "baseline_masked"
                           (too few usable baseline weeks because earlier weeks were under-reporting); null if assessed
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
THRESHOLD = 7.5     # EARS C2 score
MIN_CASES = 0       # count floor: no alert on fewer cases than this in the week
BASELINE = 7        # weeks in the baseline window
GUARD = 2           # guard band (weeks between the baseline and the scored week)
MIN_N = 5           # usable baseline weeks needed to score a week
WARM_UP_WEEKS = GUARD + MIN_N   # first scored position of a series (tests/test_alerts_assessed.py checks this)
SEV_MEDIUM = 1.5    # score / threshold ratios for the severity label
SEV_HIGH = 2.0
KEYS = ["district_code", "disease", "week_start_date"]
COLUMNS = ["district_code", "disease", "epi_year", "epi_week", "detector", "score", "threshold",
           "alert_flag", "severity", "reporting_artefact_flag", "run_id", "week_start_date", "min_cases",
           "assessed", "not_assessed_reason"]


def _norm_keys(df):
    """Same key types whichever engine produced the frame (Spark can return dates as objects)."""
    df = df.copy()
    df["district_code"] = df["district_code"].astype(str).str.zfill(2)
    wk = pd.to_datetime(df["week_start_date"])
    if getattr(wk.dt, "tz", None) is not None:
        wk = wk.dt.tz_localize(None)
    # round, not normalize: the Spark round trip shifts midnight by the local UTC offset (Mon 00:00 -> Sun 18:00)
    df["week_start_date"] = wk.dt.round("D").astype("datetime64[ns]")
    return df


def build_alerts(data, threshold=THRESHOLD, min_cases=MIN_CASES, run_id="local", engine="pandas"):
    series = data.groupby(["district_code", "disease"]).ngroups
    with timed("detection_ears_c2", engine, len(data), series):
        if engine == "spark":
            scored = on_spark(lambda s: ears_c2_spark(s, threshold=threshold, run_id=run_id, baseline=BASELINE,
                                                      guard=GUARD, min_n=MIN_N), data, "episcope-detection")
        else:
            scored = ears_c2_pandas(data, threshold=threshold, run_id=run_id, baseline=BASELINE,
                                    guard=GUARD, min_n=MIN_N)
    scored = _norm_keys(scored.drop(columns=["cases", "epi_year", "epi_week"], errors="ignore"))
    scored = scored.merge(_norm_keys(data)[KEYS + ["cases", "epi_year", "epi_week"]], on=KEYS, how="left")
    if scored["epi_year"].isna().any():
        raise SystemExit(f"{int(scored['epi_year'].isna().sum())} scored rows did not match the input weeks "
                         f"(engine={engine}); key dtypes: {dict(scored[KEYS].dtypes.astype(str))}")
    # keep EVERY week: a week without a score is reported as "not assessed", never silently dropped
    scored = scored.sort_values(["district_code", "disease", "week_start_date"]).reset_index(drop=True)
    pos = scored.groupby(["district_code", "disease"]).cumcount()
    assessed = scored["score"].notna()
    reason = pd.Series(np.where(assessed, None, np.where(pos < WARM_UP_WEEKS, "warm_up", "baseline_masked")),
                       index=scored.index, dtype="object")
    if "reporting_artefact_flag" in scored.columns:
        art = scored["reporting_artefact_flag"].fillna(False).astype(bool)
    else:
        art = pd.Series(False, index=scored.index)
    over = (scored["score"] >= threshold) & (scored["cases"] >= min_cases)   # null score -> False
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
        "assessed": assessed.astype(bool),
        "not_assessed_reason": reason,
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
    not_ass = alerts[~alerts["assessed"]]
    n_warm = int((not_ass["not_assessed_reason"] == "warm_up").sum())
    n_mask = int((not_ass["not_assessed_reason"] == "baseline_masked").sum())
    last = alerts["week_start_date"].max()
    now = alerts[(alerts["week_start_date"] == last) & alerts["alert_flag"]]
    print(f"{DETECTOR} threshold {a.threshold} floor {a.min_cases}: {len(alerts):,} weeks, "
          f"{int(alerts['assessed'].sum()):,} assessed, {len(not_ass):,} not assessed "
          f"({n_warm:,} warm-up, {n_mask:,} baseline masked), "
          f"{n_alert} alerts, {n_supp} above-threshold weeks suppressed as reporting artefacts")
    print(f"latest report week {pd.Timestamp(last).date()}: {len(now)} active alerts")
    print(f"wrote {out / 'gold_alerts.parquet'}")


if __name__ == "__main__":
    main()

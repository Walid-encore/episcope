"""Detection evaluation on the report-week gold tables (AN-1, AN-2, AN-3, conflict C1).

    python -m analytics.detection.run_detection [--gold data/standin_gold] [--max-fa 0.5]
                                                [--baseline usable|calendar]

Runs EARS C2 and CUSUM once. EARS C2 uses the last-7-USABLE-weeks baseline by default (--baseline usable,
the variant make_alerts.py ships); --baseline calendar runs the original calendar-window C2 as
"ears_c2_calendar" for the before/after comparison in the report (separate output files). (scores do not depend on the threshold), sweeps the threshold,
compares against ground truth (only this script and evaluate.py read data/truth), and shows
the artefact rule's effect (robust vs naive = artefact suppression switched off).

Evaluation choices (state them in the report):
  * scored diseases: dengue and diarrhoeal only (the gold marts).
  * alerts are on REPORT weeks, outbreaks on ONSET dates, so a window grace of --grace days
    (default 14) lets late-arriving signals count as in-window.
  * operating point = the (threshold, minimum-case floor) with the highest sensitivity whose false
    alerts per district-year stay <= --max-fa (ties: fewest median days to detect). If none is
    feasible the summary says feasible=False and shows the least noisy point instead.
  * AN-3 artefact check and a per-outbreak "was it already alerting?" diagnostic are written to
    docs/experiments/.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .cusum import cusum_pandas
from .ears_c2 import ears_c2_pandas
from .evaluate import (artefact_check, evaluate, load_artefacts, load_truth, per_outbreak_context,
                       threshold_sweep)
from .gold_input import load_detector_input
from .variants import ears_c2_usable_pandas

ROOT = Path(__file__).resolve().parents[2]
SCORED = ["dengue", "diarrhoeal"]
GRIDS = {"ears_c2": np.arange(1.0, 12.01, 0.5),
         "ears_c2_calendar": np.arange(1.0, 12.01, 0.5),
         "cusum": np.concatenate([np.arange(1.0, 10.0, 0.5), np.arange(10.0, 30.01, 2.0)])}
DEFAULT_T = {"ears_c2": 3.0, "ears_c2_calendar": 3.0, "cusum": 5.0}   # textbook / default thresholds, for the artefact check
FLOORS = (0, 3, 5, 8, 10, 15)   # minimum cases in the week for an alert (count floor)


def pick_operating_point(curve, max_fa):
    """Highest sensitivity with false alerts per district-year <= max_fa. Returns (row, feasible).
    If nothing meets the limit, returns the row with the fewest false alerts and feasible=False."""
    ok = curve[curve["false_alerts_per_district_year"] <= max_fa]
    if ok.empty:
        return curve.sort_values("false_alerts_per_district_year").iloc[0], False
    return ok.sort_values(["sensitivity", "median_days_to_detect"], ascending=[False, True]).iloc[0], True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "standin_gold"))
    ap.add_argument("--truth", default=str(ROOT / "data" / "truth" / "ground_truth_outbreaks.csv"))
    ap.add_argument("--artefacts", default=str(ROOT / "data" / "truth" / "ground_truth_artefacts.csv"))
    ap.add_argument("--max-fa", type=float, default=0.5)
    ap.add_argument("--grace", type=int, default=14)
    ap.add_argument("--baseline", choices=["usable", "calendar"], default="usable",
                    help="EARS C2 baseline: last 7 usable weeks (default, what gold_alerts uses) or the calendar window")
    a = ap.parse_args()

    data = load_detector_input(a.gold)
    truth = load_truth(a.truth)
    truth = truth[truth["disease"].isin(SCORED)].reset_index(drop=True)
    print(f"series: {data.groupby(['district_code', 'disease']).ngroups}, scored outbreaks: {len(truth)}")

    if a.baseline == "usable":
        runs = {"ears_c2": ears_c2_usable_pandas, "cusum": cusum_pandas}
    else:
        runs = {"ears_c2_calendar": ears_c2_pandas, "cusum": cusum_pandas}
    exp = ROOT / "docs" / "experiments"
    exp.mkdir(parents=True, exist_ok=True)
    summary = []
    artefacts = load_artefacts(a.artefacts)
    keys = ["district_code", "disease", "week_start_date"]
    for name, fn in runs.items():
        scored = fn(data, threshold=float(GRIDS[name][0]))
        scored = scored.merge(data[keys + ["cases"]], on=keys, how="left")
        curves = [threshold_sweep(scored, truth, GRIDS[name], window_grace_days=a.grace, min_cases=f)
                  for f in FLOORS]
        curve = pd.concat(curves, ignore_index=True)
        curve.insert(0, "detector", name)
        curve.to_csv(exp / f"detection_sweep_{name}.csv", index=False)
        op, feasible = pick_operating_point(curve, a.max_fa)
        at = scored.copy()
        at["alert_flag"] = (at["score"] >= op["threshold"]) & (at["cases"] >= op["min_cases"])
        robust = evaluate(at, truth, a.grace)
        naive = evaluate(at.drop(columns=["reporting_artefact_flag"]), truth, a.grace)
        per_outbreak_context(at, truth, a.grace).to_csv(exp / f"detection_per_outbreak_{name}.csv", index=False)
        art = artefact_check(at, artefacts)
        art.to_csv(exp / f"detection_artefact_check_{name}.csv", index=False)
        dflt = scored.copy()
        dflt["alert_flag"] = dflt["score"] >= DEFAULT_T[name]
        art_d = artefact_check(dflt, artefacts)
        art_d.to_csv(exp / f"detection_artefact_check_default_{name}.csv", index=False)
        summary.append(dict(detector=name, feasible=feasible, threshold=op["threshold"],
                            min_cases=int(op["min_cases"]), sensitivity=robust["sensitivity"],
                            false_per_district_year=robust["false_alerts_per_district_year"],
                            median_days_to_detect=robust["median_days_to_detect"],
                            artefact_raw_alert_weeks=int(art["raw_alert_weeks"].sum()),
                            artefact_effective_alert_weeks=int(art["effective_alert_weeks"].sum()),
                            artefact_backfill_flagged=f"{int(art['backfill_week_flagged'].sum())}/{len(art)}",
                            default_t=DEFAULT_T[name],
                            artefact_raw_at_default=int(art_d["raw_alert_weeks"].sum()),
                            artefact_effective_at_default=int(art_d["effective_alert_weeks"].sum())))
        best = curve.sort_values("false_alerts_per_district_year").groupby("min_cases").head(1)
        print(f"\n{name}: lowest false-alert row per count floor")
        print(best.round(3).to_string(index=False))
    s = pd.DataFrame(summary)
    pd.set_option("display.width", 220)
    print(f"\nOperating points (feasible = false alerts per district-year <= {a.max_fa}):")
    print(s.round(3).to_string(index=False))
    suffix = "" if a.baseline == "usable" else "_calendar"
    s.to_csv(exp / f"detection_operating_points{suffix}.csv", index=False)
    e = s[s["detector"].str.startswith("ears_c2")].iloc[0]
    print(f"\nPaste into make_alerts.py (EARS C2, {a.baseline} baseline, feasible={bool(e['feasible'])}):")
    print(f"THRESHOLD = {float(e['threshold'])}\nMIN_CASES = {int(e['min_cases'])}")


if __name__ == "__main__":
    main()

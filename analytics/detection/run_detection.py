"""Detection evaluation on the report-week gold tables (AN-1, AN-2, AN-3, conflict C1).

    python -m analytics.detection.run_detection [--gold data/gold_csv] [--max-fa 0.5] [--diseases dengue ili ...]
                                                [--run-id RUN] [--out data/analytics]

Runs EARS C2 (last-7-usable-weeks baseline, variants.py) and CUSUM once (scores do not depend on the threshold), sweeps the threshold,
compares against ground truth (only this script and evaluate.py read data/truth), and shows
the artefact rule's effect (robust vs naive = artefact suppression switched off).

Evaluation choices (state them in the report):
  * scored diseases: every disease present in the gold marts (override with --diseases). The ground-truth
    outbreaks are filtered to the same diseases, so alerts and truth always cover the same set; an outbreak
    whose disease is not in gold is reported as unscorable, never silently dropped.
  * alerts are on REPORT weeks, outbreaks on ONSET dates, so a window grace of --grace days
    (default 14) lets late-arriving signals count as in-window.
  * false alerts are episodes outside every outbreak window, per district-year, with all scored diseases summed
    (one district officer sees every disease). detection_by_disease.csv adds the per-disease breakdown.
  * operating point = the (threshold, minimum-case floor) with the highest sensitivity whose false
    alerts per district-year stay <= --max-fa (ties: fewest median days to detect). If none is
    feasible the summary says feasible=False and shows the least noisy point instead.
  * AN-3 artefact check and a per-outbreak "was it already alerting?" diagnostic are written to
    docs/experiments/.
  * AN-2 results are also written to gold_eval_metrics (component 'detection'): the operating point of each
    detector (threshold null) and its trade-off curve at the operating point's count floor (threshold set).
    run_id defaults to the run_id in gold_alerts. Rows are replaced per (run_id, component).
After a run, copy the EARS C2 operating point (threshold, min_cases) into THRESHOLD / MIN_CASES in make_alerts.py
and re-run make_alerts, so the alerts file and this evaluation use the same point.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from analytics.eval_metrics import detection_rows, resolve_run_id, upsert

from .cusum import cusum_pandas
from .evaluate import (artefact_check, evaluate, load_artefacts, load_truth, per_outbreak_context,
                       threshold_sweep)
from .gold_input import load_detector_input
from .variants import ears_c2_usable_pandas

ROOT = Path(__file__).resolve().parents[2]
GRIDS = {"ears_c2": np.arange(1.0, 12.01, 0.5),
         "cusum": np.concatenate([np.arange(1.0, 10.0, 0.5), np.arange(10.0, 30.01, 2.0)])}
DEFAULT_T = {"ears_c2": 3.0, "cusum": 5.0}   # textbook / default thresholds, for the artefact check
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
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold_csv"))
    ap.add_argument("--truth", default=str(ROOT / "data" / "truth" / "ground_truth_outbreaks.csv"))
    ap.add_argument("--artefacts", default=str(ROOT / "data" / "truth" / "ground_truth_artefacts.csv"))
    ap.add_argument("--max-fa", type=float, default=0.5)
    ap.add_argument("--grace", type=int, default=14)
    ap.add_argument("--diseases", nargs="+", default=None,
                    help="restrict scoring to these diseases (default: every disease in gold)")
    ap.add_argument("--out", default=str(ROOT / "data" / "analytics"), help="folder of gold_eval_metrics")
    ap.add_argument("--alerts", default=str(ROOT / "data" / "analytics" / "gold_alerts.parquet"))
    ap.add_argument("--run-id", default=None, help="default: the run_id in gold_alerts")
    a = ap.parse_args()

    data = load_detector_input(a.gold)
    gold_diseases = sorted(data["disease"].unique())
    scored_diseases = gold_diseases if a.diseases is None else sorted(set(a.diseases) & set(gold_diseases))
    if not scored_diseases:
        raise SystemExit(f"none of the requested diseases {a.diseases} exist in gold {gold_diseases}")
    data = data[data["disease"].isin(scored_diseases)].reset_index(drop=True)   # alerts and truth: same diseases

    truth_all = load_truth(a.truth)
    truth = truth_all[truth_all["disease"].isin(scored_diseases)].reset_index(drop=True)
    unscorable = truth_all[~truth_all["disease"].isin(scored_diseases)]
    print(f"scored diseases: {scored_diseases}")
    print(f"series: {data.groupby(['district_code', 'disease']).ngroups}, "
          f"scored outbreaks: {len(truth)} of {len(truth_all)} in the truth file")
    if len(unscorable):
        print(f"WARNING: {len(unscorable)} outbreak(s) not scorable (disease not scored): "
              f"{unscorable.groupby('disease').size().to_dict()}")

    # EARS C2 uses the same baseline rule as make_alerts.BASELINE_RULE ("usable"); the calendar-window version
    # (ears_c2_pandas) is compared in analytics/compare_baselines.py. Keep the two in step.
    runs = {"ears_c2": ears_c2_usable_pandas, "cusum": cusum_pandas}
    exp = ROOT / "docs" / "experiments"
    exp.mkdir(parents=True, exist_ok=True)
    summary, by_disease, curves_by = [], [], {}
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
        curves_by[name] = curve
        op, feasible = pick_operating_point(curve, a.max_fa)
        at = scored.copy()
        at["alert_flag"] = (at["score"] >= op["threshold"]) & (at["cases"] >= op["min_cases"])
        robust = evaluate(at, truth, a.grace)
        naive = evaluate(at.drop(columns=["reporting_artefact_flag"]), truth, a.grace)
        per_outbreak_context(at, truth, a.grace).to_csv(exp / f"detection_per_outbreak_{name}.csv", index=False)
        art = artefact_check(at, artefacts, diseases=scored_diseases)
        art.to_csv(exp / f"detection_artefact_check_{name}.csv", index=False)
        dflt = scored.copy()
        dflt["alert_flag"] = dflt["score"] >= DEFAULT_T[name]
        art_d = artefact_check(dflt, artefacts, diseases=scored_diseases)
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
        # per-disease breakdown at the operating point (each disease against its own outbreaks only)
        for dis in scored_diseases:
            tr_d = truth[truth["disease"] == dis].reset_index(drop=True)
            if tr_d.empty:
                continue
            r = evaluate(at[at["disease"] == dis], tr_d, a.grace)
            by_disease.append(dict(detector=name, disease=dis, threshold=op["threshold"],
                                   min_cases=int(op["min_cases"]), n_outbreaks=len(tr_d),
                                   n_detected=int(round(r["sensitivity"] * len(tr_d))),
                                   sensitivity=r["sensitivity"],
                                   false_per_district_year=r["false_alerts_per_district_year"],
                                   median_days_to_detect=r["median_days_to_detect"]))
        best = curve.sort_values("false_alerts_per_district_year").groupby("min_cases").head(1)
        print(f"\n{name}: lowest false-alert row per count floor")
        print(best.round(3).to_string(index=False))
    s = pd.DataFrame(summary)
    pd.set_option("display.width", 220)
    print(f"\nOperating points (feasible = false alerts per district-year <= {a.max_fa}):")
    print(s.round(3).to_string(index=False))
    s.to_csv(exp / "detection_operating_points.csv", index=False)
    if by_disease:
        bd = pd.DataFrame(by_disease)
        bd.to_csv(exp / "detection_by_disease.csv", index=False)
        print("\nPer disease at each operating point (each disease against its own outbreaks):")
        print(bd.round(3).to_string(index=False))

    run_id = resolve_run_id(a.run_id, a.alerts)
    path, n = upsert(detection_rows(s, curves_by, run_id), Path(a.out))
    print(f"\nwrote {path.name}: run_id {run_id}, {n} rows in table (detection rows replaced for this run)")

    e = s[s["detector"] == "ears_c2"].iloc[0]
    print(f"\nNext: set THRESHOLD = {float(e['threshold'])} and MIN_CASES = {int(e['min_cases'])} in "
          f"analytics/detection/make_alerts.py, then re-run make_alerts so the alerts match this evaluation.")


if __name__ == "__main__":
    main()

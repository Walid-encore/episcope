"""AN-2: verify the detection rows of gold_eval_metrics.

    python -m analytics.check_an2 [--run-id RUN] [--max-fa 0.5]

Checks the table against the evaluation files written by run_detection (docs/experiments/detection_*.csv),
recomputes the operating-point rule from the full sweep, and tests basic properties of a threshold sweep.
Reads no ground truth (data/truth). Exit code 1 if any check fails.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from analytics.eval_metrics import COLS, resolve_run_id

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / "data" / "analytics"
EXP = ROOT / "docs" / "experiments"
METRICS = ("sensitivity", "false_alerts_per_district_year", "median_days_to_detect")


def close(x, y):
    return bool(np.isclose(float(x), float(y), rtol=1e-6, atol=1e-9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=str(A / "gold_eval_metrics.parquet"))
    ap.add_argument("--alerts", default=str(A / "gold_alerts.parquet"))
    ap.add_argument("--exp", default=str(EXP))
    ap.add_argument("--max-fa", type=float, default=0.5, help="same limit as run_detection --max-fa")
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args()

    exp = Path(a.exp)
    run_id = resolve_run_id(a.run_id, a.alerts)
    t = pd.read_parquet(a.table)
    ops = pd.read_csv(exp / "detection_operating_points.csv")
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    print(f"table {a.table}: {len(t)} rows, run_ids {sorted(t['run_id'].astype(str).unique())}")
    print(f"checking run_id {run_id}")
    key = ["run_id", "component", "metric", "model_or_detector", "threshold"]
    check("table has exactly the contract columns", list(t.columns) == COLS)
    check("no duplicate metric rows", not t.duplicated(key).any(), f"{int(t.duplicated(key).sum())} duplicates")

    r = t[(t["run_id"].astype(str) == run_id) & (t["component"] == "detection")]
    check("run has detection rows", len(r) > 0, f"{len(r)} rows")
    check("no NaN values", not r["value"].isna().any())
    sens = r[r["metric"] == "sensitivity"]["value"]
    check("sensitivity within [0, 1]", bool(((sens >= 0) & (sens <= 1)).all()))
    for m in ("false_alerts_per_district_year", "median_days_to_detect"):
        v = r[r["metric"] == m]["value"]
        check(f"{m} is not negative", bool((v >= 0).all()))

    dets = sorted(r["model_or_detector"].unique())
    check("two detectors compared (AN-3)", len(dets) >= 2, str(dets))
    check("detectors equal those in detection_operating_points.csv", set(dets) == set(ops["detector"]))

    for _, o in ops.iterrows():
        det = o["detector"]
        d = r[r["model_or_detector"] == det]
        opr = d[d["threshold"].isna()].set_index("metric")["value"]
        print(f"\n{det}: operating point threshold {o['threshold']}, floor {int(o['min_cases'])}, "
              f"feasible {bool(o['feasible'])}")
        want = {"sensitivity": o["sensitivity"], "false_alerts_per_district_year": o["false_per_district_year"],
                "median_days_to_detect": o["median_days_to_detect"], "operating_threshold": o["threshold"],
                "operating_min_cases": o["min_cases"], "operating_point_feasible": float(bool(o["feasible"]))}
        want = {k: v for k, v in want.items() if pd.notna(v)}
        ok = set(opr.index) == set(want) and all(close(opr[k], want[k]) for k in want)
        check(f"{det}: operating-point rows equal detection_operating_points.csv", ok,
              f"{len(opr)} rows, expected {len(want)}")

        sweep = pd.read_csv(exp / f"detection_sweep_{det}.csv")
        floor = int(o["min_cases"])
        sw = sweep[sweep["min_cases"] == floor]
        cv = d[d["threshold"].notna()]
        check(f"{det}: trade-off curve has at least 5 thresholds", cv["threshold"].nunique() >= 5,
              f"{cv['threshold'].nunique()} thresholds")
        expd = {(m, round(float(p["threshold"]), 9)): float(p[m])
                for _, p in sw.iterrows() for m in METRICS if pd.notna(p[m])}
        got = {(m, round(float(th), 9)): float(v) for m, th, v in zip(cv["metric"], cv["threshold"], cv["value"])}
        ok = set(expd) == set(got) and all(close(expd[k], got[k]) for k in expd)
        check(f"{det}: curve rows equal the sweep at count floor {floor}", ok,
              f"{len(got)} rows, expected {len(expd)}")

        s_ = sw.sort_values("threshold")["sensitivity"].to_numpy(float)
        check(f"{det}: sensitivity never rises when the threshold rises", bool((np.diff(s_) <= 1e-12).all()))
        k = ("sensitivity", round(float(o["threshold"]), 9))
        check(f"{det}: operating point lies on its curve",
              k in got and close(got[k], o["sensitivity"]),
              f"curve {got.get(k)} vs operating {o['sensitivity']}")

        ok_rows = sweep[sweep["false_alerts_per_district_year"] <= a.max_fa]
        if bool(o["feasible"]):
            best_s = ok_rows["sensitivity"].max() if len(ok_rows) else np.nan
            best_d = ok_rows[ok_rows["sensitivity"] == best_s]["median_days_to_detect"].min() if len(ok_rows) else np.nan
            ok = (len(ok_rows) > 0 and close(o["sensitivity"], best_s) and close(o["median_days_to_detect"], best_d)
                  and o["false_per_district_year"] <= a.max_fa + 1e-9)
            check(f"{det}: operating point is the best point with false alerts <= {a.max_fa}", ok,
                  f"operating sens {o['sensitivity']:.4f} / days {o['median_days_to_detect']}, "
                  f"recomputed {best_s:.4f} / {best_d}")
        else:
            check(f"{det}: marked infeasible and no sweep point meets the limit {a.max_fa}", ok_rows.empty,
                  f"{len(ok_rows)} points meet it")

    if all(results):
        print("\nALL CHECKS PASSED")
        sys.exit(0)
    print("\nSOME CHECKS FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()

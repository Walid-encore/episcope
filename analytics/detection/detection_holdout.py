"""Temporal hold-out for the detection evaluation (AN-2): tune the operating point on one period, score it on the other.

    python -m analytics.detection.detection_holdout [--gold data/gold] [--cutoff 2023-07-03] [--grace 14] [--max-fa 0.5]

Why: run_detection.py picks (threshold, count floor) on the same 34 outbreaks it then scores, which is an in-sample
estimate. Here the outbreaks are split in time at --cutoff (default: the median outbreak start, moved to a Monday):
  early = outbreaks that END (plus the grace window) before the cutoff     late = outbreaks that START on/after it
  excluded = outbreaks that straddle the cutoff (reported, not scored)
  Fold A (forward):  choose the operating point on EARLY, report sensitivity / false alerts on LATE.
  Fold B (reverse):  choose it on LATE, report on EARLY (a second out-of-sample estimate, not forecast-style).
  Pooled: every scored outbreak is judged once by a point that did not see it.
The detector itself never sees truth: scores are computed once on the whole series (each week uses only earlier weeks),
so no future information leaks into the scores. The truth only picks the threshold and floor, as in run_detection.py.

Each period only counts alerts and false alerts inside that period. Weeks inside the window of an outbreak that belongs
to the OTHER period (or is excluded) are dropped, so a real outbreak is never miscounted as a false alert there.
False alerts per district-year = episodes / (districts x weeks x 7 / 365.25) of the period, the same definition
run_detection.py uses. A full-period reproduction of the official operating point is printed as a consistency check.
The grids and floors below must match run_detection.py.

Writes docs/experiments/detection_holdout.csv.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .cusum import cusum_pandas
from .evaluate import load_truth, threshold_sweep
from .gold_input import load_detector_input
from .variants import ears_c2_usable_pandas

ROOT = Path(__file__).resolve().parents[2]
GRIDS = {"ears_c2": np.arange(1.0, 12.01, 0.5),
         "cusum": np.concatenate([np.arange(1.0, 10.0, 0.5), np.arange(10.0, 30.01, 2.0)])}
FLOORS = (0, 3, 5, 8, 10, 15)
KEYS = ["district_code", "disease", "week_start_date"]
RUNS = {"ears_c2": ears_c2_usable_pandas, "cusum": cusum_pandas}


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def district_years(frame):
    return frame["district_code"].nunique() * frame["week_start_date"].nunique() * 7 / 365.25


def scrub(frame, other_truth, grace):
    """Drop the weeks inside the window of outbreaks that are not part of this period's truth."""
    if other_truth.empty:
        return frame
    pad = pd.Timedelta(days=grace)
    drop = np.zeros(len(frame), dtype=bool)
    for o in other_truth.itertuples(index=False):
        drop |= ((frame["district_code"] == o.district_code) & (frame["disease"] == o.disease)
                 & (frame["week_start_date"] >= o.start_date - pad)
                 & (frame["week_start_date"] <= o.end_date + pad)).to_numpy()
    return frame[~drop]


def curve(frame, truth, grid, grace, dy):
    c = pd.concat([threshold_sweep(frame, truth, grid, window_grace_days=grace, min_cases=f) for f in FLOORS],
                  ignore_index=True)
    c["fa"] = c["false_alert_episodes"] / dy
    return c


def pick(c, max_fa):
    ok = c[c["fa"] <= max_fa]
    if ok.empty:
        return c.sort_values("fa").iloc[0], False
    return ok.sort_values(["sensitivity", "median_days_to_detect"], ascending=[False, True]).iloc[0], True


def at_point(frame, truth, t, floor, grace, dy):
    r = threshold_sweep(frame, truth, np.array([float(t)]), window_grace_days=grace, min_cases=int(floor)).iloc[0]
    return r, float(r["false_alert_episodes"]) / dy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold"))
    ap.add_argument("--truth", default=str(ROOT / "data" / "truth" / "ground_truth_outbreaks.csv"))
    ap.add_argument("--cutoff", default=None, help="YYYY-MM-DD; default = median outbreak start")
    ap.add_argument("--grace", type=int, default=14)
    ap.add_argument("--max-fa", type=float, default=0.5)
    a = ap.parse_args()
    exp = ROOT / "docs" / "experiments"

    data = load_detector_input(a.gold)
    truth = load_truth(a.truth)
    truth["district_code"] = truth["district_code"].astype(str).str.zfill(2)
    for c in ("start_date", "end_date"):
        truth[c] = pd.to_datetime(truth[c])
    truth = truth[truth["disease"].isin(data["disease"].unique())].reset_index(drop=True)

    cut = pd.Timestamp(a.cutoff) if a.cutoff else pd.Timestamp(truth["start_date"].median())
    cut = (cut - pd.Timedelta(days=cut.weekday())).normalize()
    pad = pd.Timedelta(days=a.grace)
    is_early = truth["end_date"] + pad < cut
    is_late = truth["start_date"] >= cut
    early, late = truth[is_early].reset_index(drop=True), truth[is_late].reset_index(drop=True)
    excluded = truth[~is_early & ~is_late].reset_index(drop=True)
    print(f"cutoff {cut.date()}: {len(early)} early outbreaks, {len(late)} late outbreaks, "
          f"{len(excluded)} straddling the cutoff (not scored: {excluded['outbreak_id'].tolist()})")
    if min(len(early), len(late)) < 8:
        print("WARNING: fewer than 8 outbreaks in one period; the hold-out estimate will be very noisy")

    official = {}
    p = exp / "detection_operating_points.csv"
    if p.exists():
        for _, r in pd.read_csv(p).iterrows():
            official[r["detector"]] = (float(r["threshold"]), int(r["min_cases"]), float(r["sensitivity"]))

    rows = []
    for name, fn in RUNS.items():
        scored = fn(data, threshold=float(GRIDS[name][0]))
        scored = scored.merge(data[KEYS + ["cases"]], on=KEYS, how="left")
        e_raw, l_raw = scored[scored["week_start_date"] < cut], scored[scored["week_start_date"] >= cut]
        dy = {"early": district_years(e_raw), "late": district_years(l_raw), "full": district_years(scored)}
        parts = {"early": (scrub(e_raw, pd.concat([late, excluded]), a.grace), early),
                 "late": (scrub(l_raw, pd.concat([early, excluded]), a.grace), late)}

        # consistency: the official point must be reproducible with this script's definitions on the full data
        full = curve(scored, truth, GRIDS[name], a.grace, dy["full"])
        op_full, _ = pick(full, a.max_fa)
        if name in official:
            ok = (abs(op_full["threshold"] - official[name][0]) < 1e-9 and int(op_full["min_cases"]) == official[name][1]
                  and abs(op_full["sensitivity"] - official[name][2]) < 1e-3)
            print(f"[{'PASS' if ok else 'WARN'}] {name}: full-period operating point reproduces detection_operating_points.csv "
                  f"(here threshold {op_full['threshold']}, floor {int(op_full['min_cases'])}, sens {op_full['sensitivity']:.3f}; "
                  f"official {official[name]})")

        for fold, tune, test in [("A forward (early -> late)", "early", "late"),
                                 ("B reverse (late -> early)", "late", "early")]:
            tf, tt = parts[tune]
            ef, et = parts[test]
            if tt.empty or et.empty:
                continue
            c = curve(tf, tt, GRIDS[name], a.grace, dy[tune])
            op, feasible = pick(c, a.max_fa)
            r, fa = at_point(ef, et, op["threshold"], op["min_cases"], a.grace, dy[test])
            k, n = int(r["n_detected"]), int(r["n_outbreaks"])
            lo, hi = wilson(k, n)
            rows.append(dict(detector=name, fold=fold, tuned_on=tune, tested_on=test, feasible_when_tuned=feasible,
                             threshold=float(op["threshold"]), min_cases=int(op["min_cases"]),
                             tune_outbreaks=int(op["n_outbreaks"]), tune_sensitivity=float(op["sensitivity"]),
                             tune_false_per_district_year=float(op["fa"]),
                             test_outbreaks=n, test_detected=k, test_sensitivity=k / n, ci_low=lo, ci_high=hi,
                             test_false_per_district_year=fa, test_false_episodes=int(r["false_alert_episodes"]),
                             test_district_years=dy[test], test_median_days=r["median_days_to_detect"],
                             fa_cap_met_on_test=bool(fa <= a.max_fa)))
        if name in official:
            t0, f0 = official[name][0], official[name][1]
            for period in ("early", "late"):
                fr, tr = parts[period]
                if tr.empty:
                    continue
                r, fa = at_point(fr, tr, t0, f0, a.grace, dy[period])
                k, n = int(r["n_detected"]), int(r["n_outbreaks"])
                lo, hi = wilson(k, n)
                rows.append(dict(detector=name, fold=f"official point (tuned on all) on {period} only", tuned_on="all",
                                 tested_on=period, threshold=t0, min_cases=f0, test_outbreaks=n, test_detected=k,
                                 test_sensitivity=k / n, ci_low=lo, ci_high=hi, test_false_per_district_year=fa,
                                 test_false_episodes=int(r["false_alert_episodes"]), test_district_years=dy[period],
                                 test_median_days=r["median_days_to_detect"], fa_cap_met_on_test=bool(fa <= a.max_fa)))

    res = pd.DataFrame(rows)
    res.to_csv(exp / "detection_holdout.csv", index=False)
    pd.set_option("display.width", 220)
    show = ["detector", "fold", "threshold", "min_cases", "tune_sensitivity", "test_outbreaks", "test_detected",
            "test_sensitivity", "ci_low", "ci_high", "test_false_per_district_year", "test_median_days", "fa_cap_met_on_test"]
    print("\nOut-of-sample results (thresholds chosen on the other period):")
    print(res[show].round(3).to_string(index=False))
    for name in RUNS:
        f = res[(res["detector"] == name) & res["fold"].str.startswith(("A", "B"))]
        if len(f) == 2:
            k, n = int(f["test_detected"].sum()), int(f["test_outbreaks"].sum())
            lo, hi = wilson(k, n)
            fa = f["test_false_episodes"].sum() / f["test_district_years"].sum()
            print(f"\n{name} pooled two-fold: {k} of {n} outbreaks detected out of sample "
                  f"(sensitivity {k / n:.3f}, 95% CI {lo:.2f} to {hi:.2f}), {fa:.3f} false alerts per district-year")
    print("\nwrote docs/experiments/detection_holdout.csv")


if __name__ == "__main__":
    main()

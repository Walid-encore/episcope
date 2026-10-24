"""AN-5: verify the forecast rows of gold_eval_metrics against an independent recomputation
from gold_forecast (own MAE / coverage / WIS code, does not call summarise or to_eval_metrics).

    python -m analytics.check_eval_metrics [--run-id RUN] [--forecast PATH] [--table PATH] [--alerts PATH]

Default run_id: the one in gold_alerts. Exit code 1 if any check fails.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from analytics.eval_metrics import COLS, resolve_run_id

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / "data" / "analytics"
KEY = ["district_code", "disease", "origin_epi_year", "origin_epi_week", "horizon_weeks"]


def recompute(fc):
    """{(metric_name, model): value} for MAE, coverage_80, WIS and WIS ratio vs seasonal_naive."""
    b = fc[fc["is_backtest"].astype(bool)].copy()
    b = b[b.groupby(KEY)["model"].transform("nunique") == b["model"].nunique()]
    y = b["actual_cases"].to_numpy(float)
    lo, hi = b["q10"].to_numpy(float), b["q90"].to_numpy(float)
    med, mean = b["q50"].to_numpy(float), b["pred_mean"].to_numpy(float)
    b["ae"] = np.abs(y - mean)
    b["cov"] = ((y >= lo) & (y <= hi)).astype(float)
    # alpha = 0.2: interval score = (u-l) + 10*(l-y)+ + 10*(y-u)+ ; WIS = (0.5|y-m| + 0.1*IS) / 1.5
    inter = (hi - lo) + 10 * np.maximum(lo - y, 0) + 10 * np.maximum(y - hi, 0)
    b["wis"] = (0.5 * np.abs(y - med) + 0.1 * inter) / 1.5

    out = {}

    def add(g, suffix):
        s = g.groupby("model")[["ae", "cov", "wis"]].mean()
        for model, r in s.iterrows():
            out[("mae" + suffix, model)] = r["ae"]
            out[("coverage_80" + suffix, model)] = r["cov"]
            out[("wis" + suffix, model)] = r["wis"]
        if "seasonal_naive" in s.index:
            base = s.loc["seasonal_naive", "wis"]
            for model, r in s.iterrows():
                if model != "seasonal_naive":
                    out[("wis_ratio_vs_naive" + suffix, model)] = r["wis"] / base

    add(b, "")
    for h, g in b.groupby("horizon_weeks"):
        add(g, f"_h{int(h)}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forecast", default=str(A / "gold_forecast.parquet"))
    ap.add_argument("--table", default=str(A / "gold_eval_metrics.parquet"))
    ap.add_argument("--alerts", default=str(A / "gold_alerts.parquet"))
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args()

    run_id = resolve_run_id(a.run_id, a.alerts)
    t = pd.read_parquet(a.table)
    fc = pd.read_parquet(a.forecast)
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    print(f"table {a.table}: {len(t)} rows, run_ids {sorted(t['run_id'].astype(str).unique())}")
    print(f"checking run_id {run_id}")
    check("table has exactly the contract columns", list(t.columns) == COLS, str(list(t.columns)))
    key = ["run_id", "component", "metric", "model_or_detector", "threshold"]
    check("no duplicate metric rows (history keeps one row per run, component, metric, model)",
          not t.duplicated(key).any(), f"{int(t.duplicated(key).sum())} duplicates")

    r = t[(t["run_id"].astype(str) == run_id) & (t["component"] == "forecast")]
    check("run has forecast rows", len(r) > 0, f"{len(r)} rows")
    check("no NaN values", not r["value"].isna().any())
    check("threshold is null for forecast rows", r["threshold"].isna().all())

    exp = recompute(fc)
    got = {(m, mod): v for m, mod, v in zip(r["metric"], r["model_or_detector"], r["value"])}
    missing = sorted(set(exp) - set(got))
    extra = sorted(set(got) - set(exp))
    check("every recomputed metric is in the table", not missing, f"{len(exp)} expected, missing {missing[:3]}")
    check("table has no unexpected metric rows", not extra, f"extra {extra[:3]}")

    both = sorted(set(exp) & set(got))
    diffs = np.array([abs(exp[k] - got[k]) for k in both]) if both else np.array([np.inf])
    ok = all(np.isclose(exp[k], got[k], rtol=1e-9, atol=1e-12) for k in both) and bool(both)
    check("table values equal the recomputed values", ok,
          f"{len(both)} compared, max abs diff {diffs.max():.2e}")

    for m in ("mae", "coverage_80", "wis", "wis_ratio_vs_naive"):
        for (name, model), v in sorted(exp.items()):
            if name == m:
                print(f"    {m:20s} {model:18s} {v:.4f}")

    if all(results):
        print("\nALL CHECKS PASSED")
        sys.exit(0)
    print("\nSOME CHECKS FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()

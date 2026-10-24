"""gold_eval_metrics writer (contract: run_id, component, metric, value, model_or_detector, threshold).

Rows are replaced per (run_id, component), so re-running a stage never duplicates and never
removes another component's rows (e.g. detection).
"""
from pathlib import Path

import numpy as np
import pandas as pd

COLS = ["run_id", "component", "metric", "value", "model_or_detector", "threshold"]
KEY = ["run_id", "component", "metric", "model_or_detector", "threshold"]


def resolve_run_id(explicit, alerts_path):
    if explicit:
        return str(explicit)
    p = Path(alerts_path)
    if not p.exists():
        raise SystemExit(f"no --run-id given and {p} not found; run detection first or pass --run-id")
    ids = pd.read_parquet(p, columns=["run_id"])["run_id"].dropna().astype(str).unique()
    if len(ids) != 1:
        raise SystemExit(f"{p} has {len(ids)} run_ids {list(ids)[:5]}; pass --run-id")
    return ids[0]


def forecast_rows(summary, run_id):
    """mae / coverage_80 / wis per model and horizon (via to_eval_metrics) + wis_ratio_vs_naive."""
    from analytics.forecast.forecast import to_eval_metrics
    base = to_eval_metrics(summary, run_id)
    extra = []
    for _, r in summary[summary["model"] != "seasonal_naive"].iterrows():
        suffix = "" if str(r["horizon_weeks"]) == "all" else f"_h{r['horizon_weeks']}"
        extra.append(dict(run_id=run_id, component="forecast", metric="wis_ratio_vs_naive" + suffix,
                          value=float(r["wis_ratio_vs_naive"]), model_or_detector=r["model"],
                          threshold=np.nan))
    return pd.concat([base, pd.DataFrame(extra)], ignore_index=True)[COLS]


def upsert(new, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pq = out / "gold_eval_metrics.parquet"
    new = new[COLS].copy()
    new["threshold"] = new["threshold"].astype(float)
    if new.duplicated(KEY).any():
        raise ValueError("duplicate metric rows in new gold_eval_metrics batch")
    if new["value"].isna().any():
        raise ValueError("NaN metric value; refusing to write gold_eval_metrics")
    if pq.exists():
        old = pd.read_parquet(pq)
        keys = new[["run_id", "component"]].drop_duplicates()
        old = old.merge(keys, on=["run_id", "component"], how="left", indicator=True)
        old = old[old["_merge"] == "left_only"].drop(columns="_merge")
        new = pd.concat([old[COLS], new], ignore_index=True)
    new = new.reset_index(drop=True)
    new.to_parquet(pq, index=False)
    new.to_csv(out / "gold_eval_metrics.csv", index=False)
    return pq, len(new)

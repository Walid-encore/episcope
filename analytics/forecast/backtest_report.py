"""AN-5: summarise the rolling-origin backtest stored in gold_forecast (MAE, 80% coverage, WIS,
WIS ratio against seasonal-naive), overall, per horizon and per disease, and write the overall
summary to gold_eval_metrics.

    python -m analytics.forecast.backtest_report [--forecast data/analytics/gold_forecast.parquet]
                                                 [--out data/analytics] [--run-id RUN]
Writes forecast_backtest.csv, forecast_backtest_by_disease.csv and gold_eval_metrics.(parquet|csv).
Reads only gold_forecast and gold_alerts (for run_id), never data/truth.
"""
import argparse
from pathlib import Path

import pandas as pd

from analytics.eval_metrics import forecast_rows, resolve_run_id, upsert
from analytics.forecast.forecast import summarise

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forecast", default=str(ROOT / "data" / "analytics" / "gold_forecast.parquet"))
    ap.add_argument("--alerts", default=str(ROOT / "data" / "analytics" / "gold_alerts.parquet"))
    ap.add_argument("--out", default=str(ROOT / "data" / "analytics"))
    ap.add_argument("--run-id", default=None, help="default: the run_id in gold_alerts")
    a = ap.parse_args()
    f = pd.read_parquet(a.forecast)
    b = f[f["is_backtest"].astype(bool)]
    if b.empty:
        raise SystemExit("gold_forecast has no backtest rows")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    overall = summarise(b)
    overall.to_csv(out / "forecast_backtest.csv", index=False)
    parts = []
    for dis, g in b.groupby("disease"):
        s = summarise(g)
        s.insert(0, "disease", dis)
        parts.append(s)
    pd.concat(parts, ignore_index=True).to_csv(out / "forecast_backtest_by_disease.csv", index=False)

    run_id = resolve_run_id(a.run_id, a.alerts)
    path, n = upsert(forecast_rows(overall, run_id), out)

    pd.set_option("display.width", 200)
    print(overall.round(3).to_string(index=False))
    print(f"wrote forecast_backtest.csv and forecast_backtest_by_disease.csv to {out}")
    print(f"wrote {path.name}: run_id {run_id}, {n} rows in table")


if __name__ == "__main__":
    main()

"""One-command analytics run (ENG-1): gold tables -> alerts, forecast, demand, allocation, calendar.

    python -m analytics.run_all [--gold data/gold_csv] [--engine spark|pandas] [--evaluate]

Reads gold_weekly_cases.csv (and the other gold tables make_alerts needs) from --gold. Nothing is
hard-coded: dates, week ranges and the calendar year all come from the data. Detection and forecast
code never read data/truth; --evaluate runs the ground-truth evaluation (run_detection) and is
skipped when the truth file is not present.

Outputs (data/analytics): gold_alerts.parquet, gold_forecast.parquet, gold_demand.parquet,
planning_totals.parquet, allocation/, allocation_peak*/, allocation_sensitivity/,
preparedness_calendar.csv, seasonal_profile.csv,
run_all_timing.csv.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "analytics"
TRUTH = ROOT / "data" / "truth" / "ground_truth_outbreaks.csv"


def step(name, module_args, timings):
    print(f"\n=== {name} ===", flush=True)
    t0 = time.time()
    r = subprocess.run([sys.executable, "-m", *module_args], cwd=ROOT)
    secs = time.time() - t0
    if r.returncode != 0:
        raise SystemExit(f"[{name}] FAILED with exit code {r.returncode} after {secs:.0f}s")
    timings.append({"stage": name, "seconds": round(secs, 1)})
    print(f"[{name}] done in {secs:.0f}s", flush=True)


def need(path):
    if not Path(path).exists():
        raise SystemExit(f"expected output is missing: {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold_csv"),
                    help="folder holding the gold tables as CSV (gold_weekly_cases.csv, ...)")
    ap.add_argument("--engine", choices=["spark", "pandas"], default="spark")
    ap.add_argument("--threshold", default=None, help="detector threshold (default: make_alerts default)")
    ap.add_argument("--min-cases", default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--trim-last-weeks", default=None)
    ap.add_argument("--budget-frac", default=None)
    ap.add_argument("--skip-scenarios", action="store_true",
                    help="skip the peak-season allocation scenarios and sensitivity (about 1-2 minutes)")
    ap.add_argument("--evaluate", action="store_true",
                    help="also run the ground-truth evaluation (needs data/truth)")
    a = ap.parse_args()
    if a.run_id is None:  # unique id per run => gold_eval_metrics keeps one set of rows per run
        a.run_id = time.strftime("run_%Y%m%d_%H%M%S")

    gold = Path(a.gold).resolve()
    cases = gold / "gold_weekly_cases.csv"
    need(cases)
    OUT.mkdir(parents=True, exist_ok=True)
    timings = []

    args = ["analytics.detection.make_alerts", "--gold", str(gold), "--engine", a.engine]
    for flag, val in (("--threshold", a.threshold), ("--min-cases", a.min_cases), ("--run-id", a.run_id)):
        if val is not None:
            args += [flag, str(val)]
    step("detection (AN-1, AN-3)", args, timings)
    need(OUT / "gold_alerts.parquet")

    args = ["analytics.optimise.run_demand", "--cases", str(cases), "--engine", a.engine]
    if a.trim_last_weeks is not None:
        args += ["--trim-last-weeks", str(a.trim_last_weeks)]
    step("forecast + demand (AN-4, AN-6)", args, timings)
    for f in ("gold_forecast.parquet", "gold_demand.parquet", "planning_totals.parquet"):
        need(OUT / f)

    step("forecast backtest summary (AN-5)", ["analytics.forecast.backtest_report", "--out", str(OUT), "--run-id", str(a.run_id)],
         timings)
    need(OUT / "forecast_backtest.csv")

    planning_csv = OUT / "planning_totals.csv"
    pd.read_parquet(OUT / "planning_totals.parquet").to_csv(planning_csv, index=False)

    args = ["analytics.optimise.run_allocation", "--demand", str(planning_csv),
            "--out", str(OUT / "allocation"), "--cases", str(cases)]
    if a.budget_frac is not None:
        args += ["--budget-frac", str(a.budget_frac)]
    step("allocation (AN-7, AN-8)", args, timings)
    need(OUT / "allocation" / "recommendation.json")

    if not a.skip_scenarios:
        # peak-season demand rebuilt from THIS run's backtest, then severity / coverage-floor / equal-weight
        # allocations and the transport, cost and weight sensitivity (docs/allocation_assumptions.md)
        step("allocation scenarios + sensitivity (AN-7, AN-8)",
             ["analytics.optimise.run_scenarios", "--gold", str(gold), "--analytics", str(OUT), "--refresh"],
             timings)
        need(OUT / "allocation_peak" / "recommendation.json")
        need(OUT / "allocation_sensitivity" / "transport.csv")

    step("preparedness calendar (AN-9)",
         ["analytics.calendar.preparedness", "--gold", str(gold), "--out", str(OUT)], timings)
    need(OUT / "preparedness_calendar.csv")

    if a.evaluate:
        if TRUTH.exists():
            step("detection evaluation (AN-2)", ["analytics.detection.run_detection", "--gold", str(gold), "--run-id", str(a.run_id)],
                 timings)
        else:
            print(f"\n--evaluate skipped: {TRUTH} not found")

    pd.DataFrame(timings).to_csv(OUT / "run_all_timing.csv", index=False)
    print("\nAnalytics run complete:")
    print(pd.DataFrame(timings).to_string(index=False))
    print(f"outputs in {OUT}")


if __name__ == "__main__":
    main()

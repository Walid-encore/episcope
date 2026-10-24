"""One command for the allocation scenarios the pipeline did not regenerate (AN-7, AN-8).

Runs after the forecast stage of analytics.run_all (needs gold_forecast with backtest rows):
  1. peak-season planning demand            (scenario_demand, only if the CSV is missing or --refresh)
  2. peak allocation, severity weights      -> allocation_peak
  3. peak allocation + coverage floor       -> allocation_peak_cover<NN>
  4. peak allocation, equal weights         -> allocation_peak_equalw   (weight sensitivity)
  5. transport, cost and weight sensitivity -> allocation_sensitivity + docs/allocation_assumptions.md

  python -m analytics.optimise.run_scenarios [--gold data/gold_csv] [--min-cover 0.25] [--refresh]
Nothing is hard-coded to a date: the stock cut-off is read from planning_totals_peak.json.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def sh(*args):
    print("\n$ python -m " + " ".join(args), flush=True)
    subprocess.run([sys.executable, "-m", *args], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="data/gold_csv", help="folder holding gold_weekly_cases.csv")
    ap.add_argument("--analytics", default="data/analytics")
    ap.add_argument("--min-cover", type=float, default=0.25)
    ap.add_argument("--refresh", action="store_true", help="rebuild the peak planning demand")
    a = ap.parse_args()

    an = Path(a.analytics)
    peak = an / "planning_totals_peak.csv"
    if a.refresh or not peak.exists():
        sh("analytics.optimise.scenario_demand")
    as_of = json.loads(peak.with_suffix(".json").read_text())["as_of"]

    cases = Path(a.gold) / "gold_weekly_cases.csv"
    if not cases.exists():
        cases = Path(a.gold) / "gold_weekly_cases"          # parquet directory layout
    if not cases.exists():
        raise SystemExit(f"gold_weekly_cases not found under {a.gold} (csv or parquet dir)")

    base = ["analytics.optimise.run_allocation", "--demand", str(peak), "--as-of", as_of]
    tag = f"{round(a.min_cover * 100):02d}"
    sh(*base, "--cases", str(cases), "--out", str(an / "allocation_peak"))
    sh(*base, "--cases", str(cases), "--min-cover", str(a.min_cover), "--out", str(an / f"allocation_peak_cover{tag}"))
    sh(*base, "--min-cover", str(a.min_cover), "--out", str(an / "allocation_peak_equalw"))
    sh("analytics.optimise.allocation_sensitivity", "--demand", str(peak), "--as-of", as_of,
       "--cases", str(cases), "--min-cover", str(a.min_cover), "--out", str(an / "allocation_sensitivity"))
    print("\nallocation scenarios done")


if __name__ == "__main__":
    main()

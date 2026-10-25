"""One command for the allocation scenarios the pipeline did not regenerate (AN-7, AN-8).

Runs after the forecast stage of analytics.run_all / pipeline.run_all (needs gold_forecast with backtest rows):
  1. peak-season planning demand            (scenario_demand, only if the CSV is missing or --refresh)
  2. peak allocation WITH the coverage floor -> allocation_peak          (the recommended policy, served to the dashboard)
  3. peak allocation, no floor               -> allocation_peak_nofloor  (comparison for the efficiency-equity argument)
  4. peak allocation, equal weights          -> allocation_peak_equalw   (weight sensitivity)
  5. transport, cost and weight sensitivity  -> allocation_sensitivity + docs/allocation_assumptions.md

  python -m analytics.optimise.run_scenarios [--gold data/gold_csv] [--min-cover 0.25] [--refresh]

Nothing is hard-coded to a date: the origin and the stock cut-off are read from planning_totals_peak.json, which
scenario_demand fills with the backtest origin of highest cost-weighted q90 demand.

Every peak recommendation.json gets: scenario, as_of_week (forecast origin, e.g. "2025-W27"), peak_week_start (the
Monday of that week), horizon_start (first forecast week), origin_epi_week, selection_rule, retrospective.
`as_of` keeps its meaning from run_allocation: the stock cut-off date (ISO).
allocation_peak is built in a temporary folder and renamed only when complete, so a failed run never leaves a
half-written folder for the serving export.
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

SELECTION_RULE = "backtest origin with the highest cost-weighted q90 demand (complete 4-horizon origins only)"


def sh(*args):
    print("\n$ python -m " + " ".join(args), flush=True)
    subprocess.run([sys.executable, "-m", *args], check=True)


def peak_metadata(meta):
    """Labels for the peak scenario from planning_totals_peak.json ({"origin": 202527, "as_of": "2025-07-06"}).
    as_of is the day before the first forecast week, so the origin week is the 7 days ending on as_of."""
    origin = int(meta["origin"])
    as_of = date.fromisoformat(str(meta["as_of"]))
    year, week = divmod(origin, 100)
    start = as_of - timedelta(days=6)
    if as_of.weekday() != 6:
        print(f"WARNING: as_of {as_of} is not a Sunday; peak_week_start may not be a Monday", flush=True)
    return {"scenario": "peak", "retrospective": True, "selection_rule": SELECTION_RULE,
            "origin_epi_week": origin, "as_of_week": f"{year}-W{week:02d}",
            "peak_week_start": start.isoformat(), "horizon_start": (as_of + timedelta(days=1)).isoformat()}


def annotate(folder, meta):
    """Add the peak labels to <folder>/recommendation.json (as_of stays the stock cut-off date)."""
    p = Path(folder) / "recommendation.json"
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec.update(peak_metadata(meta))
    p.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def build_served_peak(an, run_args, meta):
    """Build allocation_peak in a temporary folder, label it, then swap it into place."""
    tmp, final = an / "allocation_peak.tmp", an / "allocation_peak"
    shutil.rmtree(tmp, ignore_errors=True)
    sh(*run_args, "--out", str(tmp))
    annotate(tmp, meta)
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="data/gold_csv", help="folder holding gold_weekly_cases.csv")
    ap.add_argument("--analytics", default="data/analytics")
    ap.add_argument("--min-cover", type=float, default=0.25,
                    help="coverage floor of the recommended peak policy (0 = no floor)")
    ap.add_argument("--refresh", action="store_true", help="rebuild the peak planning demand")
    a = ap.parse_args()

    an = Path(a.analytics)
    peak = an / "planning_totals_peak.csv"
    if a.refresh or not peak.exists():
        sh("analytics.optimise.scenario_demand")
    meta = json.loads(peak.with_suffix(".json").read_text(encoding="utf-8-sig"))
    as_of = meta["as_of"]

    cases = Path(a.gold) / "gold_weekly_cases.csv"
    if not cases.exists():
        cases = Path(a.gold) / "gold_weekly_cases"          # parquet directory layout
    if not cases.exists():
        raise SystemExit(f"gold_weekly_cases not found under {a.gold} (csv or parquet dir)")

    base = ["analytics.optimise.run_allocation", "--demand", str(peak), "--as-of", as_of]
    floor = ["--min-cover", str(a.min_cover)]
    build_served_peak(an, [*base, "--cases", str(cases), *floor], meta)
    sh(*base, "--cases", str(cases), "--out", str(an / "allocation_peak_nofloor"))
    annotate(an / "allocation_peak_nofloor", meta)
    sh(*base, *floor, "--out", str(an / "allocation_peak_equalw"))
    annotate(an / "allocation_peak_equalw", meta)
    sh("analytics.optimise.allocation_sensitivity", "--demand", str(peak), "--as-of", as_of,
       "--cases", str(cases), *floor, "--out", str(an / "allocation_sensitivity"))
    print("\nallocation scenarios done")


if __name__ == "__main__":
    main()

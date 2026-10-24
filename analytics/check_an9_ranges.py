"""AN-9 range checks on the real calendar.  python -m analytics.check_an9_ranges
Run after `python -m analytics.calendar.preparedness --gold data\\gold_csv` and `python -m analytics.check_an9`
(check_an9 proves the calendar was built from the real gold; this adds checks for the range columns)."""
import sys
from pathlib import Path

import pandas as pd

CAL = Path("data/analytics/preparedness_calendar.csv")
GOLD = Path("data/gold_csv")
results = []


def check(ok, name, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def circ(a, b):
    """Signed circular distance in weeks from b to a on a 52-week year."""
    return (a - b + 26) % 52 - 26


if not CAL.exists():
    sys.exit(f"{CAL} not found - run analytics.calendar.preparedness first")
cal = pd.read_csv(CAL)
clear = cal[cal["clear_season"].astype(str).str.lower() == "true"]

print("A. provenance")
gold_files = [f for f in GOLD.rglob("*") if f.is_file()] if GOLD.exists() else []
check(len(gold_files) > 0, "real gold_csv folder is present and not empty", f"{len(gold_files)} files in {GOLD}")
if gold_files:
    newest_gold = max(f.stat().st_mtime for f in gold_files)
    check(CAL.stat().st_mtime >= newest_gold, "calendar is newer than every file in gold_csv")

print("B. range columns")
need = {"mean_weekly_cases", "low_volume", "peak_week_earliest", "peak_week_latest", "onset_week_earliest"}
check(need <= set(cal.columns), "new columns present", ", ".join(sorted(need - set(cal.columns))) or "all there")
if need <= set(cal.columns):
    y = clear.dropna(subset=["peak_week_earliest", "peak_week_latest", "onset_week_earliest"])
    width = (y["peak_week_latest"] - y["peak_week_earliest"]) % 52
    check((width <= 26).all(), "yearly peak range is a sensible window (<= 26 weeks, wrap-aware; ILI no longer 3..51)",
          f"widest {int(width.max())} weeks")
    lo_on = circ(y["onset_week_earliest"], y["onset_week"])
    check((lo_on <= 0).all(), "onset_week_earliest is never later than the calendar onset (wrap-aware)",
          f"{int((lo_on > 0).sum())} violations")
    e_date = pd.to_datetime(y["prepose_supplies_date_early"])
    m_date = pd.to_datetime(y["prepose_supplies_date"])
    check((e_date <= m_date).all(), "early pre-position date is never later than the main pre-position date")
    check(cal["mean_weekly_cases"].notna().all() and (cal["mean_weekly_cases"] >= 0).all(),
          f"mean_weekly_cases filled for all {len(cal)} rows")
    check(cal["low_volume"].astype(str).str.lower().isin(["true", "false"]).all(), "low_volume is boolean")
    n_low = int((cal["low_volume"].astype(str).str.lower() == "true").sum())
    print(f"    low-volume series: {n_low} of {len(cal)}")

    # information, not a pass/fail: the calendar peak comes from the multi-year seasonal profile
    out = y[(circ(y["peak_week_earliest"], y["peak_week"]) > 0) | (circ(y["peak_week_latest"], y["peak_week"]) < 0)]
    print(f"NOTE: calendar peak lies outside the range of yearly peaks in {len(out)} of {len(y)} clear series"
          + (" (profile peak differs from the yearly peaks; mention in report section 9):" if len(out) else ""))
    if len(out):
        print(out[["division", "disease", "peak_week", "peak_week_earliest", "peak_week_latest", "low_volume"]]
              .to_string(index=False))

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)

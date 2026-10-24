"""AN-9 acceptance check: was the preparedness calendar built from the REAL gold data, and does it meet the brief?

  python -m analytics.check_an9

Requirement: "Seasonal preparedness calendar for the next year, per division and disease group, showing when to
pre-position supplies and start prevention campaigns, derived from seasonal decomposition of the data."

A. Provenance   data/gold_csv (what run_all reads) equals the real gold parquet in data/gold, and is newer than it
B. Coverage     one row per division x disease; every column the brief needs; dates fall in the planned year
C. Logic        onset <= peak <= end, fixed lead times, dates are week starts, dates agree with weeks
D. Independent  peak week recomputed from the raw real gold counts and compared with the calendar
Exit code 1 if a hard check fails; D is informational (different peak definitions can differ by a few weeks).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GOLD_PQ, GOLD_CSV = ROOT / "data" / "gold" / "gold_weekly_cases", ROOT / "data" / "gold_csv" / "gold_weekly_cases.csv"
CAL = ROOT / "data" / "analytics" / "preparedness_calendar.csv"
DIST = ROOT / "data" / "reference" / "districts.csv"
need_cols = ["division", "disease", "clear_season", "onset_week", "peak_week", "end_week",
             "prepose_supplies_week", "start_campaign_week", "onset_date", "peak_date",
             "prepose_supplies_date", "start_campaign_date"]
results = []


def check(ok, name, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def cdist(a, b, n=52):
    d = (np.asarray(a) - np.asarray(b)) % n
    return np.minimum(d, n - d)


def main():
    cal = pd.read_csv(CAL, parse_dates=["onset_date", "peak_date", "prepose_supplies_date", "start_campaign_date"])
    dis = pd.read_csv(DIST, dtype={"district_code": str}, encoding="utf-8-sig")
    dis["district_code"] = dis["district_code"].str.zfill(2)

    print("A. provenance")
    pq = pd.read_parquet(GOLD_PQ)
    csv = pd.read_csv(GOLD_CSV, dtype={"district_code": str})
    sums = [c for c in ("cases", "admitted_cases", "deaths") if c in pq.columns and c in csv.columns]
    same = len(pq) == len(csv) and all(abs(pq[c].sum() - csv[c].sum()) < 1e-6 for c in sums)
    check(same, "gold_csv (read by run_all) equals the real gold parquet",
          f"rows {len(pq):,} vs {len(csv):,}; sums checked: {sums}")
    t_csv, t_cal = GOLD_CSV.stat().st_mtime, CAL.stat().st_mtime
    check(t_cal >= t_csv, "calendar is newer than the gold CSV it was built from")
    wk = pd.to_datetime(pq["week_start_date"])
    last_year = int(wk.max().year)
    print(f"    real gold covers {wk.min().date()} to {wk.max().date()}")

    print("B. coverage")
    miss = [c for c in need_cols if c not in cal.columns]
    check(not miss, "required columns present", str(miss) if miss else "")
    divs = dis["division"].nunique()
    ds = pq["disease"].nunique()
    check(len(cal) == divs * ds and not cal.duplicated(["division", "disease"]).any(),
          "one row per division x disease", f"{len(cal)} rows vs {divs} x {ds}")
    clear = cal[cal["clear_season"].astype(bool)]
    for col in ("onset_date", "prepose_supplies_date", "start_campaign_date"):
        yrs = [int(y) for y in sorted(clear[col].dt.year.unique())]
        check(yrs and min(yrs) >= last_year, f"{col} is in the year ahead", f"years {yrs}, data ends {last_year}")
    check(len(clear) > 0 and clear[need_cols[1:]].notna().all().all(), "every clear season has all fields filled")
    print(f"    {len(clear)} of {len(cal)} series have a clear season; the rest are reported as none")

    print("C. logic")
    inside = ((clear["peak_week"] - clear["onset_week"]) % 52 <= (clear["end_week"] - clear["onset_week"]) % 52)
    check(inside.all(), "peak lies between onset and end (wrap-around aware)", f"{int((~inside).sum())} violations")
    lp = ((clear["onset_week"] - clear["prepose_supplies_week"]) % 52).unique()
    lc = ((clear["onset_week"] - clear["start_campaign_week"]) % 52).unique()
    check(len(lp) == 1 and len(lc) == 1 and lc[0] > lp[0] > 0,
          "fixed lead times (campaign earlier than pre-position, both before onset)",
          f"pre-position {[int(x) for x in lp]} weeks, campaign {[int(x) for x in lc]} weeks before onset")
    for col in ("onset_date", "peak_date", "prepose_supplies_date", "start_campaign_date"):
        check((clear[col].dt.dayofweek == 0).all(), f"{col} is a week start (Monday)")
    iso = clear["onset_date"].dt.isocalendar().week.astype(int)
    check((cdist(iso, clear["onset_week"]) == 0).all(), "onset_date agrees with onset_week")
    after = clear["peak_date"] >= clear["onset_date"]
    check(after.all(), "peak_date is on or after onset_date",
          f"{int((~after).sum())} rows have the peak BEFORE the onset: " +
          ", ".join(sorted(set(clear.loc[~after, 'disease']))) if not after.all() else "")
    print(f"    shortest lead: pre-position {lp[0] if len(lp) else '?'} weeks; generator restock lead is 3-10 days, "
          "so the 4-week lead is a stated, conservative assumption")

    print("D. independent recomputation from raw real gold (informational)")
    pq["district_code"] = pq["district_code"].astype(str).str.zfill(2)
    g = pq.merge(dis[["district_code", "division"]], on="district_code")
    g["w"] = pd.to_datetime(g["week_start_date"])
    g["y"], g["wk"] = g["w"].dt.isocalendar().year.astype(int), g["w"].dt.isocalendar().week.astype(int)
    ok_years = g.groupby("y")["w"].nunique()
    ok_years = ok_years[ok_years >= 52].index
    g = g[g["y"].isin(ok_years) & (g["wk"] <= 52)]
    prof = g.groupby(["division", "disease", "y", "wk"])["cases"].sum().groupby(["division", "disease", "wk"]).mean()
    rows = []
    for r in clear.itertuples():
        s = prof.get((r.division, r.disease))
        if s is None:
            continue
        v = s.reindex(range(1, 53), fill_value=0).to_numpy(float)
        sm = np.convolve(np.r_[v[-2:], v, v[:2]], np.ones(5) / 5, mode="valid")
        rows.append((r.division, r.disease, int(np.argmax(sm)) + 1, r.peak_week))
    cmp_ = pd.DataFrame(rows, columns=["division", "disease", "raw_peak_week", "calendar_peak_week"])
    cmp_["gap_weeks"] = cdist(cmp_["raw_peak_week"], cmp_["calendar_peak_week"])
    share = float((cmp_["gap_weeks"] <= 4).mean()) if len(cmp_) else float("nan")
    print(f"    {len(cmp_)} series; calendar peak within 4 weeks of the raw seasonal peak in {share:.0%}; "
          f"median gap {cmp_['gap_weeks'].median():.0f} weeks, max {cmp_['gap_weeks'].max():.0f}")
    far = cmp_[cmp_["gap_weeks"] > 6]
    if len(far):
        print("    gap above 6 weeks (explain or fix):")
        print(far.to_string(index=False))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

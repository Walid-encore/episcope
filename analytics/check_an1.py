"""AN-1 acceptance checks on gold_alerts.parquet (no ground truth is read).

    python -m analytics.check_an1 [--alerts data/analytics/gold_alerts.parquet] [--gold data/gold_csv]
                                  [--other data/analytics_pandas/gold_alerts.parquet]

--gold   also checks that every input week has exactly one alert row.
--other  a second run (e.g. the pandas engine) whose scores must equal the main file's.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ["district_code", "disease", "epi_year", "epi_week", "detector", "score", "threshold",
            "alert_flag", "severity", "reporting_artefact_flag", "run_id"]
EXTRA = ["week_start_date", "assessed", "not_assessed_reason"]
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alerts", default=str(ROOT / "data" / "analytics" / "gold_alerts.parquet"))
    ap.add_argument("--gold", default=None)
    ap.add_argument("--other", default=None)
    a = ap.parse_args()

    al = pd.read_parquet(a.alerts)
    al["district_code"] = al["district_code"].astype(str).str.zfill(2)
    al["week_start_date"] = pd.to_datetime(al["week_start_date"])
    k = ["district_code", "disease", "week_start_date"]
    ser = ["district_code", "disease"]

    print(f"file: {a.alerts}")
    print(f"rows {len(al):,} | districts {al.district_code.nunique()} | diseases {sorted(al.disease.unique())} | "
          f"series {al.groupby(ser).ngroups} | weeks {al.week_start_date.min().date()} .. {al.week_start_date.max().date()}")
    print(f"detector {al.detector.unique().tolist()} | threshold {al.threshold.unique().tolist()} | "
          f"min_cases {al['min_cases'].unique().tolist() if 'min_cases' in al.columns else 'n/a'}\n")

    miss = [c for c in CONTRACT + EXTRA if c not in al.columns]
    check("contract + additive columns present", not miss, f"missing {miss}" if miss else "")
    check("one row per district x disease x week", not al.duplicated(k).any(),
          f"{int(al.duplicated(k).sum())} duplicates")
    gaps = al.sort_values(k).groupby(ser)["week_start_date"].apply(lambda s: (s.diff().dropna() != pd.Timedelta(days=7)).sum())
    check("weeks are consecutive within every series (the detectors assume this)", gaps.sum() == 0,
          f"{int((gaps > 0).sum())} series with gaps")
    check("every series has the same number of weeks", al.groupby(ser).size().nunique() == 1,
          f"min {al.groupby(ser).size().min()}, max {al.groupby(ser).size().max()}")
    check("assessed == score is not null", (al["assessed"] == al["score"].notna()).all())
    check("not_assessed_reason set exactly when not assessed",
          (al["not_assessed_reason"].notna() == ~al["assessed"]).all()
          and set(al["not_assessed_reason"].dropna()) <= {"warm_up", "baseline_masked"})
    check("an unscored week never raises an alert", not (al.alert_flag & ~al.assessed).any())
    thr = al["threshold"]
    check("alert implies score >= threshold", (al.loc[al.alert_flag, "score"] >= thr[al.alert_flag]).all())
    check("alerts are never inside a reporting artefact", not (al.alert_flag & al.reporting_artefact_flag).any())
    check("severity set only on alert weeks", (al["severity"].notna() == al["alert_flag"]).all()
          and set(al["severity"].dropna()) <= {"low", "medium", "high"})
    first7 = al.sort_values(k).groupby(ser).cumcount() < 7
    check("the first 7 weeks of each series are warm-up", (al.loc[first7, "not_assessed_reason"] == "warm_up").all())
    check("no NaN/inf scores", not np.isinf(al["score"].astype(float)).any())

    if a.gold:
        from analytics.detection.gold_input import load_detector_input
        d = load_detector_input(a.gold)
        d["district_code"] = d["district_code"].astype(str).str.zfill(2)
        d["week_start_date"] = pd.to_datetime(d["week_start_date"])
        merged = d[k].merge(al[k], on=k, how="outer", indicator=True)
        check("alert rows match the detector input weeks exactly", (merged["_merge"] == "both").all(),
              f"{int((merged['_merge'] == 'left_only').sum())} input weeks without a row, "
              f"{int((merged['_merge'] == 'right_only').sum())} alert rows without input")
    if a.other:
        o = pd.read_parquet(a.other)
        o["district_code"] = o["district_code"].astype(str).str.zfill(2)
        o["week_start_date"] = pd.to_datetime(o["week_start_date"])
        m = al.merge(o, on=k, suffixes=("", "_o"))
        check("second run covers the same weeks", len(m) == len(al) == len(o))
        check("scores equal between the two runs (atol 1e-3, rounded to 3 dp)",
              np.allclose(m.score.astype(float), m.score_o.astype(float), equal_nan=True, atol=1e-3))
        check("alert flags equal between the two runs", (m.alert_flag == m.alert_flag_o).all())

    n_years = (al.week_start_date.max() - al.week_start_date.min()).days / 365.25
    print("\nSummary (no truth involved):")
    print(f"  assessed {al.assessed.mean():.1%}; not assessed by reason: {al.not_assessed_reason.value_counts().to_dict()}")
    print(f"  alerts {int(al.alert_flag.sum())} = {al.alert_flag.sum() / al.groupby(ser).ngroups / n_years:.2f} per series-year; "
          f"artefact-suppressed above-threshold weeks: {int((al.reporting_artefact_flag & (al.score >= thr)).sum())}")
    print("  alerts by disease:", al[al.alert_flag].groupby("disease").size().to_dict())
    last = al.week_start_date.max()
    print(f"  latest week {last.date()}: {int((al.alert_flag & (al.week_start_date == last)).sum())} active alerts")
    print(f"\n{sum(results)}/{len(results)} checks passed")
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

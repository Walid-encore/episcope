"""AN-3 artefact demonstration: does a NAIVE detector alert on the injected reporting artefact, and does the
pipeline prevent it?  (evaluation script: reads ground_truth_artefacts.csv only, never the outbreak truth)

    python -m analytics.detection.artefact_demo [--gold data/gold] [--artefacts data/truth/ground_truth_artefacts.csv]
                                                [--threshold T --min-cases N]

For every injected artefact (a facility goes silent, then back-fills) and every week from the start of the
silence to 3 weeks after the back-fill, it compares
  naive    EARS C2, fixed 7-week calendar baseline built from EVERY week (silent weeks included), no suppression
  pipeline EARS C2 with the "usable" baseline (masked weeks excluded) AND alerts suppressed inside an artefact
           (this is what make_alerts.py does)
over a grid of thresholds. Operating point (threshold, floor) defaults to the one run_detection.py selected.
Also shows CUSUM (default threshold 5) week by week, to see why suppression did not remove its one alert.

Writes docs/experiments/detection_artefact_demo.csv (threshold grid) and detection_artefact_weekly.csv (every week).
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .cusum import cusum_pandas
from .ears_c2 import ears_c2_pandas
from .gold_input import load_detector_input
from .variants import ears_c2_usable_pandas

ROOT = Path(__file__).resolve().parents[2]
GRID = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
KEYS = ["district_code", "disease", "week_start_date"]
AFTER_DAYS = 21          # weeks after the back-fill that still count as "the artefact window"


def _with_cases(scored, data):
    s = scored.drop(columns=["cases"], errors="ignore")
    return s.merge(data[KEYS + ["cases"]], on=KEYS, how="left")


def _operating_point(args, exp):
    if args.threshold is not None and args.min_cases is not None:
        return float(args.threshold), int(args.min_cases)
    p = exp / "detection_operating_points.csv"
    if p.exists():
        op = pd.read_csv(p)
        r = op[op["detector"] == "ears_c2"].iloc[0]
        return float(r["threshold"]), int(r["min_cases"])
    raise SystemExit("run run_detection.py first, or pass --threshold and --min-cases")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold"))
    ap.add_argument("--artefacts", default=str(ROOT / "data" / "truth" / "ground_truth_artefacts.csv"))
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--min-cases", type=int, default=None)
    a = ap.parse_args()
    exp = ROOT / "docs" / "experiments"
    op_t, op_floor = _operating_point(a, exp)

    data = load_detector_input(a.gold)
    art = pd.read_csv(a.artefacts, dtype={"district_code": str})
    art["district_code"] = art["district_code"].str.zfill(2)
    for c in ("silent_start", "silent_end", "backfill_week_start"):
        art[c] = pd.to_datetime(art[c])

    naive = _with_cases(ears_c2_pandas(data, threshold=3.0, mask=False), data)
    pipe = _with_cases(ears_c2_usable_pandas(data, threshold=3.0), data)
    cus = _with_cases(cusum_pandas(data, threshold=5.0), data)

    rows = []
    for _, r in art.iterrows():
        lo, hi = r["silent_start"], r["backfill_week_start"] + pd.Timedelta(days=AFTER_DAYS)

        def win(df):
            m = ((df["district_code"] == r["district_code"]) & (df["disease"] == r["disease"])
                 & (df["week_start_date"] >= lo) & (df["week_start_date"] <= hi))
            return df[m].set_index("week_start_date").sort_index()
        n, p, c = win(naive), win(pipe), win(cus)
        for w in p.index:
            rows.append(dict(
                district_code=r["district_code"], disease=r["disease"], facility_id=r.get("facility_id"),
                week_start_date=w.date(),
                phase="silent" if w < r["backfill_week_start"] else ("backfill" if w == r["backfill_week_start"] else "after"),
                cases=p.at[w, "cases"], naive_score=n.at[w, "score"], pipeline_score=p.at[w, "score"],
                artefact_flag=bool(p.at[w, "reporting_artefact_flag"]),
                cusum_score=c.at[w, "score"], cusum_flag=bool(c.at[w, "reporting_artefact_flag"])))
    wk = pd.DataFrame(rows)
    wk.to_csv(exp / "detection_artefact_weekly.csv", index=False)

    def counts(t, floor):
        naive_hit = (wk["naive_score"] >= t) & (wk["cases"] >= floor)
        pipe_over = (wk["pipeline_score"] >= t) & (wk["cases"] >= floor)
        return dict(threshold=t, min_cases=floor, naive_alert_weeks=int(naive_hit.sum()),
                    pipeline_over_threshold_weeks=int(pipe_over.sum()),
                    pipeline_suppressed_weeks=int((pipe_over & wk["artefact_flag"]).sum()),
                    pipeline_alert_weeks=int((pipe_over & ~wk["artefact_flag"]).sum()))
    grid = pd.DataFrame([counts(t, f) for f in sorted({0, op_floor}) for t in GRID])
    grid.to_csv(exp / "detection_artefact_demo.csv", index=False)

    pd.set_option("display.width", 200)
    print(f"{len(art)} injected artefacts, {len(wk)} series-weeks in the artefact windows "
          f"(silent start .. back-fill + {AFTER_DAYS} days)")
    print("\nNaive vs pipeline alert weeks inside the artefact windows, by threshold:")
    print(grid.to_string(index=False))
    for t, f, label in [(3.0, 0, "textbook threshold 3.0"), (op_t, op_floor, f"operating point {op_t}, floor {op_floor}")]:
        r = counts(t, f)
        print(f"\n{label}: naive alerts on {r['naive_alert_weeks']} artefact week(s); "
              f"pipeline alerts on {r['pipeline_alert_weeks']} (suppressed {r['pipeline_suppressed_weeks']})")
    print(f"\nmax score inside the windows: naive {np.nanmax(wk['naive_score']):.2f}, "
          f"pipeline {np.nanmax(wk['pipeline_score']):.2f}, CUSUM {np.nanmax(wk['cusum_score']):.2f}")
    if (wk["naive_score"] >= 3.0).sum() == 0:
        print("NOTE: even the naive detector never reaches the textbook threshold on these artefacts, so they are too "
              "small at district level to be a real test. State this honestly and ask the Data Engineer for a larger artefact "
              "(the brief asks for a facility silent for THREE weeks, then back-filling).")
    top = wk.assign(m=wk["naive_score"].fillna(-9)).groupby(["district_code", "disease"])["m"].max().nlargest(2)
    for (d, dis), _ in top.items():
        print(f"\nWeek by week, district {d} {dis}:")
        print(wk[(wk["district_code"] == d) & (wk["disease"] == dis)]
              .drop(columns=["district_code", "disease", "facility_id"]).round(2).to_string(index=False))
    cs = wk[(wk["cusum_score"] >= 5.0)]
    if len(cs):
        print("\nWeeks where CUSUM (threshold 5) is over threshold inside an artefact window:")
        print(cs[["district_code", "disease", "week_start_date", "phase", "cases", "cusum_score", "cusum_flag"]]
              .round(2).to_string(index=False))
    print("\nwrote docs/experiments/detection_artefact_demo.csv and detection_artefact_weekly.csv")


if __name__ == "__main__":
    main()

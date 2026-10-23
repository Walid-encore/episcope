"""AN-3 artefact stress test: inject a MATERIAL silent-then-back-fill artefact into the real gold counts and measure
how many false alert weeks it creates for a naive detector versus the pipeline.

    python -m analytics.detection.artefact_stress_test [--gold data/gold] [--shares 0.3 0.5] [--silent-weeks 3]
                                                       [--min-median 30] [--seed 42]

Why: the artefacts the generator injected are too small at district level (the silent facility barely moves the
district counts), so no detector, naive or not, false-alarms on them. This is a semi-synthetic stress test done in the
analytics layer: it changes NOTHING in the lake and reads NO ground truth.

Every series (district x disease) gets exactly one artefact at a random position (seeded):
  * for --silent-weeks weeks a facility holding `share` of the volume goes silent:
        cases x (1 - share), reporting_completeness x (1 - share)  (the completeness drop is what the pipeline sees)
  * in the following week the missed reports arrive: cases + (all the cases missed during the silence)
Two detectors are run on the unmodified and on the modified table:
  naive     EARS C2, fixed calendar baseline built from EVERY week, no suppression
  pipeline  EARS C2 "usable" baseline (under-reporting weeks excluded) + alerts suppressed on the back-fill week
            (what make_alerts.py does)
An ARTEFACT-INDUCED alert week = an alert inside the artefact window (silence .. back-fill + 3 weeks) in the modified
table that the unmodified table did not have for the same week, so real outbreaks and background noise cancel out.

Writes docs/experiments/detection_artefact_stress.csv.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .ears_c2 import ears_c2_pandas
from .gold_input import load_detector_input
from .variants import ears_c2_usable_pandas

ROOT = Path(__file__).resolve().parents[2]
SER = ["district_code", "disease"]
KEYS = SER + ["week_start_date"]
GRID = [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
AFTER = 3                      # weeks after the back-fill that still belong to the artefact window
FIRST_START = 26               # keep the artefact away from the series warm-up


def operating_point(exp):
    p = exp / "detection_operating_points.csv"
    if not p.exists():
        raise SystemExit("run run_detection.py first (needs detection_operating_points.csv)")
    r = pd.read_csv(p).query("detector == 'ears_c2'").iloc[0]
    return float(r["threshold"]), int(r["min_cases"])


def inject(data, share, silent_weeks, seed):
    d = data.sort_values(KEYS).reset_index(drop=True).copy()
    cases = d["cases"].to_numpy(dtype=float)
    comp = d["reporting_completeness"].to_numpy(dtype=float)
    new_cases, new_comp = cases.copy(), comp.copy()
    win = np.zeros(len(d), dtype=bool)
    med = np.zeros(len(d))
    rng = np.random.default_rng(seed)
    for _, pos in d.groupby(SER, sort=False).indices.items():
        n = len(pos)
        t0 = int(rng.integers(FIRST_START, n - silent_weeks - AFTER - 2))
        sil = pos[t0:t0 + silent_weeks]
        back = pos[t0 + silent_weeks]
        low = np.round(cases[sil] * (1 - share))
        new_cases[sil] = low
        new_comp[sil] = comp[sil] * (1 - share)
        new_cases[back] = cases[back] + (cases[sil] - low).sum()
        win[pos[t0:t0 + silent_weeks + 1 + AFTER]] = True
        med[pos] = np.median(cases[pos])
    d["cases"] = new_cases.astype(int)
    d["reporting_completeness"] = new_comp
    d["win"], d["med"] = win, med
    return d


def run(data):
    nv = ears_c2_pandas(data, threshold=3.0, mask=False)[KEYS + ["score"]].rename(columns={"score": "naive"})
    pp = ears_c2_usable_pandas(data, threshold=3.0)[KEYS + ["score", "reporting_artefact_flag"]].rename(
        columns={"score": "pipe", "reporting_artefact_flag": "flag"})
    out = nv.merge(pp, on=KEYS).merge(data[KEYS + ["cases"]], on=KEYS)
    out["flag"] = out["flag"].fillna(False).astype(bool)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold"))
    ap.add_argument("--shares", type=float, nargs="+", default=[0.3, 0.5])
    ap.add_argument("--silent-weeks", type=int, default=3)
    ap.add_argument("--min-median", type=float, default=30.0, help="median weekly cases of a 'high-volume' series")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    exp = ROOT / "docs" / "experiments"
    op_t, op_floor = operating_point(exp)

    data = load_detector_input(a.gold)
    base = run(data)
    rows, example = [], None
    for share in a.shares:
        mod = inject(data, share, a.silent_weeks, a.seed)
        res = run(mod)
        x = res.merge(base, on=KEYS, suffixes=("", "_o")).merge(
            mod[KEYS + ["win", "med", "reporting_completeness"]], on=KEYS)
        for floor in sorted({0, op_floor}):
            for t in sorted(set(GRID) | {op_t}):
                nv_m, nv_o = (x["naive"] >= t) & (x["cases"] >= floor), (x["naive_o"] >= t) & (x["cases_o"] >= floor)
                pp_m = (x["pipe"] >= t) & ~x["flag"] & (x["cases"] >= floor)
                pp_o = (x["pipe_o"] >= t) & ~x["flag_o"] & (x["cases_o"] >= floor)
                ind_n, ind_p = nv_m & ~nv_o & x["win"], pp_m & ~pp_o & x["win"]
                for grp, sel in [("all", x["win"] | True), ("high_volume", x["med"] >= a.min_median)]:
                    n_series = x[sel].groupby(SER).ngroups
                    sn = int(ind_n[sel].groupby([x["district_code"][sel], x["disease"][sel]]).any().sum())
                    sp = int(ind_p[sel].groupby([x["district_code"][sel], x["disease"][sel]]).any().sum())
                    rows.append(dict(share=share, silent_weeks=a.silent_weeks, threshold=t, min_cases=floor, group=grp,
                                     n_series=n_series, naive_series_alerting=sn, pipeline_series_alerting=sp,
                                     naive_alert_weeks=int(ind_n[sel].sum()), pipeline_alert_weeks=int(ind_p[sel].sum())))
        if example is None:
            hv = x[x["win"] & (x["med"] >= a.min_median)]
            if len(hv):
                best = hv.groupby(SER)["naive"].max().idxmax()
                ex_df = x[(x["district_code"] == best[0]) & (x["disease"] == best[1]) & x["win"]][
                    ["week_start_date", "cases_o", "cases", "reporting_completeness", "naive", "pipe", "flag"]]
                example = (share, best, ex_df)
    out = pd.DataFrame(rows)
    out.to_csv(exp / "detection_artefact_stress.csv", index=False)

    pd.set_option("display.width", 200)
    show = out[out["threshold"].isin({3.0, 5.0, op_t}) & out["min_cases"].isin({0, op_floor})]
    print(f"stress test: one artefact per series ({data.groupby(SER).ngroups} series), silent {a.silent_weeks} weeks, "
          f"then back-fill; operating point threshold {op_t}, floor {op_floor}")
    print("artefact-induced alert weeks (inside the artefact window), naive vs pipeline:\n")
    print(show.to_string(index=False))
    if example:
        share, best, ex = example
        print(f"\nExample (share {share}), district {best[0]} {best[1]}, week by week "
              f"(cases_o = before injection; flag = pipeline artefact flag):")
        print(ex.assign(week_start_date=ex["week_start_date"].dt.date).round(2).to_string(index=False))
    for share in a.shares:
        r = out[(out["share"] == share) & (out["group"] == "high_volume") & (out["min_cases"] == 0) & (out["threshold"] == 3.0)].iloc[0]
        print(f"\nshare {share}, high-volume series, threshold 3.0, floor 0: naive alerts in {r['naive_series_alerting']} "
              f"of {r['n_series']} series, pipeline in {r['pipeline_series_alerting']}")
    print("\nwrote docs/experiments/detection_artefact_stress.csv")


if __name__ == "__main__":
    main()

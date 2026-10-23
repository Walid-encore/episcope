"""AN-2 acceptance checks on the detection evaluation (reads the CSVs run_detection.py wrote).

    python -m analytics.check_an2 [--gold data/gold] [--truth data/truth/ground_truth_outbreaks.csv]
                                  [--exp docs/experiments] [--alerts data/analytics/gold_alerts.parquet]
                                  [--max-fa 0.5]

Run run_detection.py first. This script is read-only and does not re-run any detector.
It reads the truth file only to count outbreaks and list the missed ones.
Hard checks print [PASS]/[FAIL]; [WARN] marks things to fix or justify in the report.
"""
import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DETECTORS = ["ears_c2", "cusum"]
results, warns = [], []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def warn(name, detail=""):
    warns.append(name)
    print(f"[WARN] {name}" + (f"  ({detail})" if detail else ""))


def head(t):
    print(f"\n--- {t} " + "-" * max(0, 70 - len(t)))


def pick(df, exact, loose=None):
    for c in exact:
        if c in df.columns:
            return c
    if loose:
        for c in df.columns:
            if loose in c.lower():
                return c
    return None


def read(p):
    return pd.read_csv(p) if Path(p).exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold"))
    ap.add_argument("--truth", default=str(ROOT / "data" / "truth" / "ground_truth_outbreaks.csv"))
    ap.add_argument("--exp", default=str(ROOT / "docs" / "experiments"))
    ap.add_argument("--alerts", default=str(ROOT / "data" / "analytics" / "gold_alerts.parquet"))
    ap.add_argument("--max-fa", type=float, default=0.5)
    a = ap.parse_args()
    exp = Path(a.exp)

    # ------------------------------------------------------------------ 1. truth vs gold
    head("1. Ground truth vs what gold can detect")
    n_scorable = None
    truth = read(a.truth)
    check("truth file found", truth is not None, a.truth)
    if truth is not None:
        print(f"truth: {len(truth)} outbreaks | by disease {truth.groupby('disease').size().to_dict()}"
              + (f" | kinds {truth['kind'].value_counts().to_dict()}" if "kind" in truth.columns else ""))
        check("at least 30 injected outbreaks in the truth file", len(truth) >= 30, f"{len(truth)}")
        try:
            from analytics.detection.gold_input import load_detector_input
            g = load_detector_input(a.gold)
            gd = set(g["disease"].unique())
            n_scorable = int(truth["disease"].isin(gd).sum())
            missing = sorted(set(truth["disease"]) - gd)
            print(f"gold: diseases {sorted(gd)} | {g.groupby(['district_code', 'disease']).ngroups} series")
            check("every truth outbreak is scorable (its disease exists in gold)", not missing,
                  f"{n_scorable} of {len(truth)} scorable; diseases missing from gold: {missing}" if missing else
                  f"{n_scorable} of {len(truth)}")
        except Exception as e:  # noqa: BLE001
            warn("could not load gold", f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------ 2. sweeps
    head("2. Threshold sweeps (conflict C1)")
    sweeps = {}
    for det in DETECTORS:
        d = read(exp / f"detection_sweep_{det}.csv")
        check(f"{det}: detection_sweep_{det}.csv exists", d is not None)
        if d is None:
            continue
        sweeps[det] = d
        thr = pick(d, ["threshold"], "thresh")
        sens = pick(d, ["sensitivity"], "sens")
        fa = pick(d, ["false_alerts_per_district_year", "false_per_district_year"], "per_district")
        dly = pick(d, ["median_days_to_detect"], "median")
        flo = pick(d, ["min_cases"], "floor")
        nd, no = pick(d, ["n_detected"]), pick(d, ["n_outbreaks"])
        need = {"threshold": thr, "sensitivity": sens, "false alerts": fa, "median days": dly}
        miss = [k for k, v in need.items() if v is None]
        check(f"{det}: sweep has threshold, sensitivity, false alerts and median days", not miss,
              f"missing {miss}" if miss else f"{len(d)} rows")
        if miss:
            continue
        check(f"{det}: at least 8 distinct thresholds saved", d[thr].nunique() >= 8,
              f"{d[thr].nunique()} thresholds, {d[flo].nunique() if flo else 1} count floors")
        check(f"{det}: sensitivity within [0, 1]", d[sens].between(0, 1).all())
        bad_s = bad_f = 0
        for _, g in (d.groupby(flo) if flo else [(None, d)]):
            g = g.sort_values(thr)
            bad_s += int((g[sens].diff().dropna() > 1e-9).any())
            bad_f += int((g[fa].diff().dropna() > 0.02).any())
        check(f"{det}: sensitivity never rises as the threshold rises", bad_s == 0, f"{bad_s} floor group(s) violate")
        if bad_f:
            warn(f"{det}: false alerts rise with the threshold in {bad_f} floor group(s)",
                 "episodes can split when a higher threshold breaks one alert run into two; check")
        if nd and no:
            check(f"{det}: sensitivity == n_detected / n_outbreaks",
                  np.allclose(d[sens], (d[nd] / d[no]).round(3), atol=1.5e-3))
            check(f"{det}: one outbreak count across the sweep", d[no].nunique() == 1, f"{sorted(d[no].unique())}")
            if n_scorable is not None:
                check(f"{det}: outbreaks scored == scorable outbreaks in gold", int(d[no].iloc[0]) == n_scorable,
                      f"scored {int(d[no].iloc[0])}, scorable {n_scorable}")

    # ------------------------------------------------------------------ 3. operating points
    head("3. Operating points")
    op = read(exp / "detection_operating_points.csv")
    check("detection_operating_points.csv exists", op is not None)
    op_row = {}
    if op is not None:
        print(op.to_string(index=False))
        for _, r in op.iterrows():
            det = r["detector"]
            op_row[det] = r
            fa = r.get("false_per_district_year", np.nan)
            feas = str(r.get("feasible", "")).lower() == "true"
            if feas:
                check(f"{det}: feasible point has false alerts <= {a.max_fa} per district-year", fa <= a.max_fa + 1e-9,
                      f"{fa}")
            else:
                warn(f"{det}: no feasible operating point under {a.max_fa} false alerts per district-year")
            md = r.get("median_days_to_detect", np.nan)
            if pd.notna(md) and md <= 0:
                warn(f"{det}: median days to detection is {md}",
                     "alerts before onset or inside the grace window may be counted; check before quoting")
            sw = sweeps.get(det)
            if sw is not None:
                thr = pick(sw, ["threshold"], "thresh")
                sens = pick(sw, ["sensitivity"], "sens")
                flo = pick(sw, ["min_cases"], "floor")
                m = sw[(sw[thr] - r["threshold"]).abs() < 1e-9]
                if flo:
                    m = m[m[flo] == r["min_cases"]]
                check(f"{det}: operating point appears in its sweep with the same sensitivity",
                      len(m) > 0 and abs(float(m.iloc[0][sens]) - float(r["sensitivity"])) < 1e-3)
        # artefacts (AN-3)
        raw = [r.get("artefact_raw_alert_weeks", 0) for _, r in op.iterrows()] + \
              [r.get("artefact_raw_at_default", 0) for _, r in op.iterrows()]
        if all(pd.isna(x) or x == 0 for x in raw):
            warn("AN-3: the naive detector never fires on the artefact (all raw artefact alert weeks are 0)",
                 "find a case where it fires and the pipeline suppresses the alert")
        else:
            print(f"artefact raw alert weeks (operating point / default): {raw}")
        for _, r in op.iterrows():
            bf = str(r.get("artefact_backfill_flagged", ""))
            m = re.fullmatch(r"(\d+)/(\d+)", bf)
            if m:
                check(f"{r['detector']}: all back-fill weeks flagged as artefacts", m.group(1) == m.group(2), bf)

    # ------------------------------------------------------------------ 4. per-outbreak
    head("4. Per-outbreak results and missed outbreaks")
    for det in DETECTORS:
        po = read(exp / f"detection_per_outbreak_{det}.csv")
        if po is None:
            warn(f"{det}: detection_per_outbreak_{det}.csv not found")
            continue
        det_c = pick(po, ["detected"], "detect")
        dly_c = pick(po, ["days_to_detect", "delay_days"], "delay")
        oid = pick(po, ["outbreak_id"], "outbreak")
        if n_scorable is not None:
            check(f"{det}: per-outbreak file has one row per scorable outbreak", len(po) == n_scorable,
                  f"{len(po)} rows, {n_scorable} scorable")
        if dly_c:
            v = pd.to_numeric(po[dly_c], errors="coerce")
            if (v < 0).any():
                warn(f"{det}: {int((v < 0).sum())} negative detection delays", "alert before onset")
            if (v == 0).any():
                warn(f"{det}: {int((v == 0).sum())} zero-day detections", "same-day or grace-window alerts")
        if det_c and oid:
            hit = po[det_c].astype(str).str.lower().isin(["true", "1", "yes"])
            missed = po[~hit]
            if det in op_row and "n_detected" in op.columns:
                check(f"{det}: detected count matches the operating point", int(hit.sum()) == int(op_row[det]["n_detected"]),
                      f"{int(hit.sum())} vs {int(op_row[det]['n_detected'])}")
            if det == "ears_c2":
                print(f"EARS C2 detected {int(hit.sum())} of {len(po)}; missed:")
                if len(missed) and truth is not None:
                    info = truth[truth["outbreak_id"].isin(missed[oid])]
                    print(info.to_string(index=False))
                elif len(missed):
                    print(missed[oid].tolist())

    # ------------------------------------------------------------------ 5. alerts file
    head("5. gold_alerts matches the evaluated operating point")
    ap_path = Path(a.alerts)
    if ap_path.exists() and "ears_c2" in op_row:
        al = pd.read_parquet(ap_path, columns=["threshold", "min_cases", "district_code", "disease"])
        r = op_row["ears_c2"]
        check("gold_alerts threshold equals the evaluated EARS C2 threshold",
              set(al["threshold"].unique()) == {float(r["threshold"])}, f"{al['threshold'].unique().tolist()} vs {r['threshold']}")
        check("gold_alerts min_cases equals the evaluated floor",
              set(al["min_cases"].unique()) == {int(r["min_cases"])}, f"{al['min_cases'].unique().tolist()} vs {r['min_cases']}")
        print(f"gold_alerts: {al.groupby(['district_code', 'disease']).ngroups} series, diseases {sorted(al.disease.unique())}")
    else:
        warn("gold_alerts.parquet or the EARS C2 operating point missing; skipped")

    # ------------------------------------------------------------------ 6. leakage
    head("6. Ground-truth leakage")
    allowed = {"run_detection.py", "evaluate.py", "check_an1.py", "check_an2.py", "plot_tradeoff.py",
               "compare_baselines.py", "run_all.py", "run_fixture.py"}      # evaluation drivers
    # code that actually loads the truth; comments/docstrings saying "never reads truth" do not count
    pat = re.compile(r"(read_csv|read_parquet|load_truth|open)\s*\(.*truth|^\s*[A-Z_]*TRUTH\s*=", re.I)
    hits = []
    for p in (ROOT / "analytics").rglob("*.py"):
        if p.name in allowed or p.name.startswith("diag_"):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if pat.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:80]}")
    check("only run_detection.py / evaluate.py (and diagnostics) reference the truth file", not hits,
          f"{len(hits)} hit(s)" if hits else "")
    for h in hits:
        print("   ", h)

    # ------------------------------------------------------------------ 7. plot
    head("7. C1 plot")
    png = exp / "c1_tradeoff.png"
    check("c1_tradeoff.png exists", png.exists())
    sweeps_mtime = max([p.stat().st_mtime for p in exp.glob("detection_sweep_*.csv")] or [0])
    if png.exists() and png.stat().st_mtime < sweeps_mtime:
        warn("c1_tradeoff.png is older than the latest sweep CSV", "re-run analytics.detection.plot_tradeoff")

    print(f"\n{sum(results)}/{len(results)} checks passed, {len(warns)} warning(s)")
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

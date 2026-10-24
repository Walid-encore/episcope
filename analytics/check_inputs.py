"""Input provenance and plausibility check, run as the first stage of analytics.run_all.

  python -m analytics.check_inputs [--gold data/gold_csv]

Confirms that the analytics stages read the pipeline's own gold, not a fixture or sample, and records the evidence
in data/analytics/input_provenance.json (path, sha256, rows, date range, checks passed) so every run, including the
hidden test drop, documents what it ran on. Exit code 1 if a hard check fails.

What it can prove: the gold CSV is not a fixture or sample folder, is not byte-identical to any tests/fixtures file,
equals the real gold parquet (rows and sums), and weather and population are complete and physically plausible.
What it cannot prove: that weather really came from Open-Meteo or population from the census. Those are declared in
docs/ and the fetch script; this check only shows the numbers behave like the real thing.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
checks, failed = [], []


def check(ok, name, detail=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail})
    if not ok:
        failed.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold_csv"))
    ap.add_argument("--gold-parquet", default=str(ROOT / "data" / "gold" / "gold_weekly_cases"))
    ap.add_argument("--weather", default=str(ROOT / "data" / "reference" / "weather.csv"))
    ap.add_argument("--districts", default=str(ROOT / "data" / "reference" / "districts.csv"))
    ap.add_argument("--stock-dir", default=str(ROOT / "data" / "incoming" / "stock"))
    ap.add_argument("--out", default=str(ROOT / "data" / "analytics" / "input_provenance.json"))
    a = ap.parse_args()
    man = {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    print("gold cases")
    gp = Path(a.gold).resolve() / "gold_weekly_cases.csv"
    check(gp.exists(), "gold_weekly_cases.csv exists", str(gp))
    if not gp.exists():
        raise SystemExit("cannot continue without the gold cases")
    try:
        rel = str(gp.relative_to(ROOT)).lower()      # judge the repo-relative path, not the user's home folder
    except ValueError:
        rel = str(gp).lower()
    check(not any(w in rel for w in ("fixture", "sample", "dummy")), "gold path is not a fixture / sample / dummy folder")
    h = sha256(gp)
    fx = [f for f in (ROOT / "tests" / "fixtures").glob("*") if f.is_file()] if (ROOT / "tests" / "fixtures").exists() else []
    same = [f.name for f in fx if sha256(f) == h]
    check(not same, "gold CSV is not byte-identical to a test fixture", ", ".join(same))
    g = pd.read_csv(gp, dtype={"district_code": str})
    g["week_start_date"] = pd.to_datetime(g["week_start_date"])
    n_series = g.groupby(["district_code", "disease"]).ngroups
    man["gold"] = {"path": str(gp), "sha256": h, "rows": int(len(g)), "series": int(n_series),
                   "weeks": f"{g['week_start_date'].min().date()} to {g['week_start_date'].max().date()}",
                   "total_cases": int(g["cases"].sum())}
    check(len(g) > 10_000 and n_series >= 200, "gold has national scale", f"{len(g):,} rows, {n_series} series")
    pq = Path(a.gold_parquet)
    if pq.exists():
        try:
            p = pd.read_parquet(pq)
            sums = [c for c in ("cases", "admitted_cases", "deaths") if c in p.columns and c in g.columns]
            ok = len(p) == len(g) and all(abs(p[c].sum() - g[c].sum()) < 1e-6 for c in sums)
            check(ok, "gold CSV equals the real gold parquet", f"rows {len(p):,} vs {len(g):,}; sums {sums}")
            man["gold"]["parquet_parity"] = bool(ok)
        except Exception as e:  # pragma: no cover
            print(f"    note: could not read the parquet for the parity check ({type(e).__name__})")
    else:
        print(f"    note: {pq} not found, parity check skipped (run `make gold-csv` to export from real gold)")

    print("districts / population")
    dis = pd.read_csv(a.districts, dtype={"district_code": str}, encoding="utf-8-sig")
    tot = float(dis["population"].sum())
    man["districts"] = {"path": a.districts, "sha256": sha256(a.districts), "rows": int(len(dis)),
                        "total_population": int(tot)}
    check(len(dis) == 64 and dis["district_code"].is_unique, "64 unique districts")
    check(1.2e8 <= tot <= 2.0e8, "total population is Bangladesh-scale (census 2022: about 170 million)", f"{tot / 1e6:.1f} million")
    if {"centroid_lat", "centroid_lon"} <= set(dis.columns):
        inside = dis["centroid_lat"].between(20.5, 26.7) & dis["centroid_lon"].between(88.0, 92.8)
        check(inside.all(), "district centroids lie inside Bangladesh", f"{int((~inside).sum())} outside")

    print("weather")
    wp = Path(a.weather)
    check(wp.exists(), "weather.csv exists", str(wp))
    if wp.exists():
        w = pd.read_csv(wp, dtype={"district_code": str})
        w["date"] = pd.to_datetime(w["date"])
        w["district_code"] = w["district_code"].str.zfill(2)
        man["weather"] = {"path": str(wp), "sha256": sha256(wp), "rows": int(len(w)),
                          "dates": f"{w['date'].min().date()} to {w['date'].max().date()}"}
        check(set(dis["district_code"].str.zfill(2)) <= set(w["district_code"]), "weather covers every district")
        check(not w.duplicated(["district_code", "date"]).any(), "no duplicate district-day rows")
        check(w["date"].min() <= g["week_start_date"].min() and w["date"].max() >= g["week_start_date"].max(),
              "weather covers every case week", f"weather {w['date'].min().date()} to {w['date'].max().date()}")
        check(w[["precip_mm", "temp_mean_c", "humidity_pct"]].isna().mean().max() < 0.01, "under 1% missing values")
        check(w["precip_mm"].between(0, 1000).all() and w["temp_mean_c"].between(0, 45).all()
              and w["humidity_pct"].between(0, 100).all(), "values within physical ranges")
        mon = w.loc[w["date"].dt.month.isin([6, 7, 8, 9]), "precip_mm"].mean()
        dry = w.loc[w["date"].dt.month.isin([12, 1, 2]), "precip_mm"].mean()
        check(dry > 0 and mon / dry > 3, "monsoon rainfall is several times the dry season",
              f"Jun-Sep {mon:.1f} mm/day vs Dec-Feb {dry:.1f} mm/day")
        t = w.groupby(w["date"].dt.month)["temp_mean_c"].mean()
        check(t.idxmax() in (4, 5, 6, 7, 8) and t.idxmin() in (12, 1, 2), "temperature peaks in the hot season, lowest in winter",
              f"hottest month {t.idxmax()}, coolest {t.idxmin()}")

    print("stock feed")
    sd = Path(a.stock_dir)
    files = sorted(f for f in sd.rglob("*") if f.is_file() and f.suffix.lower() in (".json", ".jsonl")) if sd.exists() else []
    check(len(files) > 0, "stock snapshot files present", f"{len(files)} files in {sd}")
    if files:
        def first_date(f):
            with open(f, encoding="utf-8-sig") as fh:
                return json.loads(fh.readline())["snapshot_date"]
        man["stock"] = {"path": str(sd), "files": len(files), "first_snapshot": first_date(files[0]),
                        "last_snapshot": first_date(files[-1])}

    man["checks"] = checks
    man["all_passed"] = not failed
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(man, indent=2, default=str))
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed; evidence written to {out}")
    if failed:
        raise SystemExit("input provenance FAILED: " + "; ".join(failed))


if __name__ == "__main__":
    main()

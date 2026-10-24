"""AN-5 verification: is the rolling-origin backtest real, leak-free and correctly scored?

    python -m analytics.check_an5 [--forecast data/analytics/gold_forecast.parquet]
                                  [--gold data/gold/gold_weekly_cases]
                                  [--summary data/analytics/forecast_backtest.csv]
                                  [--docs-copy docs/experiments/forecast_backtest_real.csv]

Recomputes everything from the raw backtest rows and the gold counts WITHOUT using the forecasting code:
  1. the backtest covers 52 consecutive target weeks for every district x disease, horizons 1-4, both models
  2. target dates are Mondays and origin = target - horizon weeks
  3. actual_cases equal the gold counts for EVERY row (not just horizon 1)
  4. the seasonal-naive point equals the gold count 52 weeks before the target, exactly
  5. no missing predictions, 80% interval is ordered
  6. MAE, 80% coverage and WIS are recomputed and compared with the saved summary files
Exit code 1 if any check fails. Reads no ground truth.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
K = ["district_code", "disease"]
ALPHA = 0.2
FAILS = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def read_any(path):
    p = Path(path)
    if p.is_dir() or p.suffix.lower() == ".parquet":
        return pd.read_parquet(p)
    return pd.read_csv(p, dtype={"district_code": str})


def recompute(b):
    y, m, l, u = (b[c].to_numpy(float) for c in ("actual_cases", "q50", "q10", "q90"))
    is_ = (u - l) + (2 / ALPHA) * np.maximum(l - y, 0) + (2 / ALPHA) * np.maximum(y - u, 0)
    b = b.assign(abs_error=np.abs(y - b["pred_mean"].to_numpy(float)),
                 covered=(y >= l) & (y <= u),
                 wis=(0.5 * np.abs(y - m) + (ALPHA / 2) * is_) / 1.5)
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forecast", default=str(ROOT / "data" / "analytics" / "gold_forecast.parquet"))
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold" / "gold_weekly_cases"))
    ap.add_argument("--summary", default=str(ROOT / "data" / "analytics" / "forecast_backtest.csv"))
    ap.add_argument("--docs-copy", default=str(ROOT / "docs" / "experiments" / "forecast_backtest_real.csv"))
    ap.add_argument("--n-backtest", type=int, default=52)
    a = ap.parse_args()

    fc = read_any(a.forecast)
    fc["district_code"] = fc["district_code"].astype(str).str.zfill(2)
    fc["disease"] = fc["disease"].astype(str)
    b = fc[fc["is_backtest"].astype(bool)].copy()
    b["target"] = pd.to_datetime(b["target_week_start_date"])

    g = read_any(a.gold)
    g["district_code"] = g["district_code"].astype(str).str.zfill(2)
    g["disease"] = g["disease"].astype(str)
    g["week"] = pd.to_datetime(g["week_start_date"]).dt.round("D")
    g = g.groupby(K + ["week"], as_index=False, observed=True)["cases"].sum()
    print(f"backtest rows {len(b):,}; gold series {g.groupby(K).ngroups}, weeks "
          f"{g['week'].min().date()}..{g['week'].max().date()}")

    # 1. coverage of the design
    models = sorted(b["model"].unique())
    check("seasonal-naive baseline and at least one other model present",
          "seasonal_naive" in models and len(models) >= 2, str(models))
    check("horizons are 1-4", sorted(b["horizon_weeks"].unique()) == [1, 2, 3, 4])
    check("every gold series is backtested", b.groupby(K).ngroups == g.groupby(K).ngroups,
          f"{b.groupby(K).ngroups} of {g.groupby(K).ngroups}")
    cnt = b.groupby(K + ["model", "horizon_weeks"])["target"].nunique()
    check(f"{a.n_backtest} target weeks per series, model and horizon", bool((cnt == a.n_backtest).all()),
          f"min {cnt.min()}, max {cnt.max()}")
    weeks = sorted(b["target"].unique())
    check("the same 52 consecutive weeks for all series",
          len(weeks) == a.n_backtest and bool((np.diff(np.array(weeks, dtype="datetime64[D]")).astype(int) == 7).all()),
          f"{pd.Timestamp(weeks[0]).date()} .. {pd.Timestamp(weeks[-1]).date()}")
    check("backtest window ends at the last gold week or one trimmed week before",
          pd.Timestamp(weeks[-1]) in (g["week"].max(), g["week"].max() - pd.Timedelta(weeks=1)),
          f"last target {pd.Timestamp(weeks[-1]).date()}, last gold week {g['week'].max().date()}")

    # 2. dates and origins
    check("target dates are Mondays", bool((b["target"].dt.weekday == 0).all()))
    origin = b["target"] - pd.to_timedelta(b["horizon_weeks"] * 7, unit="D")
    iso = origin.dt.isocalendar()
    check("origin = target - horizon weeks (ISO year/week)",
          bool(((iso["year"] == b["origin_epi_year"]) & (iso["week"] == b["origin_epi_week"])).all()))

    # 3. actuals
    m = b.merge(g, left_on=K + ["target"], right_on=K + ["week"], how="left")
    check("every backtest row matches a gold week", bool(m["cases"].notna().all()),
          f"{int(m['cases'].isna().sum())} unmatched")
    check("actual_cases equal gold counts for ALL rows", bool((m["actual_cases"] == m["cases"]).all()),
          f"{int((m['actual_cases'] != m['cases']).sum())} differ of {len(m):,}")

    # 4. independent seasonal-naive
    sn = m[m["model"] == "seasonal_naive"].copy()
    sn["prev"] = sn["target"] - pd.Timedelta(weeks=52)
    sn = sn.merge(g.rename(columns={"week": "prev", "cases": "cases_prev"}), on=K + ["prev"], how="left")
    check("seasonal-naive point = gold count 52 weeks earlier (all rows)",
          bool(sn["cases_prev"].notna().all() and (sn["pred_mean"] == sn["cases_prev"]).all()),
          f"{int((sn['pred_mean'] != sn['cases_prev']).sum())} differ of {len(sn):,}")

    # 5. sanity
    cols = ["pred_mean", "q10", "q50", "q90", "actual_cases"]
    check("no missing predictions or actuals", bool(b[cols].notna().all().all()))
    check("80% interval ordered (q10 <= q90, q10 >= 0)", bool(((b["q10"] <= b["q90"]) & (b["q10"] >= 0)).all()))
    print(f"  info: median outside [q10, q90] in {(~b['q50'].between(b['q10'], b['q90'])).mean():.3%} of rows")

    # 6. metrics recomputed vs saved summaries
    r = recompute(b)
    mine = (r.groupby("model").agg(n=("wis", "size"), mae=("abs_error", "mean"),
                                   coverage_80=("covered", "mean"), wis=("wis", "mean")))
    # the saved summary only keeps rows where all models exist; here that is every row if counts agree
    print("\nrecomputed (overall):")
    print(mine.round(3).to_string())
    for label, path in (("data summary", a.summary), ("docs copy", a.docs_copy)):
        if not Path(path).exists():
            check(f"{label} exists", False, path)
            continue
        s = pd.read_csv(path)
        s = s[s["horizon_weeks"].astype(str) == "all"].set_index("model")
        ok = all(np.isclose(s.loc[mod, c], mine.loc[mod, c], rtol=1e-6)
                 for mod in mine.index if mod in s.index for c in ("n", "mae", "coverage_80", "wis"))
        check(f"{label} matches the recomputed metrics", bool(ok and set(mine.index) <= set(s.index)), str(path))
    if {"seasonal_naive", "nb_lagged_weather"} <= set(mine.index):
        ratio = mine.loc["nb_lagged_weather", "wis"] / mine.loc["seasonal_naive", "wis"]
        print(f"\nWIS ratio nb_lagged_weather / seasonal_naive = {ratio:.3f}")

    print("\nALL CHECKS PASSED" if not FAILS else f"\n{len(FAILS)} CHECK(S) FAILED: {FAILS}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()

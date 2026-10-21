"""Forecasting and rolling-origin backtest (AN-4, AN-5).  Output follows gold_forecast in docs/gold_contract.md.

Models (per district x disease, horizons 1-4 weeks, direct strategy = one fit per horizon)
  seasonal_naive     point = cases in the same week one year (52 weeks) earlier.
                     80% interval = point + 10th/90th percentile of that rule's past errors.
  nb_lagged_weather  negative-binomial GLM (log link). Features for the target week T, all known at the origin:
                       seasonal sin/cos of day-of-year        (annual cycle)
                       log1p of mean rain over weeks T-4..T-8 (lagged weather, 4-8 week lag)
                       mean temperature over weeks T-4..T-8
                       log1p of the last observed weekly count (cases at T-h)
                     Interpretable by design (5 coefficients), which answers conflict C5.
                     Intervals are NB quantiles; coefficient uncertainty is ignored (stated limitation).
No leakage: weather lags are >= 4 weeks and horizons are <= 4, so every feature exists at the origin.
Backtest: for each of the last `n_backtest` target weeks and each horizon, refit using only data up to the
origin (origin = target - horizon) and predict. Origin = last observed week gives the live forecast rows.
Input per series needs: district_code, disease, week_start_date, cases, precip_sum_mm, temp_mean_c
(weeks contiguous). Pandas runs locally; run_spark trains one model set per district x disease in parallel.

Not stored: diagnostics of failed fits (rows are skipped). Compare models only on rows both produced
(summarise does this).
"""
from pathlib import Path

import numpy as np
import pandas as pd

from .metrics import covered, mae, nb_quantiles, wis

ROOT = Path(__file__).resolve().parents[2]
KEYS = ["district_code", "disease"]
HORIZONS = (1, 2, 3, 4)
OUT_COLS = ["district_code", "disease", "origin_epi_year", "origin_epi_week", "horizon_weeks",
            "target_epi_year", "target_epi_week", "target_week_start_date", "model", "pred_mean",
            "q10", "q50", "q90", "is_backtest", "actual_cases"]
SPARK_SCHEMA = ("district_code string, disease string, origin_epi_year int, origin_epi_week int, "
                "horizon_weeks int, target_epi_year int, target_epi_week int, target_week_start_date date, "
                "model string, pred_mean double, q10 double, q50 double, q90 double, "
                "is_backtest boolean, actual_cases double")
SEASON = 52


# ------------------------------------------------------------------ negative-binomial GLM (IRLS)
def fit_nb(X, y, iters=30, alpha_rounds=3, ridge=1e-4):
    """Log-link NB2 GLM by iteratively reweighted least squares (the algorithm statsmodels GLM uses).
    alpha (variance = mu + alpha*mu^2) is re-estimated by the method of moments after each round."""
    n, p = X.shape
    alpha = 0.1
    beta = np.zeros(p)
    beta[0] = np.log(max(float(y.mean()), 0.5))
    R = ridge * np.eye(p)
    R[0, 0] = 0.0
    for _ in range(alpha_rounds):
        for _ in range(iters):
            eta = np.clip(X @ beta, -20, 20)
            mu = np.exp(eta)
            w = mu / (1.0 + alpha * mu)
            z = eta + (y - mu) / mu
            xtw = X.T * w
            new = np.linalg.solve(xtw @ X + R, xtw @ z)
            done = np.max(np.abs(new - beta)) < 1e-8
            beta = new
            if done:
                break
        mu = np.exp(np.clip(X @ beta, -20, 20))
        a = np.sum(((y - mu) ** 2 - mu) / mu ** 2) / max(n - p, 1)
        alpha = float(np.clip(a, 1e-4, 10.0))
    return beta, alpha


# ------------------------------------------------------------------ feature frame for one series
def make_frame(series, extra=max(HORIZONS)):
    s = series.sort_values("week_start_date").copy()
    s["week_start_date"] = pd.to_datetime(s["week_start_date"])
    if (s["week_start_date"].diff().dropna() != pd.Timedelta(days=7)).any():
        raise ValueError("weeks are not contiguous for one district x disease; fill gaps upstream")
    n = len(s)
    idx = pd.date_range(s["week_start_date"].iloc[0], periods=n + extra, freq="7D")
    f = s.set_index("week_start_date")[["cases", "precip_sum_mm", "temp_mean_c"]].reindex(idx).astype(float)
    for name, col in (("rain", "precip_sum_mm"), ("temp", "temp_mean_c")):
        stack = np.column_stack([f[col].shift(L).to_numpy() for L in range(4, 9)])
        m = stack.mean(axis=1)
        f[name] = np.log1p(m) if name == "rain" else m
    ang = 2 * np.pi * idx.dayofyear.to_numpy() / 365.25
    f["sin"], f["cos"] = np.sin(ang), np.cos(ang)
    iso = idx.isocalendar()
    f["epi_year"], f["epi_week"] = iso["year"].to_numpy(), iso["week"].to_numpy()
    return f, n


def _design(f, cases, positions, h):
    """Rows of [sin, cos, rain, temp, log1p(cases[p-h])] for the given positions (NaN if unavailable)."""
    last = np.full(len(positions), np.nan)
    ok = positions - h >= 0
    last[ok] = np.log1p(cases[positions[ok] - h])
    return np.column_stack([f["sin"].to_numpy()[positions], f["cos"].to_numpy()[positions],
                            f["rain"].to_numpy()[positions], f["temp"].to_numpy()[positions], last])


def _nb_forecast(f, cases, o, h, min_train):
    pos = np.arange(0, o + 1)
    X = _design(f, cases, pos, h)
    y = cases[pos]
    ok = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
    if ok.sum() < min_train:
        return None
    X, y = X[ok], y[ok]
    mean, std = X[:, 2:].mean(axis=0), X[:, 2:].std(axis=0)
    std[std < 1e-9] = 1.0
    def prep(M):
        M = M.copy(); M[:, 2:] = (M[:, 2:] - mean) / std
        return np.column_stack([np.ones(len(M)), M])
    beta, alpha = fit_nb(prep(X), y)
    xt = _design(f, cases, np.array([o + h]), h)
    if np.isnan(xt).any():
        return None
    mu = float(np.exp(np.clip(prep(xt) @ beta, -20, 20))[0])
    q10, q50, q90 = nb_quantiles(mu, alpha)
    return mu, q10, q50, q90


def _naive_forecast(cases, o, h, min_resid=20):
    t = o + h
    if t - SEASON < 0:
        return None
    pos = np.arange(SEASON, o + 1)
    err = cases[pos] - cases[pos - SEASON]
    err = err[~np.isnan(err)]
    if len(err) < min_resid:
        return None
    point = cases[t - SEASON]
    if np.isnan(point):
        return None
    lo, hi = np.percentile(err, [10, 90])
    return float(point), max(0.0, point + lo), float(point), max(0.0, point + hi)


# ------------------------------------------------------------------ one series: backtest + live forecast
def forecast_series(series, horizons=HORIZONS, n_backtest=52, min_train=52):
    f, n = make_frame(series, extra=max(horizons))
    cases = f["cases"].to_numpy()
    dist, dis = series["district_code"].iloc[0], series["disease"].iloc[0]
    back_targets = set(range(n - n_backtest, n))
    origins = sorted({t - h for t in back_targets for h in horizons} | {n - 1})
    rows = []
    for o in origins:
        if o < 0:
            continue
        for h in horizons:
            t = o + h
            is_back = t in back_targets
            if not (is_back or o == n - 1):
                continue
            for model, res in (("seasonal_naive", _naive_forecast(cases, o, h)),
                               ("nb_lagged_weather", _nb_forecast(f, cases, o, h, min_train))):
                if res is None:
                    continue
                mean, q10, q50, q90 = res
                rows.append(dict(
                    district_code=dist, disease=dis,
                    origin_epi_year=int(f["epi_year"].iloc[o]), origin_epi_week=int(f["epi_week"].iloc[o]),
                    horizon_weeks=h,
                    target_epi_year=int(f["epi_year"].iloc[t]), target_epi_week=int(f["epi_week"].iloc[t]),
                    target_week_start_date=f.index[t].date(), model=model,
                    pred_mean=mean, q10=q10, q50=q50, q90=q90, is_backtest=bool(is_back),
                    actual_cases=float(cases[t]) if is_back else np.nan))
    return pd.DataFrame(rows, columns=OUT_COLS)


def run_pandas(model_table, **kw):
    """model_table: one row per district x disease x week with cases and weather columns."""
    parts = [forecast_series(g, **kw) for _, g in model_table.groupby(KEYS)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=OUT_COLS)


def run_spark(sdf, **kw):
    """One model set per district x disease, trained in parallel with applyInPandas.
    sdf columns: district_code, disease, week_start_date, cases, precip_sum_mm, temp_mean_c."""
    def per_series(pdf):
        return forecast_series(pdf, **kw)
    return sdf.groupBy(*KEYS).applyInPandas(per_series, SPARK_SCHEMA)


# ------------------------------------------------------------------ scoring
def score_rows(fc):
    """Backtest rows with both models present, plus per-row abs_error, covered_80 and wis."""
    b = fc[fc["is_backtest"]].copy()
    key = KEYS + ["origin_epi_year", "origin_epi_week", "horizon_weeks"]
    n_models = b["model"].nunique()
    b = b[b.groupby(key)["model"].transform("nunique") == n_models].copy()
    b["abs_error"] = np.abs(b["actual_cases"] - b["pred_mean"])
    b["covered_80"] = covered(b["actual_cases"], b["q10"], b["q90"])
    b["wis"] = wis(b["actual_cases"], b["q50"], b["q10"], b["q90"])
    return b


def summarise(fc):
    """MAE, 80% coverage, mean WIS per model, overall and per horizon, plus WIS ratio vs seasonal_naive."""
    b = score_rows(fc)
    def agg(g):
        return pd.Series(dict(n=len(g), mae=g["abs_error"].mean(),
                              coverage_80=g["covered_80"].mean(), wis=g["wis"].mean()))
    overall = b.groupby("model").apply(agg, include_groups=False).reset_index()
    overall.insert(1, "horizon_weeks", "all")
    per_h = b.groupby(["model", "horizon_weeks"]).apply(agg, include_groups=False).reset_index()
    per_h["horizon_weeks"] = per_h["horizon_weeks"].astype(str)
    out = pd.concat([overall, per_h], ignore_index=True)
    base = out[out["model"] == "seasonal_naive"].set_index("horizon_weeks")["wis"]
    out["wis_ratio_vs_naive"] = out.apply(lambda r: r["wis"] / base.get(r["horizon_weeks"], np.nan), axis=1)
    return out


def to_eval_metrics(summary, run_id):
    """Long format for gold_eval_metrics: run_id, component, metric, value, model_or_detector, threshold."""
    rows = []
    for _, r in summary.iterrows():
        suffix = "" if r["horizon_weeks"] == "all" else f"_h{r['horizon_weeks']}"
        for m in ("mae", "coverage_80", "wis"):
            rows.append(dict(run_id=run_id, component="forecast", metric=m + suffix, value=float(r[m]),
                             model_or_detector=r["model"], threshold=np.nan))
    return pd.DataFrame(rows)


def main():
    """Backtest on the tests/fixtures cases with the real weekly weather (prototype run)."""
    from .features import build_weekly_weather
    fix = ROOT / "tests" / "fixtures" / "gold_weekly_cases_fixture.csv"
    cases = pd.read_csv(fix, dtype={"district_code": str})
    wx = build_weekly_weather(pd.read_csv(ROOT / "data" / "reference" / "weather.csv", dtype={"district_code": str}))
    cases["week_start_date"] = pd.to_datetime(cases["week_start_date"])
    wx = wx[["district_code", "week_start_date", "precip_sum_mm", "temp_mean_c"]]
    table = cases.merge(wx, on=["district_code", "week_start_date"], how="left")
    fc = run_pandas(table)
    s = summarise(fc)
    pd.set_option("display.width", 200)
    print(s.round(3).to_string(index=False))
    out = ROOT / "docs" / "experiments"
    out.mkdir(parents=True, exist_ok=True)
    s.to_csv(out / "forecast_backtest_fixture.csv", index=False)
    print(f"wrote {out / 'forecast_backtest_fixture.csv'}")


if __name__ == "__main__":
    main()

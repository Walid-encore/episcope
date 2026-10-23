"""SEIR epidemic scenario module (AN-10): calibrate on the national flu-like-illness (ILI) surge,
then run what-if intervention scenarios.

    python -m analytics.scenario.seir --series data/standin_gold/national_weekly_ili.csv [--sf 0.05]

Model: S -> E -> I -> R, national population N, latent period 3 days, infectious period 5 days
(fixed assumptions). Weekly reported ILI = baseline + rho * new symptomatic infections.
  * baseline b: median of the 26 weeks before the fit window (not fitted).
  * rho = --attend x --sf: share of symptomatic infections that reach a facility and are coded ILI,
    times the data scale factor. NOT identifiable from counts alone (it trades off against the
    infected count), so it is an assumption and every scenario figure scales with it.
  * fitted: R0 and the initial infected count I0, by least squares on square-rooted counts,
    9 starting points; 90% intervals from a residual bootstrap.
  * fit window: --start/--end, or automatically 10 weeks before to 8 weeks after the week where
    cases are highest relative to a 53-week rolling median.
Scenarios: transmission cut of 20/40/60% starting 6/4/2 weeks before the unmitigated peak.
Hospital beds: --hosp share of symptomatic cases admitted, --stay days in bed, compared with
national bed capacity from the facility registry. Output is a planning aid, not a forecast.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.integrate import odeint
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[2]
SIGMA, GAMMA = 1 / 3, 1 / 5          # per day: 3-day latent, 5-day infectious period


def _rhs(y, t, beta, N):
    S, E, I, R, C = y
    inf = beta * S * I / N
    return [-inf, inf - SIGMA * E, SIGMA * E - GAMMA * I, GAMMA * I, SIGMA * E]


def simulate(R0, I0, N, days, intervention=None):
    """Daily state array (days+1, 5): S, E, I, R, cumulative symptomatic cases.
    intervention = (start_day, cut) multiplies transmission by (1 - cut) from start_day on."""
    beta = R0 * GAMMA
    t = np.arange(days + 1, dtype=float)
    y0 = [N - 2 * I0, I0, I0, 0.0, 0.0]
    if intervention is None or intervention[0] >= days:
        return odeint(_rhs, y0, t, args=(beta, N))
    k = max(int(round(intervention[0])), 0)
    if k == 0:
        return odeint(_rhs, y0, t, args=(beta * (1 - intervention[1]), N))
    a = odeint(_rhs, y0, t[:k + 1], args=(beta, N))
    b = odeint(_rhs, a[-1], t[k:], args=(beta * (1 - intervention[1]), N))
    return np.vstack([a, b[1:]])


def weekly_new(sol):
    return np.diff(sol[::7, 4])


def predict(R0, I0, N, weeks, rho, b):
    return b + rho * weekly_new(simulate(R0, I0, N, 7 * weeks))


def fit(obs, N, rho, b, starts=None, seed_only=None):
    """Least squares on sqrt counts. Returns dict(R0, I0, rmse, r2, pred)."""
    obs = np.asarray(obs, dtype=float)
    weeks = len(obs)

    def resid(th):
        return np.sqrt(obs) - np.sqrt(np.maximum(predict(th[0], 10 ** th[1], N, weeks, rho, b), 0.0))

    grid = [(r, i) for r in (1.3, 2.0, 3.5) for i in (2, 4, 6)] if starts is None else starts
    best = None
    for r0, li in grid:
        li = min(li, np.log10(N / 4))
        res = least_squares(resid, [r0, li], bounds=([1.02, 0.0], [8.0, np.log10(N / 4)]))
        if best is None or res.cost < best.cost:
            best = res
    R0, I0 = float(best.x[0]), float(10 ** best.x[1])
    pred = predict(R0, I0, N, weeks, rho, b)
    sst = float(np.sum((np.sqrt(obs) - np.sqrt(obs).mean()) ** 2))
    sse = float(np.sum((np.sqrt(obs) - np.sqrt(np.maximum(pred, 0))) ** 2))
    return dict(R0=R0, I0=I0, rmse=float(np.sqrt(np.mean((obs - pred) ** 2))),
                r2_sqrt=1 - sse / sst if sst > 0 else float("nan"), pred=pred, theta=best.x)


def bootstrap(fitres, obs, N, rho, b, n=100, seed=0, horizon_weeks=52):
    """Residual bootstrap on the sqrt scale; returns arrays of R0 and unmitigated peak week."""
    rng = np.random.default_rng(seed)
    obs = np.asarray(obs, dtype=float)
    base = np.sqrt(np.maximum(fitres["pred"], 0))
    res = np.sqrt(obs) - base
    r0s, peaks = [], []
    for _ in range(n):
        ys = np.maximum(base + rng.choice(res, size=len(obs)), 0) ** 2
        f = fit(ys, N, rho, b, starts=[tuple(fitres["theta"])])
        r0s.append(f["R0"])
        peaks.append(int(np.argmax(weekly_new(simulate(f["R0"], f["I0"], N, 7 * horizon_weeks)))))
    return np.array(r0s), np.array(peaks)


def run_scenarios(R0, I0, N, horizon_weeks=52, cuts=(0.2, 0.4, 0.6), leads=(6, 4, 2),
                  hosp=0.05, stay=7, capacity=None):
    days = 7 * horizon_weeks
    base = simulate(R0, I0, N, days)
    peak_w = int(np.argmax(weekly_new(base)))
    rows, curves = [], []

    def summarise(label, cut, lead, sol):
        wk = weekly_new(sol)
        daily = np.diff(sol[:, 4])
        beds = np.convolve(hosp * daily, np.ones(stay))[:len(daily)]
        row = dict(scenario=label, cut=cut, weeks_before_peak=lead, peak_week_index=int(np.argmax(wk)),
                   peak_weekly_symptomatic=float(wk.max()), attack_rate=float(sol[-1, 4] / N),
                   peak_beds_needed=float(beds.max()))
        if capacity:
            row["peak_beds_vs_capacity"] = float(beds.max() / capacity)
        rows.append(row)
        for w in range(len(wk)):
            curves.append(dict(scenario=label, week_index=w, new_symptomatic=float(wk[w]),
                               beds_needed=float(beds[min(7 * w + 6, len(beds) - 1)])))

    summarise("unmitigated", 0.0, np.nan, base)
    for cut in cuts:
        for lead in leads:
            start = max(7 * (peak_w - lead), 0)
            summarise(f"cut{int(cut * 100)}_lead{lead}w", cut, lead,
                      simulate(R0, I0, N, days, intervention=(start, cut)))
    return pd.DataFrame(rows), pd.DataFrame(curves), peak_w


def auto_window(s):
    ratio = s / s.rolling(53, center=True, min_periods=20).median().replace(0, np.nan)
    peak = ratio.idxmax()
    return peak - pd.Timedelta(weeks=10), peak + pd.Timedelta(weeks=8)


def bed_capacity(path):
    fac = pd.read_csv(path)
    cols = [c for c in fac.columns if "bed" in c.lower() and "icu" not in c.lower()
            and "occup" not in c.lower()]
    return float(fac[cols[0]].sum()) if cols else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default=str(ROOT / "data" / "standin_gold" / "national_weekly_ili.csv"))
    ap.add_argument("--districts", default=str(ROOT / "data" / "reference" / "districts.csv"))
    ap.add_argument("--facilities", default=str(ROOT / "data" / "reference" / "facilities.csv"))
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--sf", type=float, default=1.0, help="scale factor the data was generated at")
    ap.add_argument("--attend", type=float, default=0.10)
    ap.add_argument("--hosp", type=float, default=0.05)
    ap.add_argument("--stay", type=int, default=7)
    ap.add_argument("--boot", type=int, default=100)
    ap.add_argument("--out", default=str(ROOT / "data" / "outputs" / "seir"))
    a = ap.parse_args()

    s = pd.read_csv(a.series, parse_dates=["week_start_date"]).set_index("week_start_date")["cases"].astype(float)
    start, end = (pd.Timestamp(a.start), pd.Timestamp(a.end)) if a.start and a.end else auto_window(s)
    win = s[(s.index >= start) & (s.index <= end)]
    pre = s[(s.index < start)].tail(26)
    b = float(pre.median()) if len(pre) >= 8 else float(win.head(3).median())
    N = float(pd.read_csv(a.districts)["population"].sum())
    rho = a.attend * a.sf
    cap = bed_capacity(a.facilities)
    print(f"window {win.index.min().date()}..{win.index.max().date()} ({len(win)} weeks), "
          f"baseline {b:.0f}/week, N={N:,.0f}, rho={rho:.5f}, bed capacity={cap}")

    f = fit(win.to_numpy(), N, rho, b)
    r0s, peaks = bootstrap(f, win.to_numpy(), N, rho, b, a.boot)
    summ = dict(window_start=str(win.index.min().date()), window_end=str(win.index.max().date()),
                baseline_per_week=b, rho=rho, attend=a.attend, sf=a.sf, R0=f["R0"],
                R0_p05=float(np.percentile(r0s, 5)), R0_p95=float(np.percentile(r0s, 95)),
                I0=f["I0"], rmse_cases=f["rmse"], r2_sqrt=f["r2_sqrt"],
                peak_week_index_p05=int(np.percentile(peaks, 5)), peak_week_index_p95=int(np.percentile(peaks, 95)),
                sigma_days=1 / SIGMA, gamma_days=1 / GAMMA, hosp=a.hosp, stay_days=a.stay)
    scen, curves, peak_w = run_scenarios(f["R0"], f["I0"], N, hosp=a.hosp, stay=a.stay, capacity=cap)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "seir_fit.json").write_text(json.dumps(summ, indent=2))
    pd.DataFrame({"week_start_date": win.index, "observed": win.to_numpy(), "fitted": f["pred"]}
                 ).to_csv(out / "seir_fit_series.csv", index=False)
    scen.to_csv(out / "seir_scenarios.csv", index=False)
    curves.to_csv(out / "seir_curves.csv", index=False)
    print(json.dumps(summ, indent=2))
    pd.set_option("display.width", 200)
    print(scen.round(3).to_string(index=False))
    print(f"wrote results to {out}")


if __name__ == "__main__":
    main()

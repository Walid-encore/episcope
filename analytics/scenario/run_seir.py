"""AN-10 epidemic scenario module: SEIR calibrated on the novel-respiratory surge in the REAL gold data.

    python -m analytics.scenario.run_seir --gold-csv data/gold_csv

What it does (national level, homogeneous mixing; a stylised model, not a forecast):
 1. Reads data/gold_csv/gold_weekly_cases.csv (the gold the rest of the pipeline uses) and sums ILI over districts.
 2. Finds the surge FROM THE DATA ALONE: excess = weekly ILI minus a leave-one-year-out seasonal median; the surge window
    is the run of weeks around the largest excess. No outbreak-label file is read here (compare the printed window with
    the injected outbreak list in your evaluation code, not in this module).
 3. Fits R0, reporting fraction and the seed to the excess cases (least squares on sqrt counts, multi-start), with a
    residual bootstrap for uncertainty.
 4. Runs what-if scenarios (contact reduction 20% / 40%, early vs late start) with the same fitted parameters.
Writes data/analytics/seir_scenarios.csv (weekly curves with bands), seir_summary.csv and seir_fit.json
(provenance: gold file, rows, sha256, date range; assumptions; fit quality).
Fixed assumptions (not fitted, change them and re-run): latent period 4 d, infectious period 6 d.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

LATENT_DAYS = 4.0
INFECTIOUS_DAYS = 6.0
DT = 0.25                      # days per Euler step
PAD_WEEKS = 8                  # weeks before/after the surge window used for the fit
BOUNDS_LO = np.array([1.02, -7.0, -9.0])   # R0, log10 reporting fraction, log10 seed fraction
BOUNDS_HI = np.array([8.0, 0.0, -3.0])


# ------------------------------------------------------------------ model
def simulate(n_pop, r0, rho, seed, n_weeks, reduction=0.0, start_day=None):
    """Weekly reported symptomatic cases (E -> I flow times reporting fraction). Contact reduction applies from start_day."""
    sigma, gamma = 1.0 / LATENT_DAYS, 1.0 / INFECTIOUS_DAYS
    s, e, i = n_pop * (1.0 - seed), n_pop * seed, 0.0
    steps_per_week = int(round(7 / DT))
    out = np.zeros(n_weeks)
    t = 0.0
    for w in range(n_weeks):
        acc = 0.0
        for _ in range(steps_per_week):
            cut = reduction if (start_day is not None and t >= start_day) else 0.0
            beta = r0 * gamma * (1.0 - cut)
            inf = beta * s * i / n_pop * DT
            flow = sigma * e * DT
            rec = gamma * i * DT
            s -= inf
            e += inf - flow
            i += flow - rec
            acc += flow
            t += DT
        out[w] = rho * acc
    return out


def _theta_to_params(theta):
    return float(theta[0]), 10.0 ** float(theta[1]), 10.0 ** float(theta[2])


def fit_seir(obs, n_pop, starts=None):
    """Least squares on sqrt counts, multi-start. Returns (theta, cost)."""
    obs = np.clip(np.asarray(obs, dtype=float), 0, None)
    target = np.sqrt(obs)

    def resid(theta):
        r0, rho, seed = _theta_to_params(theta)
        return np.sqrt(np.clip(simulate(n_pop, r0, rho, seed, len(obs)), 0, None)) - target

    starts = starts or [(1.4, -2.0, -6.0), (2.0, -2.0, -6.0), (3.0, -1.5, -7.0), (1.8, -3.0, -5.0),
                        (1.9, -4.5, -6.0), (1.9, -6.0, -6.0)]
    best = None
    for x0 in starts:
        try:
            r = least_squares(resid, np.array(x0, dtype=float), bounds=(BOUNDS_LO, BOUNDS_HI), max_nfev=60)
        except Exception:
            continue
        if best is None or r.cost < best.cost:
            best = r
    if best is None:
        raise RuntimeError("SEIR fit failed from every starting point")
    return best.x, float(best.cost)


# ------------------------------------------------------------------ data
def load_national(gold_csv: Path, disease: str) -> pd.DataFrame:
    path = gold_csv / "gold_weekly_cases.csv"
    low = str(gold_csv).lower()
    if "fixture" in low or "sample" in low:
        sys.exit(f"refusing to run on {gold_csv}: this stage must use the real gold, not a fixture or sample folder")
    if not path.exists():
        sys.exit(f"{path} not found: run the `export` stage first")
    c = pd.read_csv(path, dtype={"district_code": str})
    need = {"disease", "epi_year", "epi_week", "week_start_date", "cases"}
    if not need <= set(c.columns):
        sys.exit(f"gold_weekly_cases.csv: missing columns {sorted(need - set(c.columns))}; found {list(c.columns)}")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    d = c[c["disease"] == disease]
    if d.empty:
        sys.exit(f"no rows for disease {disease!r}; diseases in gold: {sorted(c['disease'].unique())}")
    nat = (d.groupby(["epi_year", "epi_week", "week_start_date"], as_index=False)["cases"].sum()
           .sort_values("week_start_date").reset_index(drop=True))
    nat["week_start_date"] = pd.to_datetime(nat["week_start_date"])
    nat.attrs.update(path=str(path), rows=int(len(c)), sha256=sha)
    return nat


def find_surge(nat: pd.DataFrame):
    """Excess over a leave-one-year-out seasonal median; window = weeks around the largest excess."""
    n = nat.copy()
    n["wk"] = n["epi_week"].clip(upper=52)
    base = []
    for _, r in n.iterrows():
        others = n[(n["wk"] == r["wk"]) & (n["epi_year"] != r["epi_year"])]["cases"]
        base.append(others.median() if len(others) else np.nan)
    n["baseline"] = base
    n["excess"] = n["cases"] - n["baseline"]
    e = n["excess"].to_numpy(dtype=float)
    ok = ~np.isnan(e)
    sigma = 1.4826 * np.nanmedian(np.abs(e[ok] - np.nanmedian(e[ok])))
    sigma = max(sigma, 1.0)
    p = int(np.nanargmax(e))
    peak = e[p]
    if peak < 5 * sigma or peak < 0.25 * n["baseline"].iloc[p]:
        sys.exit(f"no clear surge in the data (largest excess {peak:.0f}, noise sigma {sigma:.0f}); nothing to calibrate")
    floor = max(2 * sigma, 0.1 * peak)
    lo = hi = p
    while lo > 0 and e[lo - 1] > floor:
        lo -= 1
    while hi < len(e) - 1 and e[hi + 1] > floor:
        hi += 1
    if hi - lo + 1 < 6:
        sys.exit(f"surge window only {hi - lo + 1} weeks long; too short to fit an SEIR curve")
    return n, lo, hi, float(sigma)


# ------------------------------------------------------------------ scenarios
def scenario_defs(early_lag, late_lag):
    return [("baseline_no_intervention", 0.0, None),
            ("contact_reduction_20pct_early", 0.2, early_lag),
            ("contact_reduction_40pct_early", 0.4, early_lag),
            ("contact_reduction_40pct_late", 0.4, late_lag)]


def run(gold_csv: Path, out_dir: Path, disease="ili", population=None, n_boot=40, early_lag=4, late_lag=8, seed=0):
    nat = load_national(gold_csv, disease)
    n, lo, hi, sigma = find_surge(nat)
    a, b = max(0, lo - PAD_WEEKS), min(len(n) - 1, hi + PAD_WEEKS)
    span = n.iloc[a:b + 1].reset_index(drop=True)
    obs = np.clip(span["excess"].to_numpy(dtype=float), 0, None)
    w0 = lo - a                                     # index of the surge-window start inside the span
    if population is None:
        dist = pd.read_csv(Path("data/reference/districts.csv"))
        population = float(dist["population"].sum())
    theta, cost = fit_seir(obs, population)
    r0, rho, sd = _theta_to_params(theta)
    model = simulate(population, r0, rho, sd, len(obs))
    inwin = slice(w0, hi - a + 1)
    ss_res = float(((obs[inwin] - model[inwin]) ** 2).sum())
    ss_tot = float(((obs[inwin] - obs[inwin].mean()) ** 2).sum())
    at_bound = [nm for nm, v, l, h in zip(("R0", "reporting_fraction", "seed_fraction"), theta, BOUNDS_LO, BOUNDS_HI)
                if min(v - l, h - v) < 0.02 * (h - l)]
    fit = dict(parameters_at_bound=at_bound, r2_in_window=round(1 - ss_res / ss_tot, 3) if ss_tot > 0 else None,
               nrmse_in_window=round(float(np.sqrt(ss_res / (hi - lo + 1)) / max(obs[inwin].mean(), 1e-9)), 3))

    # residual bootstrap in sqrt space
    rng = np.random.default_rng(seed)
    res = np.sqrt(obs) - np.sqrt(np.clip(model, 0, None))
    boots = [theta]
    for _ in range(n_boot):
        synth = (np.sqrt(np.clip(model, 0, None)) + rng.choice(res, size=len(res), replace=True)).clip(0) ** 2
        try:
            tb, _ = fit_seir(synth, population, starts=[tuple(theta)])
            boots.append(tb)
        except RuntimeError:
            continue
    boots = np.array(boots)

    defs = scenario_defs(early_lag, late_lag)
    curves, rows = {}, []
    for name, red, lag in defs:
        start_day = None if lag is None else (w0 + lag) * 7.0
        sims = np.array([simulate(population, *_theta_to_params(t), len(obs), red, start_day) for t in boots])
        curves[name] = (sims[0], np.percentile(sims, 10, axis=0), np.percentile(sims, 90, axis=0), sims)
    base_sims = curves["baseline_no_intervention"][3]
    for name, red, lag in defs:
        c, lo_b, hi_b, sims = curves[name]
        tot, pk, pkw = sims.sum(axis=1), sims.max(axis=1), sims.argmax(axis=1)
        averted = 100 * (1 - tot / base_sims.sum(axis=1))
        rows.append(dict(scenario=name, contact_reduction=red,
                         start_week=None if lag is None else str(span["week_start_date"].iloc[w0 + lag].date()),
                         peak_weekly_cases=round(c.max(), 0), peak_p10=round(np.percentile(pk, 10), 0),
                         peak_p90=round(np.percentile(pk, 90), 0),
                         peak_week=str(span["week_start_date"].iloc[int(c.argmax())].date()),
                         total_cases=round(c.sum(), 0), total_p10=round(np.percentile(tot, 10), 0),
                         total_p90=round(np.percentile(tot, 90), 0),
                         cases_averted_pct=round(float(averted[0]), 1),
                         averted_p10=round(float(np.percentile(averted, 10)), 1),
                         averted_p90=round(float(np.percentile(averted, 90)), 1)))
    long = []
    for name, _, _ in defs:
        c, lo_b, hi_b, _ = curves[name]
        for k in range(len(obs)):
            long.append(dict(scenario=name, week_start=str(span["week_start_date"].iloc[k].date()),
                             observed_excess=round(float(obs[k]), 1), model_cases=round(float(c[k]), 1),
                             p10=round(float(lo_b[k]), 1), p90=round(float(hi_b[k]), 1)))

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(long).to_csv(out_dir / "seir_scenarios.csv", index=False)
    summary = pd.DataFrame(rows)
    summary.to_csv(out_dir / "seir_summary.csv", index=False)
    r0s, rhos = boots[:, 0], 10.0 ** boots[:, 1]
    info = dict(
        provenance=dict(gold_file=nat.attrs["path"], gold_rows=nat.attrs["rows"], gold_sha256=nat.attrs["sha256"],
                        disease=disease, data_from=str(nat["week_start_date"].min().date()),
                        data_to=str(nat["week_start_date"].max().date()),
                        note="gold built from the synthetic generator (assignment S4); no outbreak-label file is read"),
        surge_window=dict(first_week=str(n["week_start_date"].iloc[lo].date()),
                          last_week=str(n["week_start_date"].iloc[hi].date()), weeks=int(hi - lo + 1),
                          peak_excess=float(n["excess"].iloc[lo:hi + 1].max()), noise_sigma=round(sigma, 1)),
        population=population,
        assumptions=dict(latent_days=LATENT_DAYS, infectious_days=INFECTIOUS_DAYS, mixing="homogeneous, national",
                         intervention="proportional cut in transmission from the start week",
                         early_start_weeks_after_surge_start=early_lag, late_start_weeks_after_surge_start=late_lag),
        fitted=dict(R0=round(r0, 2), R0_p10=round(float(np.percentile(r0s, 10)), 2),
                    R0_p90=round(float(np.percentile(r0s, 90)), 2), reporting_fraction=float(f"{rho:.3g}"),
                    reporting_p10=float(f"{np.percentile(rhos, 10):.3g}"),
                    reporting_p90=float(f"{np.percentile(rhos, 90):.3g}"), seed_fraction=sd,
                    n_bootstrap=int(len(boots) - 1)),
        fit_quality=fit)
    (out_dir / "seir_fit.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"gold: {nat.attrs['path']} ({nat.attrs['rows']:,} rows, sha256 {nat.attrs['sha256'][:12]}...)")
    print(f"surge window {info['surge_window']['first_week']} .. {info['surge_window']['last_week']} "
          f"({info['surge_window']['weeks']} weeks); fit R0 {r0:.2f} [{info['fitted']['R0_p10']}, {info['fitted']['R0_p90']}], "
          f"reporting {rho:.2e}, R2 in window {fit['r2_in_window']}")
    print(summary[["scenario", "peak_weekly_cases", "total_cases", "cases_averted_pct", "averted_p10", "averted_p90"]]
          .to_string(index=False))
    if at_bound:
        print(f"WARNING: fitted parameter(s) on a bound: {at_bound}; do not quote them as estimates")
    print(f"wrote {out_dir / 'seir_scenarios.csv'}, seir_summary.csv, seir_fit.json")
    return info, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold-csv", default="data/gold_csv")
    ap.add_argument("--out", default="data/analytics")
    ap.add_argument("--disease", default="ili")
    ap.add_argument("--n-boot", type=int, default=40)
    a = ap.parse_args(argv)
    run(Path(a.gold_csv), Path(a.out), a.disease, n_boot=a.n_boot)
    return 0


if __name__ == "__main__":
    sys.exit(main())

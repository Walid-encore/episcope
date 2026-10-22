"""Demand conversion (AN-6): case forecasts -> beds, IV saline, NS1 kits, ORS sachets.  Output = gold_demand.

Conversion (every number lives in ASSUMPTIONS and is written to docs/assumptions.md, so doc and code agree):
  admissions     = cases x admission_rate        rate per district x disease, observed in gold_weekly_cases
                                                 (admitted_cases / cases), shrunk toward the national rate
                                                 with k pseudo-cases so small districts are not noisy
  beds_needed    = admissions x length_of_stay_days / 7     (Little's law, steady state: occupied beds = arrivals x stay)
  iv_saline      = admissions x saline_units_per_admission
  ns1_kits       = cases x tests_per_case x (1 + wastage)   (dengue)
  ors_sachets    = cases x sachets_per_case                 (diarrhoeal)
Central value uses the forecast mean; the *_q90 columns apply the same conversion to the forecast's 90th
percentile. That carries FORECAST uncertainty only. Conversion-parameter uncertainty is shown separately by
assumption_sensitivity(). The optimiser should plan on the q90 columns (docs: "use an upper prediction quantile").

THE NUMBERS IN ASSUMPTIONS ARE PLACEHOLDERS CHOSEN FOR THIS PROTOTYPE, NOT TAKEN FROM MoH OR WHO PROTOCOLS.
Replace them with sourced values (and cite them) before the report, or state them as stated assumptions.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
KEYS = ["district_code", "disease"]
RESOURCES = ["beds", "iv_saline_units", "ns1_kits", "ors_sachets"]

ASSUMPTIONS = {
    "los_days": {"dengue": 4.0, "diarrhoeal": 2.0},                     # mean length of stay per admission
    "saline_units_per_admission": {"dengue": 4.0, "diarrhoeal": 3.0},   # IV saline units per admitted case
    "ns1_tests_per_case": {"dengue": 0.5, "diarrhoeal": 0.0},           # share of cases that get an NS1 test
    "ns1_wastage": 0.10,                                                # extra kits lost or expired
    "ors_sachets_per_case": {"dengue": 0.0, "diarrhoeal": 6.0},         # ORS sachets per case
    "admission_shrink_k": 50.0,                                         # pseudo-cases for shrinkage
    "fallback_admission_rate": {"dengue": 0.15, "diarrhoeal": 0.05},    # if no history exists
}


def admission_rates(cases, shrink_k=None, fallback=None):
    """Admission rate per district x disease from gold_weekly_cases (cases, admitted_cases),
    shrunk toward the national rate: (admitted + k*national) / (cases + k)."""
    k = ASSUMPTIONS["admission_shrink_k"] if shrink_k is None else shrink_k
    fb = ASSUMPTIONS["fallback_admission_rate"] if fallback is None else fallback
    c = cases.copy()
    c["district_code"] = c["district_code"].astype(str).str.zfill(2)
    g = c.groupby(KEYS, as_index=False)[["cases", "admitted_cases"]].sum()
    nat = c.groupby("disease")[["cases", "admitted_cases"]].sum()
    nat_rate = (nat["admitted_cases"] / nat["cases"].replace(0, np.nan)).fillna(pd.Series(fb))
    g["national_rate"] = g["disease"].map(nat_rate)
    g["admission_rate"] = (g["admitted_cases"] + k * g["national_rate"]) / (g["cases"] + k)
    return g[KEYS + ["admission_rate"]]


def _convert(cases, admissions, disease, a):
    return {
        "beds": admissions * disease.map(a["los_days"]) / 7.0,
        "iv_saline_units": admissions * disease.map(a["saline_units_per_admission"]),
        "ns1_kits": cases * disease.map(a["ns1_tests_per_case"]) * (1 + a["ns1_wastage"]),
        "ors_sachets": cases * disease.map(a["ors_sachets_per_case"]),
    }


def _require_full_horizons(f, max_horizon=4):
    got = f.groupby(KEYS)["horizon_weeks"].nunique()
    short = got[got < max_horizon]
    if len(short):
        raise ValueError(f"{len(short)} series lack all {max_horizon} live horizons, "
                         f"e.g. {short.index[:3].tolist()}")


def compute_demand(forecast, rates, assumptions=None, model="nb_lagged_weather", max_horizon=4):
    """forecast = gold_forecast rows. Uses live (non-backtest) rows of `model` from the latest origin per series."""
    a = ASSUMPTIONS if assumptions is None else assumptions
    f = forecast[(forecast["model"] == model) & (~forecast["is_backtest"].astype(bool))].copy()
    f["district_code"] = f["district_code"].astype(str).str.zfill(2)
    f["_origin"] = f["origin_epi_year"] * 100 + f["origin_epi_week"]
    f = f[f["_origin"] == f.groupby(KEYS)["_origin"].transform("max")]
    _require_full_horizons(f, max_horizon)
    f = f.merge(rates, on=KEYS, how="left", validate="many_to_one")
    f["admission_rate"] = f["admission_rate"].fillna(f["disease"].map(a["fallback_admission_rate"]))
    out = f[KEYS + ["target_epi_year", "target_epi_week", "target_week_start_date", "horizon_weeks"]].copy()
    for suffix, col in (("_needed", "pred_mean"), ("_needed_q90", "q90")):
        cases = f[col].astype(float)
        conv = _convert(cases, cases * f["admission_rate"], f["disease"], a)
        for res, val in conv.items():
            out[f"{res}{suffix}"] = val
    cols = KEYS + ["target_epi_year", "target_epi_week", "target_week_start_date", "horizon_weeks",
                   "beds_needed", "iv_saline_units_needed", "ns1_kits_needed", "ors_sachets_needed",
                   "beds_needed_q90", "iv_saline_units_needed_q90", "ns1_kits_needed_q90",
                   "ors_sachets_needed_q90"]
    return out[cols].sort_values(KEYS + ["horizon_weeks"]).reset_index(drop=True)


def planning_totals(demand, max_horizon=4):
    """Demand per district over the planning horizon, long format for the optimiser:
    district_code, resource, demand, demand_q90.
    Consumables (saline, kits, ORS) add up over weeks and diseases. Beds are not consumed, so a district needs
    the PEAK weekly total across diseases, not the sum over weeks."""
    d = demand[demand["horizon_weeks"] <= max_horizon]
    rows = []
    for suffix, label in (("_needed", "demand"), ("_needed_q90", "demand_q90")):
        sup = d.groupby("district_code")[[f"{r}{suffix}" for r in RESOURCES[1:]]].sum()
        beds = (d.groupby(["district_code", "horizon_weeks"])[f"beds{suffix}"].sum()
                  .groupby("district_code").max())
        wide = sup.assign(**{f"beds{suffix}": beds})
        long = wide.rename(columns={f"{r}{suffix}": r for r in RESOURCES}).stack().rename(label)
        rows.append(long)
    out = pd.concat(rows, axis=1).reset_index()
    out.columns = ["district_code", "resource", "demand", "demand_q90"]
    return out.sort_values(["resource", "district_code"]).reset_index(drop=True)


def assumption_sensitivity(forecast, rates, factors=(0.75, 1.25), model="nb_lagged_weather"):
    """Scale each conversion assumption by each factor and report the national planning total per resource.
    Shows how much the demand figures depend on numbers that are assumed, not measured."""
    base = planning_totals(compute_demand(forecast, rates, model=model)).groupby("resource")["demand"].sum()
    scalable = ["los_days", "saline_units_per_admission", "ns1_tests_per_case", "ns1_wastage",
                "ors_sachets_per_case"]
    rows = []
    for name in scalable:
        for fac in factors:
            a = {k: (dict(v) if isinstance(v, dict) else v) for k, v in ASSUMPTIONS.items()}
            if isinstance(a[name], dict):
                a[name] = {d: v * fac for d, v in a[name].items()}
            else:
                a[name] = a[name] * fac
            tot = planning_totals(compute_demand(forecast, rates, assumptions=a, model=model)
                                  ).groupby("resource")["demand"].sum()
            for res in RESOURCES:
                if base[res] > 0 and abs(tot[res] - base[res]) > 1e-9:
                    rows.append(dict(assumption=name, factor=fac, resource=res,
                                     national_demand=tot[res], change_pct=100 * (tot[res] / base[res] - 1)))
    return pd.DataFrame(rows)


def assumptions_markdown():
    a = ASSUMPTIONS
    lines = ["# Demand-conversion assumptions (AN-6)", "",
             "Generated by `analytics/optimise/demand.py`. **These values are placeholders chosen for this prototype. "
             "They are not taken from MoH or WHO protocols; replace them with sourced values or present them as stated "
             "assumptions.** `assumption_sensitivity()` shows how much each one moves the totals.", "",
             "| Quantity | Dengue | Diarrhoeal | Meaning |", "|---|---|---|---|"]
    rows = [("los_days", "Mean length of stay (days)"),
            ("saline_units_per_admission", "IV saline units per admitted case"),
            ("ns1_tests_per_case", "Share of cases given an NS1 test"),
            ("ors_sachets_per_case", "ORS sachets per case"),
            ("fallback_admission_rate", "Admission rate used if a district has no history")]
    for key, text in rows:
        lines.append(f"| {key} | {a[key]['dengue']} | {a[key]['diarrhoeal']} | {text} |")
    lines += ["", f"- NS1 wastage: {a['ns1_wastage']:.0%} extra kits.",
              f"- Admission rate: observed admitted/cases per district and disease, shrunk toward the national rate "
              f"with k = {a['admission_shrink_k']:.0f} pseudo-cases.", "",
              "## Formulae", "",
              "- admissions = cases x admission_rate",
              "- beds = admissions x length_of_stay / 7 (Little's law, steady state)",
              "- IV saline = admissions x saline units per admission",
              "- NS1 kits = cases x tests per case x (1 + wastage)",
              "- ORS sachets = cases x sachets per case", "",
              "## Uncertainty", "",
              "- `*_q90` columns apply the same formulae to the forecast's 90th percentile (forecast uncertainty only).",
              "- Beds are peak weekly occupancy over the horizon; consumables are summed over weeks and diseases.",
              "- Not modelled: carry-over of patients admitted before the forecast window, "
              "admission rates rising during outbreaks, and district-specific clinical practice."]
    return "\n".join(lines) + "\n"


def main():
    out = ROOT / "docs" / "assumptions.md"
    out.write_text(assumptions_markdown(), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

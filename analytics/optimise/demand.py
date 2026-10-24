"""Demand conversion (AN-6): case forecasts -> beds, IV saline, NS1 kits, ORS sachets.  Output = gold_demand.

Every number lives in ASSUMPTIONS and is written to docs/demand_assumptions.md, so doc and code agree.

  admissions     = cases x admission_rate        rate per district x disease, observed in gold_weekly_cases
                                                 (admitted_cases / cases), shrunk toward the national rate
                                                 with k pseudo-cases so small districts are not noisy
  beds_needed    = admissions x length_of_stay_days / 7     (Little's law: occupied beds = arrivals x stay)
  iv_saline      = cases x saline_units_per_case            (all four diseases)
  ns1_kits       = cases x tests_per_case x (1 + wastage)   (dengue)
  ors_sachets    = cases x sachets_per_case                 (diarrhoeal)

Central value = forecast mean. *_q90 = the same conversion on the forecast 90th percentile, and *_q10 is added
when the forecast has a q10 column. That carries FORECAST uncertainty only; conversion-parameter uncertainty is
shown by assumption_sensitivity(). The optimiser plans on the q90 columns (brief: "use an upper prediction quantile").

THE NUMBERS IN ASSUMPTIONS ARE THE PARAMETERS OF THE SYNTHETIC DATA GENERATOR (generator/model.py), NOT CLINICAL
GUIDANCE. Real protocols differ, so the sensitivity run shows how much each value matters.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
KEYS = ["district_code", "disease"]
RESOURCES = ["beds", "iv_saline_units", "ns1_kits", "ors_sachets"]
DISEASES = ["dengue", "diarrhoeal", "malaria", "ili"]

ASSUMPTIONS = {
    # Source: generator/model.py: ITEM_COEF (per encounter), ADMIT_P, 5-day stay in simulate_stock
    "los_days": {"dengue": 5.0, "diarrhoeal": 5.0, "malaria": 5.0, "ili": 5.0},      # mean days per admission
    "saline_units_per_case": {"dengue": 1.12, "diarrhoeal": 0.36, "malaria": 0.25, "ili": 0.06},
    "ns1_tests_per_case": {"dengue": 0.6, "diarrhoeal": 0.0, "malaria": 0.0, "ili": 0.0},
    "ns1_wastage": 0.0,                                                              # generator has none
    "ors_sachets_per_case": {"dengue": 0.0, "diarrhoeal": 4.0, "malaria": 0.0, "ili": 0.0},
    "admission_shrink_k": 50.0,                                                      # pseudo-cases for shrinkage
    "fallback_admission_rate": {"dengue": 0.28, "diarrhoeal": 0.12, "malaria": 0.25, "ili": 0.06},
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
        "iv_saline_units": cases * disease.map(a["saline_units_per_case"]),
        "ns1_kits": cases * disease.map(a["ns1_tests_per_case"]) * (1 + a["ns1_wastage"]),
        "ors_sachets": cases * disease.map(a["ors_sachets_per_case"]),
    }


def _require_full_horizons(f, max_horizon=4):
    got = f.groupby(KEYS)["horizon_weeks"].nunique()
    short = got[got < max_horizon]
    if len(short):
        raise ValueError(f"{len(short)} series lack all {max_horizon} live horizons, "
                         f"e.g. {short.index[:3].tolist()}")


def _require_known_diseases(f, a):
    """A disease without conversion values would silently become NaN and drop out of every total."""
    for key in ("los_days", "saline_units_per_case", "ns1_tests_per_case", "ors_sachets_per_case",
                "fallback_admission_rate"):
        missing = sorted(set(f["disease"]) - set(a[key]))
        if missing:
            raise ValueError(f"no conversion values in ASSUMPTIONS['{key}'] for disease(s) {missing}")


def compute_demand(forecast, rates, assumptions=None, model="nb_lagged_weather", max_horizon=4, origin=None):
    """forecast = gold_forecast rows. By default uses live (non-backtest) rows of `model` from the latest origin
    per series. origin = year*100 + week uses that forecast origin instead (backtest rows allowed), for
    planning scenarios at a past date such as the seasonal peak."""
    a = ASSUMPTIONS if assumptions is None else assumptions
    f = forecast[forecast["model"] == model].copy()
    f["district_code"] = f["district_code"].astype(str).str.zfill(2)
    f["_origin"] = f["origin_epi_year"] * 100 + f["origin_epi_week"]
    if origin is None:
        f = f[~f["is_backtest"].astype(bool)]
        f = f[f["_origin"] == f.groupby(KEYS)["_origin"].transform("max")]
    else:
        f = f[f["_origin"] == int(origin)].drop_duplicates(KEYS + ["horizon_weeks"], keep="last")
        if f.empty:
            raise ValueError(f"no forecast rows for model {model} at origin {origin}")
    _require_full_horizons(f, max_horizon)
    _require_known_diseases(f, a)
    f = f.merge(rates, on=KEYS, how="left", validate="many_to_one")
    f["admission_rate"] = f["admission_rate"].fillna(f["disease"].map(a["fallback_admission_rate"]))
    out = f[KEYS + ["target_epi_year", "target_epi_week", "target_week_start_date", "horizon_weeks"]].copy()
    variants = [("_needed", "pred_mean"), ("_needed_q90", "q90")]
    if "q10" in f.columns:
        variants.append(("_needed_q10", "q10"))
    for suffix, col in variants:
        cases = f[col].astype(float)
        if col == "q90":
            cases = np.maximum(cases, f["pred_mean"].astype(float))
        elif col == "q10":
            cases = np.minimum(cases, f["pred_mean"].astype(float))
        conv = _convert(cases, cases * f["admission_rate"], f["disease"], a)
        for res, val in conv.items():
            out[f"{res}{suffix}"] = val
    cols = KEYS + ["target_epi_year", "target_epi_week", "target_week_start_date", "horizon_weeks"]
    for suffix, _ in variants:
        cols += [f"{r}{suffix}" for r in RESOURCES]
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


def implied_admission_rates(forecast, demand, model="nb_lagged_weather", assumptions=None):
    """Recover the admission rates behind a gold_demand table: beds x 7 / (stay x forecast mean)."""
    a = ASSUMPTIONS if assumptions is None else assumptions
    f = forecast[(forecast["model"] == model) & (~forecast["is_backtest"].astype(bool))].copy()
    f["district_code"] = f["district_code"].astype(str).str.zfill(2)
    d = demand.copy()
    d["district_code"] = d["district_code"].astype(str).str.zfill(2)
    m = d.merge(f[KEYS + ["horizon_weeks", "pred_mean"]], on=KEYS + ["horizon_weeks"])
    m = m[m["pred_mean"] > 0].copy()
    m["admission_rate"] = m["beds_needed"] * 7 / (m["disease"].map(a["los_days"]) * m["pred_mean"])
    return m.groupby(KEYS, as_index=False)["admission_rate"].median()


SCALABLE = ["los_days", "saline_units_per_case", "ns1_tests_per_case", "ors_sachets_per_case"]


def assumption_sensitivity(forecast, rates, factors=(0.75, 1.25), model="nb_lagged_weather"):
    """Scale each conversion assumption by each factor and report the national planning total per resource
    (mean and q90). ns1_wastage is excluded: its base value is 0, so scaling it changes nothing."""
    def totals(a):
        t = planning_totals(compute_demand(forecast, rates, assumptions=a, model=model))
        return t.groupby("resource")[["demand", "demand_q90"]].sum()

    base = totals(ASSUMPTIONS)
    rows = []
    for name in SCALABLE:
        for fac in factors:
            a = {k: (dict(v) if isinstance(v, dict) else v) for k, v in ASSUMPTIONS.items()}
            a[name] = {d: v * fac for d, v in a[name].items()}
            tot = totals(a)
            for res in RESOURCES:
                if base.loc[res, "demand"] > 0 and abs(tot.loc[res, "demand"] - base.loc[res, "demand"]) > 1e-9:
                    rows.append(dict(
                        assumption=name, factor=fac, resource=res,
                        national_demand=tot.loc[res, "demand"],
                        change_pct=100 * (tot.loc[res, "demand"] / base.loc[res, "demand"] - 1),
                        national_demand_q90=tot.loc[res, "demand_q90"],
                        change_pct_q90=100 * (tot.loc[res, "demand_q90"] / base.loc[res, "demand_q90"] - 1)))
    return pd.DataFrame(rows)


def assumptions_markdown():
    a = ASSUMPTIONS
    lines = ["# Demand-conversion assumptions (AN-6)", "",
             "Generated by `analytics/optimise/demand.py`. **These values are the parameters of the synthetic data "
             "generator (`generator/model.py`), not clinical guidance. Real protocols differ; present them as stated "
             "assumptions.** `assumption_sensitivity()` shows how much each one moves the totals.", "",
             "| Quantity | " + " | ".join(d.capitalize() if d != "ili" else "ILI" for d in DISEASES) + " | Meaning |",
             "|---|" + "---|" * len(DISEASES) + "---|"]
    rows = [("los_days", "Mean length of stay (days)"),
            ("saline_units_per_case", "IV saline units per case"),
            ("ns1_tests_per_case", "NS1 tests per case"),
            ("ors_sachets_per_case", "ORS sachets per case"),
            ("fallback_admission_rate", "Admission rate used if a district has no history")]
    for key, text in rows:
        lines.append("| " + key + " | " + " | ".join(str(a[key][d]) for d in DISEASES) + f" | {text} |")
    lines += ["", f"- NS1 wastage: {a['ns1_wastage']:.0%} extra kits.",
              f"- Admission rate: observed admitted/cases per district and disease, shrunk toward the national rate "
              f"with k = {a['admission_shrink_k']:.0f} pseudo-cases.", "",
              "## Formulae", "",
              "- admissions = cases x admission_rate",
              "- beds = admissions x length_of_stay / 7 (Little's law, steady state)",
              "- IV saline = cases x saline units per case",
              "- NS1 kits = cases x tests per case x (1 + wastage)",
              "- ORS sachets = cases x sachets per case", "",
              "## Scope and uncertainty", "",
              "- Source: generator/model.py (ITEM_COEF per encounter, ADMIT_P, 5-day stay). Malaria and ILI need beds "
              "and IV saline only. Planning horizon: 4 weeks; planning demand: q90.",
              "- `*_q90` columns apply the same formulae to the forecast's 90th percentile (forecast uncertainty only).",
              "- Forecast quantiles are integers, so q90 can fall below the mean for low-count series; q90 demand is floored at the mean (q10 capped at it). "
              "Summing weekly q90 values over 4 weeks assumes the weeks move together, which is conservative.",
              "- Beds are peak weekly occupancy over the horizon; consumables are summed over weeks and diseases.",
              "- Beds are the extra load from these four diseases, not total occupancy: background admissions from "
              "other conditions are not modelled. Antimalarial courses are not converted.",
              "- Not modelled: carry-over of patients admitted before the forecast window, "
              "admission rates rising during outbreaks, and district-specific clinical practice."]
    return "\n".join(lines) + "\n"


def main():
    out = ROOT / "docs" / "demand_assumptions.md"
    out.write_text(assumptions_markdown(), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

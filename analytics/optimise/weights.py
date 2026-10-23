"""Severity weights w_d for the allocation objective (AN-7, section 6.3).

w_d says how costly one unit of unmet demand is in district d. It is a policy choice, so it is
simple and documented in docs/assumptions.md:

  adm_rel   = shrunk admission rate of district d / national admission rate
  death_rel = shrunk death rate of district d     / national death rate
  w_d       = clip(0.5 * adm_rel + 0.5 * death_rel, lo, hi), then rescaled to mean 1

Shrinkage toward the national rate, (events + k * national) / (cases + k), stops a district
with a handful of cases from getting an extreme weight. Districts with no data get weight 1.

Notes for the report (limitations)
  * Clipping to [lo, hi] happens BEFORE the rescale to mean 1, so final weights can fall outside
    [lo, hi] (the bounds limit relative severity, not the final values). describe() reports how
    many districts hit a bound.
  * Rates are computed over all diseases pooled. A district's admission/death rate therefore
    reflects its disease mix (e.g. dengue-heavy vs. diarrhoea-heavy) as well as vulnerability,
    and one weight per district is applied to every resource. Per-disease or per-resource
    weights would need disease-level demand, which the allocation input does not carry.
  * If the national totals of cases, admissions or deaths are zero, all weights fall back to 1
    and a warning is printed (the allocation is then unweighted).
"""
import warnings

import pandas as pd

LO, HI = 0.5, 2.0
K_ADMISSION, K_DEATH = 100, 1000     # deaths are rarer, so they need stronger shrinkage


def severity_weights(cases, district_codes, lo=LO, hi=HI, k_adm=K_ADMISSION, k_death=K_DEATH):
    """cases: gold_weekly_cases rows (district_code, cases, admitted_cases, deaths).
    Returns a Series indexed by district_code (all of district_codes)."""
    c = cases.copy()
    c["district_code"] = c["district_code"].astype(str).str.zfill(2)
    g = c.groupby("district_code")[["cases", "admitted_cases", "deaths"]].sum()
    tot = g.sum()
    if tot["cases"] <= 0 or tot["admitted_cases"] <= 0 or tot["deaths"] <= 0:
        warnings.warn("severity_weights: national cases/admissions/deaths total is zero; "
                      "falling back to equal weights (all 1.0)")
        print("WARNING: severity weights fell back to equal weights (zero national totals)")
        eq = pd.Series(1.0, index=pd.Index(district_codes, name="district_code"))
        eq.attrs["n_clipped"] = 0
        return eq
    nat_adm, nat_death = tot["admitted_cases"] / tot["cases"], tot["deaths"] / tot["cases"]
    adm = (g["admitted_cases"] + k_adm * nat_adm) / (g["cases"] + k_adm)
    dth = (g["deaths"] + k_death * nat_death) / (g["cases"] + k_death)
    raw = 0.5 * adm / nat_adm + 0.5 * dth / nat_death
    w = raw.clip(lo, hi)
    n_clipped = int(((raw < lo) | (raw > hi)).sum())
    w = w.reindex(pd.Index(district_codes, name="district_code")).fillna(1.0)
    out = w / w.mean()
    out.attrs["n_clipped"] = n_clipped
    return out


def describe(w):
    return (f"weights: min {w.min():.2f}, median {w.median():.2f}, max {w.max():.2f}, "
            f"share != 1.0 (+-0.02): {(abs(w - 1) > 0.02).mean():.0%}, "
            f"districts clipped to [{LO}, {HI}] before rescale: {w.attrs.get('n_clipped', 'n/a')}")

"""Seasonal preparedness calendar (AN-9): per division and disease, when the season starts, peaks
and ends, and when to pre-position supplies and start prevention campaigns.

    python -m analytics.calendar.preparedness [--gold data/standin_gold] [--year 2026]

Method
  1. Weekly incidence per 100,000 for each division x disease (onset weeks, edge weeks trimmed).
  2. STL decomposition (period 52, robust) separates trend, seasonal cycle and noise.
  3. Seasonal index for ISO week w = 1 + mean seasonal component in week w / mean trend
     (1.0 = average week; 1.5 = 50% above average). Smoothed over 3 weeks (circular).
  4. Peak = highest index. High season = the contiguous weeks around the peak whose index stays
     above 1 + frac x (peak index - 1) (default frac = 0.5, i.e. half-way up the peak).
  5. Actions, counted back from season onset (ASSUMPTIONS, change with the options below):
     pre-position supplies --lead-supplies weeks before onset (default 4);
     start prevention campaigns --lead-campaign weeks before onset (default 8).
  Divisions with a weak cycle get "no clear season": either the STL seasonal strength is below
  --min-strength (default 0.5, the index is then flattened), or the peak index is below
  1 + --min-amplitude.
ISO week 53 is merged into week 52. Calendar dates are the Mondays of the requested year.
"""
import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

ROOT = Path(__file__).resolve().parents[2]
WEEKS = 52


def seasonal_index(weekly, min_strength=0.5):
    """weekly: Series of weekly incidence indexed by Monday dates. Returns (index[1..52], strength)."""
    s = weekly.sort_index().asfreq("7D").fillna(0.0)
    res = STL(s, period=WEEKS, robust=True).fit()
    trend_mean = float(res.trend.mean())
    if trend_mean <= 0:
        return pd.Series(1.0, index=range(1, WEEKS + 1)), 0.0
    woy = s.index.isocalendar().week.to_numpy().clip(max=WEEKS)
    prof = pd.Series(res.seasonal.to_numpy()).groupby(woy).mean().reindex(range(1, WEEKS + 1)).fillna(0.0)
    idx = 1.0 + prof / trend_mean
    a = idx.to_numpy()
    sm = (np.roll(a, 1) + a + np.roll(a, -1)) / 3.0
    denom = float((res.seasonal + res.resid).var())
    strength = max(0.0, 1.0 - float(res.resid.var()) / denom) if denom > 0 else 0.0
    if strength < min_strength:
        sm = np.ones(WEEKS)  # no reliable seasonality: flat index, so no clear season
    return pd.Series(sm, index=range(1, WEEKS + 1)), strength


def season_window(idx, frac=0.5, min_amplitude=0.2):
    """Return dict(peak, onset, end, peak_index, clear) with ISO weeks 1..52 (circular)."""
    a = idx.to_numpy()
    p = int(np.argmax(a))
    amp = a[p] - 1.0
    out = dict(peak_index=float(a[p]), peak=p + 1, onset=None, end=None, clear=amp >= min_amplitude)
    if not out["clear"]:
        return out
    thr = 1.0 + frac * amp
    left = right = 0
    while left < WEEKS - 1 and a[(p - left - 1) % WEEKS] >= thr:
        left += 1
    while right < WEEKS - 1 - left and a[(p + right + 1) % WEEKS] >= thr:
        right += 1
    out["onset"] = (p - left) % WEEKS + 1
    out["end"] = (p + right) % WEEKS + 1
    return out


def shift_week(w, weeks):
    return (w - 1 + weeks) % WEEKS + 1


def week_date(year, week):
    return dt.date.fromisocalendar(year, week, 1)


def build_calendar(cases, districts, year, frac, min_amp, lead_supplies, lead_campaign,
                   min_strength=0.5):
    d = cases.merge(districts[["district_code", "division", "population"]], on="district_code")
    pop = districts.groupby("division")["population"].sum()
    g = d.groupby(["division", "disease", "week_start_date"], as_index=False)["cases"].sum()
    g["incidence"] = g["cases"] / g["division"].map(pop) * 1e5
    cal, prof = [], []
    for (div, dis), s in g.groupby(["division", "disease"]):
        idx, strength = seasonal_index(s.set_index("week_start_date")["incidence"], min_strength)
        w = season_window(idx, frac, min_amp)
        row = dict(division=div, disease=dis, seasonal_strength=round(strength, 3),
                   peak_index=round(w["peak_index"], 2), clear_season=w["clear"])
        if w["clear"]:
            sup, cam = shift_week(w["onset"], -lead_supplies), shift_week(w["onset"], -lead_campaign)
            row.update(onset_week=w["onset"], peak_week=w["peak"], end_week=w["end"],
                       prepose_supplies_week=sup, start_campaign_week=cam,
                       onset_date=week_date(year, w["onset"]), peak_date=week_date(year, w["peak"]),
                       prepose_supplies_date=week_date(year, sup), start_campaign_date=week_date(year, cam))
        cal.append(row)
        prof += [dict(division=div, disease=dis, epi_week=k, seasonal_index=round(float(v), 3))
                 for k, v in idx.items()]
    return pd.DataFrame(cal), pd.DataFrame(prof)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "standin_gold"))
    ap.add_argument("--districts", default=str(ROOT / "data" / "reference" / "districts.csv"))
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--frac", type=float, default=0.5)
    ap.add_argument("--min-amplitude", type=float, default=0.2)
    ap.add_argument("--min-strength", type=float, default=0.5)
    ap.add_argument("--lead-supplies", type=int, default=4)
    ap.add_argument("--lead-campaign", type=int, default=8)
    ap.add_argument("--out", default=str(ROOT / "data" / "analytics"))
    a = ap.parse_args()
    cases = pd.read_csv(Path(a.gold) / "gold_weekly_cases.csv", dtype={"district_code": str})
    cases["week_start_date"] = pd.to_datetime(cases["week_start_date"])
    cases = cases[cases["week_start_date"] >= pd.Timestamp("2021-01-04")]
    cases = cases[cases["week_start_date"] <= cases["week_start_date"].max() - pd.Timedelta(weeks=3)]
    dis = pd.read_csv(a.districts, dtype={"district_code": str})
    cal, prof = build_calendar(cases, dis, a.year, a.frac, a.min_amplitude, a.lead_supplies, a.lead_campaign,
                             a.min_strength)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cal.to_csv(out / "preparedness_calendar.csv", index=False)
    prof.to_csv(out / "seasonal_profile.csv", index=False)
    pd.set_option("display.width", 220)
    print(cal.to_string(index=False))
    print(f"wrote {out / 'preparedness_calendar.csv'} and seasonal_profile.csv")


if __name__ == "__main__":
    main()

"""Run from C:\\episcope:  python apply_patch.py
Applies, with exact-match checks (aborts without writing if any anchor is not found exactly once):
  A. prepare_cases wired into run_demand, run_forecast_demand, preparedness; live-horizon guard in demand.py
  B. Dhaka dengue onset cap in preparedness.py, data-derived --year, tests/test_season_window.py
"""
from pathlib import Path

ROOT = Path.cwd()


def patch(rel, pairs):
    p = ROOT / rel
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        n = s.count(old)
        assert n == 1, f"{rel}: expected exactly 1 match, found {n} for:\n{old}"
        s = s.replace(old, new)
    return p, s


jobs = []

# ---------------------------------------------------------------- run_demand.py
jobs.append(patch("analytics/optimise/run_demand.py", [
    ("from analytics.forecast.features import build_weekly_weather\n",
     "from analytics.forecast.features import build_weekly_weather\n"
     "from analytics.forecast.prep import prepare_cases\n"),
    ('    ap.add_argument("--weather", default=str(ROOT / "data" / "reference" / "weather.csv"))\n',
     '    ap.add_argument("--weather", default=str(ROOT / "data" / "reference" / "weather.csv"))\n'
     '    ap.add_argument("--trim-last-weeks", type=int, default=3,\n'
     '                    help="drop the newest k weeks (partial week + late reports)")\n'),
    ('    table = cases.merge(wx, on=["district_code", "week_start_date"], how="left")\n',
     '    cases = prepare_cases(cases, wx["week_start_date"].max(), a.trim_last_weeks)\n'
     '    table = cases.merge(wx, on=["district_code", "week_start_date"], how="left")\n'),
    ('    totals.to_parquet(OUT / "planning_totals.parquet", index=False)\n',
     '    totals.to_parquet(OUT / "planning_totals.parquet", index=False)\n'
     '    totals.to_csv(OUT / "planning_totals.csv", index=False)  # run_allocation reads CSV\n'),
]))

# ---------------------------------------------------------------- run_forecast_demand.py
jobs.append(patch("analytics/run_forecast_demand.py", [
    ('    cases = cases[cases["week_start_date"] >= pd.Timestamp("2021-01-04")]\n'
     '    last = cases["week_start_date"].max() - pd.Timedelta(weeks=a.trim_last_weeks)\n'
     '    cases = cases[cases["week_start_date"] <= last]\n',
     '    from analytics.forecast.prep import prepare_cases\n'
     '    _wend = pd.read_parquet(ROOT / "data" / "analytics" / "gold_weekly_weather.parquet",\n'
     '                            columns=["week_start_date"])["week_start_date"].max()\n'
     '    cases = prepare_cases(cases, _wend, a.trim_last_weeks)\n'),
]))

# ---------------------------------------------------------------- demand.py (guard)
jobs.append(patch("analytics/optimise/demand.py", [
    ('def compute_demand(forecast, rates, assumptions=None, model="nb_lagged_weather"):\n',
     'def _require_full_horizons(f, max_horizon=4):\n'
     '    got = f.groupby(KEYS)["horizon_weeks"].nunique()\n'
     '    short = got[got < max_horizon]\n'
     '    if len(short):\n'
     '        raise ValueError(f"{len(short)} series lack all {max_horizon} live horizons, "\n'
     '                         f"e.g. {short.index[:3].tolist()}")\n'
     '\n\n'
     'def compute_demand(forecast, rates, assumptions=None, model="nb_lagged_weather", max_horizon=4):\n'),
    ('    f = f[f["_origin"] == f.groupby(KEYS)["_origin"].transform("max")]\n',
     '    f = f[f["_origin"] == f.groupby(KEYS)["_origin"].transform("max")]\n'
     '    _require_full_horizons(f, max_horizon)\n'),
]))

# ---------------------------------------------------------------- preparedness.py
jobs.append(patch("analytics/calendar/preparedness.py", [
    ('def season_window(idx, frac=0.5, min_amplitude=0.2):\n',
     'ONSET_CAP = 0.25  # in season once 25% above the annual average (threshold never stricter than this)\n\n\n'
     'def season_window(idx, frac=0.5, min_amplitude=0.2):\n'),
    ('    thr = 1.0 + frac * amp\n',
     '    thr = min(1.0 + frac * amp, 1.0 + ONSET_CAP)\n'),
    ('    ap.add_argument("--year", type=int, default=2026)\n',
     '    ap.add_argument("--year", type=int, default=None,\n'
     '                    help="target year; default = latest ISO year in the data + 1")\n'),
    ('    cases = cases[cases["week_start_date"] >= pd.Timestamp("2021-01-04")]\n'
     '    cases = cases[cases["week_start_date"] <= cases["week_start_date"].max() - pd.Timedelta(weeks=3)]\n',
     '    from analytics.forecast.prep import prepare_cases\n'
     '    cases = prepare_cases(cases)\n'
     '    if a.year is None:\n'
     '        a.year = int(cases["week_start_date"].dt.isocalendar().year.max()) + 1\n'),
]))

# all anchors matched: now write
for p, s in jobs:
    p.write_text(s, encoding="utf-8")
    print("patched", p.relative_to(ROOT))

t = ROOT / "tests" / "test_season_window.py"
t.write_text('''import numpy as np, pandas as pd
from analytics.calendar.preparedness import season_window


def test_sharp_peak_inside_long_elevated_season_uses_cap():
    a = np.full(52, 0.5)
    a[24:46] = 1.6          # weeks 25..46 elevated
    a[43] = 2.5             # sharp peak at week 44
    w = season_window(pd.Series(a, index=range(1, 53)))
    assert (w["onset"], w["peak"], w["end"]) == (25, 44, 46)
''', encoding="utf-8")
print("created tests/test_season_window.py")

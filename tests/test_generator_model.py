"""Tests for generator/model.py. No Spark needed. Run:  python -m pytest tests -q
(or, without pytest:  python tests/test_generator_model.py)"""
import datetime as dt
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generator import model as M          # noqa: E402
from geo_common import DIVISIONS, code_table   # noqa: E402

CFG = M.Config(seed=7, sf=0.02, start=dt.date(2021, 1, 1), end=dt.date(2022, 12, 31))


def make_reference(tmp: Path, last=dt.date(2022, 12, 31)):
    """Small synthetic stand-in for data/reference (64 districts, monsoon-shaped weather)."""
    rng = np.random.default_rng(0)
    codes = code_table()
    div = {n: d for d, names in DIVISIONS.items() for n in names}
    rows = []
    for name, code in codes.items():
        pop = int(rng.lognormal(14.3, 0.45)) * (4 if name == "Dhaka" else 1)
        rows.append((code, name, div[name], pop, round(rng.uniform(21, 26), 4), round(rng.uniform(88.5, 92), 4)))
    dist = pd.DataFrame(rows, columns=["district_code", "district_name", "division", "population",
                                       "centroid_lat", "centroid_lon"])
    dates = pd.date_range("2020-01-01", pd.Timestamp(last))
    w = []
    for c in dist["district_code"]:
        doy = dates.dayofyear.to_numpy()
        rain = np.maximum(0, rng.gamma(0.4, 12, len(dates)) * (1 + 1.6 * np.sin((doy - 120) / 365 * 2 * np.pi)))
        temp = 26 + 5 * np.sin((doy - 100) / 365 * 2 * np.pi) + rng.normal(0, 1, len(dates))
        w.append(pd.DataFrame({"district_code": c, "date": dates.strftime("%Y-%m-%d"),
                               "precip_mm": rain.round(1), "temp_mean_c": temp.round(1),
                               "humidity_pct": 80.0}))
    tmp.mkdir(parents=True, exist_ok=True)
    dist.to_csv(tmp / "districts.csv", index=False)
    pd.concat(w).to_csv(tmp / "weather.csv", index=False)


_cache = {}


def model():
    if "m" not in _cache:
        tmp = Path(tempfile.mkdtemp())
        make_reference(tmp)
        _cache["m"] = M.build(CFG, tmp)
        _cache["tmp"] = tmp
    return _cache["m"]


def test_facilities_follow_schema():
    f = model().fac
    assert len(f) >= 600 and f["facility_id"].is_unique
    assert set(f["facility_type"]) == {"hospital", "upazila_health_complex", "clinic"}
    assert (f["beds"] >= 0).all() and (f["icu_beds"] >= 0).all() and (f["icu_beds"] <= f["beds"]).all()
    assert np.allclose(f.groupby("district_code")["weight"].sum(), 1.0)


def test_volume_matches_target_scale():
    m = model()
    target = M.ROWS_AT_SF1 * CFG.sf * CFG.n_days / M.FULL_DAYS
    base = m.mu.sum()
    assert abs(base / target - 1) < 1e-6
    assert 0.95 < m.counts.sum() / target < 1.25      # baseline noise + outbreak extras


def test_deterministic():
    a = model()
    b = M.build(CFG, _cache["tmp"])
    assert (a.counts == b.counts).all()
    assert a.outbreaks.equals(b.outbreaks) and a.fac.equals(b.fac)


def test_outbreak_ground_truth():
    gt = model().outbreaks
    ob = gt[gt.kind != "reporting_artefact"]
    assert len(ob) >= 30
    assert {"local_sharp", "local_slow", "national_surge"} <= set(ob.kind)
    assert (gt.kind == "reporting_artefact").sum() >= 1
    assert (ob["realised_extra_cases"].astype(int) > 0).all()
    assert (pd.to_datetime(ob.start_date) <= pd.to_datetime(ob.peak_date)).all()
    assert (pd.to_datetime(ob.peak_date) <= pd.to_datetime(ob.end_date)).all()
    # no two local outbreaks of the same disease overlap in the same district
    loc = ob[ob.kind != "national_surge"]
    for _, g in loc.groupby(["district_code", "disease_group"]):
        g = g.sort_values("start_date")
        assert (pd.to_datetime(g.start_date.iloc[1:].values) > pd.to_datetime(g.end_date.iloc[:-1].values)).all()


def test_truth_export_matches_schema_md():
    main, extras, art = M.export_truth(model().outbreaks)
    assert list(main.columns) == ["outbreak_id", "district_code", "disease", "start_date", "end_date",
                                  "peak_multiplier", "kind"]
    assert set(main["kind"]) <= {"local", "slow_growth", "national_surge"}
    assert {"local", "slow_growth", "national_surge"} <= set(main["kind"])
    assert (main["district_code"].isin(["ALL"]) | main["district_code"].str.fullmatch(r"\d\d")).all()
    assert set(main["disease"]) <= {"dengue", "diarrhoeal", "ili", "malaria"}
    assert len(main) == len(extras) and len(art) == len(model().artefacts)
    assert list(art.columns) == M.ARTEFACT_COLS


def test_artefact_respects_21_day_rule():
    for a in model().artefacts:
        silent = (pd.Timestamp(a["silent_end"]) - pd.Timestamp(a["silent_start"])).days + 1
        assert silent <= 17
        assert (pd.Timestamp(a["backfill_date"]) - pd.Timestamp(a["silent_start"])).days <= 21


def test_dengue_follows_rain_with_4_to_8_week_lag():
    m = model()
    ext = M.LAG_PAD
    wdf = pd.read_csv(_cache["tmp"] / "weather.csv", dtype={"district_code": str})
    rain = (wdf.pivot(index="date", columns="district_code", values="precip_mm")
            .reindex(m.dist.district_code, axis=1).to_numpy())
    rain = rain[-len(m.dates) - ext:]
    nat_rain = rain.mean(axis=1)
    dengue = m.mu[:, :, 0].sum(axis=1)
    def corr(lag):
        r = pd.Series(nat_rain).shift(lag).to_numpy()[ext:]
        ok = ~np.isnan(r)
        return np.corrcoef(r[ok], dengue[ok])[0, 1]
    assert corr(42) > corr(0) + 0.1


def test_stock_simulation_constraints():
    m = model()
    s = M.simulate_stock(m)
    for k in ("iv_saline_units", "ns1_kits", "ors_sachets", "antimalarial_courses", "occupied_beds"):
        assert s[k].shape == (len(m.dates), len(m.fac)) and s[k].min() >= 0
    assert (s["occupied_beds"] <= np.ceil(1.15 * m.fac.beds.to_numpy())[None, :]).all()


def test_weather_extends_past_file_with_climatology():
    tmp = Path(tempfile.mkdtemp())
    make_reference(tmp, last=dt.date(2021, 12, 31))        # weather ends a year early
    m = M.build(CFG, tmp)
    assert np.isfinite(m.mu).all() and m.mu.min() > 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)

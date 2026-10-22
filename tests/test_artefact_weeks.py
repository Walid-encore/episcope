"""Silent-then-backfill artefacts: whole Monday-to-Sunday weeks, Monday back-fill, big facility, clear of outbreaks.

Runs on a small synthetic districts/weather pair (no real reference files needed), model.py only (no Spark).
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from generator import model as M

NAMES = ["Dhaka", "Chattogram", "Narayanganj", "Gazipur", "Khulna", "Bandarban", "Rangamati", "Khagrachhari",
         "Cox's Bazar", "Sylhet", "Moulvibazar", "Sunamganj", "Netrokona", "Sherpur", "Mymensingh", "Habiganj"]


def _make_ref(path, seed=0):
    rng = np.random.default_rng(seed)
    n = 64
    names = NAMES + [f"District{i:02d}" for i in range(len(NAMES), n)]
    dist = pd.DataFrame({
        "district_code": [f"{i + 1:02d}" for i in range(n)],
        "district_name": names,
        "division": ["Dhaka"] * n,
        "population": (rng.lognormal(np.log(2.4e6), 0.5, n)).astype(int),
        "centroid_lat": rng.uniform(21.0, 26.5, n).round(4),
        "centroid_lon": rng.uniform(88.2, 92.4, n).round(4),
    })
    days = pd.date_range("2020-11-01", "2025-12-31", freq="D")
    doy = days.dayofyear.to_numpy()
    rows = []
    for c in dist["district_code"]:
        rain = np.clip(rng.gamma(0.6, 8.0, len(days)) * (1 + np.sin((doy - 120) / 365 * 2 * np.pi)), 0, None)
        temp = 26 + 5 * np.sin((doy - 100) / 365 * 2 * np.pi) + rng.normal(0, 1, len(days))
        rows.append(pd.DataFrame({"district_code": c, "date": days.strftime("%Y-%m-%d"),
                                  "precip_mm": rain.round(2), "temp_mean_c": temp.round(2), "humidity_pct": 75.0}))
    path.mkdir(parents=True, exist_ok=True)
    dist.to_csv(path / "districts.csv", index=False)
    pd.concat(rows).to_csv(path / "weather.csv", index=False)
    return path


@pytest.fixture(scope="module")
def ref(tmp_path_factory):
    return _make_ref(tmp_path_factory.mktemp("ref"))


def _build(ref, seed):
    return M.build(M.Config(seed=seed, sf=0.05), ref)


@pytest.mark.parametrize("seed", [1, 2, 3, 42])
def test_artefacts_are_whole_monday_weeks(ref, seed):
    m = _build(ref, seed)
    main, extras, art = M.export_truth(m.outbreaks)
    assert list(art.columns) == M.ARTEFACT_COLS
    assert len(art) == len(m.artefacts) * len(M.ARTEFACT_DISEASES)
    for col in ("silent_start", "silent_end", "backfill_week_start"):
        assert (pd.to_datetime(art[col]).dt.weekday == 0).all(), col
    gap = pd.to_datetime(art["backfill_week_start"]) - pd.to_datetime(art["silent_end"])
    assert (gap == pd.Timedelta(days=7)).all()
    assert (pd.to_datetime(art["silent_end"]) >= pd.to_datetime(art["silent_start"])).all()
    assert art["district_code"].str.len().eq(2).all()          # leading zero kept as a string
    for a in m.artefacts:
        assert a["weeks"] in (1, 2, 3)
        # report_date - onset_date <= 21: the oldest silent-window record is reported on the backfill Monday
        delay = (pd.Timestamp(a["backfill_date"]) - pd.Timestamp(a["silent_start"])).days
        assert delay == 7 * a["weeks"] <= 21
        assert pd.Timestamp(a["silent_end"]).weekday() == 6      # internal silent_end is the last silent Sunday


@pytest.mark.parametrize("seed", [1, 2, 3, 42])
def test_silent_facility_is_the_largest_in_its_district(ref, seed):
    m = _build(ref, seed)
    for a in m.artefacts:
        f = m.fac[m.fac.district_code == a["district_code"]]
        assert a["facility_id"] == f.loc[f["weight"].idxmax(), "facility_id"]
    # at least one of the two reaches the 20% volume-share target (the other may use the documented fallback)
    shares = [float(m.fac.set_index("facility_id").loc[a["facility_id"], "weight"]) for a in m.artefacts]
    assert max(shares) >= M.ART_MIN_SHARE


@pytest.mark.parametrize("seed", [1, 2, 3, 42])
def test_artefacts_keep_clear_of_outbreaks(ref, seed):
    m = _build(ref, seed)
    main, _, art = M.export_truth(m.outbreaks)
    for a in m.artefacts:
        lo = pd.Timestamp(a["silent_start"])
        hi = pd.Timestamp(a["backfill_date"]) + pd.Timedelta(days=6)      # end of the back-fill week
        same = main[main["district_code"].isin([a["district_code"], "ALL"])]
        for r in same.itertuples():
            s, e = pd.Timestamp(r.start_date), pd.Timestamp(r.end_date)
            gap = max((s - hi).days, (lo - e).days)                        # days between the two windows
            assert gap >= 28, f"{r.outbreak_id} ({r.kind}) is only {gap} days from artefact {a['artefact_id']}"


def test_same_seed_same_artefacts(ref):
    a = _build(ref, 7)
    b = _build(ref, 7)
    assert a.artefacts == b.artefacts
    pd.testing.assert_frame_equal(M.export_truth(a.outbreaks)[2], M.export_truth(b.outbreaks)[2])

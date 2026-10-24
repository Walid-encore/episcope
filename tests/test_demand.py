"""Tests for analytics/optimise/demand.py with hand-calculated numbers."""
import pandas as pd
import pytest

from analytics.optimise import demand as _demand_mod
from analytics.optimise.demand import (ASSUMPTIONS, admission_rates, assumption_sensitivity,
                                       assumptions_markdown, compute_demand, planning_totals)


def _fc(rows):
    base = dict(model="nb_lagged_weather", is_backtest=False, origin_epi_year=2025, origin_epi_week=40,
                target_epi_year=2025, target_epi_week=41, target_week_start_date="2025-10-06")
    return pd.DataFrame([{**base, **r} for r in rows])


def _rates(rate_dengue=0.2, rate_diar=0.1, code="01"):
    return pd.DataFrame([dict(district_code=code, disease="dengue", admission_rate=rate_dengue),
                         dict(district_code=code, disease="diarrhoeal", admission_rate=rate_diar)])


@pytest.fixture(autouse=True)
def _relax_horizon_guard(request, monkeypatch):
    """The by-hand fixtures use 1-2 horizons on purpose; only the guard test keeps the real check."""
    if request.node.name == "test_guard_rejects_short_horizons":
        return
    real = _demand_mod._require_full_horizons
    monkeypatch.setattr(_demand_mod, "_require_full_horizons", lambda f, max_horizon=4: real(f, 1))


def test_dengue_conversion_by_hand():
    fc = _fc([dict(district_code="01", disease="dengue", horizon_weeks=1, pred_mean=100.0, q90=150.0)])
    d = compute_demand(fc, _rates()).iloc[0]
    assert d.beds_needed == pytest.approx(100 * 0.2 * 5 / 7)          # 20 admissions x 5 days / 7
    assert d.iv_saline_units_needed == pytest.approx(100 * 1.12)      # saline is per case
    assert d.ns1_kits_needed == pytest.approx(100 * 0.6)
    assert d.ors_sachets_needed == 0
    assert d.iv_saline_units_needed_q90 == pytest.approx(150 * 1.12)


def test_diarrhoeal_conversion_by_hand():
    fc = _fc([dict(district_code="01", disease="diarrhoeal", horizon_weeks=1, pred_mean=200.0, q90=260.0)])
    d = compute_demand(fc, _rates()).iloc[0]
    assert d.ors_sachets_needed == pytest.approx(200 * 4)
    assert d.iv_saline_units_needed == pytest.approx(200 * 0.36)
    assert d.ns1_kits_needed == 0
    assert d.beds_needed == pytest.approx(200 * 0.1 * 5 / 7)


def test_malaria_and_ili_need_beds_and_saline_only():
    fc = _fc([dict(district_code="01", disease=ds, horizon_weeks=1, pred_mean=70.0, q90=70.0)
              for ds in ("malaria", "ili")])
    r = pd.DataFrame([dict(district_code="01", disease="malaria", admission_rate=0.2),
                      dict(district_code="01", disease="ili", admission_rate=0.1)])
    d = compute_demand(fc, r).set_index("disease")
    assert d.loc["malaria", "beds_needed"] == pytest.approx(70 * 0.2 * 5 / 7)
    assert d.loc["malaria", "iv_saline_units_needed"] == pytest.approx(70 * 0.25)
    assert d.loc["ili", "iv_saline_units_needed"] == pytest.approx(70 * 0.06)
    for res in ("ns1_kits_needed", "ors_sachets_needed"):
        assert (d[res] == 0).all()
    assert d[[c for c in d.columns if c.endswith("_needed")]].notna().all().all()


def test_unknown_disease_is_rejected_not_dropped():
    fc = _fc([dict(district_code="01", disease="cholera", horizon_weeks=1, pred_mean=10.0, q90=12.0)])
    with pytest.raises(ValueError, match="cholera"):
        compute_demand(fc, _rates())


def test_assumption_tables_cover_every_disease():
    ds = set(ASSUMPTIONS["los_days"])
    assert ds == {"dengue", "diarrhoeal", "malaria", "ili"}
    for k in ("saline_units_per_case", "ns1_tests_per_case", "ors_sachets_per_case", "fallback_admission_rate"):
        assert set(ASSUMPTIONS[k]) == ds


def test_q90_never_below_central():
    fc = _fc([dict(district_code="01", disease=ds, horizon_weeks=h, pred_mean=50.0 * h, q90=80.0 * h)
              for ds in ("dengue", "diarrhoeal") for h in (1, 2)])
    d = compute_demand(fc, _rates())
    for r in ("beds", "iv_saline_units", "ns1_kits", "ors_sachets"):
        assert (d[f"{r}_needed_q90"] >= d[f"{r}_needed"]).all()


def test_lower_quantile_is_added_when_forecast_has_q10():
    fc = _fc([dict(district_code="01", disease="dengue", horizon_weeks=1, pred_mean=100.0, q10=60.0, q90=150.0)])
    d = compute_demand(fc, _rates()).iloc[0]
    assert d.iv_saline_units_needed_q10 == pytest.approx(60 * 1.12)
    assert d.iv_saline_units_needed_q10 <= d.iv_saline_units_needed <= d.iv_saline_units_needed_q90
    assert "iv_saline_units_needed_q10" not in compute_demand(
        _fc([dict(district_code="01", disease="dengue", horizon_weeks=1, pred_mean=1.0, q90=2.0)]), _rates()).columns


def test_only_latest_origin_and_live_rows_are_used():
    rows = [dict(district_code="01", disease="dengue", horizon_weeks=1, pred_mean=100.0, q90=150.0),
            dict(district_code="01", disease="dengue", horizon_weeks=1, pred_mean=999.0, q90=999.0,
                 origin_epi_week=30),                                   # older origin
            dict(district_code="01", disease="dengue", horizon_weeks=1, pred_mean=888.0, q90=888.0,
                 is_backtest=True),                                     # backtest row
            dict(district_code="01", disease="dengue", horizon_weeks=1, pred_mean=777.0, q90=777.0,
                 model="seasonal_naive")]                               # other model
    d = compute_demand(_fc(rows), _rates())
    assert len(d) == 1 and d.iv_saline_units_needed.iloc[0] == pytest.approx(100 * 1.12)


def test_planning_totals_beds_peak_consumables_sum():
    fc = _fc([dict(district_code="01", disease="dengue", horizon_weeks=h, pred_mean=70.0, q90=70.0)
              for h in (1, 2)] +
             [dict(district_code="01", disease="diarrhoeal", horizon_weeks=1, pred_mean=140.0, q90=140.0)])
    t = planning_totals(compute_demand(fc, _rates())).set_index("resource")
    beds_w1 = 70 * 0.2 * 5 / 7 + 140 * 0.1 * 5 / 7                       # both diseases in week 1
    beds_w2 = 70 * 0.2 * 5 / 7
    assert t.loc["beds", "demand"] == pytest.approx(max(beds_w1, beds_w2))
    assert t.loc["iv_saline_units", "demand"] == pytest.approx(2 * 70 * 1.12 + 140 * 0.36)
    assert t.loc["ors_sachets", "demand"] == pytest.approx(140 * 4)
    assert set(t.index) == {"beds", "iv_saline_units", "ns1_kits", "ors_sachets"}


def test_admission_rates_shrink_toward_national():
    cases = pd.DataFrame([
        dict(district_code="01", disease="dengue", cases=1000, admitted_cases=300),
        dict(district_code="02", disease="dengue", cases=1000, admitted_cases=100),
        dict(district_code="03", disease="dengue", cases=0, admitted_cases=0)])
    r = admission_rates(cases, shrink_k=50).set_index("district_code")["admission_rate"]
    national = 400 / 2000
    assert r["03"] == pytest.approx(national)                            # no history -> national rate
    assert national < r["01"] < 0.3 and 0.1 < r["02"] < national          # pulled toward national, not past it


def test_missing_rate_uses_fallback():
    fc = _fc([dict(district_code="09", disease="dengue", horizon_weeks=1, pred_mean=100.0, q90=100.0)])
    d = compute_demand(fc, _rates(code="01"))
    assert d.beds_needed.iloc[0] == pytest.approx(100 * 0.28 * 5 / 7)


def test_sensitivity_and_markdown():
    fc = _fc([dict(district_code="01", disease=ds, horizon_weeks=1, pred_mean=100.0, q90=120.0)
              for ds in ("dengue", "diarrhoeal")])
    s = assumption_sensitivity(fc, _rates())
    row = s[(s.assumption == "ors_sachets_per_case") & (s.factor == 1.25) & (s.resource == "ors_sachets")].iloc[0]
    assert row.change_pct == pytest.approx(25.0) and row.change_pct_q90 == pytest.approx(25.0)
    assert "ns1_wastage" not in set(s.assumption)
    md = assumptions_markdown()
    assert "generator" in md and "Malaria" in md and "ILI" in md and str(ASSUMPTIONS["los_days"]["dengue"]) in md


def test_guard_rejects_short_horizons():
    fc = _fc([dict(district_code="01", disease="dengue", horizon_weeks=h, pred_mean=10.0, q90=12.0)
              for h in (1, 2)])
    with pytest.raises(ValueError, match="lack all 4 live horizons"):
        compute_demand(fc, _rates())


def test_q90_is_floored_at_the_mean():
    fc = _fc([dict(district_code="01", disease="dengue", horizon_weeks=1, pred_mean=2.4, q90=0.0)])
    d = compute_demand(fc, _rates()).iloc[0]
    assert d.iv_saline_units_needed_q90 == pytest.approx(2.4 * 1.12)


def test_origin_selects_a_backtest_origin():
    rows = [dict(district_code="01", disease="dengue", horizon_weeks=1, pred_mean=100.0, q90=150.0),
            dict(district_code="01", disease="dengue", horizon_weeks=1, pred_mean=50.0, q90=70.0,
                 origin_epi_week=30, is_backtest=True)]
    d = compute_demand(_fc(rows), _rates(), origin=202530)
    assert len(d) == 1 and d.iv_saline_units_needed.iloc[0] == pytest.approx(50 * 1.12)
    with pytest.raises(ValueError, match="origin"):
        compute_demand(_fc(rows), _rates(), origin=209999)


def test_implied_rates_round_trip():
    from analytics.optimise.demand import implied_admission_rates
    fc = _fc([dict(district_code="01", disease=ds, horizon_weeks=1, pred_mean=40.0, q90=60.0)
              for ds in ("dengue", "diarrhoeal")])
    got = implied_admission_rates(fc, compute_demand(fc, _rates(0.2, 0.1))).set_index("disease")["admission_rate"]
    assert got["dengue"] == pytest.approx(0.2) and got["diarrhoeal"] == pytest.approx(0.1)

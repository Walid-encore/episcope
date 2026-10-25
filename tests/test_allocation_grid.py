"""Tests for the what-if grid, transport caps, as-of stock and the coverage floor (run_allocation / allocate)."""
import json

import pandas as pd
import pytest

from analytics.optimise.allocate import proportional_baseline, solve_allocation
from analytics.optimise.run_allocation import (ALPHAS, BUDGET_FRACS, UNIT_COSTS, VOLUME, allocation_grid,
                                               division_caps, latest_stock_by_district, resolve_as_of,
                                               transport_report)


def _inp():
    rows = []
    for d, div in (("01", "A"), ("02", "A"), ("03", "B")):
        for r, dem, st in (("iv_saline_units", 100.0, 20.0), ("ns1_kits", 40.0, 10.0), ("ors_sachets", 300.0, 50.0)):
            rows.append(dict(district_code=d, division=div, resource=r, demand=dem * (1 + int(d)), stock=st))
    return pd.DataFrame(rows)


POP = pd.Series({"01": 100.0, "02": 300.0, "03": 600.0})


def _budget(inp, frac):
    return frac * float(((inp.demand - inp.stock).clip(lower=0) * inp.resource.map(UNIT_COSTS)).sum())


def test_grid_has_one_row_per_policy_budget_alpha_district_resource():
    g = allocation_grid(_inp(), POP, None)
    assert list(g.columns) == ["budget_frac", "alpha", "policy", "district_code", "resource", "x", "unmet"]
    n_cells = 3 * 3
    assert len(g) == len(BUDGET_FRACS) * (len(ALPHAS) + 1) * n_cells
    assert set(g.policy) == {"optimised", "proportional"}
    assert g.loc[g.policy == "proportional", "alpha"].isna().all()
    assert (g[["x", "unmet"]] >= 0).all().all()


def test_grid_rows_respect_the_budget():
    inp, g = _inp(), None
    g = allocation_grid(inp, POP, None)
    for (policy, f, a), part in g.groupby(["policy", "budget_frac", "alpha"], dropna=False):
        spent = (part["x"] * part["resource"].map(UNIT_COSTS)).sum()
        assert spent <= _budget(inp, f) + 0.05, (policy, f, a)


def test_coverage_floor_is_met_for_every_district():
    inp = _inp()
    plan = solve_allocation(inp, POP, UNIT_COSTS, _budget(inp, 0.6), 0.0, min_cover=0.2)
    need = (plan.demand - plan.stock).clip(lower=0)
    assert (plan.x >= 0.2 * need - 1e-6).all()


def test_equity_floor_gives_each_district_its_population_share():
    inp = _inp()
    plan = solve_allocation(inp, POP, UNIT_COSTS, _budget(inp, 0.6), 0.5)
    for r, part in plan.groupby("resource"):
        share = POP / POP.sum()
        assert (part.set_index("district_code").x >= 0.5 * share * part.x.sum() - 1e-6).all()


def test_transport_cap_is_respected_and_reported():
    inp = _inp()
    caps = division_caps(inp, 0.1)
    plan = solve_allocation(inp, POP, UNIT_COSTS, _budget(inp, 1.0), 0.0, vol=VOLUME, caps=caps)
    used = (plan.x * plan.resource.map(VOLUME)).groupby(plan.division).sum()
    for k, cap in caps.items():
        assert used[k] <= cap + 1e-6
    rep = transport_report(plan, VOLUME, caps)
    assert rep["max_utilisation"] <= 1.0 + 1e-6 and rep["binding_divisions"]
    assert transport_report(plan, None, None) is None


def test_division_caps_scale_with_own_need_volume():
    inp = _inp()
    c1, c2 = division_caps(inp, 0.2), division_caps(inp, 0.4)
    assert set(c1) == {"A", "B"} and all(c2[k] == pytest.approx(2 * c1[k]) for k in c1)


def _write_stock(tmp_path):
    rows = [dict(facility_id=f, snapshot_date=d, iv_saline_units=v, ns1_kits=v, ors_sachets=v)
            for f in ("F1", "F2") for d, v in (("2025-01-01", 10), ("2025-02-01", 20), ("2025-03-01", 30))]
    (tmp_path / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return pd.DataFrame(dict(facility_id=["F1", "F2"], district_code=["01", "01"]))


def test_as_of_ignores_later_snapshots(tmp_path):
    fac = _write_stock(tmp_path)
    latest, _ = latest_stock_by_district(tmp_path, fac)
    early, _ = latest_stock_by_district(tmp_path, fac, as_of="2025-02-15")
    assert latest.set_index("resource").stock["ors_sachets"] == 60
    assert early.set_index("resource").stock["ors_sachets"] == 40
    with pytest.raises(SystemExit):
        latest_stock_by_district(tmp_path, fac, as_of="2024-01-01")


def test_resolve_as_of_reads_json_or_passes_dates_through(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"as_of": "2025-07-06", "origin": 202527}), encoding="utf-8")
    assert resolve_as_of(str(p)) == "2025-07-06"
    assert resolve_as_of("2025-01-01") == "2025-01-01" and resolve_as_of(None) is None


def test_baseline_is_not_better_than_optimised_on_weighted_unmet():
    inp = _inp()
    B = _budget(inp, 0.5)
    opt = solve_allocation(inp, POP, UNIT_COSTS, B, 0.0)
    base = proportional_baseline(inp, POP, UNIT_COSTS, B)
    cost = lambda df: float((df.unmet * df.resource.map(UNIT_COSTS)).sum())
    assert cost(opt) <= cost(base) + 1e-6

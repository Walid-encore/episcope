"""ENG-4: optimiser constraint tests for the parts test_allocate.py does not cover:
coverage floor (and its half-budget cap), severity weights, and the population-proportional baseline."""
import pandas as pd
import pytest

from analytics.optimise.allocate import solve_allocation, proportional_baseline

COSTS = {"saline": 2.0, "ns1": 5.0}
POP = pd.Series({1: 1000, 2: 3000, 3: 6000})


def _demand():
    rows = []
    for d, div, dem in [(1, "A", 400), (2, "A", 100), (3, "B", 50)]:
        for r, m in [("saline", 1.0), ("ns1", 0.5)]:
            rows.append({"district_code": d, "division": div, "resource": r,
                         "demand": dem * m, "stock": 10.0})
    return pd.DataFrame(rows)


def _need_cost(d):
    return float(((d["demand"] - d["stock"]).clip(lower=0) * d["resource"].map(COSTS)).sum())


def test_coverage_floor_holds_for_every_district_and_resource():
    d = _demand()
    B = 0.5 * _need_cost(d)
    res = solve_allocation(d, POP, COSTS, B, 0.0, min_cover=0.2)
    need = (res["demand"] - res["stock"]).clip(lower=0)
    assert (res["x"] >= 0.2 * need - 1e-6).all()
    assert res.attrs["min_cover_used"] == pytest.approx(0.2)


def test_coverage_floor_is_capped_at_half_the_budget():
    d = _demand()
    nc = _need_cost(d)
    B = 0.5 * nc
    res = solve_allocation(d, POP, COSTS, B, 0.0, min_cover=0.9)
    assert res.attrs["min_cover_used"] == pytest.approx(0.5 * B / nc)    # 0.25 at a 50% budget
    assert (res["x"] * res["resource"].map(COSTS)).sum() <= B + 1e-6


def test_severity_weights_send_scarce_stock_to_the_heavier_district():
    d = _demand()
    B = 0.1 * _need_cost(d)                       # far below total need, above district 3's need (155)
    w = pd.Series({1: 1.0, 2: 1.0, 3: 5.0})
    res = solve_allocation(d, POP, COSTS, B, 0.0, weights=w)
    assert res[res["district_code"] == 3]["unmet"].sum() == pytest.approx(0.0, abs=1e-6)


def test_proportional_baseline_spends_the_budget_and_splits_by_population():
    d = _demand()
    B = 300.0
    base = proportional_baseline(d, POP, COSTS, B)
    assert (base["x"] * base["resource"].map(COSTS)).sum() == pytest.approx(B)
    p = POP / POP.sum()
    for _, g in base.groupby("resource"):
        for _, row in g.iterrows():
            assert row["x"] / g["x"].sum() == pytest.approx(p[row["district_code"]])

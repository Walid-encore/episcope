import numpy as np
import pandas as pd
import pytest

from analytics.optimise.allocate import (solve_allocation, proportional_baseline, metrics,
                                         efficiency_equity_curve, recommend_alpha)

COSTS = {"saline": 2.0, "ns1": 5.0}
POP = pd.Series({1: 1000, 2: 3000, 3: 6000})


def _demand():
    rows = []
    for d, div, dem in [(1, "A", 400), (2, "A", 100), (3, "B", 50)]:
        for r, m in [("saline", 1.0), ("ns1", 0.5)]:
            rows.append({"district_code": d, "division": div, "resource": r,
                         "demand": dem * m, "stock": 10.0})
    return pd.DataFrame(rows)


def test_budget_respected():
    res = solve_allocation(_demand(), POP, COSTS, budget=500, alpha=0.0)
    assert (res["x"] * res["resource"].map(COSTS)).sum() <= 500 + 1e-6


def test_equity_floor_holds():
    res = solve_allocation(_demand(), POP, COSTS, budget=500, alpha=0.5)
    p = POP / POP.sum()
    for r, g in res.groupby("resource"):
        tot = g["x"].sum()
        for _, row in g.iterrows():
            assert row["x"] >= 0.5 * p[row["district_code"]] * tot - 1e-6


def test_transport_cap_holds():
    vol = {"saline": 1.0, "ns1": 1.0}
    res = solve_allocation(_demand(), POP, COSTS, budget=10_000, alpha=0.0,
                           vol=vol, caps={"A": 100.0, "B": 1000.0})
    assert res[res["division"] == "A"]["x"].sum() <= 100.0 + 1e-6


def test_optimiser_beats_population_baseline():
    d, B = _demand(), 400
    opt = metrics(solve_allocation(d, POP, COSTS, B, 0.0), COSTS)
    base = metrics(proportional_baseline(d, POP, COSTS, B), COSTS)
    assert opt["weighted_unmet"] <= base["weighted_unmet"] + 1e-6


def test_unmet_falls_as_budget_rises_and_full_budget_covers_need():
    curve = efficiency_equity_curve(_demand(), POP, COSTS, alphas=(0.0,))
    o = curve[curve["policy"] == "optimised"].sort_values("budget_frac")
    assert o["weighted_unmet"].is_monotonic_decreasing
    assert o.iloc[-1]["weighted_unmet"] == pytest.approx(0.0, abs=1e-6)


def test_higher_alpha_never_improves_efficiency_and_recommendation_in_range():
    curve = efficiency_equity_curve(_demand(), POP, COSTS)
    low = curve[(curve["policy"] == "optimised") & (curve["budget_frac"] == 0.4)]
    vals = low.sort_values("alpha")["weighted_unmet"].to_numpy()
    assert (np.diff(vals) >= -1e-6).all()
    assert 0.0 <= recommend_alpha(curve) < 1.0


def test_missing_columns_raise():
    with pytest.raises(ValueError):
        solve_allocation(_demand().drop(columns=["stock"]), POP, COSTS, 100)

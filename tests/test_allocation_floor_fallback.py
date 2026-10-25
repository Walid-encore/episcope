"""Coverage floor + equity floor can be infeasible together; solve_allocation must degrade, not crash."""
import pandas as pd
import pytest

from analytics.optimise.allocate import solve_allocation


def _case():
    codes = [f"{i:02d}" for i in range(10)]
    df = pd.DataFrame({"district_code": codes, "division": "A", "resource": "r",
                       "demand": [1000.0] + [1.0] * 9, "stock": 0.0})
    pop = pd.Series(1.0, index=codes)
    return df, pop


def test_infeasible_floor_degrades_instead_of_crashing():
    df, pop = _case()
    res = solve_allocation(df, pop, {"r": 1.0}, budget=101.0, alpha=0.8, min_cover=0.25)
    used = res.attrs["min_cover_used"]
    assert 0 < used < 0.05                      # capped at 0.05 by the budget, then halved to fit
    assert (res["x"] >= used * res["demand"] - 1e-6).all()


def test_floor_is_kept_when_feasible():
    df, pop = _case()
    res = solve_allocation(df, pop, {"r": 1.0}, budget=1009.0, alpha=0.0, min_cover=0.25)
    assert res.attrs["min_cover_used"] == pytest.approx(0.25)

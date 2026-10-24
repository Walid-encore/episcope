import numpy as np
import pandas as pd
import pytest

from analytics.optimise.allocate import solve_allocation, proportional_baseline, metrics
from analytics.optimise.allocation_sensitivity import (division_capacities, division_load, need_volume,
                                                       transport_analysis, cost_sensitivity,
                                                       weight_sensitivity, assumptions_markdown)

COSTS = {"saline": 2.0, "ns1": 5.0}
VOL = {"saline": 1.0, "ns1": 0.2}
POP = pd.Series({1: 1000, 2: 3000, 3: 6000})
W = pd.Series({1: 1.5, 2: 1.0, 3: 0.6})


def _inp():
    rows = []
    for d, div, dem in [(1, "A", 400), (2, "A", 300), (3, "B", 250)]:
        for r, m in [("saline", 1.0), ("ns1", 0.5)]:
            rows.append({"district_code": d, "division": div, "resource": r, "demand": dem * m, "stock": 10.0})
    return pd.DataFrame(rows)


def _budget(inp, frac=0.5):
    need = (inp["demand"] - inp["stock"]).clip(lower=0) * inp["resource"].map(COSTS)
    return frac * need.sum()


def test_capacities_sum_to_kappa_times_need_volume():
    inp = _inp()
    caps = division_capacities(inp, POP, VOL, 0.5)
    assert set(caps) == {"A", "B"}
    assert sum(caps.values()) == pytest.approx(0.5 * need_volume(inp, VOL))
    assert caps["B"] / caps["A"] == pytest.approx(6000 / 4000)


def test_transport_constraint_binds_when_tight_and_not_when_loose():
    inp = _inp()
    tr, free = transport_analysis(inp, POP, COSTS, VOL, W, _budget(inp), 0.0, kappas=(100.0, 0.1))
    loose, tight = tr.iloc[0], tr.iloc[1]
    assert loose["n_binding_divisions"] == 0
    assert loose["unmet_vs_unconstrained_pct"] == pytest.approx(0.0, abs=1e-6)
    assert tight["n_binding_divisions"] >= 1
    assert tight["weighted_unmet"] >= free - 1e-6          # a constraint can never help


def test_transport_load_respects_capacity():
    inp = _inp()
    caps = division_capacities(inp, POP, VOL, 0.1)
    plan = solve_allocation(inp, POP, COSTS, _budget(inp), 0.0, weights=W, vol=VOL, caps=caps)
    load = division_load(plan, VOL)
    for k, c in caps.items():
        assert load[k] <= c + 1e-6


def test_scaling_all_costs_and_budget_together_scales_cost_weighted_unmet_only():
    # allocate.metrics reports COST-weighted unmet, so doubling costs doubles it; the plan itself is unchanged
    inp, B = _inp(), _budget(_inp())
    a = metrics(solve_allocation(inp, POP, COSTS, B, 0.0, weights=W), COSTS, W)["weighted_unmet"]
    c2 = {k: 2 * v for k, v in COSTS.items()}
    b = metrics(solve_allocation(inp, POP, c2, 2 * B, 0.0, weights=W), c2, W)["weighted_unmet"]
    assert 2 * a == pytest.approx(b, abs=1e-4)


def test_cost_sensitivity_shape_and_optimiser_never_worse_than_baseline():
    inp = _inp()
    cs = cost_sensitivity(inp, POP, COSTS, W, 0.5, 0.0)
    assert len(cs) == 1 + len(COSTS) * 2 * 2             # base + 2 resources x 2 factors x 2 budget rules
    assert (cs["optimised_unmet"] <= cs["baseline_unmet"] + 1e-6).all()


def test_weight_sensitivity_equal_weight_plan_is_no_better_under_severity():
    inp = _inp()
    ws = weight_sensitivity(inp, POP, COSTS, W, _budget(inp), 0.0).set_index(["optimised_with", "evaluated_with"])
    sev = ws.loc[("severity", "severity"), "optimised_unmet"]
    eq_plan_sev = ws.loc[("equal", "severity"), "optimised_unmet"]
    assert sev <= eq_plan_sev + 1e-6                     # optimising for the weights you evaluate on is best
    assert weight_sensitivity(inp, POP, COSTS, None, 10.0, 0.0).empty


def test_doc_lists_every_cost_and_volume():
    inp = _inp()
    tr, free = transport_analysis(inp, POP, COSTS, VOL, W, _budget(inp), 0.0, kappas=(1.0,))
    cs = cost_sensitivity(inp, POP, COSTS, W, 0.5, 0.0)
    md = assumptions_markdown(COSTS, dict(budget=_budget(inp), budget_frac=0.5, min_cover=0.0,
                                          v_need=need_volume(inp, VOL), free_unmet=free), tr, cs, pd.DataFrame(), vol=VOL)
    for r in COSTS:
        assert r in md

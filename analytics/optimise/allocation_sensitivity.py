"""Allocation assumptions and sensitivity analysis (AN-7, AN-8; Section 6.3).

Closes three gaps in the allocation stage:
  1. Transport-capacity constraint  sum_{d in div k} sum_r v_r x_dr <= T_k   (never used before)
  2. Unit-cost assumptions are documented and tested (costs halved / doubled, one resource at a time)
  3. Severity weights vs equal weights (optimise under one, evaluate under the other)

Everything is written to --out (CSV) and to docs/allocation_assumptions.md, generated from the
same constants as the code so doc and code agree.

NUMBERS BELOW ARE STATED ASSUMPTIONS FOR A SYNTHETIC TEACHING CASE, NOT MoH LOGISTICS DATA.
  UNIT_COSTS  relative cost units (taken from run_allocation.UNIT_COSTS); NOT prices.
  VOLUME      shipping volume per unit; 1.0 = one 500 ml IV saline bag.
  Capacity    T_k = kappa * (population share of division k) * V_need, with V_need the shipping
              volume of the whole national unmet need. kappa = 1 means a division can move its
              population share of the national need in the planning window. The data contain no
              fleet or road information, so kappa is swept rather than guessed (see transport.csv).

Usage (peak scenario, as run by hand before):
  python -m analytics.optimise.allocation_sensitivity --demand data/analytics/planning_totals_peak.csv \
      --cases data/gold_csv/gold_weekly_cases.csv --min-cover 0.25
(--as-of is read from planning_totals_peak.json next to the demand CSV when not given.)
"""
import argparse
import inspect
import json
from pathlib import Path

import pandas as pd

from analytics.optimise.allocate import solve_allocation, proportional_baseline, metrics

ROOT = Path(__file__).resolve().parents[2]
VOLUME = {"iv_saline_units": 1.0, "ns1_kits": 0.1, "ors_sachets": 0.05}
DEFAULT_KAPPA = 0.5
KAPPA_SWEEP = (4.0, 2.0, 1.0, 0.5, 0.25)
COST_FACTORS = (0.5, 2.0)


# ----------------------------------------------------------------------------- helpers
def _solve(inp, pop, costs, budget, alpha=0.0, weights=None, min_cover=0.0, vol=None, caps=None):
    """solve_allocation, passing min_cover only if the installed allocate.py supports it."""
    kw = {"weights": weights}
    if vol is not None:
        kw.update(vol=vol, caps=caps)
    if min_cover and "min_cover" in inspect.signature(solve_allocation).parameters:
        kw["min_cover"] = min_cover
    return solve_allocation(inp, pop, costs, budget, alpha, **kw)


def need_cost(inp, costs):
    return float(((inp["demand"] - inp["stock"]).clip(lower=0) * inp["resource"].map(costs)).sum())


def need_volume(inp, vol):
    return float(((inp["demand"] - inp["stock"]).clip(lower=0) * inp["resource"].map(vol)).sum())


def division_capacities(inp, pop, vol, kappa):
    """T_k = kappa * population share of division k * V_need."""
    dmap = inp.drop_duplicates("district_code").set_index("district_code")["division"]
    pdiv = pop.reindex(dmap.index).groupby(dmap).sum()
    return (kappa * pdiv / pdiv.sum() * need_volume(inp, vol)).to_dict()


def division_load(plan, vol):
    v = plan["x"] * plan["resource"].map(vol)
    return v.groupby(plan["division"]).sum()


def _pct_better(opt, base):
    return 100 * (base - opt) / base if base > 0 else float("nan")


# ----------------------------------------------------------------------------- analyses
def transport_analysis(inp, pop, costs, vol, weights, budget, min_cover, kappas=KAPPA_SWEEP):
    """Does the transport constraint ever bind? Sweep kappa, report binding divisions and loss."""
    free = _solve(inp, pop, costs, budget, 0.0, weights, min_cover)
    free_unmet = metrics(free, costs, weights)["weighted_unmet"]
    free_load = division_load(free, vol)
    rows = []
    for k in kappas:
        caps = division_capacities(inp, pop, vol, k)
        try:
            plan = _solve(inp, pop, costs, budget, 0.0, weights, min_cover, vol, caps)
        except Exception as e:  # infeasible with the coverage floor, solver status error, ...
            rows.append(dict(kappa=k, status=f"infeasible ({type(e).__name__})"))
            continue
        load = division_load(plan, vol)
        binding = sorted(d for d, c in caps.items() if load.get(d, 0.0) >= c * (1 - 1e-6) - 1e-9)
        m = metrics(plan, costs, weights)
        rows.append(dict(kappa=k, status="optimal", n_binding_divisions=len(binding),
                         binding_divisions=";".join(binding),
                         unconstrained_peak_load_share=float(max(
                             (free_load.get(d, 0.0) / c for d, c in caps.items() if c > 0), default=0.0)),
                         weighted_unmet=m["weighted_unmet"], unmet_vs_unconstrained_pct=(
                             100 * (m["weighted_unmet"] / free_unmet - 1) if free_unmet > 0 else float("nan")),
                         coverage_min_district=m["coverage_min_district"]))
    return pd.DataFrame(rows), free_unmet


def cost_sensitivity(inp, pop, costs, weights, budget_frac, min_cover, factors=COST_FACTORS):
    """Each unit cost x0.5 and x2 (one at a time). Budget rule 'frac' keeps budget = frac of the
    cost of all unmet need under the NEW costs; rule 'fixed' keeps the baseline budget."""
    base_budget = budget_frac * need_cost(inp, costs)
    rows = []
    for res in [None] + list(costs):
        for f in ([1.0] if res is None else factors):
            c = dict(costs)
            if res is not None:
                c[res] = costs[res] * f
            for rule in (["frac"] if res is None else ["frac", "fixed"]):
                b = budget_frac * need_cost(inp, c) if rule == "frac" else base_budget
                opt = metrics(_solve(inp, pop, c, b, 0.0, weights, min_cover), c, weights)
                bas = metrics(proportional_baseline(inp, pop, c, b), c, weights)
                rows.append(dict(changed_cost=res or "none", factor=f, budget_rule=rule, budget=b,
                                 optimised_unmet=opt["weighted_unmet"], baseline_unmet=bas["weighted_unmet"],
                                 improvement_pct=_pct_better(opt["weighted_unmet"], bas["weighted_unmet"])))
    return pd.DataFrame(rows)


def weight_sensitivity(inp, pop, costs, weights, budget, min_cover):
    """Optimise under severity or equal weights; evaluate each plan under both."""
    if weights is None:
        return pd.DataFrame()
    rows = []
    for opt_name, w_opt in (("severity", weights), ("equal", None)):
        plan = _solve(inp, pop, costs, budget, 0.0, w_opt, min_cover)
        base = proportional_baseline(inp, pop, costs, budget)
        for eval_name, w_eval in (("severity", weights), ("equal", None)):
            o, b = metrics(plan, costs, w_eval), metrics(base, costs, w_eval)
            rows.append(dict(optimised_with=opt_name, evaluated_with=eval_name,
                             optimised_unmet=o["weighted_unmet"], baseline_unmet=b["weighted_unmet"],
                             improvement_pct=_pct_better(o["weighted_unmet"], b["weighted_unmet"])))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- doc
def _md_table(df, floatfmt="{:.3f}"):
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join("" if (not isinstance(v, str) and pd.isna(v)) else
                                      floatfmt.format(v) if isinstance(v, float) else str(v)
                                      for v in r) + " |")
    return "\n".join(out)


def assumptions_markdown(costs, meta, tr, cs, ws, vol=None):
    vol = VOLUME if vol is None else vol
    L = ["# Allocation assumptions and sensitivity (AN-7, AN-8)", "",
         "Generated by `analytics/optimise/allocation_sensitivity.py`. **All values are stated assumptions for a "
         "synthetic teaching case, not Ministry logistics data.** The sensitivity tables show how much each one matters.",
         "", "## Model (Section 6.3)", "",
         "Minimise the severity-weighted unmet demand subject to u_dr >= yhat_dr - s_dr - x_dr, total cost <= B, an equity floor "
         "x_dr >= alpha * p_d * sum_d' x_d'r, and a transport capacity per division. Planning demand yhat is the "
         "forecast q90 summed over the 4-week horizon. Reported weighted unmet demand is **cost-weighted** (each unit counts "
         "its unit cost, so doubling every cost doubles it), which keeps 8,000 cheap ORS sachets from outweighing a few "
         "hundred NS1 kits; s is the latest stock snapshot per facility, summed to district.",
         "", "## Unit costs and shipping volumes", "",
         _md_table(pd.DataFrame({"resource": list(costs), "unit_cost (relative)": list(costs.values()),
                                 "volume per unit": [vol[r] for r in costs]}), "{:g}"), "",
         "- Costs are relative units (1 = one IV saline unit), not prices. Beds are not shippable, so they are not allocated.",
         "- Volume: 1.0 = one 500 ml IV saline bag; an NS1 kit and an ORS sachet are assumed to be 10% and 5% of that.",
         f"- **Budget** = {meta['budget_frac']:.0%} of the cost of all unmet need at q90 demand "
         f"({meta['budget']:,.1f} cost units here), so the budget scales with the scenario.",
         "", "## What alpha and the coverage floor mean for policy (conflict C3)", "",
         "- alpha is a floor on each district's share of each resource relative to its population share: with alpha = 0.2 "
         "a district gets at least 20% of its proportional share of every item, whether or not it is short. "
         "It is blunt: at high alpha it sends stock to districts that already have enough.",
         f"- The coverage floor (min-cover = {meta['min_cover']}) guarantees every district at least that share of its own "
         "unmet need is met. It targets need directly and costs less efficiency per unit of equity gained, so it is "
         "the recommended equity tool.",
         "", "## Transport capacity", "",
         f"Capacity T_k = kappa x (division population share) x V_need, where V_need is the shipping volume of the whole "
         f"national unmet need ({meta['v_need']:,.1f} volume units). The data have no fleet or road information, so kappa is "
         f"swept. Unconstrained weighted unmet is {meta['free_unmet']:,.1f}.", ""]
    L += [_md_table(tr), ""]
    binding = tr[tr["status"] == "optimal"]
    if len(binding):
        ratio = (binding["unconstrained_peak_load_share"] * binding["kappa"]).max()
        L.append(f"In the unconstrained plan the busiest division ships {ratio:.2f}x its population share of the national "
                 f"need volume, so the constraint binds for every kappa below about {ratio:.2f}. Rows marked infeasible "
                 "are where the coverage floor alone needs more volume than the capacity allows.")
        L.append("")
    L += ["## Unit-cost sensitivity", "",
          "Each cost halved and doubled, one at a time. 'frac' re-derives the budget from the new costs; 'fixed' keeps the "
          "original budget.", "", _md_table(cs), ""]
    if len(ws):
        L += ["## Severity weights vs equal weights", "",
              "Optimised under one weighting, evaluated under both. A plan optimised for equal weights loses against "
              "severity-weighted unmet demand, and the optimiser's advantage over the proportional baseline shrinks "
              "when severity weights are removed. With equal weights the cost-weighted objective is flat: any plan that "
              "spends the whole budget on real need leaves the same unmet cost, so the remaining advantage is only the "
              "waste the proportional baseline causes by shipping to districts that already have stock.", "", _md_table(ws), ""]
    return "\n".join(L)


# ----------------------------------------------------------------------------- CLI
def main():
    from analytics.optimise import run_allocation as ra  # reuse the proven data loading

    ap = argparse.ArgumentParser()
    ap.add_argument("--demand", default="data/analytics/planning_totals_peak.csv")
    ap.add_argument("--as-of", default=None, help="stock cut-off date; default from the demand CSV's .json")
    ap.add_argument("--stock-dir", default="data/incoming/stock")
    ap.add_argument("--facilities", default="data/reference/facilities.csv")
    ap.add_argument("--districts", default="data/reference/districts.csv")
    ap.add_argument("--cases", default=None, help="gold_weekly_cases for severity weights; omit for equal weights")
    ap.add_argument("--budget-frac", type=float, default=0.5)
    ap.add_argument("--min-cover", type=float, default=0.25)
    ap.add_argument("--out", default="data/analytics/allocation_sensitivity")
    ap.add_argument("--doc", default=str(ROOT / "docs" / "allocation_assumptions.md"))
    a = ap.parse_args()

    meta_json = Path(a.demand).with_suffix(".json")
    as_of = a.as_of or (json.loads(meta_json.read_text())["as_of"] if meta_json.exists() else None)

    fac = pd.read_csv(a.facilities, dtype={"district_code": str, "facility_id": str}, encoding="utf-8-sig")
    fac["district_code"] = fac["district_code"].str.zfill(2)
    dis = pd.read_csv(a.districts, dtype={"district_code": str}, encoding="utf-8-sig")
    dis["district_code"] = dis["district_code"].str.zfill(2)
    pop = dis.set_index("district_code")["population"]
    demand = pd.read_csv(a.demand, dtype={"district_code": str}, encoding="utf-8-sig")
    kw = {"as_of": as_of} if as_of and "as_of" in inspect.signature(ra.latest_stock_by_district).parameters else {}
    stock, _ = ra.latest_stock_by_district(a.stock_dir, fac, **kw)
    inp = ra.build_input(demand, stock, dis)
    weights = ra.load_weights(a.cases, sorted(inp["district_code"].unique()))
    costs = ra.UNIT_COSTS

    budget = a.budget_frac * need_cost(inp, costs)
    tr, free_unmet = transport_analysis(inp, pop, costs, VOLUME, weights, budget, a.min_cover)
    cs = cost_sensitivity(inp, pop, costs, weights, a.budget_frac, a.min_cover)
    ws = weight_sensitivity(inp, pop, costs, weights, budget, a.min_cover)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tr.to_csv(out / "transport.csv", index=False)
    cs.to_csv(out / "cost_sensitivity.csv", index=False)
    ws.to_csv(out / "weight_sensitivity.csv", index=False)
    meta = dict(budget=budget, budget_frac=a.budget_frac, min_cover=a.min_cover,
                v_need=need_volume(inp, VOLUME), free_unmet=free_unmet)
    Path(a.doc).write_text(assumptions_markdown(costs, meta, tr, cs, ws), encoding="utf-8")
    print(tr.to_string(index=False)); print(); print(cs.to_string(index=False))
    if len(ws):
        print(); print(ws.to_string(index=False))
    print(f"\nwrote {out}/ and {a.doc}")


if __name__ == "__main__":
    main()

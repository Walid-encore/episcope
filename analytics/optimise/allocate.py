"""Allocation optimiser (AN-7) and efficiency-equity curve (AN-8).

Input contract (one row per district x resource), built from demand.py output:
    district_code, division, resource, demand (q90 over horizon), stock (current)
Other inputs:
    pop      : Series indexed by district_code (population)
    costs    : dict resource -> unit cost
    weights  : optional Series indexed by district_code (severity weight w_d, default 1)
    vol      : optional dict resource -> transport volume per unit (v_r)
    caps     : optional dict division -> transport capacity T_k

Model (Section 6.3):
    min  sum_d,r  w_d * c_r * u_dr          (c_r makes units comparable across resources)
    s.t. u_dr >= demand_dr - stock_dr - x_dr,  u_dr >= 0
         sum c_r x_dr <= B
         x_dr >= alpha * p_d * sum_d' x_d'r      (equity floor)
         sum_{d in k} sum_r v_r x_dr <= T_k      (transport)
alpha is the share of each resource guaranteed to districts by population, whatever
their risk: alpha = 0 is pure efficiency, alpha -> 1 approaches population-proportional.
"""
import numpy as np
import pandas as pd
import pulp

REQUIRED = ["district_code", "division", "resource", "demand", "stock"]


def _check(df):
    miss = [c for c in REQUIRED if c not in df.columns]
    if miss:
        raise ValueError(f"missing columns: {miss}")
    return df.copy()


def _wts(df, weights):
    if weights is None:
        return pd.Series(1.0, index=df["district_code"].unique())
    return weights


def solve_allocation(demand, pop, costs, budget, alpha=0.0, weights=None,
                     vol=None, caps=None):
    """Return demand frame with columns x (allocation) and unmet."""
    df = _check(demand)
    w = _wts(df, weights)
    p = (pop / pop.loc[df["district_code"].unique()].sum())
    keys = list(zip(df["district_code"], df["resource"]))
    prob = pulp.LpProblem("allocation", pulp.LpMinimize)
    x = {k: pulp.LpVariable(f"x_{i}", lowBound=0) for i, k in enumerate(keys)}
    u = {k: pulp.LpVariable(f"u_{i}", lowBound=0) for i, k in enumerate(keys)}
    prob += pulp.lpSum(w[d] * costs[r] * u[(d, r)] for d, r in keys)
    for (d, r), s in zip(keys, (df["demand"] - df["stock"]).to_numpy()):
        prob += u[(d, r)] >= s - x[(d, r)]
    prob += pulp.lpSum(costs[r] * x[(d, r)] for d, r in keys) <= budget
    for r in df["resource"].unique():
        tot = pulp.lpSum(x[(d, rr)] for d, rr in keys if rr == r)
        for d in df.loc[df["resource"] == r, "district_code"]:
            prob += x[(d, r)] >= alpha * float(p[d]) * tot
    if vol and caps:
        for k, T in caps.items():
            prob += pulp.lpSum(vol[r] * x[(d, r)]
                               for (d, r), dv in zip(keys, df["division"])
                               if dv == k) <= T
    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"solver status: {pulp.LpStatus[status]}")
    out = df.copy()
    out["x"] = [x[k].value() or 0.0 for k in keys]
    out["unmet"] = np.maximum(out["demand"] - out["stock"] - out["x"], 0.0)
    return out


def proportional_baseline(demand, pop, costs, budget):
    """Population-proportional baseline: budget split over resources by need cost,
    then each resource split by population share, ignoring district risk."""
    df = _check(demand)
    df["need"] = np.maximum(df["demand"] - df["stock"], 0.0)
    df["c"] = df["resource"].map(costs)
    cost_by_r = (df["need"] * df["c"]).groupby(df["resource"]).sum()
    share_r = cost_by_r / cost_by_r.sum()
    p = pop / pop.loc[df["district_code"].unique()].sum()
    units_r = {r: budget * share_r[r] / costs[r] for r in share_r.index}
    df["x"] = [units_r[r] * float(p[d]) for d, r in zip(df["district_code"], df["resource"])]
    df["unmet"] = np.maximum(df["need"] - df["x"], 0.0)
    return df.drop(columns=["need", "c"])


def metrics(res, costs, weights=None):
    """Weighted unmet cost, share of need covered, and equity of coverage by district."""
    w = _wts(res, weights)
    c = res["resource"].map(costs)
    need = np.maximum(res["demand"] - res["stock"], 0.0) * c
    unmet = res["unmet"] * c
    wu = float((res["district_code"].map(w) * unmet).sum())
    by_d = pd.DataFrame({"need": need, "unmet": unmet, "d": res["district_code"]}).groupby("d").sum()
    by_d = by_d[by_d["need"] > 0]
    cov = 1 - by_d["unmet"] / by_d["need"]
    g = np.sort(cov.to_numpy())
    n = len(g)
    gini = float((2 * np.arange(1, n + 1) - n - 1).dot(g) / (n * g.sum())) if n and g.sum() else 0.0
    return {"weighted_unmet": wu,
            "coverage_total": float(1 - unmet.sum() / need.sum()) if need.sum() else 1.0,
            "coverage_min_district": float(cov.min()) if n else 1.0,
            "coverage_gini": gini}


def efficiency_equity_curve(demand, pop, costs, budget_fracs=(0.1, 0.2, 0.4, 0.6, 0.8, 1.0),
                            alphas=(0.0, 0.2, 0.4, 0.6, 0.8), weights=None, vol=None, caps=None):
    """AN-8: one row per (budget level, alpha) plus the proportional baseline."""
    df = _check(demand)
    full = float((np.maximum(df["demand"] - df["stock"], 0) * df["resource"].map(costs)).sum())
    rows = []
    for f in budget_fracs:
        B = f * full
        base = metrics(proportional_baseline(df, pop, costs, B), costs, weights)
        rows.append({"budget_frac": f, "budget": B, "policy": "proportional", "alpha": np.nan, **base})
        for a in alphas:
            res = solve_allocation(df, pop, costs, B, a, weights, vol, caps)
            rows.append({"budget_frac": f, "budget": B, "policy": "optimised", "alpha": a,
                         **metrics(res, costs, weights)})
    return pd.DataFrame(rows)


def recommend_alpha(curve, tolerance=0.05, basis="relative"):
    """Largest alpha whose efficiency loss, versus alpha = 0, stays within `tolerance` at every
    budget below full cover. Higher alpha buys equity at a stated efficiency cost.
    basis="relative": loss as a share of the alpha = 0 weighted unmet (very strict where unmet is small,
                      i.e. at high budgets).
    basis="need":     loss as a share of the TOTAL weighted need at that budget level (budget / budget_frac):
                      the extra need left uncovered, as a fraction of everything that needed covering."""
    if basis not in ("relative", "need"):
        raise ValueError("basis must be 'relative' or 'need'")
    opt = curve[(curve["policy"] == "optimised") & (curve["budget_frac"] < 1.0)]
    ref = opt[opt["alpha"] == 0.0].set_index("budget_frac")
    ok = []
    for a, g in opt.groupby("alpha"):
        cur = g.set_index("budget_frac")
        loss = cur["weighted_unmet"] - ref["weighted_unmet"]
        if basis == "need":
            limit = tolerance * (cur["budget"] / cur.index.to_series())
        else:
            limit = tolerance * ref["weighted_unmet"]
        if (loss <= limit + 1e-9).all():
            ok.append(a)
    return max(ok) if ok else 0.0

"""EpiScope analytics diagnostics (read-only, changes nothing).
Run from C:\\episcope:   python diagnose.py
  1. choose k          smallest reporting lag where 95% of cases have arrived
  2. scale check       national stock vs forecast demand (is the stock feed on the same scale?)
  3. dengue hump       is the late-season peak repeatable across years (seasonal) or one-off (outbreak)?
"""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(".")
GOLD = ROOT / "data" / "gold_csv"
AN = ROOT / "data" / "analytics"
pd.set_option("display.width", 160)


def hr(t):
    print("\n" + "=" * 8, t, "=" * 8)


def section_k(target=0.95):
    hr("1. CHOOSE k (reporting-lag profile)")
    f = GOLD / "gold_weekly_cases_by_lag.csv"
    lag = pd.read_csv(f, dtype={"district_code": str}, parse_dates=["week_start_date"])
    max_lag = int(lag["report_lag_weeks"].max())
    # only use weeks old enough to be fully matured, else late reports are still missing
    cutoff = lag["week_start_date"].max() - pd.Timedelta(weeks=max_lag + 1)
    m = lag[lag["week_start_date"] <= cutoff]
    print(f"max observed lag: {max_lag} weeks; using onset weeks up to {cutoff.date()} "
          f"({m['week_start_date'].nunique()} weeks, {int(m['cases_new'].sum()):,} cases)")

    def k_of(df):
        prof = df.groupby("report_lag_weeks")["cases_new"].sum().sort_index()
        cum = prof.cumsum() / prof.sum()
        return int(cum[cum >= target].index.min()), prof, cum

    k, prof, cum = k_of(m)
    print(pd.DataFrame({"cases": prof.astype(int), "share": (prof / prof.sum()).round(4),
                        "cumulative": cum.round(4)}))
    print(f"\nALL DISEASES: k = {k}  (smallest lag with >= {target:.0%} of cases reported)")
    ks = {}
    for dis, g in m.groupby("disease"):
        ks[dis] = k_of(g)[0]
    print("per disease:", ks)
    print(f"-> a week is 'complete' once it is {k} weeks old; use --trim-last-weeks {max(ks.values())} "
          f"(max over diseases) and write this in docs/assumptions.md")


def section_scale():
    hr("2. SCALE CHECK (stock feed vs forecast demand)")
    tot = AN / "planning_totals.csv"
    if not tot.exists():
        print("data/analytics/planning_totals.csv missing: run python -m analytics.optimise.run_demand first")
        return
    t = pd.read_csv(tot)
    print("planning_totals columns:", list(t.columns))
    stock_dir = ROOT / "data" / "incoming" / "stock"
    batches = sorted(p for p in stock_dir.iterdir() if p.is_dir())
    parts = [pd.read_json(f, lines=True, convert_dates=False) for f in batches[-1].glob("*.jsonl")]
    rows = pd.concat(parts)
    last = rows["snapshot_date"].max()
    snap = rows[rows["snapshot_date"] == last]
    cols = ["iv_saline_units", "ns1_kits", "ors_sachets", "occupied_beds"]
    stock = snap[cols].sum()
    print(f"latest stock snapshot {last}: {snap['facility_id'].nunique()} facilities, summed nationally")
    if "resource" not in t.columns or "demand" not in t.columns:
        print(stock)
        print("planning_totals has unexpected columns, send me the header")
        return
    d = t.groupby("resource")[[c for c in ("demand", "demand_q90") if c in t.columns]].sum()
    out = []
    for res, scol in [("beds", "occupied_beds"), ("iv_saline_units", "iv_saline_units"),
                      ("ns1_kits", "ns1_kits"), ("ors_sachets", "ors_sachets")]:
        if res in d.index:
            dq = float(d.loc[res].get("demand_q90", d.loc[res, "demand"]))
            out.append({"resource": res, "national_stock": float(stock[scol]), "demand_q90_4wk": dq,
                        "demand/stock": round(dq / float(stock[scol]), 3) if stock[scol] else np.nan})
    print(pd.DataFrame(out).to_string(index=False))
    print("\nRead it like this: if demand/stock is far below 1 for every resource (say < 0.1), the stock feed is on a "
          "much larger scale than the cases, so the optimiser will find almost nothing to allocate.")


def section_peak():
    hr("3. DENGUE HUMP (repeatable across years?)")
    c = pd.read_csv(GOLD / "gold_weekly_cases.csv", dtype={"district_code": str}, parse_dates=["week_start_date"])
    d = c[(c["disease"] == "dengue") & (c["epi_week"] <= 52)]
    top = d.groupby("district_code")["cases"].sum().idxmax()
    print(f"highest-burden dengue district (proxy for Dhaka): {top}")
    s = d[d["district_code"] == top]
    rows = []
    for y, g in s.groupby("epi_year"):
        if g["epi_week"].nunique() < 40:
            continue
        pk = g.loc[g["cases"].idxmax()]
        late = g[(g["epi_week"] >= 40) & (g["epi_week"] <= 48)]["cases"].sum() / max(g["cases"].sum(), 1)
        rows.append({"year": int(y), "peak_week": int(pk["epi_week"]), "peak_cases": int(pk["cases"]),
                     "total_cases": int(g["cases"].sum()), "share_in_wk40_48": round(float(late), 2)})
    print(pd.DataFrame(rows).to_string(index=False))
    piv = s.pivot_table(index="epi_week", columns="epi_year", values="cases", aggfunc="sum")
    piv = piv.loc[:, piv.notna().sum() >= 40]
    norm = piv / piv.mean()
    print(f"\nmedian-across-years profile peaks at week {int(norm.median(axis=1).idxmax())}; "
          f"mean-across-years profile peaks at week {int(norm.mean(axis=1).idxmax())}")
    print("Peak weeks within ~4 weeks of each other every year = seasonal (weather-driven). "
          "Scattered peak weeks = injected outbreaks are shaping the average.")


for fn in (section_k, section_scale, section_peak):
    try:
        fn()
    except Exception as e:  # keep going so one failure does not hide the other sections
        print(f"\n[{fn.__name__} failed] {type(e).__name__}: {e}")

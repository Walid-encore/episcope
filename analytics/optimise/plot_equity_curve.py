"""Efficiency-equity figure for conflict C3 (AN-8): docs/experiments/c3_efficiency_equity.png

Left   weighted unmet demand against budget, one line per alpha, plus the population-proportional baseline.
Right  at one budget level: worst-district coverage against weighted unmet demand. Each alpha is a point
       (equity bought at an efficiency cost); the coverage-floor plan is marked separately.
Reads efficiency_equity_curve.csv from --peak (no floor) and, optionally, --floor (coverage floor applied).
Colour-blind-safe palette (Okabe-Ito); every marker is also distinguishable by shape and label.

  python -m analytics.optimise.plot_equity_curve --budget-frac 0.6
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OI = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--peak", default="data/analytics/allocation_peak_nofloor")
    ap.add_argument("--floor", default="data/analytics/allocation_peak")
    ap.add_argument("--budget-frac", type=float, default=0.6)
    ap.add_argument("--out", default=str(ROOT / "docs" / "experiments" / "c3_efficiency_equity.png"))
    a = ap.parse_args()

    cur = pd.read_csv(Path(a.peak) / "efficiency_equity_curve.csv")
    opt, base = cur[cur["policy"] == "optimised"], cur[cur["policy"] == "proportional"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.3))

    for i, (al, g) in enumerate(opt.groupby("alpha")):
        g = g.sort_values("budget_frac")
        ax1.plot(g["budget_frac"], g["weighted_unmet"], marker="osd^vp"[i % 6], color=OI[i % 6],
                 label=f"optimised, alpha={al:g}")
    b = base.sort_values("budget_frac")
    ax1.plot(b["budget_frac"], b["weighted_unmet"], "k--", marker="x", label="population-proportional")
    ax1.set_xlabel("budget (share of cost of all unmet need)")
    ax1.set_ylabel("severity-weighted unmet demand (cost units)")
    ax1.set_title("Efficiency: unmet demand by budget")
    ax1.legend(fontsize=7)

    f = a.budget_frac
    at = opt[(opt["budget_frac"] - f).abs() < 1e-9].sort_values("alpha")
    if at.empty:
        raise SystemExit(f"budget fraction {f} not in the curve: {sorted(opt['budget_frac'].unique())}")
    ax2.plot(at["weighted_unmet"], at["coverage_min_district"], "-o", color=OI[0], label="optimised, alpha 0 to 0.8")
    for _, r in at.iterrows():
        ax2.annotate(f"a={r['alpha']:g}", (r["weighted_unmet"], r["coverage_min_district"]),
                     textcoords="offset points", xytext=(4, 4), fontsize=7)
    bb = base[(base["budget_frac"] - f).abs() < 1e-9].iloc[0]
    ax2.scatter([bb["weighted_unmet"]], [bb["coverage_min_district"]], marker="x", color="k", s=70,
                label="population-proportional")
    fl = Path(a.floor) / "efficiency_equity_curve.csv"
    if fl.exists():
        fc = pd.read_csv(fl)
        r = fc[(fc["policy"] == "optimised") & (fc["alpha"] == 0.0) & ((fc["budget_frac"] - f).abs() < 1e-9)]
        if len(r) and r.iloc[0]["coverage_min_district"] > at.iloc[0]["coverage_min_district"] + 1e-6:
            r = r.iloc[0]
            ax2.scatter([r["weighted_unmet"]], [r["coverage_min_district"]], marker="*", s=170, color=OI[5],
                        label="alpha=0 + coverage floor (recommended)", zorder=5)
        else:
            print("WARNING: the --floor curve shows no coverage gain, so the floor was probably not applied "
                  "to that curve; the recommended-policy marker is omitted")
    ax2.set_xlabel("severity-weighted unmet demand (lower is more efficient)")
    ax2.set_ylabel("worst-district coverage (higher = more equal)")
    ax2.set_title(f"Equity bought per unit of efficiency lost (budget {f:.0%})")
    ax2.legend(fontsize=7)
    fig.tight_layout()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

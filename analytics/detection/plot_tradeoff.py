"""C1 trade-off plot: sensitivity against false alerts per district-year, one curve per detector,
with the chosen operating points marked. Reads the evaluation CSVs from docs/experiments.

    python -m analytics.detection.plot_tradeoff [--dir docs/experiments] [--min-cases 0]
Writes c1_tradeoff.png next to the inputs.
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "docs" / "experiments"))
    ap.add_argument("--min-cases", type=int, default=0, help="count floor to plot (default 0)")
    a = ap.parse_args()
    d = Path(a.dir)
    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    for det, style in (("ears_c2", "-o"), ("cusum", "-s")):
        p = d / f"detection_sweep_{det}.csv"
        if not p.exists():
            continue
        s = pd.read_csv(p)
        s = s[s["min_cases"] == a.min_cases].sort_values("false_alerts_per_district_year")
        ax.plot(s["false_alerts_per_district_year"], s["sensitivity"], style, ms=3, label=det)
    op_path = d / "detection_operating_points.csv"
    if op_path.exists():
        op = pd.read_csv(op_path)
        op = op[op["min_cases"] == a.min_cases]
        ax.scatter(op["false_per_district_year"], op["sensitivity"], s=110, facecolors="none",
                   edgecolors="black", linewidths=1.6, zorder=5, label="chosen operating point")
        for r in op.itertuples():
            ax.annotate(f"{r.detector} t={r.threshold:g}", (r.false_per_district_year, r.sensitivity),
                        textcoords="offset points", xytext=(8, -12), fontsize=8)
    ax.axvline(0.5, color="grey", ls="--", lw=0.8)
    ax.text(0.52, 0.03, "0.5 false alerts / district-year", fontsize=7, color="grey")
    ax.set_xscale("log")
    ax.set_xlabel("False alerts per district-year (log scale)")
    ax.set_ylabel("Sensitivity (share of scored outbreaks detected)")
    ax.set_ylim(0, 1.05)
    ax.set_title("C1: detection sensitivity vs false alarms")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    out = d / "c1_tradeoff.png"
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

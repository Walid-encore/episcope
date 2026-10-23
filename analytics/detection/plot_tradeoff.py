"""C1 trade-off figure: sensitivity vs false alerts per district-year for each detector.

    python -m analytics.detection.plot_tradeoff [--min-cases 0] [--limit 0.5]

Reads docs/experiments/detection_sweep_<detector>.csv and detection_operating_points.csv,
writes docs/figures/c1_tradeoff.png. Needs matplotlib.
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "docs" / "experiments"
DETECTORS = {"ears_c2": "EARS C2", "cusum": "CUSUM"}
COLOURS = {"ears_c2": "#0072B2", "cusum": "#D55E00"}  # colour-blind-safe pair


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-cases", type=float, default=0)
    ap.add_argument("--limit", type=float, default=0.5)
    a = ap.parse_args()

    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    ops = pd.read_csv(EXP / "detection_operating_points.csv")
    for det, label in DETECTORS.items():
        s = pd.read_csv(EXP / f"detection_sweep_{det}.csv")
        s = s[s["min_cases"] == a.min_cases].sort_values("false_alerts_per_district_year")
        ax.plot(s["false_alerts_per_district_year"], s["sensitivity"], marker="o", ms=3.5,
                lw=1.3, color=COLOURS[det], label=label)
        op = ops[(ops["detector"] == det) & (ops["min_cases"] == a.min_cases)]
        if not op.empty:
            r = op.iloc[0]
            ax.scatter([r["false_per_district_year"]], [r["sensitivity"]], s=110, marker="*",
                       color=COLOURS[det], edgecolor="black", zorder=5,
                       label=f"{label} chosen (threshold {r['threshold']:g})")
    ax.axvline(a.limit, ls="--", color="grey", lw=1)
    ax.text(a.limit, 0.02, f" limit {a.limit} false alerts / district-year", color="grey",
            fontsize=8, va="bottom")
    ax.set_xlabel("False alerts per district-year")
    ax.set_ylabel("Sensitivity (share of 21 scored outbreaks detected)")
    ax.set_title("Detection trade-off (conflict C1), count floor = %g" % a.min_cases)
    ax.set_xlim(left=0)
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="lower right")
    out = ROOT / "docs" / "figures"
    out.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out / "c1_tradeoff.png", dpi=200)
    print(f"wrote {out / 'c1_tradeoff.png'}")


if __name__ == "__main__":
    main()

"""National weekly ILI series for the SEIR scenario (AN-10), built from the real gold_weekly_cases.

    python -m analytics.scenario.make_ili_series [--gold data/gold]
Writes data/analytics/national_weekly_ili.csv (week_start_date, cases; edge weeks trimmed).
"""
import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold"))
    a = ap.parse_args()
    g = pd.read_parquet(Path(a.gold) / "gold_weekly_cases", columns=["disease", "week_start_date", "cases"])
    ili = g[g["disease"].astype(str) == "ili"].copy()
    ili["week_start_date"] = pd.to_datetime(ili["week_start_date"]).dt.round("D")
    w = ili.groupby("week_start_date", observed=True)["cases"].sum()
    full = pd.date_range(w.index.min(), w.index.max(), freq="7D")
    w = w.reindex(full, fill_value=0)
    w = w[(w.index >= pd.Timestamp("2021-01-04")) & (w.index <= w.index.max() - pd.Timedelta(weeks=3))]
    out = ROOT / "data" / "analytics" / "national_weekly_ili.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    w.rename_axis("week_start_date").rename("cases").reset_index().to_csv(out, index=False)
    print(f"{len(w)} weeks, {int(w.sum()):,} ILI cases, peak {int(w.max())}/week -> {out}")


if __name__ == "__main__":
    main()

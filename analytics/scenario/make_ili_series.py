"""Build the national weekly ILI series from the generated encounters (STAND-IN: pandas, small scale).
Replace with the Data Engineer's gold table when it carries ILI.

    python -m analytics.scenario.make_ili_series
Writes data/standin_gold/national_weekly_ili.csv (week_start_date, cases; onset weeks, edge weeks trimmed).
"""
from pathlib import Path

import pandas as pd

from analytics.standin_gold import _week_start, read_encounters, to_silver

ROOT = Path(__file__).resolve().parents[2]


def main():
    fac = pd.read_csv(ROOT / "data" / "reference" / "facilities.csv", dtype={"district_code": str, "facility_id": str})
    fac["district_code"] = fac["district_code"].str.zfill(2)
    silver = to_silver(read_encounters(ROOT / "data" / "incoming" / "encounters"), fac)
    ili = silver[silver["icd3"].isin(["J10", "J11"])].copy()
    ili["week_start_date"] = _week_start(ili["onset_date"])
    w = ili.groupby("week_start_date").size()
    full = pd.date_range(w.index.min(), w.index.max(), freq="7D")
    w = w.reindex(full, fill_value=0)
    w = w[(w.index >= pd.Timestamp("2021-01-04")) & (w.index <= w.index.max() - pd.Timedelta(weeks=3))]
    out = ROOT / "data" / "standin_gold" / "national_weekly_ili.csv"
    w.rename_axis("week_start_date").rename("cases").reset_index().to_csv(out, index=False)
    print(f"{len(w)} weeks, {int(w.sum()):,} ILI cases, peak {int(w.max())}/week -> {out}")


if __name__ == "__main__":
    main()

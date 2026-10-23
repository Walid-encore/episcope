"""Gold Parquet tables -> CSV with the contract's column names (docs/gold_contract.md).

    python -m analytics.export_gold [--gold data/gold] [--out data/gold_csv]

Lets every analytics script run on the Data Engineer's real gold output:
    python -m analytics.detection.make_alerts --gold data/gold_csv
Reads gold only. Never reads data/truth.
"""
import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["gold_weekly_cases", "gold_weekly_reports", "gold_reporting_status"]
OPTIONAL = ["gold_weekly_cases_by_lag", "gold_weekly_weather", "gold_stock_weekly"]
INT_COLS = {"epi_year", "epi_week", "report_epi_year", "report_epi_week"}


def fix_types(df):
    """Hive partition columns (epi_year/epi_week) can come back as categoricals: make them plain."""
    for c in df.columns:
        if isinstance(df[c].dtype, pd.CategoricalDtype):
            s = df[c].astype(str)
            df[c] = pd.to_numeric(s) if c in INT_COLS else s
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold"))
    ap.add_argument("--out", default=str(ROOT / "data" / "gold_csv"))
    a = ap.parse_args()
    gold, out = Path(a.gold), Path(a.out)
    missing = [t for t in REQUIRED if not (gold / t).exists()]
    if missing:
        raise SystemExit(f"{gold} is missing {missing}. Build gold first (bronze, silver, gold), "
                         f"or check the --gold path.")
    out.mkdir(parents=True, exist_ok=True)
    for t in REQUIRED + OPTIONAL:
        src = gold / t
        if not src.exists():
            print(f"skip {t} (not built)")
            continue
        df = fix_types(pd.read_parquet(src))
        wk = next((c for c in ("week_start_date", "report_week_start_date") if c in df.columns), None)
        span = f", {pd.to_datetime(df[wk]).min().date()}..{pd.to_datetime(df[wk]).max().date()}" if wk else ""
        df.to_csv(out / f"{t}.csv", index=False)
        print(f"{t}: {len(df):,} rows{span} -> {out / (t + '.csv')}")


if __name__ == "__main__":
    main()

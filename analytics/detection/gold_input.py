"""Detector input from the gold tables (contract v0.3): REPORT-week counts plus reporting status.

    load_detector_input(gold_dir) -> district_code, disease, epi_year, epi_week, week_start_date,
                                     cases, late_cases_ge8d, reporting_completeness

Works with:
  * the stand-in tables (data/standin_gold/*.csv), and
  * the Data Engineer's real gold lake (data/gold/<table>/report_epi_year=…/report_epi_week=…/part-*.parquet),
    or the same tables exported as CSV with the same column names.

For each table a CSV file `<gold_dir>/<name>.csv` is used if it exists, otherwise the Parquet
directory `<gold_dir>/<name>/` is read. Never reads ground truth.
"""
from pathlib import Path

import pandas as pd

REPORT_KEYS = ["district_code", "report_epi_year", "report_epi_week"]


def _read(g, name):
    """Read one gold table from CSV (stand-in) or hive-partitioned Parquet (real gold)."""
    csv = g / f"{name}.csv"
    if csv.exists():
        return pd.read_csv(csv, dtype={"district_code": str})

    pq = g / name
    if not pq.exists():
        raise FileNotFoundError(
            f"Gold table '{name}' not found in {g}: expected {csv.name} or a Parquet folder '{name}/'")
    # pyarrow ignores hidden/underscore files (.crc, _SUCCESS) and turns the
    # report_epi_year=… / report_epi_week=… folder names into columns.
    d = pd.read_parquet(pq)
    d["district_code"] = d["district_code"].astype(str)
    for c in ("report_epi_year", "report_epi_week"):
        if c in d.columns:  # partition columns may arrive as category/string
            d[c] = pd.to_numeric(d[c].astype(str)).astype("int64")
    return d


def load_detector_input(gold_dir):
    g = Path(gold_dir)
    rep = _read(g, "gold_weekly_reports")
    st = _read(g, "gold_reporting_status")
    for d in (rep, st):
        d["district_code"] = d["district_code"].str.zfill(2)
    st = st[REPORT_KEYS + ["reporting_completeness"]]
    out = rep.merge(st, on=REPORT_KEYS, how="left", validate="many_to_one")
    out = out.rename(columns={"report_epi_year": "epi_year", "report_epi_week": "epi_week",
                              "report_week_start_date": "week_start_date",
                              "reported_cases": "cases"})
    out["week_start_date"] = pd.to_datetime(out["week_start_date"])
    cols = ["district_code", "disease", "epi_year", "epi_week", "week_start_date", "cases",
            "late_cases_ge8d", "reporting_completeness"]
    return out[cols].sort_values(["district_code", "disease", "week_start_date"]).reset_index(drop=True)
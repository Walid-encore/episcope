"""Detector input from the gold tables (contract v0.3): REPORT-week counts plus reporting status.

    load_detector_input(gold_dir) -> district_code, disease, epi_year, epi_week, week_start_date,
                                     cases, late_cases_ge8d, reporting_completeness

Works with the stand-in tables (data/standin_gold) and with the Data Engineer's real gold tables
exported as CSV with the same column names. Never reads ground truth.
"""
from pathlib import Path

import pandas as pd

REPORT_KEYS = ["district_code", "report_epi_year", "report_epi_week"]


def load_detector_input(gold_dir):
    g = Path(gold_dir)
    rep = pd.read_csv(g / "gold_weekly_reports.csv", dtype={"district_code": str})
    st = pd.read_csv(g / "gold_reporting_status.csv", dtype={"district_code": str})
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

"""Check data/gold against docs/gold_contract.md v0.3 (columns + basic sanity). Read-only.
Usage (in C:\\episcope):  python -m pipeline.quality.check_gold_contract"""
import sys
from pathlib import Path

from pipeline.spark_session import get_spark

PROBLEMS = []

EXPECT = {
    "gold_weekly_cases": "district_code disease epi_year epi_week week_start_date cases lab_confirmed_cases "
                         "admitted_cases deaths population incidence_per_100k last_batch_id updated_at",
    "gold_weekly_reports": "district_code disease report_epi_year report_epi_week report_week_start_date "
                           "reported_cases late_cases_ge8d last_batch_id updated_at",
    "gold_weekly_cases_by_lag": "district_code disease epi_year epi_week week_start_date report_lag_weeks cases_new",
    "gold_reporting_status": "district_code report_epi_year report_epi_week expected_facilities "
                             "reporting_facilities usual_volume_total reporting_completeness",
    "gold_weekly_weather": "district_code epi_year epi_week temp_mean_c precip_sum_mm humidity_mean_pct",
    "gold_stock_weekly": "district_code epi_year epi_week iv_saline_units ns1_kits ors_sachets antimalarial_courses "
                         "occupied_beds beds_capacity icu_beds_capacity facilities_reporting",
    "gold_lineage": "gold_table epi_year epi_week batch_id source loaded_at",
    "gold_dq_report": "batch_id rule_id reason_code rows_checked rows_failed pass_rate run_ts",
}
KEYS = {"gold_weekly_cases": ["district_code", "disease", "epi_year", "epi_week"],
        "gold_weekly_reports": ["district_code", "disease", "report_epi_year", "report_epi_week"],
        "gold_weekly_cases_by_lag": ["district_code", "disease", "epi_year", "epi_week", "report_lag_weeks"],
        "gold_reporting_status": ["district_code", "report_epi_year", "report_epi_week"],
        "gold_weekly_weather": ["district_code", "epi_year", "epi_week"],
        "gold_stock_weekly": ["district_code", "epi_year", "epi_week"]}

spark = get_spark(app_name="check-gold")
spark.sparkContext.setLogLevel("ERROR")
root = Path("data") / "gold"
for name, cols in EXPECT.items():
    p = root / name
    if not p.exists():
        print(f"[MISSING TABLE] {name}")
        continue
    df = spark.read.parquet(str(p))
    want, have = cols.split(), df.columns
    missing = [x for x in want if x not in have]
    extra = [x for x in have if x not in want]
    msg = f"{name:<26} rows {df.count():>8,} | missing {missing or '-'} | extra {extra or '-'}"
    k = KEYS.get(name)
    if k and not missing:
        n = df.count()
        msg += f" | duplicate keys {n - df.select(*k).distinct().count()} | null keys {df.filter(' or '.join(x + ' is null' for x in k)).count()}"
    print(msg)
    if missing:
        PROBLEMS.append(f"{name}: missing columns {missing}")
    if k and not missing and "duplicate keys 0 | null keys 0" not in msg:
        PROBLEMS.append(f"{name}: duplicate or null keys")
    if "district_code" in have:
        print(f"{'':<26} districts {df.select('district_code').distinct().count()} "
              f"| bad codes {df.filter(~df.district_code.rlike('^[0-9]{2}$')).count()}")
    if name == "gold_reporting_status" and not missing:
        print(f"{'':<26} completeness outside 0..1: "
              f"{df.filter('reporting_completeness < 0 or reporting_completeness > 1').count()} | "
              f"null (warm-up): {df.filter('reporting_completeness is null').count()}")
s = root.parent / "silver" / "dq_report"
if s.exists():
    r = spark.read.parquet(str(s))
    print(f"silver dq_report: {r.select('rule_id').distinct().count()} distinct rules (DQ-1 needs >= 12)")

if PROBLEMS:
    print('CONTRACT CHECK FAILED:')
    for pr in PROBLEMS:
        print(' -', pr)
    sys.exit(1)
print('contract check passed')

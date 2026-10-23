"""Build the gold layer from silver (docs/gold_contract.md v0.3).

Run from the repo root:
  python -m pipeline.gold.build --check     # show which silver tables/columns were found, build nothing
  python -m pipeline.gold.build             # build all gold tables

Reads:   data/silver/<encounters|stock|quarantine>/  (found automatically; override with --encounters etc.)
         data/reference/{districts,facilities,weather}.csv
Writes:  data/gold/<table>/   Parquet, partitioned by epi_year/epi_week where the table has them.

Safe to re-run: every table is rebuilt from silver and written with partition overwrite, and nothing
in the output depends on the wall clock (updated_at = latest bronze load time behind the row), so a
second run reproduces the same rows. Late records (up to 21 days) change the onset-week partitions they
belong to and nothing else.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

from pyspark.sql import functions as F

from pipeline.gold import marts as M
from pipeline.gold import silver_io as S
from pipeline.gold.weeks import week_grid
from pipeline.spark_session import get_spark

PARTITIONS = {
    "gold_weekly_cases": ["epi_year", "epi_week"],
    "gold_weekly_cases_by_lag": ["epi_year", "epi_week"],
    "gold_weekly_reports": ["report_epi_year", "report_epi_week"],
    "gold_reporting_status": ["report_epi_year", "report_epi_week"],
    "gold_weekly_weather": ["epi_year", "epi_week"],
    "gold_stock_weekly": ["epi_year", "epi_week"],
}


def posix(p):
    return str(p).replace("\\", "/")


def write_table(df, name, gold: Path):
    cols = PARTITIONS.get(name)
    path = posix(gold / name)
    if cols:
        df.repartition(*cols).write.mode("overwrite").partitionBy(*cols).parquet(path)
    else:
        df.coalesce(1).write.mode("overwrite").parquet(path)


def find_silver_root(arg):
    for c in ([arg] if arg else []) + ["data/silver", "data/lakehouse/silver", "data/lake/silver"]:
        if Path(c).exists():
            return Path(c)
    return None


def show_check(spark, label, path, candidates):
    print(f"\n{label}: {path}")
    if not path:
        print("  NOT FOUND")
        return
    raw = S.read_quarantine_raw(spark, path) if label == "quarantine" else spark.read.parquet(posix(path))
    cols = raw.columns
    print(f"  columns: {cols}")
    for logical, actual in S.resolve(cols, candidates).items():
        print(f"  {logical:<20} -> {actual or '(not found)'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--silver", default=None)
    ap.add_argument("--reference", default="data/reference")
    ap.add_argument("--gold", default="data/gold")
    ap.add_argument("--encounters", default=None)
    ap.add_argument("--stock", default=None)
    ap.add_argument("--quarantine", default=None)
    ap.add_argument("--diseases", nargs="+", default=["dengue", "diarrhoeal", "ili", "malaria"])
    ap.add_argument("--check", action="store_true", help="show the silver mapping and stop")
    ap.add_argument("--driver-memory", default=None)
    a = ap.parse_args(argv)

    root = find_silver_root(a.silver)
    enc_p = Path(a.encounters) if a.encounters else (S.discover_table(root, "encounter") if root else None)
    stock_p = Path(a.stock) if a.stock else (S.discover_table(root, "stock") if root else None)
    q_p = Path(a.quarantine) if a.quarantine else (S.discover_table(root, "quarantine", exclude=()) if root else None)
    if not enc_p or not stock_p:
        sys.exit(f"Could not find silver encounters/stock under {root or 'data/silver'}. "
                 "Pass --encounters <folder> --stock <folder> (and --quarantine <folder>).")

    spark = get_spark(a.driver_memory, app_name="episcope-gold")
    spark.sparkContext.setLogLevel("ERROR")
    try:
        if a.check:
            show_check(spark, "encounters", enc_p, S.ENC)
            show_check(spark, "stock", stock_p, S.STOCK)
            show_check(spark, "quarantine", q_p, S.QUAR)
            return 0
        return run(spark, a, enc_p, stock_p, q_p)
    finally:
        spark.stop()


def run(spark, a, enc_p, stock_p, q_p) -> int:
    gold, diseases = Path(a.gold), a.diseases
    t0 = time.perf_counter()
    districts, facilities, weather = S.load_reference(spark, a.reference)
    enc, emeta = S.load_encounters(spark, enc_p)
    if "district_code" not in enc.columns:                      # silver did not carry the district: join the registry
        enc = enc.join(F.broadcast(facilities.select("facility_id", "district_code")), "facility_id")
    stock, smeta = S.load_stock(spark, stock_p)
    if emeta["first_report_fallback"]:
        print("WARNING: silver has no first_report_date; using report_date. Resubmitted records will be "
              "timed by their LAST report, which distorts the as-of tables.")

    lo, hi = M.date_range(enc, "onset_date")
    rlo, rhi = M.date_range(enc, "first_report_date")
    if lo is None:
        print("Silver encounters are empty: nothing to build.")
        return 0
    onset_weeks = week_grid(spark, lo, hi)
    report_weeks = week_grid(spark, rlo, rhi, prefix="report_")
    print(f"onset weeks {lo} .. {hi} | report weeks {rlo} .. {rhi} | diseases {diseases}")

    tables = {
        "gold_weekly_cases": lambda: M.weekly_cases(spark, enc, districts, diseases, onset_weeks),
        "gold_weekly_cases_by_lag": lambda: M.cases_by_lag(spark, enc, districts, diseases, onset_weeks),
        "gold_weekly_reports": lambda: M.weekly_reports(spark, enc, districts, diseases, report_weeks),
        "gold_reporting_status": lambda: M.reporting_status(spark, enc, facilities, report_weeks),
        "gold_weekly_weather": lambda: M.weekly_weather(weather),
        "gold_stock_weekly": lambda: M.stock_weekly(stock, facilities),
        "gold_lineage": lambda: M.lineage(enc, stock, diseases),
    }
    summary = []
    for name, make in tables.items():
        t = time.perf_counter()
        write_table(make(), name, gold)
        n = spark.read.parquet(posix(gold / name)).count()
        summary.append((name, n, time.perf_counter() - t))
        print(f"  {name:<28}{n:>12,} rows  {time.perf_counter() - t:6.1f}s", flush=True)

    # ---- reconciliation: gold must add up to silver and the as-of table to the final table
    run_ts = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    rd = lambda n: spark.read.parquet(posix(gold / n))
    cases, lag, reports = rd("gold_weekly_cases"), rd("gold_weekly_cases_by_lag"), rd("gold_weekly_reports")
    silver_n = enc.filter(F.col("disease").isin(diseases)).count()
    lag_sum = lag.groupBy(*M.KEY5).agg(F.sum("cases_new").alias("lag_sum"))
    chk = cases.join(lag_sum, M.KEY5, "left")
    checks = [
        ("GOLD_LAG_SUM_EQUALS_CASES", chk.count(), chk.filter(F.col("cases") != F.coalesce(F.col("lag_sum"), F.lit(0))).count()),
        ("GOLD_CASES_EQUAL_SILVER", silver_n, abs(silver_n - (cases.agg(F.sum("cases")).first()[0] or 0))),
        ("GOLD_REPORTS_EQUAL_SILVER", silver_n, abs(silver_n - (reports.agg(F.sum("reported_cases")).first()[0] or 0))),
    ]
    dq = None
    if q_p:
        quarantine, qmeta = S.load_quarantine(spark, q_p)
        sv_dq = (find_silver_root(a.silver) or Path("data/silver")) / "dq_report"
        if sv_dq.exists():
            dq = M.dq_report_from_silver(spark, spark.read.parquet(posix(sv_dq)), quarantine, qmeta)
        else:
            print("NOTE: data/silver/dq_report not found; gold_dq_report holds failing rules only.")
            dq = M.dq_report(spark, quarantine, qmeta, enc, emeta, stock, smeta, run_ts)
    else:
        print("NOTE: no quarantine table found; gold_dq_report will hold only the gold reconciliation rules.")
    for rule, checked, failed in checks:
        row = M.reconciliation_row(spark, rule, "GOLD_RECONCILIATION", checked, failed, run_ts)
        dq = row if dq is None else dq.unionByName(row, allowMissingColumns=True)
    write_table(dq, "gold_dq_report", gold)
    print(f"  {'gold_dq_report':<28}{spark.read.parquet(posix(gold / 'gold_dq_report')).count():>12,} rows")

    print("\nReconciliation:")
    ok = True
    for rule, checked, failed in checks:
        ok &= failed == 0
        print(f"  {rule:<28} checked {checked:>10,}  failed {failed:>6,}  {'OK' if failed == 0 else 'FAILED'}")
    print(f"\nGold written to {gold} in {time.perf_counter() - t0:.1f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""Gold marts as pure DataFrame functions (see docs/gold_contract.md v0.3).

Every mart is a deterministic function of its inputs, so re-running the pipeline rewrites the same
rows. `updated_at` is the latest bronze load time of the rows behind a row (not "now"), so a re-run
changes nothing. Week grids are dense: a district-week with no cases is a real 0, never a missing row.
"""
from __future__ import annotations

import datetime as dt
import itertools

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql.types import IntegerType, StringType, StructField, StructType

from pipeline.gold.silver_io import COUNTS
from pipeline.gold.weeks import add_week_cols, week_grid

KEY5 = ["district_code", "disease", "epi_year", "epi_week", "week_start_date"]
MAX_LAG = 4                                  # report_lag_weeks 0..4, where 4 means "4 or more"
USUAL_WINDOW, USUAL_MIN = 12, 8              # reporting_status: median of previous 12 weeks, needs >= 8


def _provenance():
    return [F.coalesce(F.max_by("batch_id", "load_ts"), F.max("batch_id")).alias("last_batch_id"),
            F.max("load_ts").alias("updated_at")]


def _dim(spark, values, name):
    return spark.createDataFrame([(v,) for v in values], f"{name} string")


def _py_grid(spark, left: DataFrame, diseases, weeks: DataFrame, lags=None) -> DataFrame:
    """Dense grid = left x diseases x weeks (x lags), built in Python and sent to Spark as ONE small
    local table. Replaces chained crossJoin, which took ~86 s per join on small machines.
    Column names and types come from the input DataFrames, so the schema is unchanged.
    ONE partition: every partition of a Python-built DataFrame starts its own Python worker process
    (~0.65 s each on Windows), so the default 16 partitions cost ~10 s per scan (ENG-3, docs/experiments)."""
    left_rows = [tuple(r) for r in left.collect()]
    week_rows = [tuple(r) for r in weeks.collect()]
    fields = list(left.schema.fields)
    parts = [left_rows]
    if diseases is not None:
        fields.append(StructField("disease", StringType()))
        parts.append([(d,) for d in diseases])
    fields += list(weeks.schema.fields)
    parts.append(week_rows)
    if lags is not None:
        fields.append(StructField("report_lag_weeks", IntegerType()))
        parts.append([(i,) for i in lags])
    rows = [tuple(v for piece in combo for v in piece) for combo in itertools.product(*parts)]
    return spark.createDataFrame(spark.sparkContext.parallelize(rows, 1), StructType(fields))


def date_range(df: DataFrame, col: str):
    r = df.agg(F.min(col).alias("lo"), F.max(col).alias("hi")).first()
    return r["lo"], r["hi"]


# ------------------------------------------------------------------ gold_weekly_cases (onset week)
def weekly_cases(spark, enc, districts, diseases, weeks) -> DataFrame:
    e = add_week_cols(enc.filter(F.col("disease").isin(diseases)), "onset_date")
    agg = (e.groupBy(KEY5).agg(
        F.count(F.lit(1)).alias("cases"),
        F.sum(F.col("lab_confirmed").cast("int")).alias("lab_confirmed_cases"),
        F.sum(F.col("admitted").cast("int")).alias("admitted_cases"),
        F.sum((F.col("outcome") == "died").cast("int")).alias("deaths"),
        *_provenance()))
    grid = _py_grid(spark, districts.select("district_code", "population"), diseases, weeks)
    out = grid.join(agg, KEY5, "left")
    for c in ("cases", "lab_confirmed_cases", "admitted_cases", "deaths"):
        out = out.withColumn(c, F.coalesce(F.col(c), F.lit(0)).cast("long"))
    out = out.withColumn("incidence_per_100k", F.col("cases") / F.col("population") * 100000)
    return out.select(*KEY5, "cases", "lab_confirmed_cases", "admitted_cases", "deaths", "population",
                      "incidence_per_100k", "last_batch_id", "updated_at")


# ------------------------------------------------------------------ gold_weekly_reports (REPORT week)
def weekly_reports(spark, enc, districts, diseases, report_weeks) -> DataFrame:
    keys = ["district_code", "disease", "report_epi_year", "report_epi_week", "report_week_start_date"]
    e = add_week_cols(enc.filter(F.col("disease").isin(diseases)), "first_report_date", "report_")
    agg = (e.groupBy(keys).agg(
        F.count(F.lit(1)).alias("reported_cases"),
        F.sum((F.datediff("first_report_date", "onset_date") >= 8).cast("int")).alias("late_cases_ge8d"),
        *_provenance()))
    grid = _py_grid(spark, districts.select("district_code"), diseases, report_weeks)
    out = grid.join(agg, keys, "left")
    for c in ("reported_cases", "late_cases_ge8d"):
        out = out.withColumn(c, F.coalesce(F.col(c), F.lit(0)).cast("long"))
    return out.select(*keys, "reported_cases", "late_cases_ge8d", "last_batch_id", "updated_at")


# ------------------------------------------------------------------ gold_weekly_cases_by_lag (as-of)
def cases_by_lag(spark, enc, districts, diseases, weeks) -> DataFrame:
    e = add_week_cols(enc.filter(F.col("disease").isin(diseases)), "onset_date")
    report_ws = F.date_trunc("week", F.col("first_report_date")).cast("date")
    weeks_late = (F.datediff(report_ws, F.col("week_start_date")) / 7).cast("int")
    e = e.withColumn("report_lag_weeks", F.least(F.lit(MAX_LAG), F.greatest(F.lit(0), weeks_late)))
    agg = e.groupBy(*KEY5, "report_lag_weeks").agg(F.count(F.lit(1)).alias("cases_new"))
    grid = _py_grid(spark, districts.select("district_code"), diseases, weeks, lags=range(MAX_LAG + 1))
    out = grid.join(agg, [*KEY5, "report_lag_weeks"], "left")
    out = out.withColumn("cases_new", F.coalesce(F.col("cases_new"), F.lit(0)).cast("long"))
    return out.select(*KEY5, "report_lag_weeks", "cases_new")


# ------------------------------------------------------------------ gold_reporting_status (disease-agnostic)
def reporting_status(spark, enc, facilities, report_weeks) -> DataFrame:
    wk = ["report_epi_year", "report_epi_week", "report_week_start_date"]
    r = add_week_cols(enc, "first_report_date", "report_")
    per_fac = r.groupBy("facility_id", *wk).agg(F.count(F.lit(1)).alias("n"))
    grid = _py_grid(spark, facilities.select("facility_id", "district_code"), None, report_weeks)
    g = grid.join(per_fac, ["facility_id", *wk], "left").withColumn("n", F.coalesce(F.col("n"), F.lit(0)))

    # usual weekly volume = median of the previous 12 weeks (current week excluded), needs >= 8 weeks
    w = Window.partitionBy("facility_id").orderBy("report_week_start_date").rowsBetween(-USUAL_WINDOW, -1)
    g = g.withColumn("_prior", F.sort_array(F.collect_list("n").over(w)))
    k = F.size("_prior")
    mid = ((k + 1) / 2).cast("int")
    half = (k / 2).cast("int")
    median = (F.when(k < USUAL_MIN, F.lit(None).cast("double"))
               .when(k % 2 == 1, F.element_at("_prior", mid).cast("double"))
               .otherwise((F.element_at("_prior", half) + F.element_at("_prior", half + 1)) / 2.0))
    g = g.withColumn("usual", median)

    out = g.groupBy("district_code", *wk).agg(
        F.count(F.lit(1)).cast("int").alias("expected_facilities"),
        F.sum((F.col("n") > 0).cast("int")).cast("int").alias("reporting_facilities"),
        F.sum("usual").alias("usual_volume_total"),
        (F.sum(F.when(F.col("usual") > 0, F.least(F.col("usual"), 2.0 * F.col("n"))).otherwise(F.lit(0.0))) / F.sum("usual")).alias("reporting_completeness"))
    return out.select("district_code", *wk, "expected_facilities", "reporting_facilities",
                      "usual_volume_total", "reporting_completeness")


# ------------------------------------------------------------------ gold_weekly_weather
def weekly_weather(weather) -> DataFrame:
    w = add_week_cols(weather, "date")
    agg = (w.groupBy("district_code", "epi_year", "epi_week", "week_start_date").agg(
        F.avg("temp_mean_c").alias("temp_mean_c"),
        F.sum("precip_mm").alias("precip_sum_mm"),
        F.avg("humidity_pct").alias("humidity_mean_pct"),
        F.count("date").cast("int").alias("days_observed")))          # < 7 at the edges of the weather file
    win = Window.partitionBy("district_code").orderBy("week_start_date")
    for i in range(1, 9):
        agg = (agg.withColumn(f"temp_mean_c_lag{i}", F.lag("temp_mean_c", i).over(win))
                  .withColumn(f"precip_sum_mm_lag{i}", F.lag("precip_sum_mm", i).over(win)))
    lag_cols = [f"{p}_lag{i}" for i in range(1, 9) for p in ("temp_mean_c", "precip_sum_mm")]
    return agg.select("district_code", "epi_year", "epi_week", "week_start_date", "temp_mean_c", "precip_sum_mm",
                      "humidity_mean_pct", *lag_cols, "days_observed")


# ------------------------------------------------------------------ gold_stock_weekly
def stock_weekly(stock, facilities) -> DataFrame:
    s = add_week_cols(stock, "snapshot_date")
    last = s.groupBy("facility_id", "epi_year", "epi_week", "week_start_date").agg(     # last snapshot of the week
        *[F.max_by(c, "snapshot_date").alias(c) for c in COUNTS])
    j = last.join(facilities.select("facility_id", "district_code"), "facility_id")
    by_d = j.groupBy("district_code", "epi_year", "epi_week", "week_start_date").agg(
        *[F.sum(c).cast("long").alias(c) for c in COUNTS], F.count(F.lit(1)).cast("int").alias("facilities_reporting"))
    caps = facilities.groupBy("district_code").agg(F.sum("beds").cast("long").alias("beds_capacity"),
                                                   F.sum("icu_beds").cast("long").alias("icu_beds_capacity"))
    return (by_d.join(caps, "district_code", "left")
            .select("district_code", "epi_year", "epi_week", "week_start_date", *COUNTS,
                    "beds_capacity", "icu_beds_capacity", "facilities_reporting"))


# ------------------------------------------------------------------ gold_lineage
def lineage(enc, stock, diseases) -> DataFrame:
    def part(df, table, source, date_col, prefix=""):
        d = add_week_cols(df, date_col, prefix)
        return (d.groupBy(F.col(f"{prefix}epi_year").alias("epi_year"), F.col(f"{prefix}epi_week").alias("epi_week"),
                          "batch_id")
                 .agg(F.min("load_ts").alias("loaded_at"))
                 .withColumn("gold_table", F.lit(table)).withColumn("source", F.lit(source)))
    ed = enc.filter(F.col("disease").isin(diseases))
    parts = [part(ed, "gold_weekly_cases", "encounters", "onset_date"),
             part(ed, "gold_weekly_cases_by_lag", "encounters", "onset_date"),
             part(ed, "gold_weekly_reports", "encounters", "first_report_date", "report_"),
             part(enc, "gold_reporting_status", "encounters", "first_report_date", "report_"),
             part(stock, "gold_stock_weekly", "stock", "snapshot_date")]
    out = parts[0]
    for p in parts[1:]:
        out = out.unionByName(p)
    return out.select("gold_table", "epi_year", "epi_week", "batch_id", "source", "loaded_at")


# ------------------------------------------------------------------ gold_dq_report
def _feed(c):
    low = F.lower(F.col(c))
    return F.when(low.contains("encounter"), "encounters").when(low.contains("stock"), "stock").otherwise(low)


def dq_report(spark, quarantine, qmeta, enc, emeta, stock, smeta, run_ts: dt.datetime) -> DataFrame:
    """One row per (batch, feed, rule) built from the quarantine table. A rule that never failed
    anywhere does not appear; rules that failed in some batch show 0 failures in the others."""
    use_batch = qmeta["has_batch"] and emeta["has_batch"] and smeta["has_batch"]
    batch = (lambda c: F.col(c)) if use_batch else (lambda c: F.lit("ALL"))
    q = (quarantine.select(batch("batch_id").alias("batch_id"), _feed("source_feed").alias("source_feed"),
                           F.col("reason_code")))
    failed = q.groupBy("batch_id", "source_feed", "reason_code").agg(F.count(F.lit(1)).alias("rows_failed"))
    failed_total = q.groupBy("batch_id", "source_feed").agg(F.count(F.lit(1)).alias("_failed_all"))
    valid = (enc.select(batch("batch_id").alias("batch_id"), F.lit("encounters").alias("source_feed"))
             .unionByName(stock.select(batch("batch_id").alias("batch_id"), F.lit("stock").alias("source_feed")))
             .groupBy("batch_id", "source_feed").agg(F.count(F.lit(1)).alias("_valid")))
    batches = valid.select("batch_id", "source_feed").unionByName(failed.select("batch_id", "source_feed")).distinct()
    rules = failed.select("source_feed", "reason_code").distinct()
    out = (batches.join(rules, "source_feed")
           .join(failed, ["batch_id", "source_feed", "reason_code"], "left")
           .join(failed_total, ["batch_id", "source_feed"], "left").join(valid, ["batch_id", "source_feed"], "left"))
    out = (out.withColumn("rows_failed", F.coalesce(F.col("rows_failed"), F.lit(0)))
              .withColumn("rows_checked", F.when(F.col("_valid").isNotNull(),
                                                 F.col("_valid") + F.coalesce(F.col("_failed_all"), F.lit(0))))
              .withColumn("pass_rate", F.when(F.col("rows_checked") > 0, 1 - F.col("rows_failed") / F.col("rows_checked")))
              .withColumn("rule_id", F.concat_ws(":", F.col("source_feed"), F.col("reason_code")))
              .withColumn("run_ts", F.lit(run_ts).cast("timestamp")))
    return out.select("batch_id", "rule_id", "reason_code", F.col("rows_checked").cast("long"),
                      F.col("rows_failed").cast("long"), "pass_rate", "run_ts")


def reconciliation_row(spark, rule_id, reason_code, checked, failed, run_ts) -> DataFrame:
    """A gold-level check expressed as a dq row (for example: lag counts must add up to weekly cases)."""
    row = ("ALL", rule_id, reason_code, int(checked), int(failed), 1.0 - failed / checked if checked else None, run_ts)
    return spark.createDataFrame(                 # ONE partition, see _py_grid: 16 partitions = 16 Python workers
        spark.sparkContext.parallelize([row], 1),
        "batch_id string, rule_id string, reason_code string, rows_checked long, rows_failed long, "
        "pass_rate double, run_ts timestamp")


def dq_report_from_silver(spark, sv: DataFrame, quarantine, qmeta) -> DataFrame:
    """gold_dq_report from silver's dq_report: EVERY rule x batch, passing rules included (rows_failed = 0).
    Keeps the contract columns (batch_id, rule_id, reason_code, rows_checked, rows_failed, pass_rate, run_ts)
    and adds dataset, severity, description, rows_in, rows_quarantined. run_ts is silver's run time, so a
    gold re-run reproduces the same rows."""
    out = sv.select(
        F.col("bronze_batch_id").alias("batch_id"),
        F.concat_ws(":", F.lower(F.col("dataset")), F.col("rule_id")).alias("rule_id"),
        F.col("rule_id").alias("reason_code"),
        F.col("rows_checked").cast("long").alias("rows_checked"),
        F.col("rows_failed").cast("long").alias("rows_failed"),
        F.col("pass_rate").alias("pass_rate"),
        F.col("run_ts").alias("run_ts"),
        F.lower(F.col("dataset")).alias("dataset"),
        F.col("severity").alias("severity"),
        F.col("description").alias("description"),
        F.col("rows_checked").cast("long").alias("rows_in"))
    rq = F.lit(None).cast("long")
    if qmeta["has_batch"]:
        qc = (quarantine.groupBy(F.col("batch_id").alias("batch_id"), _feed("source_feed").alias("dataset"))
              .agg((F.countDistinct("_qrow") if "_qrow" in quarantine.columns else F.count(F.lit(1))).cast("long").alias("_q")))
        out = out.join(qc, ["batch_id", "dataset"], "left")
        matched = qc.join(out.select("batch_id").distinct(), "batch_id").limit(1).count() > 0
        rq = F.coalesce(F.col("_q"), F.lit(0)) if matched else rq      # no id overlap: say unknown, not 0
    else:
        out = out.withColumn("_q", F.lit(None).cast("long"))
    return out.withColumn("rows_quarantined", rq.cast("long")).select(
        "batch_id", "rule_id", "reason_code", "rows_checked", "rows_failed", "pass_rate", "run_ts",
        "dataset", "severity", "description", "rows_in", "rows_quarantined")

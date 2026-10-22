"""Silver layer: typed, validated, de-identified, de-duplicated tables (ING-3, ING-4, DQ-1..3, PRV-1).

    python -m pipeline.silver.build                 # process every pending bronze batch
    python -m pipeline.silver.build --only encounters

For each dataset: read pending bronze batches -> type the columns -> apply the quality rules ->
rows that fail an "error" rule go to quarantine with reason codes -> valid rows are written to silver.

Late data / idempotency (ING-4)
- A ledger records which bronze batches were processed, so re-running does nothing.
- Encounters and stock are merged by epi-week: only the epi-weeks touched by the new data are
  rewritten (partition overwrite). Rows with a key already in silver are REPLACED by the newest
  version, so a record reported 15 days late updates its old epi-week and never duplicates.
- Assumption: a resubmitted encounter keeps its onset_date (so it stays in the same epi-week).

Privacy (PRV-1): patient_id is replaced by a salted SHA-256 (salt from .env). The raw id and the
exact age never leave bronze: silver keeps patient_hash and age_band. Quarantine rows carry the
hash too, never the raw patient_id.

Outputs (under data/silver/):
    districts/ facilities/ weather/            snapshot tables
    encounters/ stock/                         partitioned by epi_year, epi_week
    quarantine/<dataset>/bronze_batch_id=*/    rejected rows + reason_codes
    dq_report/bronze_batch_id=*/               one row per batch x rule (feeds gold DQ report)
    _ledger/batches.jsonl, runs.jsonl          processed batches and run statistics
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pyspark import StorageLevel
from pyspark.sql import DataFrame, Window, functions as F

from pipeline.bronze.ingest import append_jsonl
from pipeline.quality import rules as Q
from pipeline.silver import privacy
from pipeline.silver import transforms as T

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRONZE = ROOT / "data" / "bronze"
DEFAULT_SILVER = ROOT / "data" / "silver"
META = ["bronze_batch_id", "bronze_source_file", "bronze_load_ts"]
c = F.col


# --------------------------------------------------------------------------------------
# Context and small helpers
# --------------------------------------------------------------------------------------
@dataclass
class Ctx:
    spark: object
    bronze_root: Path
    silver_root: Path
    salt: str
    run_id: str
    run_ts: str

    def silver_path(self, name: str) -> Path:
        return self.silver_root / name

    def silver_table(self, name: str) -> DataFrame:
        p = self.silver_path(name)
        if not p.exists():
            raise RuntimeError(f"silver table '{name}' does not exist yet; its source must be ingested "
                               f"into bronze first (python -m pipeline.bronze.ingest)")
        return self.spark.read.parquet(str(p))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def ensure_columns(df: DataFrame, cols) -> DataFrame:
    """Schema drift guard: a missing expected column becomes all-null (and then fails its rule)."""
    for col in cols:
        if col not in df.columns:
            df = df.withColumn(col, F.lit(None).cast("string"))
    return df


def latest_per_key(df: DataFrame, key) -> DataFrame:
    order = [c("bronze_load_ts").desc(), c("silver_load_ts").desc(), c("bronze_source_file").desc()]
    if "report_date" in df.columns:                         # report_date_first_order: contract says latest report_date wins
        order = [c("report_date").desc_nulls_last()] + order
    w = Window.partitionBy(*key).orderBy(*order)
    if "first_report_date" in df.columns:
        # keep the EARLIEST report date across all versions, then keep only the latest version
        df = df.withColumn("first_report_date", F.min("first_report_date").over(Window.partitionBy(*key)))
    return df.withColumn("_rn", F.row_number().over(w)).filter(c("_rn") == 1).drop("_rn")


# --------------------------------------------------------------------------------------
# Dataset specifications: typer (raw strings -> typed helper columns) and final select
# --------------------------------------------------------------------------------------
@dataclass
class Spec:
    name: str
    columns: list
    typer: Callable
    rules: Callable
    final: Callable
    key: list
    partitioned: bool            # True: merge by epi-week. False: full snapshot table.
    part_cols: tuple = ()
    has_patient: bool = False


def _meta_and_run(ctx):
    return [c(m) for m in META] + [F.lit(ctx.run_ts).cast("timestamp").alias("silver_load_ts")]


# -- districts
def type_districts(raw, ctx):
    return (raw.withColumn("pop_i", T.parse_int(c("population")))
               .withColumn("lat_d", T.parse_double(c("centroid_lat")))
               .withColumn("lon_d", T.parse_double(c("centroid_lon"))))


def final_districts(v, ctx):
    return v.select(F.trim(c("district_code")).alias("district_code"), F.trim(c("district_name")).alias("district_name"),
                    F.trim(c("division")).alias("division"), c("pop_i").alias("population"),
                    c("lat_d").alias("centroid_lat"), c("lon_d").alias("centroid_lon"), *_meta_and_run(ctx))


# -- facilities
def type_facilities(raw, ctx):
    ref = ctx.silver_table("districts").select(F.col("district_code").alias("ref_district")).dropDuplicates()
    return (raw.join(F.broadcast(ref), raw["district_code"] == ref["ref_district"], "left")
               .withColumn("ftype", F.trim(c("facility_type")))
               .withColumn("beds_i", T.parse_int(c("beds")))
               .withColumn("icu_i", T.parse_int(c("icu_beds")))
               .withColumn("lat_d", T.parse_double(c("latitude")))
               .withColumn("lon_d", T.parse_double(c("longitude"))))


def final_facilities(v, ctx):
    return v.select(F.trim(c("facility_id")).alias("facility_id"), c("facility_name"), c("ftype").alias("facility_type"),
                    c("district_code"), c("lat_d").alias("latitude"), c("lon_d").alias("longitude"),
                    c("beds_i").alias("beds"), c("icu_i").alias("icu_beds"), *_meta_and_run(ctx))


# -- weather
def type_weather(raw, ctx):
    ref = ctx.silver_table("districts").select(F.col("district_code").alias("ref_district")).dropDuplicates()
    return (raw.join(F.broadcast(ref), raw["district_code"] == ref["ref_district"], "left")
               .withColumn("wx_d", T.parse_date(c("date")))
               .withColumn("precip_d", T.parse_double(c("precip_mm")))
               .withColumn("temp_d", T.parse_double(c("temp_mean_c")))
               .withColumn("hum_d", T.parse_double(c("humidity_pct"))))


def final_weather(v, ctx):
    return v.select(F.trim(c("district_code")).alias("district_code"), c("wx_d").alias("date"),
                    c("precip_d").alias("precip_mm"), c("temp_d").alias("temp_mean_c"),
                    c("hum_d").alias("humidity_pct"),
                    T.epi_year(c("wx_d")).alias("epi_year"), T.epi_week(c("wx_d")).alias("epi_week"),
                    *_meta_and_run(ctx))


# -- encounters
def type_encounters(raw, ctx):
    fac = (ctx.silver_table("facilities")
              .select("facility_id", F.col("district_code").alias("fac_district_code"))
              .dropDuplicates(["facility_id"]))
    return (raw.join(F.broadcast(fac), "facility_id", "left")
               .withColumn("onset_d", T.parse_date(c("onset_date")))
               .withColumn("report_d", T.parse_date(c("report_date")))
               .withColumn("delay_days", F.datediff(c("report_d"), c("onset_d")))
               .withColumn("age_i", T.parse_int(c("age_years")))
               .withColumn("sex_u", F.upper(F.trim(c("sex"))))
               .withColumn("icd_u", F.trim(c("icd10_code")))
               .withColumn("outcome_l", F.lower(F.trim(c("outcome"))))
               .withColumn("lab_b", T.parse_bool(c("lab_confirmed")))
               .withColumn("adm_b", T.parse_bool(c("admitted")))
               .withColumn("load_date", c("bronze_load_ts").cast("date")))


def final_encounters(v, ctx):
    return v.select(
        c("encounter_id"),
        T.hash_id(c("patient_id"), ctx.salt).alias("patient_hash"),
        c("facility_id"), c("fac_district_code").alias("district_code"),
        c("onset_d").alias("onset_date"), c("report_d").alias("report_date"),
        c("report_d").alias("first_report_date"), c("delay_days"),
        T.age_band(c("age_i")).alias("age_band"),
        F.when(c("sex_u").isin("M", "F"), c("sex_u")).otherwise("U").alias("sex"),
        c("icd_u").alias("icd10_code"), T.disease_from_icd(c("icd_u")).alias("disease"),
        c("lab_b").alias("lab_confirmed"), c("adm_b").alias("admitted"), c("outcome_l").alias("outcome"),
        T.epi_year(c("onset_d")).alias("epi_year"), T.epi_week(c("onset_d")).alias("epi_week"),
        T.epi_year(c("report_d")).alias("report_epi_year"), T.epi_week(c("report_d")).alias("report_epi_week"),
        *_meta_and_run(ctx))


# -- stock and bed snapshots
STOCK_COUNTS = ["iv_saline_units", "ns1_kits", "ors_sachets", "antimalarial_courses", "occupied_beds"]


def type_stock(raw, ctx):
    fac = (ctx.silver_table("facilities")
              .select("facility_id", F.col("district_code").alias("fac_district_code"), F.col("beds").alias("fac_beds"))
              .dropDuplicates(["facility_id"]))
    t = (raw.join(F.broadcast(fac), "facility_id", "left")
            .withColumn("snap_d", T.parse_date(c("snapshot_date")))
            .withColumn("load_date", c("bronze_load_ts").cast("date")))
    for col in STOCK_COUNTS:
        t = t.withColumn(col + "_i", T.parse_int(c(col)))
    return t


def final_stock(v, ctx):
    return v.select(c("snap_d").alias("snapshot_date"), c("facility_id"), c("fac_district_code").alias("district_code"),
                    *[c(col + "_i").alias(col) for col in STOCK_COUNTS],
                    T.epi_year(c("snap_d")).alias("epi_year"), T.epi_week(c("snap_d")).alias("epi_week"),
                    *_meta_and_run(ctx))


SPECS = [
    Spec("districts", ["district_code", "district_name", "division", "population", "centroid_lat", "centroid_lon"],
         type_districts, Q.district_rules, final_districts, ["district_code"], False),
    Spec("facilities", ["facility_id", "facility_name", "facility_type", "district_code", "latitude", "longitude",
                        "beds", "icu_beds"],
         type_facilities, Q.facility_rules, final_facilities, ["facility_id"], False),
    Spec("weather", ["district_code", "date", "precip_mm", "temp_mean_c", "humidity_pct"],
         type_weather, Q.weather_rules, final_weather, ["district_code", "date"], False),
    Spec("encounters", ["encounter_id", "patient_id", "facility_id", "onset_date", "report_date", "age_years", "sex",
                        "icd10_code", "lab_confirmed", "admitted", "outcome"],
         type_encounters, Q.encounter_rules, final_encounters, ["encounter_id"], True,
         ("epi_year", "epi_week"), True),
    Spec("stock", ["snapshot_date", "facility_id"] + STOCK_COUNTS,
         type_stock, Q.stock_rules, final_stock, ["snapshot_date", "facility_id"], True, ("epi_year", "epi_week")),
]


# --------------------------------------------------------------------------------------
# Generic processing
# --------------------------------------------------------------------------------------
def bronze_batches(ctx, dataset):
    rows = [r for r in read_jsonl(ctx.bronze_root / "_ledger" / "batches.jsonl") if r["dataset"] == dataset]
    return sorted(rows, key=lambda r: (r["load_ts"], r["batch_id"]))


def processed_ids(ctx, dataset) -> set:
    return {r["bronze_batch_id"] for r in read_jsonl(ctx.silver_root / "_ledger" / "batches.jsonl")
            if r["dataset"] == dataset}


def read_bronze(ctx, dataset, batch_ids):
    base = ctx.bronze_root / dataset
    paths = [str(base / f"bronze_batch_id={b}") for b in batch_ids]
    return ctx.spark.read.option("basePath", str(base)).parquet(*paths)


def merge_partitions(ctx, new: DataFrame, name: str, key, part_cols):
    """Rewrite only the epi-weeks touched by `new`; newer rows replace older rows with the same key."""
    path = ctx.silver_path(name)
    staging = ctx.silver_root / "_staging" / name
    shutil.rmtree(staging, ignore_errors=True)
    affected = new.select(*part_cols).distinct().cache()
    n_parts = affected.count()
    updated = 0
    if path.exists():
        existing = ctx.spark.read.parquet(str(path))
        ex = existing.join(F.broadcast(affected), list(part_cols), "left_semi")
        updated = new.join(ex.select(*key).distinct(), list(key), "left_semi").count()
        combined = ex.unionByName(new, allowMissingColumns=True)
    else:
        combined = new
    latest_per_key(combined, key).write.mode("overwrite").parquet(str(staging))   # break read/write cycle
    final = ctx.spark.read.parquet(str(staging))
    (final.repartition(*part_cols).write.mode("overwrite").partitionBy(*part_cols).parquet(str(path)))
    shutil.rmtree(staging, ignore_errors=True)
    affected.unpersist()
    return {"partitions_rewritten": n_parts, "rows_updated_existing": updated}


def process(ctx: Ctx, spec: Spec):
    t0 = time.time()
    all_batches = bronze_batches(ctx, spec.name)
    done = processed_ids(ctx, spec.name)
    pending = [b["batch_id"] for b in all_batches if b["batch_id"] not in done]
    if not pending:
        print(f"  {spec.name:<11} nothing to do")
        return None

    read_ids = pending if spec.partitioned else [b["batch_id"] for b in all_batches]
    raw = ensure_columns(read_bronze(ctx, spec.name, read_ids), spec.columns)
    typed = spec.typer(raw, ctx)
    rules = spec.rules()
    error_rules = [r for r in rules if r.severity == "error"]
    reasons = F.filter(F.array(*[F.when(r.fail, F.lit(r.code)) for r in error_rules]), lambda x: x.isNotNull())
    typed = typed.withColumn("dq_reasons", reasons).persist(StorageLevel.MEMORY_AND_DISK)

    # ---- DQ report: one aggregation pass, one row per batch x rule
    aggs = [F.count(F.lit(1)).alias("rows_in"), F.sum((F.size("dq_reasons") > 0).cast("int")).alias("rows_quarantined")]
    aggs += [F.sum(r.fail.cast("int")).alias(r.code) for r in rules]
    per_batch = {row["bronze_batch_id"]: row.asDict() for row in typed.groupBy("bronze_batch_id").agg(*aggs).collect()}
    dq_rows, ledger_rows = [], []
    for b in pending:
        s = per_batch.get(b)
        if not s:
            continue
        n = int(s["rows_in"])
        for r in rules:
            failed = int(s[r.code] or 0)
            dq_rows.append((spec.name, b, r.code, r.severity, r.description, n, failed,
                            (n - failed) / n if n else 1.0, ctx.run_id, ctx.run_ts))
        ledger_rows.append({"dataset": spec.name, "bronze_batch_id": b, "rows_in": n,
                            "rows_quarantined": int(s["rows_quarantined"] or 0),
                            "silver_run_id": ctx.run_id, "silver_ts": ctx.run_ts})
    dq_schema = ("dataset string, bronze_batch_id string, rule_id string, severity string, description string, "
                 "rows_checked long, rows_failed long, pass_rate double, silver_run_id string, run_ts string")
    (ctx.spark.createDataFrame(dq_rows, dq_schema).withColumn("run_ts", c("run_ts").cast("timestamp"))
        .write.mode("overwrite").partitionBy("bronze_batch_id").parquet(str(ctx.silver_path("dq_report"))))

    # ---- quarantine (never contains the raw patient_id)
    raw_cols = [x for x in raw.columns if x != "patient_id"]
    q_cols = [c(x) for x in raw_cols]
    if spec.has_patient:
        q_cols.append(T.hash_id(c("patient_id"), ctx.salt).alias("patient_hash"))
    q_cols += [F.concat_ws(";", c("dq_reasons")).alias("reason_codes"),
               F.lit(ctx.run_ts).cast("timestamp").alias("quarantined_at")]
    quarantine = typed.filter(F.size("dq_reasons") > 0).select(*q_cols)
    (quarantine.write.mode("overwrite").partitionBy("bronze_batch_id")
        .parquet(str(ctx.silver_path("quarantine") / spec.name)))

    # ---- valid rows -> silver
    valid = spec.final(typed.filter(F.size("dq_reasons") == 0), ctx)
    n_valid = valid.count()
    new = latest_per_key(valid, spec.key).persist(StorageLevel.MEMORY_AND_DISK)
    n_new = new.count()
    stats = {"dataset": spec.name, "rows_valid": n_valid, "rows_deduplicated": n_valid - n_new}
    if spec.partitioned:
        stats.update(merge_partitions(ctx, new, spec.name, spec.key, spec.part_cols))
    else:
        new.coalesce(1).write.mode("overwrite").parquet(str(ctx.silver_path(spec.name)))
        stats.update({"partitions_rewritten": 0, "rows_updated_existing": 0})
    new.unpersist()
    typed.unpersist()

    stats["seconds"] = round(time.time() - t0, 1)
    stats["silver_run_id"] = ctx.run_id
    append_jsonl(ctx.silver_root / "_ledger" / "batches.jsonl", ledger_rows)
    append_jsonl(ctx.silver_root / "_ledger" / "runs.jsonl", [stats])

    rows_in = sum(r["rows_in"] for r in ledger_rows)
    rows_q = sum(r["rows_quarantined"] for r in ledger_rows)
    print(f"  [{stats['seconds']:6.1f}s] {spec.name:<11} in {rows_in:>10,} | quarantined {rows_q:>8,} | "
          f"valid {n_valid:>10,} | duplicates dropped {stats['rows_deduplicated']:>7,} | "
          f"replaced existing {stats['rows_updated_existing']:>6,} | epi-weeks rewritten {stats['partitions_rewritten']}")
    return stats


def run(bronze_root=DEFAULT_BRONZE, silver_root=DEFAULT_SILVER, salt=None, only=None, spark=None) -> list:
    if spark is None:
        from pipeline.spark_session import get_spark
        spark = get_spark(app_name="episcope-silver")
    salt = salt or privacy.load_salt()
    # keep the salt out of Spark UI / explain output
    spark.conf.set("spark.sql.redaction.string.regex", re.escape(salt))
    now = datetime.now(timezone.utc)
    ctx = Ctx(spark, Path(bronze_root), Path(silver_root), salt,
              run_id=now.strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6],
              run_ts=now.strftime("%Y-%m-%d %H:%M:%S"))
    print(f"silver run {ctx.run_id}")
    results = []
    for spec in SPECS:
        if only and spec.name != only:
            continue
        results.append(process(ctx, spec))
    return [r for r in results if r]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bronze-root", default=str(DEFAULT_BRONZE))
    ap.add_argument("--silver-root", default=str(DEFAULT_SILVER))
    ap.add_argument("--only", choices=[s.name for s in SPECS])
    a = ap.parse_args(argv)
    t0 = time.time()
    run(a.bronze_root, a.silver_root, only=a.only)
    print(f"silver done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    sys.exit(main())

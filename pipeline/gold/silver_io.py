"""Reads the silver layer built in the earlier session without assuming its exact column names.

For each logical column we accept several candidate names. If something required is missing,
SilverSchemaError lists what was found. Run `python -m pipeline.gold.build --check` to see the
mapping before building anything. Reference tables (districts, facilities, weather) are read from
data/reference/*.csv, the single source of truth for district codes.
"""
from __future__ import annotations

from pathlib import Path

ENC = {
    "encounter_id": ["encounter_id"],
    "facility_id": ["facility_id"],
    "district_code": ["district_code"],
    "disease": ["disease", "disease_group"],
    "icd10_code": ["icd10_code", "icd10"],
    "onset_date": ["onset_date"],
    "first_report_date": ["first_report_date"],
    "report_date": ["report_date"],
    "outcome": ["outcome"],
    "admitted": ["admitted"],
    "lab_confirmed": ["lab_confirmed"],
    "batch_id": ["bronze_batch_id", "batch_id", "last_batch_id", "_batch_id"],
    "load_ts": ["bronze_load_ts", "loaded_at", "load_ts", "ingested_at"],
}
COUNTS = ["iv_saline_units", "ns1_kits", "ors_sachets", "antimalarial_courses", "occupied_beds"]
STOCK = {"snapshot_date": ["snapshot_date"], "facility_id": ["facility_id"],
         **{c: [c] for c in COUNTS},
         "batch_id": ENC["batch_id"], "load_ts": ENC["load_ts"]}
QUAR = {"reason_code": ["reason_code", "reason_codes", "reason", "rule_id", "rule"],
        "source_feed": ["source_feed", "feed", "dataset", "source"],
        "batch_id": ENC["batch_id"]}


class SilverSchemaError(RuntimeError):
    pass


def resolve(columns, candidates):
    """{logical name: actual column or None}, case-insensitive."""
    lower = {c.lower(): c for c in columns}
    return {k: next((lower[a.lower()] for a in alts if a.lower() in lower), None)
            for k, alts in candidates.items()}


def discover_table(root, include, exclude=("quarantine", "reject", "dq_", "_dq")):
    """First sub-folder of `root` whose name contains `include` (and none of `exclude`)."""
    root = Path(root)
    if not root.exists():
        return None
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        n = d.name.lower()
        if include in n and not any(x in n for x in exclude):
            return d
    return None


def _posix(p):
    return str(p).replace("\\", "/")


def _select(raw, mapping, names):
    from pyspark.sql import functions as F
    return raw.select(*[F.col(mapping[k]).alias(k) for k in names if mapping.get(k)])


def load_encounters(spark, path):
    """Standard columns: encounter_id, facility_id, [district_code], disease, onset_date,
    first_report_date, outcome, admitted, lab_confirmed, batch_id, load_ts. -> (df, meta)"""
    from pyspark.sql import functions as F
    raw = spark.read.parquet(_posix(path))
    m = resolve(raw.columns, ENC)
    need = ["facility_id", "onset_date", "outcome", "admitted", "lab_confirmed"]
    missing = [k for k in need if not m[k]]
    if not (m["first_report_date"] or m["report_date"]):
        missing.append("first_report_date")
    if not (m["disease"] or m["icd10_code"]):
        missing.append("disease (or icd10_code)")
    if missing:
        raise SilverSchemaError(f"{path}: missing {missing}. Columns found: {raw.columns}")

    df = _select(raw, m, ENC)
    meta = dict(first_report_fallback=not m["first_report_date"], has_batch=bool(m["batch_id"]),
                has_load_ts=bool(m["load_ts"]))
    if meta["first_report_fallback"]:      # silver should keep first_report_date; report_date mis-times resubmissions
        df = df.withColumn("first_report_date", F.col("report_date"))
    if "disease" not in df.columns:
        code = F.upper(F.col("icd10_code"))
        df = df.withColumn("disease", F.when(code.rlike("^A9[01]"), "dengue").when(code.rlike("^A0[0-9]"), "diarrhoeal")
                                       .when(code.rlike("^J1[01]"), "ili").when(code.rlike("^B5[0-4]"), "malaria")
                                       .otherwise("other"))
    df = (df.withColumn("onset_date", F.to_date("onset_date"))
            .withColumn("first_report_date", F.to_date("first_report_date"))
            .withColumn("admitted", F.col("admitted").cast("boolean"))
            .withColumn("lab_confirmed", F.col("lab_confirmed").cast("boolean")))
    df = df.withColumn("batch_id", F.col("batch_id").cast("string") if m["batch_id"] else F.lit("unknown"))
    df = df.withColumn("load_ts", F.to_timestamp("load_ts") if m["load_ts"] else F.lit(None).cast("timestamp"))
    return df, meta


def load_stock(spark, path):
    from pyspark.sql import functions as F
    raw = spark.read.parquet(_posix(path))
    m = resolve(raw.columns, STOCK)
    missing = [k for k in ["snapshot_date", "facility_id", *COUNTS] if not m[k]]
    if missing:
        raise SilverSchemaError(f"{path}: missing {missing}. Columns found: {raw.columns}")
    df = _select(raw, m, STOCK).withColumn("snapshot_date", F.to_date("snapshot_date"))
    for c in COUNTS:
        df = df.withColumn(c, F.col(c).cast("long"))
    df = df.withColumn("batch_id", F.col("batch_id").cast("string") if m["batch_id"] else F.lit("unknown"))
    df = df.withColumn("load_ts", F.to_timestamp("load_ts") if m["load_ts"] else F.lit(None).cast("timestamp"))
    return df, dict(has_batch=bool(m["batch_id"]), has_load_ts=bool(m["load_ts"]))


def read_quarantine_raw(spark, path):
    """Quarantine is usually one sub-folder per feed (encounters, stock, ...) with different columns,
    which Spark cannot read as a single table. Read each non-empty sub-folder on its own, tag it with
    its folder name as source_feed when it has no feed column, and union by name. A flat quarantine
    folder is read as before."""
    from functools import reduce
    path = Path(path)

    def has_data(d):
        return any(f.is_file() and not f.name.startswith((".", "_")) and not f.name.endswith(".crc")
                   for f in d.rglob("*"))

    subs = sorted(d for d in path.iterdir() if d.is_dir() and not d.name.startswith((".", "_")) and has_data(d))
    if not subs:
        return spark.read.parquet(_posix(path))
    frames = []
    for d in subs:
        df = spark.read.parquet(_posix(d))
        if not resolve(df.columns, QUAR)["source_feed"]:
            from pyspark.sql import functions as F
            df = df.withColumn("source_feed", F.lit(d.name))
        frames.append(df)
    return reduce(lambda a, b: a.unionByName(b, allowMissingColumns=True), frames)


def load_quarantine(spark, path):
    from pyspark.sql import functions as F
    raw = read_quarantine_raw(spark, path)
    m = resolve(raw.columns, QUAR)
    if not m["reason_code"]:
        raise SilverSchemaError(f"{path}: no reason column. Columns found: {raw.columns}")
    df = _select(raw, m, QUAR)
    # a quarantined row can carry several reasons (column `reason_codes`): one row per reason, so each rule is counted on its own
    kind = df.schema["reason_code"].dataType.simpleString()
    if kind.startswith("array"):
        df = df.withColumn("reason_code", F.explode_outer(F.col("reason_code")))
    elif kind == "string":
        cleaned = F.regexp_replace(F.col("reason_code"), r"[\[\]'\"]", "")
        df = df.withColumn("reason_code", F.explode_outer(F.split(F.trim(cleaned), r"\s*[;,|]\s*")))
    df = df.withColumn("reason_code", F.trim(F.col("reason_code").cast("string")))
    df = df.withColumn("reason_code", F.when(F.col("reason_code") == "", None).otherwise(F.col("reason_code")))
    df = df.withColumn("reason_code", F.coalesce(F.col("reason_code"), F.lit("unknown")))
    for c, default in (("source_feed", "unknown"), ("batch_id", "unknown")):
        if c not in df.columns:
            df = df.withColumn(c, F.lit(default))
    return df.withColumn("batch_id", F.col("batch_id").cast("string")), dict(has_batch=bool(m["batch_id"]))


def load_reference(spark, ref_dir):
    """districts (district_code, population), facilities (facility_id, district_code, beds, icu_beds), weather."""
    from pyspark.sql import functions as F
    ref = Path(ref_dir)

    def rd(name):
        p = ref / name
        if not p.exists():
            raise SilverSchemaError(f"missing reference file {p}")
        return spark.read.option("header", "true").csv(_posix(p))

    districts = (rd("districts.csv").select("district_code", "district_name", "division",
                                            F.col("population").cast("long").alias("population")))
    facilities = (rd("facilities.csv").select("facility_id", "district_code",
                                              F.col("beds").cast("long").alias("beds"),
                                              F.col("icu_beds").cast("long").alias("icu_beds")).dropDuplicates(["facility_id"]))
    weather = (rd("weather.csv").select("district_code", F.to_date("date").alias("date"),
                                        F.col("precip_mm").cast("double").alias("precip_mm"),
                                        F.col("temp_mean_c").cast("double").alias("temp_mean_c"),
                                        F.col("humidity_pct").cast("double").alias("humidity_pct")))
    return districts, facilities, weather

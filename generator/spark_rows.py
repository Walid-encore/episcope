"""EpiScope generator, part 2: expand per-day case counts into S4 encounter rows with Spark.

Pure Spark SQL functions (no Python UDFs, so no Python workers are needed). Every random
decision is a hash of the row's key + the seed, so the output does not depend on how Spark
partitions the work: same inputs -> identical files on any machine.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from generator.bd_calendar import shift_table
from pipeline.spark_session import get_spark      # noqa: F401  (re-exported for generate.py)

MIN_ONSET = "2021-01-01"        # schema: onset_date not before this
LETTER = {"dengue": "D", "diarrhoeal": "R", "ili": "L", "malaria": "M"}   # other -> "O"

# ICD-10 mixes per disease group. Prefixes follow schema.md:
# dengue A90/A91 | diarrhoeal A00-A09 | ili J10/J11 | malaria B50-B54 | other = the rest.
ICD = {
    "dengue": [("A90", 0.62), ("A91", 0.38)],
    "diarrhoeal": [("A09", 0.33), ("A09.0", 0.08), ("A04.9", 0.14), ("A03.9", 0.09), ("A08.4", 0.12),
                   ("A00.9", 0.04), ("A02.0", 0.04), ("A06.0", 0.04), ("A01.0", 0.05), ("A05.9", 0.02),
                   ("A07.1", 0.05)],
    "ili": [("J11", 0.35), ("J11.1", 0.20), ("J10.1", 0.30), ("J10", 0.15)],
    "malaria": [("B50.9", 0.18), ("B51.9", 0.34), ("B54", 0.40), ("B52.9", 0.03), ("B53.8", 0.05)],
    "other": [("J18.9", 0.12), ("I10", 0.10), ("E11.9", 0.07), ("K29.7", 0.08), ("N39.0", 0.07),
              ("R50.9", 0.10), ("M54.5", 0.07), ("J06.9", 0.12), ("L30.9", 0.05), ("B34.9", 0.04),
              ("S09.9", 0.03), ("Z00.0", 0.05), ("A15.0", 0.03), ("D64.9", 0.04), ("H10.9", 0.03)],
}
BAD_ICD = ["XX1", "a90", "A9", "A90.", "99", "A09.99999"]
AGE_BANDS = [(0, 4), (5, 14), (15, 44), (45, 64), (65, 95)]
AGE_P = {"dengue": [.05, .22, .55, .14, .04], "diarrhoeal": [.38, .17, .25, .12, .08],
         "ili": [.18, .20, .35, .17, .10], "malaria": [.06, .14, .50, .22, .08],
         "other": [.10, .12, .33, .27, .18]}
P_LAB = {"dengue": .35, "diarrhoeal": .03, "ili": .08, "malaria": .70, "other": .05}
P_ADMIT = {"dengue": .28, "diarrhoeal": .12, "ili": .06, "malaria": .25, "other": .08}
P_DIED = {"dengue": .004, "diarrhoeal": .003, "ili": .002, "malaria": .006, "other": .004}

# defect rates (assignment: ~2% duplicates, ~0.5% orphans, ~1% invalid ages)
RATE_DUP, RATE_ORPHAN, RATE_BAD_AGE = 0.02, 0.005, 0.01
RATE_BAD_SEX, RATE_BAD_ICD, RATE_REPORT_BEFORE_ONSET, RATE_ONSET_TOO_EARLY = 0.005, 0.005, 0.002, 0.001


# --------------------------------------------------------------------------- helpers
def u(key, salt, seed):
    """Deterministic uniform in (0,1) from a hash of key + seed + salt."""
    h = F.xxhash64(key, F.lit(f"{seed}:{salt}"))
    return (F.pmod(h, F.lit(1 << 24)).cast("double") + F.lit(0.5)) / F.lit(float(1 << 24))


def choose(uc, options):
    """options = [(value, prob), ...] summing to 1 -> CASE WHEN on the uniform column."""
    cum, items = 0.0, []
    for v, p in options:
        cum += p
        items.append((v, cum))
    e = None
    for v, c in items[:-1]:
        e = F.when(uc < c, F.lit(v)) if e is None else e.when(uc < c, F.lit(v))
    return e.otherwise(F.lit(items[-1][0]))


def per_group(disease, table, builder):
    e = None
    for g, v in table.items():
        c = builder(v)
        e = F.when(disease == g, c) if e is None else e.when(disease == g, c)
    return e.otherwise(builder(table["other"]))


# --------------------------------------------------------------------------- main build
def build_encounters(spark, work_dir, seed, end_date, artefacts, report_from=None, report_to=None) -> DataFrame:
    work = str(work_dir).replace("\\", "/")
    cells = spark.read.parquet(f"{work}/cells.parquet").filter("n > 0")
    fac = spark.read.parquet(f"{work}/fac_arrays.parquet")
    end = F.lit(end_date).cast("date")

    df = (cells.join(F.broadcast(fac), "district_code")
          .select("district_code", F.to_date("onset_date").alias("onset"), "disease", "fac_ids", "cum_w",
                  F.explode(F.sequence(F.lit(1), F.col("n"))).alias("i")))
    D = F.col("disease")
    key = F.concat_ws("|", F.col("district_code"), F.date_format("onset", "yyyyMMdd"), D,
                      F.col("i").cast("string"))
    df = df.withColumn("key", key)

    # ---- ids and facility (weighted pick inside the district)
    df = df.withColumn("encounter_id", F.concat(
        F.lit("E"), F.date_format("onset", "yyyyMMdd"), F.col("district_code"),
        per_group(D, {**LETTER, "other": "O"}, F.lit), F.lpad(F.col("i").cast("string"), 6, "0")))
    df = df.withColumn("u_fac", u(F.col("key"), "fac", seed))
    df = df.withColumn("fidx", F.least(F.size(F.filter("cum_w", lambda x: x < F.col("u_fac"))),
                                       F.size("fac_ids") - 1))
    df = (df.withColumn("facility_id", F.element_at("fac_ids", F.col("fidx") + 1))
            .drop("fac_ids", "cum_w", "u_fac", "fidx"))
    df = df.withColumn("patient_id", F.lpad(
        F.pmod(F.xxhash64(F.col("key"), F.lit(f"{seed}:pid")), F.lit(10 ** 13)).cast("string"), 13, "0"))

    # ---- reporting delay: right-skewed (log-normal), 0..21 days, pushed past weekends/holidays
    u1, u2 = u(F.col("key"), "d1", seed), u(F.col("key"), "d2", seed)
    z = F.sqrt(-2 * F.log(u1)) * F.cos(F.lit(2 * 3.141592653589793) * u2)
    df = df.withColumn("delay", F.least(F.lit(21), F.floor(F.exp(F.lit(0.55) + F.lit(0.95) * z)).cast("int")))
    df = df.withColumn("rd0", F.expr("date_add(onset, delay)"))
    df = df.withColumn("shift", u(F.col("key"), "shift", seed) < 0.85)
    # ~85% of reports that would land on a weekend/holiday move to the next working day. A small
    # calendar table + broadcast join keeps the query plan tiny (nesting the rule blew it up).
    last = dt.date.fromisoformat(end_date) + dt.timedelta(days=30)
    cal = (spark.createDataFrame(shift_table(dt.date.fromisoformat(MIN_ONSET), last), "d string, s string")
           .select(F.to_date("d").alias("rd0"), F.to_date("s").alias("rd_shift")))
    df = df.join(F.broadcast(cal), "rd0", "left")
    df = (df.withColumn("rd", F.when(F.col("shift"), F.coalesce(F.col("rd_shift"), F.col("rd0")))
                               .otherwise(F.col("rd0")))
            .drop("rd0", "rd_shift"))
    df = df.withColumn("rd", F.least(F.col("rd"), F.expr("date_add(onset, 21)")))

    # ---- silent-then-backfill facilities: everything reported during the silence arrives in one day.
    # One CASE chain (facilities differ, so the rules never overlap) keeps the query plan small.
    held = None
    for a in artefacts:
        in_window = ((F.col("facility_id") == a["facility_id"])
                     & (F.col("rd") >= F.lit(a["silent_start"]).cast("date"))
                     & (F.col("rd") <= F.lit(a["silent_end"]).cast("date")))
        value = F.least(F.lit(a["backfill_date"]).cast("date"), F.expr("date_add(onset, 21)"))
        held = F.when(in_window, value) if held is None else held.when(in_window, value)
    if held is not None:
        df = df.withColumn("rd", held.otherwise(F.col("rd")))

    df = df.filter(F.col("rd") <= end)                      # reports that have not arrived yet are unseen

    # ---- clinical fields
    band = per_group(D, AGE_P, lambda p: choose(u(F.col("key"), "band", seed), list(enumerate(p))))
    lo = F.element_at(F.array(*[F.lit(b[0]) for b in AGE_BANDS]), band + 1)
    hi = F.element_at(F.array(*[F.lit(b[1]) for b in AGE_BANDS]), band + 1)
    age_ok = (lo + F.floor(u(F.col("key"), "age", seed) * (hi - lo + 1))).cast("int")
    ub = u(F.col("key"), "agebad", seed)
    age_bad = F.when(u(F.col("key"), "agebad2", seed) < 0.7,
                     (F.lit(121) + F.floor(u(F.col("key"), "agebad3", seed) * 130)).cast("int")) \
        .otherwise((-(F.lit(1) + F.floor(u(F.col("key"), "agebad3", seed) * 9))).cast("int"))
    df = df.withColumn("age_ok", age_ok).withColumn(
        "age_years", F.when(ub < RATE_BAD_AGE, age_bad).otherwise(age_ok))

    sex_ok = F.when(u(F.col("key"), "sex", seed) < 0.5, "M").otherwise("F")
    sex_bad = choose(u(F.col("key"), "sexbad2", seed), [("X", 1 / 3), ("U", 1 / 3), ("Unknown", 1 / 3)])
    df = df.withColumn("sex", F.when(u(F.col("key"), "sexbad", seed) < RATE_BAD_SEX, sex_bad).otherwise(sex_ok))

    icd_ok = per_group(D, ICD, lambda opts: choose(u(F.col("key"), "icd", seed), opts))
    icd_bad = F.element_at(F.array(*[F.lit(c) for c in BAD_ICD]),
                           (F.floor(u(F.col("key"), "icdbad2", seed) * len(BAD_ICD)) + 1).cast("int"))
    df = df.withColumn("icd10_code", F.when(u(F.col("key"), "icdbad", seed) < RATE_BAD_ICD, icd_bad).otherwise(icd_ok))

    df = df.withColumn("lab_confirmed", u(F.col("key"), "lab", seed) < per_group(D, P_LAB, F.lit))
    adm_p = per_group(D, P_ADMIT, F.lit) * F.when((F.col("age_ok") < 5) | (F.col("age_ok") >= 65), 1.6).otherwise(1.0)
    df = df.withColumn("admitted", u(F.col("key"), "adm", seed) < adm_p)

    uo = u(F.col("key"), "outcome", seed)
    p_died = per_group(D, P_DIED, F.lit) * F.when(F.col("admitted"), 4.0).otherwise(0.5)
    p_ref = F.when(F.col("admitted"), 0.08).otherwise(0.03)
    p_ong = F.when(F.col("delay") <= 2, 0.25).otherwise(0.06)
    df = df.withColumn("outcome", F.when(uo < p_died, "died").when(uo < p_died + p_ref, "referred")
                                   .when(uo < p_died + p_ref + p_ong, "ongoing").otherwise("recovered"))

    # ---- date defects (quarantine material)
    ud = u(F.col("key"), "baddate", seed)
    df = df.withColumn("report_date", F.when(
        ud < RATE_REPORT_BEFORE_ONSET,
        F.expr("date_sub(onset, cast(1 + floor(" + _sql_u("bd2", seed) + " * 5) as int))")).otherwise(F.col("rd")))
    df = df.withColumn("onset_date", F.when(
        (ud >= RATE_REPORT_BEFORE_ONSET) & (ud < RATE_REPORT_BEFORE_ONSET + RATE_ONSET_TOO_EARLY),
        F.expr(f"date_sub(to_date('{MIN_ONSET}'), cast(1 + floor(" + _sql_u("bd3", seed) + " * 300) as int))"))
        .otherwise(F.col("onset")))

    enc = df.select("encounter_id", "patient_id", "facility_id", "onset_date", "report_date", "age_years",
                    "sex", "icd10_code", "lab_confirmed", "admitted", "outcome", "onset", "delay")

    # ---- duplicates: ~2% of encounters are re-submitted with the SAME encounter_id.
    # A second copy is produced by exploding, so the query plan is not duplicated by a union.
    uid = lambda s: u(F.col("encounter_id"), s, seed)
    enc = enc.withColumn("copy", F.explode(F.sequence(F.lit(1), F.when(uid("dup") < RATE_DUP, 2).otherwise(1))))
    is_revised = (F.col("copy") == 2) & (uid("dupkind") < 0.5)       # copy 2 is a revision half the time
    gap = (F.lit(1) + F.floor(uid("dupgap") * 7)).cast("int")
    enc = (enc.withColumn("gap", gap)
              .withColumn("report_date", F.when(is_revised, F.least(
                  F.expr("date_add(onset, 21)"), F.least(F.expr("date_add(report_date, gap)"), end)))
                  .otherwise(F.col("report_date")))
              .withColumn("outcome", F.when(is_revised & (F.col("outcome") == "ongoing"), "recovered")
                                      .otherwise(F.col("outcome")))
              .drop("gap", "copy"))

    # ---- orphan keys: ~0.5% of encounters point at a facility that does not exist
    orphan = F.concat(F.lit("FX"), F.lpad((F.floor(uid("orph2") * 90000) + 10000).cast("string"), 5, "0"))
    enc = enc.withColumn("facility_id", F.when(uid("orphan") < RATE_ORPHAN, orphan).otherwise(F.col("facility_id")))

    if report_from:
        enc = enc.filter(F.col("report_date") >= F.lit(report_from).cast("date"))
    if report_to:
        enc = enc.filter(F.col("report_date") <= F.lit(report_to).cast("date"))

    enc = enc.withColumn("batch_week", F.date_format(F.date_trunc("week", F.col("report_date")), "yyyy-MM-dd"))
    return enc.select("encounter_id", "patient_id", "facility_id", "onset_date", "report_date", "age_years",
                      "sex", "icd10_code", "lab_confirmed", "admitted", "outcome", "batch_week")


def _sql_u(salt, seed):
    """SQL text of u(key, salt) for use inside F.expr (the key column is named `key`)."""
    return f"((pmod(xxhash64(`key`, '{seed}:{salt}'), 16777216) + 0.5) / 16777216.0)"


def write_encounters(enc: DataFrame, out_dir):
    path = str(out_dir).replace("\\", "/")
    (enc.repartition(F.col("batch_week")).write.mode("overwrite").partitionBy("batch_week")
        .option("header", "true").csv(path))
    for crc in Path(out_dir).rglob("*.crc"):          # Spark's hidden checksum files
        crc.unlink()
    for marker in Path(out_dir).rglob("_SUCCESS"):
        marker.unlink()


# --------------------------------------------------------------------------- verification
def verify(spark, enc_dir, fac_csv):
    """Read the written files back and print the realised defect rates."""
    path = str(enc_dir).replace("\\", "/")
    df = (spark.read.option("header", "true").option("recursiveFileLookup", "true").csv(path)
          .withColumn("onset_date", F.to_date("onset_date")).withColumn("report_date", F.to_date("report_date")))
    fac = spark.read.option("header", "true").csv(str(fac_csv).replace("\\", "/")).select("facility_id")
    code = F.col("icd10_code")
    grp = (F.when(code.rlike("^A9[01]"), "dengue").when(code.rlike("^A0[0-9]"), "diarrhoeal")
            .when(code.rlike("^J1[01]"), "ili").when(code.rlike("^B5[0-4]"), "malaria").otherwise("other"))
    total = df.count()
    ids = df.select("encounter_id").distinct().count()
    orphans = df.join(F.broadcast(fac), "facility_id", "left_anti").count()
    age = F.col("age_years").cast("int")
    stats = df.agg(
        F.sum(((age < 0) | (age > 120)).cast("int")).alias("bad_age"),
        F.sum((~F.col("sex").isin("M", "F")).cast("int")).alias("bad_sex"),
        F.sum((F.col("report_date") < F.col("onset_date")).cast("int")).alias("report_before_onset"),
        F.sum((F.col("onset_date") < F.lit(MIN_ONSET).cast("date")).cast("int")).alias("onset_too_early"),
        F.sum((~code.rlike(r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")).cast("int")).alias("bad_icd"),
        F.avg(F.datediff("report_date", "onset_date").cast("double")).alias("mean_delay"),
        F.min("onset_date").alias("min_onset"), F.max("report_date").alias("max_report")).first()
    print(f"\nrows {total:,} | distinct encounter_id {ids:,} | duplicate rows {1 - ids / total:.2%}")
    print(f"orphan facility rows {orphans / total:.2%} | bad age {stats.bad_age / total:.2%} | "
          f"bad sex {stats.bad_sex / total:.2%} | bad ICD {stats.bad_icd / total:.2%}")
    print(f"report<onset {stats.report_before_onset / total:.2%} | onset<2021 {stats.onset_too_early / total:.2%} | "
          f"mean delay {stats.mean_delay:.2f} d | onset from {stats.min_onset} | last report {stats.max_report}")
    print("disease mix (from ICD-10):")
    for r in df.withColumn("g", grp).groupBy("g").count().orderBy(F.desc("count")).collect():
        print(f"  {r['g']:<11} {r['count']:>12,}  {r['count'] / total:6.1%}")

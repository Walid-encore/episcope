"""Silver-layer tests: quality rules, de-identification, late-data merge, idempotency (needs PySpark)."""
import hashlib
import time
from pathlib import Path

import pytest

pytest.importorskip("pyspark")
from pyspark.sql import functions as F  # noqa: E402

from pipeline.bronze import ingest as B  # noqa: E402
from pipeline.quality import rules as Q  # noqa: E402
from pipeline.silver import build as S  # noqa: E402
from pipeline.silver import privacy  # noqa: E402
from pipeline.silver import transforms as T  # noqa: E402
from pipeline.spark_session import get_spark  # noqa: E402

SALT = "unit-test-salt-0123456789abcdef"
ENC_HDR = "encounter_id,patient_id,facility_id,onset_date,report_date,age_years,sex,icd10_code,lab_confirmed,admitted,outcome\n"
DISTRICTS = ("district_code,district_name,division,population,centroid_lat,centroid_lon\n"
             "D1,Alpha,Dhaka,1000000,23.8,90.4\nD2,Beta,Sylhet,500000,24.9,91.8\n")
FACILITIES = ("facility_id,facility_name,facility_type,district_code,latitude,longitude,beds,icu_beds\n"
              "F1,Hosp A,hospital,D1,23.8,90.4,100,10\nF2,Clinic B,clinic,D2,24.9,91.8,20,0\n")
ENC_ROWS = [
    "E1,P1,F1,2024-03-04,2024-03-06,30,M,A90,true,false,recovered",
    "E1,P1,F1,2024-03-04,2024-03-06,30,M,A90,true,false,recovered",    # exact resubmission
    "E2,P2,F1,2024-03-05,2024-03-06,212,F,A90,true,false,recovered",   # AGE_INVALID
    "E3,P3,F9,2024-03-05,2024-03-06,30,F,A90,true,false,recovered",    # FACILITY_ORPHAN
    "E4,P4,F2,2024-03-10,2024-03-11,5,X,A91,false,true,ongoing",       # sex repaired to U, kept
    "E5,P5,F1,2024-03-10,2024-03-01,30,M,A90,true,false,recovered",    # REPORT_BEFORE_ONSET
    "E6,P6,F1,2020-12-31,2021-01-02,30,M,A90,true,false,recovered",    # ONSET_BEFORE_MIN_DATE
    "E7,P7,F1,2024-03-05,2024-03-06,30,M,XYZ,true,false,recovered",    # ICD10_INVALID
]


def w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture(scope="module")
def spark():
    return get_spark(app_name="episcope-tests")


@pytest.fixture()
def lake(tmp_path, spark):
    src, bronze, silver = tmp_path / "in", tmp_path / "bronze", tmp_path / "silver"
    w(src / "ref" / "districts.csv", DISTRICTS)
    w(src / "ref" / "facilities.csv", FACILITIES)
    w(src / "day1" / "enc.csv", ENC_HDR + "\n".join(ENC_ROWS) + "\n")
    B.run([src], bronze)
    return src, bronze, silver


def test_rule_catalogue_has_at_least_12_rules(spark):
    assert len(Q.encounter_rules()) >= 12


def test_epi_week_is_iso(spark):
    df = spark.createDataFrame([("2024-03-04",), ("2021-01-03",), ("2024-12-30",)], "d string").select(F.to_date("d").alias("d"))
    got = {r["d"].isoformat(): (r["y"], r["w"]) for r in
           df.select("d", T.epi_year(F.col("d")).alias("y"), T.epi_week(F.col("d")).alias("w")).collect()}
    assert got["2024-03-04"] == (2024, 10)
    assert got["2021-01-03"] == (2020, 53)
    assert got["2024-12-30"] == (2025, 1)


def test_salt_is_generated_once_and_never_the_placeholder(tmp_path, monkeypatch):
    monkeypatch.delenv("EPISCOPE_SALT", raising=False)
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nEPISCOPE_SALT=change-me-to-a-long-random-string\n", encoding="ascii")
    s1 = privacy.load_salt(env)
    assert len(s1) >= 32 and not s1.startswith("change-me")
    assert privacy.load_salt(env) == s1
    assert "OTHER=1" in env.read_text()


def test_silver_rules_hashing_and_quarantine(lake, spark):
    _src, bronze, silver = lake
    S.run(bronze, silver, salt=SALT, spark=spark)
    enc = spark.read.parquet(str(silver / "encounters"))

    assert "patient_id" not in enc.columns and "age_years" not in enc.columns
    assert enc.count() == 2                                            # E1 once, E4
    rows = {r["encounter_id"]: r for r in enc.collect()}
    assert set(rows) == {"E1", "E4"}
    assert rows["E1"]["patient_hash"] == hashlib.sha256(f"{SALT}|P1".encode()).hexdigest()
    assert rows["E1"]["district_code"] == "D1" and rows["E1"]["age_band"] == "25-44"
    assert rows["E1"]["disease"] == "dengue" and rows["E1"]["epi_week"] == 10
    assert rows["E4"]["sex"] == "U"                                    # repaired, not quarantined

    q = {r["encounter_id"]: r["reason_codes"] for r in spark.read.parquet(str(silver / "quarantine" / "encounters")).collect()}
    assert q == {"E2": "AGE_INVALID", "E3": "FACILITY_ORPHAN", "E5": "REPORT_BEFORE_ONSET",
                 "E6": "ONSET_BEFORE_MIN_DATE", "E7": "ICD10_INVALID"}
    qdf = spark.read.parquet(str(silver / "quarantine" / "encounters"))
    assert "patient_id" not in qdf.columns and "patient_hash" in qdf.columns

    dq = spark.read.parquet(str(silver / "dq_report")).filter("dataset = 'encounters'")
    failed = {r["rule_id"]: r["rows_failed"] for r in dq.collect()}
    assert failed["AGE_INVALID"] == 1 and failed["SEX_INVALID"] == 1 and failed["FACILITY_ORPHAN"] == 1


def test_idempotent_rerun_and_late_update(lake, spark):
    src, bronze, silver = lake
    S.run(bronze, silver, salt=SALT, spark=spark)
    assert S.run(bronze, silver, salt=SALT, spark=spark) == []         # nothing pending

    # Late file: a NEW encounter and a corrected version of E1 (same onset week)
    w(src / "day2" / "late.csv", ENC_HDR
      + "E8,P8,F1,2024-03-04,2024-03-20,40,F,A91,false,true,ongoing\n"
      + "E1,P1,F1,2024-03-04,2024-03-06,30,M,A90,true,true,died\n")
    time.sleep(1.1)   # make the late batch's load time strictly newer
    B.run([src], bronze)
    res = S.run(bronze, silver, salt=SALT, spark=spark)
    stats = next(r for r in res if r["dataset"] == "encounters")
    assert stats["rows_updated_existing"] == 1
    enc = spark.read.parquet(str(silver / "encounters"))
    assert enc.count() == 3                                            # E1, E4, E8: no duplicates
    e1 = enc.filter("encounter_id = 'E1'").collect()
    assert len(e1) == 1 and e1[0]["outcome"] == "died" and e1[0]["admitted"] is True
    e8 = enc.filter("encounter_id = 'E8'").collect()[0]
    assert e8["delay_days"] == 16 and e8["epi_week"] == 10             # late record lands in its onset week

"""Tests for the bronze layer. The Spark test is skipped when PySpark is not installed."""
import json
from pathlib import Path

import pytest

from pipeline.bronze import ingest as B


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


ENC = "encounter_id,patient_id,facility_id,onset_date,report_date,age_years,sex,icd10_code,lab_confirmed,admitted,outcome\n"
ENC_ROW = "E1,P1,F1,2024-01-01,2024-01-03,30,M,A90,true,false,recovered\n"
STOCK_LINE = json.dumps({"snapshot_date": "2024-01-01", "facility_id": "F1", "iv_saline_units": 5,
                         "ns1_kits": 2, "ors_sachets": 9, "antimalarial_courses": 1, "occupied_beds": 3})


def test_discovery_skips_hidden_and_underscore(tmp_path):
    write(tmp_path / "a" / "part-0.csv", ENC + ENC_ROW)
    write(tmp_path / "a" / ".part-0.csv.crc", "x")
    write(tmp_path / "a" / ".hidden.csv", ENC)
    write(tmp_path / "a" / "_SUCCESS", "")
    write(tmp_path / "_tmp" / "b.csv", ENC)
    write(tmp_path / "deep" / "er" / "x.jsonl", STOCK_LINE + "\n")
    found = sorted(p.name for p in B.discover([tmp_path]))
    assert found == ["part-0.csv", "x.jsonl"]


def test_classify_by_content_not_name(tmp_path):
    enc = write(tmp_path / "anything.csv", ENC + ENC_ROW)
    stock = write(tmp_path / "whatever.jsonl", STOCK_LINE + "\n")
    fac = write(tmp_path / "f.csv", "facility_id,facility_name,facility_type,district_code,latitude,longitude,beds,icu_beds\nF1,x,clinic,BD-01,1,1,5,1\n")
    junk = write(tmp_path / "junk.csv", "a,b,c\n1,2,3\n")
    empty = write(tmp_path / "empty.csv", "")
    assert B.classify_file(enc)[0] == "encounters"
    assert B.classify_file(stock)[0] == "stock"
    assert B.classify_file(fac)[0] == "facilities"
    assert B.classify_file(junk) is None
    assert B.classify_file(empty) is None


def test_batch_id_is_deterministic_and_order_independent():
    assert B.batch_id_for(["a", "b"]) == B.batch_id_for(["b", "a"])
    assert B.batch_id_for(["a", "b"]) != B.batch_id_for(["a", "c"])


def test_checksum_changes_with_content(tmp_path):
    p = write(tmp_path / "x.csv", ENC + ENC_ROW)
    c1 = B.sha256_file(p)
    p.write_text(ENC + ENC_ROW + ENC_ROW, encoding="utf-8")
    assert B.sha256_file(p) != c1


def test_dry_run_loads_nothing(tmp_path):
    src = tmp_path / "in"
    write(src / "e.csv", ENC + ENC_ROW)
    out = tmp_path / "bronze"
    s = B.run([src], out, dry_run=True)
    assert s["new_files"] == 1 and not (out / "encounters").exists()


def test_idempotent_rerun_and_new_late_file(tmp_path):
    pytest.importorskip("pyspark")
    from pyspark.sql import SparkSession
    src, out = tmp_path / "in", tmp_path / "bronze"
    write(src / "day1" / "a.csv", ENC + ENC_ROW)
    write(src / "day1" / "b.jsonl", STOCK_LINE + "\n")

    s1 = B.run([src], out)
    assert {b["dataset"] for b in s1["batches"]} == {"encounters", "stock"}

    # Re-run: nothing new, nothing written
    s2 = B.run([src], out)
    assert s2["new_files"] == 0 and s2["batches"] == []

    # Renamed copy of an already-loaded file is still recognised (checksum), no duplicate rows
    write(src / "day1_copy" / "renamed.csv", ENC + ENC_ROW)
    s3 = B.run([src], out)
    assert s3["new_files"] == 0

    # A genuinely new (late) file becomes a second batch
    write(src / "day2" / "late.csv", ENC + "E2,P2,F1,2024-01-02,2024-01-20,40,F,A91,false,true,ongoing\n")
    s4 = B.run([src], out)
    assert s4["new_files"] == 1

    spark = SparkSession.getActiveSession()
    df = spark.read.parquet(str(out / "encounters"))
    assert df.count() == 2
    assert {"bronze_source_file", "bronze_load_ts", "bronze_batch_id", "bronze_checksum"} <= set(df.columns)
    assert df.filter("bronze_checksum is null").count() == 0
    lines = (out / "_ledger" / "files.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3

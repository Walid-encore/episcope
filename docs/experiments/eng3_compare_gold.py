"""ENG-3 correctness check, NOT part of the repo.

Compares two gold folders table by table: row counts plus an exact two-way row difference (exceptAll).
Usage (venv active):  python C:\\episcope_clone\\compare_gold.py <gold_folder_A> <gold_folder_B>
run_ts is ignored: the reconciliation rows in gold_dq_report carry the wall-clock time of each run.
"""
import os
import sys
from pathlib import Path

os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

from pyspark.sql import SparkSession

IGNORE = {"run_ts"}

if len(sys.argv) != 3:
    sys.exit("usage: python compare_gold.py <gold_folder_A> <gold_folder_B>")
a_root, b_root = Path(sys.argv[1]), Path(sys.argv[2])

spark = (SparkSession.builder.master("local[*]").appName("compare-gold")
         .config("spark.driver.memory", "4g")
         .config("spark.ui.showConsoleProgress", "false").getOrCreate())
spark.sparkContext.setLogLevel("ERROR")


def posix(p):
    return str(p).replace("\\", "/")


bad = 0
for t in sorted(d.name for d in a_root.iterdir() if d.is_dir()):
    if not (b_root / t).exists():
        print(f"{t:<28} MISSING in {b_root}")
        bad += 1
        continue
    a = spark.read.parquet(posix(a_root / t))
    b = spark.read.parquet(posix(b_root / t))
    cols = [c for c in a.columns if c not in IGNORE]
    if set(cols) != {c for c in b.columns if c not in IGNORE}:
        print(f"{t:<28} DIFFERENT COLUMNS: {a.columns} vs {b.columns}")
        bad += 1
        continue
    a, b = a.select(*cols), b.select(*cols)
    na, nb = a.count(), b.count()
    only_a, only_b = a.exceptAll(b).count(), b.exceptAll(a).count()
    same = na == nb and only_a == 0 and only_b == 0
    bad += 0 if same else 1
    print(f"{t:<28} rows {na:>9,} vs {nb:>9,} | only in A: {only_a:,} | only in B: {only_b:,} | "
          f"{'IDENTICAL' if same else 'DIFFERENT'}", flush=True)

print("\nRESULT:", "all tables identical" if bad == 0 else f"{bad} table(s) differ")
spark.stop()
sys.exit(1 if bad else 0)

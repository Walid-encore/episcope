"""ENG-3 scratch test, NOT part of the repo.

Question: why does collecting a few hundred Python-built rows take ~10 s in the gold stage?
Run from the activated venv:  python C:\\episcope_clone\\pyrdd_test.py
Each variant is run three times; the first run of a variant can include warm-up.
"""
import os
import sys
import time

# PySpark defaults to a "python3" executable, which does not exist on Windows: use this venv's python
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

from pyspark.sql import SparkSession

spark = (SparkSession.builder.master("local[*]").appName("pyrdd-test")
         .config("spark.driver.memory", "2g")
         .config("spark.ui.showConsoleProgress", "false").getOrCreate())
spark.sparkContext.setLogLevel("ERROR")
spark.range(1).collect()                                  # warm-up: JVM and session start are not measured

rows = [(i, f"w{i}") for i in range(262)]                 # about the size of the week grid
SCHEMA = "i int, s string"


def timed(label, make, runs=3):
    secs = []
    try:
        for _ in range(runs):
            t0 = time.perf_counter()
            n = len(make().collect())
            secs.append(time.perf_counter() - t0)
    except Exception as e:                                   # show the failure instead of dying silently
        print(f"{label:<52} FAILED: {str(e)[:300]}", flush=True)
        return
    print(f"{label:<52} rows={n}  runs (s): " + ", ".join(f"{x:.2f}" for x in secs), flush=True)


print("defaultParallelism =", spark.sparkContext.defaultParallelism, flush=True)
timed("A createDataFrame(list), default partitions",
      lambda: spark.createDataFrame(rows, SCHEMA))
timed("B createDataFrame(parallelize(list, 1)), 1 partition",
      lambda: spark.createDataFrame(spark.sparkContext.parallelize(rows, 1), SCHEMA))
timed("C spark.range(262), no Python involved",
      lambda: spark.range(262).selectExpr("cast(id as int) as i", "concat('w', id) as s"))
timed("D sequence() weekly dates, native SQL",
      lambda: spark.sql("SELECT explode(sequence(to_date('2021-01-04'), to_date('2025-12-29'), "
                        "INTERVAL 7 DAYS)) AS week_start_date"))
spark.stop()

import os, sys
from pyspark.sql import SparkSession

def get_spark(app_name="episcope", driver_mem=None):
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    mem = driver_mem or os.environ.get("SPARK_DRIVER_MEMORY", "4g")
    return (
        SparkSession.builder.master("local[*]").appName(app_name)
        .config("spark.driver.memory", mem)
        .config("spark.sql.shuffle.partitions", "16")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .config("spark.ui.showConsoleProgress", "false")
        .getOrCreate()
    )

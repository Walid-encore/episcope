import os, sys
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
from pyspark.sql import SparkSession

spark = SparkSession.builder.master("local[*]").appName("test").getOrCreate()
df = spark.range(1000000).withColumnRenamed("id", "n")
df.write.mode("overwrite").parquet("C:/episcope/test_parquet")
print("ROWS READ BACK:", spark.read.parquet("C:/episcope/test_parquet").count())
spark.stop()
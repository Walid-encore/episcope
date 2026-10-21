import pandas as pd
from pipeline.spark_session import get_spark

s = get_spark()
df = s.createDataFrame(pd.DataFrame({"g": [1, 1, 2], "v": [1.0, 2.0, 3.0]}))
out = df.groupBy("g").applyInPandas(
    lambda p: p.assign(m=p.v.mean()), "g long, v double, m double"
)
out.show()
s.stop()

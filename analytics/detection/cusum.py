"""One-sided upper CUSUM (Page 1954) on baseline-standardised counts - AN-1, AN-3.

z(t)     = same standardised score as EARS C2 (7-week baseline, 2-week guard band)
S(t)     = max(0, S(t-1) + min(z(t), z_cap) - k)          score = S(t)
Alert when S(t) >= h (the threshold; default 5).
k  = allowance (default 1.0). The baseline lags a rising season, so z is positive for weeks even without
     an outbreak; with k=0.5 that drift piled up in S and false alerts did not fall as h rose. k=1.0 stops it.
     Defaults come from the 4-outbreak fixture only: RE-TUNE k and h on the real generated data.
z_cap stops one huge value (e.g. a back-fill spike) from dominating the sum.
S is NOT reset after an alert, so the score stays continuous for the threshold sweep.
Weeks without a baseline have z = NaN and contribute 0. So does an artefact-flagged (back-fill) week when
mask=True: the spike is excluded from the sum, not only from the baselines. Under-reporting weeks are NOT
masked: their low counts are real evidence of nothing unusual and masking them only raised S.
CUSUM accumulates small persistent rises, so it should catch slow-growth outbreaks that EARS C2 misses.
"""
import numpy as np

from .common import (KEYS, ORDER, add_artefact_flag_pandas, add_artefact_flag_spark,
                     baseline_z_pandas, baseline_z_spark, finalize_pandas, finalize_spark)


def cusum_scores(z, k=0.5, z_cap=6.0):
    s, out = 0.0, np.empty(len(z))
    for i, v in enumerate(z):
        v = 0.0 if np.isnan(v) else min(v, z_cap)
        s = max(0.0, s + v - k)
        out[i] = s
    return out


def cusum_pandas(df, threshold=5.0, k=1.0, z_cap=6.0, baseline=7, guard=2, min_n=5,
                 min_std=1.0, under_ratio=0.85, mask=True, run_id="local"):
    d = add_artefact_flag_pandas(df, under_ratio)
    d = baseline_z_pandas(d, baseline, guard, min_n, min_std, mask)
    if mask:
        d.loc[d["reporting_artefact_flag"], "z"] = np.nan
    d["score"] = d.groupby(KEYS)["z"].transform(
        lambda z: pd_series_cusum(z, k, z_cap))
    return finalize_pandas(d, "cusum", threshold, run_id)


def pd_series_cusum(z, k, z_cap):
    import pandas as pd
    return pd.Series(cusum_scores(z.to_numpy(dtype=float), k, z_cap), index=z.index)


def cusum_spark(sdf, threshold=5.0, k=1.0, z_cap=6.0, baseline=7, guard=2, min_n=5,
                min_std=1.0, under_ratio=0.85, mask=True, run_id="local"):
    """Windows give z; the recursion runs per district x disease with applyInPandas."""
    from pyspark.sql.types import DoubleType, StructField, StructType
    d = add_artefact_flag_spark(sdf, under_ratio)
    d = baseline_z_spark(d, baseline, guard, min_n, min_std, mask)
    if mask:
        from pyspark.sql import functions as F
        d = d.withColumn("z", F.when(~F.col("reporting_artefact_flag"), F.col("z")))
    schema = StructType(d.schema.fields + [StructField("score", DoubleType(), True)])

    def run(pdf):
        pdf = pdf.sort_values(ORDER).copy()
        pdf["score"] = cusum_scores(pdf["z"].to_numpy(dtype=float), k, z_cap)
        return pdf

    d = d.groupBy(*KEYS).applyInPandas(run, schema)
    return finalize_spark(d, "cusum", threshold, run_id)

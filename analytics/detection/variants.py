"""Alternative EARS C2 baseline for the sensitivity analysis (conflict C1, AN-3). Never reads ground truth.

ears_c2_usable_pandas gives the same score as ears_c2, but the baseline is the `baseline` most recent
USABLE weeks (not under-reporting, not a back-fill week) among the weeks at least guard + 1 weeks before t,
instead of a fixed calendar window. A stretch of masked weeks no longer leaves the baseline too thin to
score: the window reaches further back. With nothing masked it is identical to ears_c2.
The Spark version (ears_c2_usable_spark) gives the same scores: per series it collects the usable weeks up to
t - (guard + 1), keeps the last `baseline` of them and computes mean and sample std from that array.
Like the pandas version it assumes consecutive weeks per district x disease (dense gold marts).
"""
import numpy as np

from .common import (KEYS, ORDER, add_artefact_flag_pandas, add_artefact_flag_spark,
                     finalize_pandas, finalize_spark)


def usable_baseline_z_pandas(df, baseline=7, guard=2, min_n=5, min_std=1.0):
    """df must come from add_artefact_flag_pandas (sorted, with _under and reporting_artefact_flag)."""
    d = df.copy()
    usable = d["cases"].astype(float).where(~d["_under"] & ~d["reporting_artefact_flag"]).to_numpy()
    cases = d["cases"].astype(float).to_numpy()
    z = np.full(len(d), np.nan)
    for _, pos in d.groupby(KEYS, sort=False).indices.items():
        v, c = usable[pos], cases[pos]
        out = np.full(len(pos), np.nan)
        for t in range(len(pos)):
            hist = v[: max(t - guard, 0)]                 # weeks 0 .. t-(guard+1)
            hist = hist[~np.isnan(hist)][-baseline:]      # the most recent usable ones
            if len(hist) >= min_n:
                out[t] = (c[t] - hist.mean()) / max(hist.std(ddof=1), min_std)
        z[pos] = out
    d["z"] = z
    return d


def ears_c2_usable_pandas(df, threshold=3.0, baseline=7, guard=2, min_n=5, min_std=1.0,
                          under_ratio=0.85, run_id="local"):
    d = add_artefact_flag_pandas(df, under_ratio)
    d = usable_baseline_z_pandas(d, baseline, guard, min_n, min_std)
    d["score"] = d["z"]
    return finalize_pandas(d, "ears_c2_usable", threshold, run_id)


# ---------------------------------------------------------------- Spark version
def usable_baseline_z_spark(sdf, baseline=7, guard=2, min_n=5, min_std=1.0):
    """sdf must come from add_artefact_flag_spark (has _under and reporting_artefact_flag)."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F
    w = (Window.partitionBy(*KEYS).orderBy(ORDER)
         .rowsBetween(Window.unboundedPreceding, -(guard + 1)))
    cases = F.col("cases").cast("double")
    usable = F.when(~F.col("_under") & ~F.col("reporting_artefact_flag"), cases)
    d = sdf.withColumn("_v", usable)
    # a struct is never null itself, so wrap it in when(_v is not null): collect_list then skips masked weeks.
    # Sorting the structs by week fixes the order explicitly.
    d = d.withColumn("_hist", F.sort_array(F.collect_list(
        F.when(F.col("_v").isNotNull(), F.struct(F.col(ORDER).alias("_w"), F.col("_v")))).over(w)))
    d = d.withColumn("_hist", F.expr("transform(_hist, x -> x._v)"))
    d = d.withColumn("_hist", F.expr(f"slice(_hist, greatest(size(_hist) - {int(baseline)} + 1, 1), {int(baseline)})"))
    d = d.withColumn("_n", F.size("_hist"))
    d = d.withColumn("_mean", F.expr("aggregate(_hist, cast(0 as double), (a, x) -> a + x) / nullif(size(_hist), 0)"))
    d = d.withColumn("_sd", F.expr(
        "sqrt(aggregate(_hist, cast(0 as double), (a, x) -> a + (x - _mean) * (x - _mean)) / nullif(size(_hist) - 1, 0))"))
    z = (cases - F.col("_mean")) / F.greatest(F.col("_sd"), F.lit(float(min_std)))
    return d.withColumn("z", F.when(F.col("_n") >= min_n, z)).drop("_v", "_hist", "_n", "_mean", "_sd")


def ears_c2_usable_spark(sdf, threshold=3.0, baseline=7, guard=2, min_n=5, min_std=1.0,
                         under_ratio=0.85, run_id="local"):
    d = add_artefact_flag_spark(sdf, under_ratio)
    d = usable_baseline_z_spark(d, baseline, guard, min_n, min_std)
    d = d.withColumnRenamed("z", "score")
    return finalize_spark(d, "ears_c2_usable", threshold, run_id)

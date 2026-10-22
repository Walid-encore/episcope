"""Shared pieces for the detectors (AN-1, AN-3).

Input table (gold contract v0.3): detection runs on REPORT weeks, built by gold_input.py from
gold_weekly_reports + gold_reporting_status. Needed columns:
    district_code, disease, epi_year, epi_week, week_start_date, cases (= reported_cases),
    reporting_completeness   (share of usual volume that reported; null during warm-up)
Legacy input (fixtures): reporting_facilities and expected_facilities are used when
reporting_completeness is absent.
Weeks must be consecutive per district x disease (gold marts should be dense; fill gaps with 0 upstream).

Detectors NEVER read ground truth. Only evaluate.py may.

Reporting-artefact rule (the silent-then-backfill facility):
    under(t) = reporting_completeness(t) < under_ratio        (null completeness = no information = not under)
    legacy:  reporting_facilities(t) < under_ratio * expected_facilities
    reporting_artefact_flag(t) = under(t-1) AND NOT under(t)     # first full week after under-reporting
Weeks that are under-reporting, or flagged as artefacts, are left OUT of every later baseline
(treated as missing), so neither the silent weeks nor the back-fill spike distort future scores.
alert_flag stays RAW (before suppression) so the dashboard can show "suppressed: reporting artefact".
evaluate.py counts only alert_flag AND NOT reporting_artefact_flag.
"""
import numpy as np
import pandas as pd

KEYS = ["district_code", "disease"]
ORDER = "week_start_date"
OUT_COLS = ["district_code", "disease", "epi_year", "epi_week", "week_start_date", "detector",
            "score", "threshold", "alert_flag", "severity", "reporting_artefact_flag", "run_id"]


# ---------------------------------------------------------------- pandas versions
def add_artefact_flag_pandas(df, under_ratio=0.85):
    df = df.sort_values(KEYS + [ORDER]).reset_index(drop=True).copy()
    if "reporting_completeness" in df.columns:
        df["_under"] = df["reporting_completeness"].astype(float) < under_ratio  # NaN -> False
    else:
        df["_under"] = df["reporting_facilities"] < under_ratio * df["expected_facilities"]
    prev = df.groupby(KEYS)["_under"].shift(1).eq(True)
    df["reporting_artefact_flag"] = prev & ~df["_under"]
    return df


def baseline_z_pandas(df, baseline=7, guard=2, min_n=5, min_std=1.0, mask=True):
    """z = (cases - baseline mean) / max(baseline std, min_std).
    Baseline = the `baseline` weeks ending `guard` weeks before t (EARS C2: 7 weeks, 2-week guard band)."""
    df = df.copy()
    if mask:
        valid = df["cases"].astype(float).where(~df["_under"] & ~df["reporting_artefact_flag"])
    else:
        valid = df["cases"].astype(float)
    df["_v"] = valid
    shift = guard + 1
    g = df.groupby(KEYS)["_v"]
    mean = g.transform(lambda x: x.shift(shift).rolling(baseline, min_periods=min_n).mean())
    std = g.transform(lambda x: x.shift(shift).rolling(baseline, min_periods=min_n).std())
    df["z"] = (df["cases"] - mean) / np.maximum(std, min_std)
    return df.drop(columns=["_v"])


def finalize_pandas(df, detector, threshold, run_id):
    thr = float(threshold)
    score = df["score"]
    flag = (score >= thr).fillna(False)
    sev = np.select([flag & (score >= 2 * thr), flag & (score >= 1.5 * thr), flag],
                    ["high", "medium", "low"], default=None)
    out = df.copy()
    out["detector"] = detector
    out["threshold"] = thr
    out["alert_flag"] = flag.astype(bool)
    out["severity"] = pd.Series(sev, index=df.index, dtype=object).where(flag, None)
    out["run_id"] = run_id
    return out[OUT_COLS]


# ---------------------------------------------------------------- Spark versions
def add_artefact_flag_spark(sdf, under_ratio=0.85):
    from pyspark.sql import Window
    from pyspark.sql import functions as F
    w = Window.partitionBy(*KEYS).orderBy(ORDER)
    if "reporting_completeness" in sdf.columns:
        under = F.coalesce(F.col("reporting_completeness") < F.lit(under_ratio), F.lit(False))
    else:
        under = F.col("reporting_facilities") < F.lit(under_ratio) * F.col("expected_facilities")
    sdf = sdf.withColumn("_under", under)
    prev = F.coalesce(F.lag("_under", 1).over(w), F.lit(False))
    return sdf.withColumn("reporting_artefact_flag", prev & ~F.col("_under"))


def baseline_z_spark(sdf, baseline=7, guard=2, min_n=5, min_std=1.0, mask=True):
    from pyspark.sql import Window
    from pyspark.sql import functions as F
    w = Window.partitionBy(*KEYS).orderBy(ORDER).rowsBetween(-(guard + baseline), -(guard + 1))
    cases = F.col("cases").cast("double")
    valid = F.when(~F.col("_under") & ~F.col("reporting_artefact_flag"), cases) if mask else cases
    sdf = sdf.withColumn("_v", valid)
    sdf = (sdf.withColumn("_mean", F.avg("_v").over(w))
              .withColumn("_sd", F.stddev_samp("_v").over(w))
              .withColumn("_n", F.count("_v").over(w)))
    z = (cases - F.col("_mean")) / F.greatest(F.col("_sd"), F.lit(float(min_std)))
    return sdf.withColumn("z", F.when(F.col("_n") >= min_n, z)).drop("_v", "_mean", "_sd", "_n")


def finalize_spark(sdf, detector, threshold, run_id):
    from pyspark.sql import functions as F
    thr = F.lit(float(threshold))
    s = F.col("score")
    flag = F.coalesce(s >= thr, F.lit(False))
    sev = (F.when(flag & (s >= 2 * thr), "high")
            .when(flag & (s >= 1.5 * thr), "medium")
            .when(flag, "low"))
    return sdf.select(
        "district_code", "disease", "epi_year", "epi_week", "week_start_date",
        F.lit(detector).alias("detector"), "score", thr.alias("threshold"),
        flag.alias("alert_flag"), sev.alias("severity"),
        "reporting_artefact_flag", F.lit(run_id).alias("run_id"))

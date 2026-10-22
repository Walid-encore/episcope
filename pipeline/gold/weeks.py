"""ISO-8601 week helpers (Monday start; epi_year is the ISO YEAR). Pure-Python parts need no Spark."""
from __future__ import annotations

import datetime as dt


def monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def week_list(first: dt.date, last: dt.date):
    """Every Monday from the week of `first` to the week of `last`, inclusive."""
    start, end = monday(first), monday(last)
    return [start + dt.timedelta(days=7 * i) for i in range((end - start).days // 7 + 1)]


def iso_year_week(d: dt.date):
    y, w, _ = d.isocalendar()
    return y, w


# ---- Spark versions (pyspark imported lazily so the helpers above stay importable without it)
def add_week_cols(df, date_col: str, prefix: str = ""):
    """Adds {prefix}week_start_date (Monday), {prefix}epi_year (ISO year), {prefix}epi_week (1-53)."""
    from pyspark.sql import functions as F
    d = F.col(date_col)
    ws = F.date_trunc("week", d).cast("date")                      # Spark truncates to Monday
    return (df.withColumn(f"{prefix}week_start_date", ws)
              .withColumn(f"{prefix}epi_year", F.year(F.date_add(ws, 3)))   # ISO year = year of the week's Thursday
              .withColumn(f"{prefix}epi_week", F.weekofyear(d)))


def week_grid(spark, first: dt.date, last: dt.date, prefix: str = ""):
    """One row per week with {prefix}week_start_date, {prefix}epi_year, {prefix}epi_week."""
    df = spark.createDataFrame([(d,) for d in week_list(first, last)], "d date")
    return add_week_cols(df, "d", prefix).drop("d")

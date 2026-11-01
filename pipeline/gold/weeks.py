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
    """One row per week with {prefix}week_start_date, {prefix}epi_year, {prefix}epi_week.

    Built inside Spark (sequence + explode), same weeks as week_list(first, last). A DataFrame made from a
    Python list needs one Python worker process per partition (16 by default), about 0.65 s each on Windows,
    so collecting this grid took ~10 s for 262 rows (ENG-3, docs/experiments)."""
    from pyspark.sql import functions as F
    start, end = monday(first), monday(last)
    if end < start:                                                 # same as week_list: no weeks
        days = spark.range(0).select(F.to_date(F.lit("1970-01-01")).alias("d"))
    else:                                                           # ISO strings: no timezone conversion of a Python date
        days = spark.range(1).select(F.explode(F.sequence(
            F.to_date(F.lit(start.isoformat())), F.to_date(F.lit(end.isoformat())),
            F.expr("INTERVAL 7 DAYS"))).alias("d"))
    return add_week_cols(days, "d", prefix).drop("d")

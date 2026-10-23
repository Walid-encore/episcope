"""Spark / pandas switch and timing for the heavy analytics stages (AN-1, AN-4, ENG-2).

Spark is the default engine for the real run (AN-1 and AN-4 require it). pandas stays available
(--engine pandas) for quick local runs and is the reference the Spark parity tests compare against.
Every run appends one row to docs/experiments/analytics_timing.csv, which the Data Engineer can use
for the ENG-2 scalability table.
"""
import csv
import datetime as dt
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMING = ROOT / "docs" / "experiments" / "analytics_timing.csv"
FORECAST_COLS = ["district_code", "disease", "week_start_date", "cases", "precip_sum_mm", "temp_mean_c"]


@contextmanager
def timed(stage, engine, rows, series):
    t0 = time.perf_counter()
    yield
    sec = time.perf_counter() - t0
    TIMING.parent.mkdir(parents=True, exist_ok=True)
    new = not TIMING.exists()
    with TIMING.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["run_at", "stage", "engine", "seconds", "rows", "series"])
        w.writerow([dt.datetime.now().isoformat(timespec="seconds"), stage, engine, round(sec, 2), rows, series])
    print(f"[timing] {stage} on {engine}: {sec:.1f}s ({rows:,} rows, {series} series)")


def on_spark(fn, pdf, app_name):
    """Run fn(spark DataFrame) -> spark DataFrame on pdf and return a pandas DataFrame."""
    try:
        import inspect
        from pipeline.spark_session import get_spark
        params = inspect.signature(get_spark).parameters
        name_kw = next((n for n in ("app_name", "appName", "name") if n in params), None)
        spark = get_spark(**({name_kw: app_name} if name_kw else {}))
    except Exception as e:
        raise SystemExit(f"Spark is not available ({type(e).__name__}: {e}). "
                         "Fix Java / HADOOP_HOME, or re-run with --engine pandas.")
    try:
        return fn(spark.createDataFrame(pdf)).toPandas()
    finally:
        spark.stop()


def run_forecast(table, engine="spark", **kw):
    """Per-district x disease forecasts (AN-4). table: cases + weather columns, one row per series-week."""
    series = table.groupby(["district_code", "disease"]).ngroups
    with timed("forecast", engine, len(table), series):
        if engine == "spark":
            from analytics.forecast.forecast import run_spark
            return on_spark(lambda s: run_spark(s, **kw), table[FORECAST_COLS], "episcope-forecast")
        from analytics.forecast.forecast import run_pandas
        return run_pandas(table, **kw)

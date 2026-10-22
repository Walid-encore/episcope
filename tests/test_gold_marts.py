"""Spark tests for pipeline/gold/marts.py. Every expected value is worked out by hand in the comments.
Needs pyspark. Run: python -m pytest tests/test_gold_marts.py -q"""
import datetime as dt
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyspark")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.gold import marts as M                                   # noqa: E402
from pipeline.gold import silver_io as S                               # noqa: E402
from pipeline.gold.weeks import add_week_cols, week_grid               # noqa: E402
from pipeline.spark_session import get_spark                           # noqa: E402

D = dt.date
ENC_SCHEMA = ("facility_id string, district_code string, disease string, onset_date date, first_report_date date, "
              "outcome string, admitted boolean, lab_confirmed boolean, batch_id string, load_ts timestamp")
TS = lambda day, h=10: dt.datetime(2021, 1, day, h, 0)                 # noqa: E731


@pytest.fixture(scope="module")
def spark():
    return get_spark("2g", 4, app_name="gold-test")


def enc_df(spark, rows):
    return spark.createDataFrame(rows, ENC_SCHEMA)


def districts_df(spark):
    return spark.createDataFrame([("01", 1_000_000), ("02", 500_000)], "district_code string, population long")


def weeks_for(spark, enc):
    lo, hi = M.date_range(enc, "onset_date")
    return week_grid(spark, lo, hi)


def row(fac="A", dist="01", disease="dengue", onset=D(2021, 1, 4), rep=D(2021, 1, 5), outcome="recovered",
        admitted=False, lab=False, batch="b1", ts=TS(6)):
    return (fac, dist, disease, onset, rep, outcome, admitted, lab, batch, ts)


def test_iso_week_columns_match_python_around_year_ends(spark):
    days = [D(y, 12, 15) + dt.timedelta(d) for y in (2020, 2024, 2026) for d in range(0, 40)]
    df = spark.createDataFrame([(d,) for d in days], "d date")
    got = {r["d"]: (r["epi_year"], r["epi_week"], r["week_start_date"]) for r in add_week_cols(df, "d").collect()}
    for d in days:
        y, w, _ = d.isocalendar()
        assert got[d] == (y, w, d - dt.timedelta(days=d.weekday())), d


def test_weekly_cases_values_dense_grid_and_provenance(spark):
    enc = enc_df(spark, [
        row(onset=D(2021, 1, 4), rep=D(2021, 1, 5), outcome="died", admitted=True, lab=True, batch="b1", ts=TS(6)),
        row(onset=D(2021, 1, 6), rep=D(2021, 1, 12), batch="b2", ts=TS(13)),
        row(disease="diarrhoeal", onset=D(2021, 1, 11), rep=D(2021, 1, 30), batch="b2", ts=TS(31)),
        row(disease="other", onset=D(2021, 1, 4)),                          # not a gold disease: excluded
    ])
    out = M.weekly_cases(spark, enc, districts_df(spark), ["dengue", "diarrhoeal"], weeks_for(spark, enc))
    r = {(x["district_code"], x["disease"], x["week_start_date"]): x for x in out.collect()}
    assert len(r) == 2 * 2 * 2                                              # 2 districts x 2 diseases x 2 weeks, all present
    d1 = r[("01", "dengue", D(2021, 1, 4))]
    assert (d1["epi_year"], d1["epi_week"]) == (2021, 1)
    assert (d1["cases"], d1["lab_confirmed_cases"], d1["admitted_cases"], d1["deaths"]) == (2, 1, 1, 1)
    assert d1["population"] == 1_000_000 and d1["incidence_per_100k"] == pytest.approx(0.2)   # 2 / 1e6 * 1e5
    assert d1["last_batch_id"] == "b2" and d1["updated_at"] == TS(13)       # latest load wins
    assert r[("01", "dengue", D(2021, 1, 11))]["cases"] == 0                # zero is a real row
    assert r[("01", "diarrhoeal", D(2021, 1, 11))]["cases"] == 1
    assert r[("02", "dengue", D(2021, 1, 4))]["cases"] == 0
    assert r[("02", "dengue", D(2021, 1, 4))]["last_batch_id"] is None


def test_early_january_belongs_to_the_previous_iso_year(spark):
    enc = enc_df(spark, [row(onset=D(2021, 1, 1), rep=D(2021, 1, 2))])      # Friday = ISO 2020-W53
    out = M.weekly_cases(spark, enc, districts_df(spark), ["dengue"], weeks_for(spark, enc)).collect()
    hit = [x for x in out if x["district_code"] == "01"]
    assert [(x["epi_year"], x["epi_week"], x["week_start_date"], x["cases"]) for x in hit] == [(2020, 53, D(2020, 12, 28), 1)]


def test_cases_by_lag_adds_up_to_weekly_cases(spark):
    enc = enc_df(spark, [
        row(onset=D(2021, 1, 4), rep=D(2021, 1, 5)),                       # same week            -> lag 0
        row(onset=D(2021, 1, 6), rep=D(2021, 1, 12)),                      # next week            -> lag 1
        row(disease="diarrhoeal", onset=D(2021, 1, 11), rep=D(2021, 1, 30)),   # 2 weeks later    -> lag 2
        row(onset=D(2021, 1, 5), rep=D(2021, 3, 15)),                      # 10 weeks later       -> capped at lag 4
    ])
    weeks, dis = weeks_for(spark, enc), ["dengue", "diarrhoeal"]
    lag = M.cases_by_lag(spark, enc, districts_df(spark), dis, weeks).collect()
    got = {(x["district_code"], x["disease"], x["week_start_date"], x["report_lag_weeks"]): x["cases_new"] for x in lag}
    assert got[("01", "dengue", D(2021, 1, 4), 0)] == 1
    assert got[("01", "dengue", D(2021, 1, 4), 1)] == 1
    assert got[("01", "dengue", D(2021, 1, 4), 4)] == 1
    assert got[("01", "diarrhoeal", D(2021, 1, 11), 2)] == 1
    assert len(got) == 2 * 2 * 2 * 5                                        # dense: every lag 0..4 present
    cases = M.weekly_cases(spark, enc, districts_df(spark), dis, weeks).collect()
    for c in cases:                                                         # the contract's reconciliation property
        total = sum(v for (d, ds, w, _), v in got.items() if (d, ds, w) == (c["district_code"], c["disease"], c["week_start_date"]))
        assert total == c["cases"]


def test_weekly_reports_uses_report_week_and_counts_late_cases(spark):
    enc = enc_df(spark, [
        row(onset=D(2021, 1, 1), rep=D(2021, 1, 12)),                      # 11 days late
        row(onset=D(2021, 1, 10), rep=D(2021, 1, 12)),                     # 2 days
    ])
    lo, hi = M.date_range(enc, "first_report_date")
    out = M.weekly_reports(spark, enc, districts_df(spark), ["dengue"], week_grid(spark, lo, hi, prefix="report_")).collect()
    x = [r for r in out if r["district_code"] == "01"][0]
    assert x["report_week_start_date"] == D(2021, 1, 11) and (x["report_epi_year"], x["report_epi_week"]) == (2021, 2)
    assert (x["reported_cases"], x["late_cases_ge8d"]) == (2, 1)


def test_silent_facility_lowers_completeness_but_small_gaps_do_not(spark):
    """A: 20 cases a week, silent in weeks 11-13. B: 5 a week. Completeness while A is silent = 5 / 25 = 0.2."""
    start, rows = D(2021, 3, 1), []
    for i in range(14):
        rep = start + dt.timedelta(days=7 * i + 1)
        n_a = 0 if i >= 11 else 20
        rows += [row(fac="A", onset=rep, rep=rep, batch="b1") for _ in range(n_a)]
        rows += [row(fac="B", onset=rep, rep=rep, batch="b1") for _ in range(5)]
    enc = enc_df(spark, rows)
    fac = spark.createDataFrame([("A", "01", 100, 5), ("B", "01", 30, 0)],
                                "facility_id string, district_code string, beds long, icu_beds long")
    lo, hi = M.date_range(enc, "first_report_date")
    out = M.reporting_status(spark, enc, fac, week_grid(spark, lo, hi, prefix="report_")).orderBy("report_week_start_date").collect()
    assert len(out) == 14 and all(r["expected_facilities"] == 2 for r in out)
    comp = [r["reporting_completeness"] for r in out]
    assert comp[:8] == [None] * 8                                           # warm-up: needs 8 prior weeks
    assert comp[8:11] == [pytest.approx(1.0)] * 3
    assert comp[11:] == [pytest.approx(0.2)] * 3                            # the artefact is visible
    assert [r["reporting_facilities"] for r in out[11:]] == [1, 1, 1]
    assert out[8]["usual_volume_total"] == pytest.approx(25.0)


def test_weekly_weather_sums_means_and_lags(spark):
    days = [D(2021, 1, 4) + dt.timedelta(d) for d in range(21)]
    rows = [("01", d, float(1 + i // 7), 20.0 + 2 * (i // 7), 80.0) for i, d in enumerate(days)]
    w = spark.createDataFrame(rows, "district_code string, date date, precip_mm double, temp_mean_c double, humidity_pct double")
    out = {r["week_start_date"]: r for r in M.weekly_weather(w).collect()}
    assert out[D(2021, 1, 4)]["precip_sum_mm"] == 7.0 and out[D(2021, 1, 11)]["precip_sum_mm"] == 14.0
    assert out[D(2021, 1, 18)]["temp_mean_c"] == 24.0 and out[D(2021, 1, 4)]["days_observed"] == 7
    assert out[D(2021, 1, 11)]["precip_sum_mm_lag1"] == 7.0 and out[D(2021, 1, 4)]["precip_sum_mm_lag1"] is None
    assert out[D(2021, 1, 18)]["temp_mean_c_lag2"] == 20.0


def test_stock_weekly_takes_last_snapshot_and_sums_districts(spark):
    sch = ("snapshot_date date, facility_id string, iv_saline_units long, ns1_kits long, ors_sachets long, "
           "antimalarial_courses long, occupied_beds long, batch_id string, load_ts timestamp")
    rows = []
    for i in range(7):                                                      # Mon..Sun, values grow during the week
        d = D(2021, 1, 4) + dt.timedelta(i)
        rows += [(d, "A", i + 1, 5, 5, 5, 10 + i, "b1", TS(11)), (d, "B", 10 + i, 7, 7, 7, 20, "b1", TS(11))]
    stock = spark.createDataFrame(rows, sch)
    fac = spark.createDataFrame([("A", "01", 100, 5), ("B", "01", 30, 0), ("C", "01", 10, 0)],
                                "facility_id string, district_code string, beds long, icu_beds long")
    r = M.stock_weekly(stock, fac).collect()
    assert len(r) == 1
    assert r[0]["iv_saline_units"] == 7 + 16 and r[0]["occupied_beds"] == 16 + 20      # Sunday values
    assert r[0]["facilities_reporting"] == 2 and r[0]["beds_capacity"] == 140 and r[0]["icu_beds_capacity"] == 5


def test_dq_report_counts_failures_per_batch_and_rule(spark):
    enc = enc_df(spark, [row(batch="b1") for _ in range(7)] + [row(batch="b2") for _ in range(3)])
    stock = spark.createDataFrame([], "snapshot_date date, facility_id string, batch_id string")
    q = spark.createDataFrame([("b1", "encounters", "BAD_AGE")] * 2 + [("b1", "encounters", "BAD_SEX"), ("b2", "encounters", "BAD_AGE")],
                              "batch_id string, source_feed string, reason_code string")
    meta = {"has_batch": True}
    out = {(r["batch_id"], r["reason_code"]): r for r in
           M.dq_report(spark, q, meta, enc, meta, stock, meta, dt.datetime(2021, 1, 1)).collect()}
    a = out[("b1", "BAD_AGE")]
    assert (a["rows_failed"], a["rows_checked"], a["rule_id"]) == (2, 10, "encounters:BAD_AGE")   # 7 valid + 3 quarantined
    assert a["pass_rate"] == pytest.approx(0.8)
    assert out[("b2", "BAD_SEX")]["rows_failed"] == 0 and out[("b2", "BAD_SEX")]["pass_rate"] == pytest.approx(1.0)


def test_lineage_lists_the_batches_behind_each_week(spark):
    enc = enc_df(spark, [row(batch="b1", ts=TS(6)), row(onset=D(2021, 1, 5), rep=D(2021, 1, 12), batch="b2", ts=TS(13))])
    stock = spark.createDataFrame([], "snapshot_date date, facility_id string, batch_id string, load_ts timestamp")
    rows = {(r["gold_table"], r["epi_week"], r["batch_id"]) for r in M.lineage(enc, stock, ["dengue"]).collect()}
    assert ("gold_weekly_cases", 1, "b1") in rows and ("gold_weekly_cases", 1, "b2") in rows
    assert ("gold_weekly_reports", 2, "b2") in rows and ("gold_reporting_status", 1, "b1") in rows


def test_marts_are_deterministic(spark):
    enc = enc_df(spark, [row(onset=D(2021, 1, 4) + dt.timedelta(i), rep=D(2021, 1, 5) + dt.timedelta(i), ts=TS(6 + i % 3)) for i in range(20)])
    weeks, dis = weeks_for(spark, enc), ["dengue", "diarrhoeal"]
    make = lambda: sorted(map(tuple, M.weekly_cases(spark, enc, districts_df(spark), dis, weeks).collect()), key=str)   # noqa: E731
    assert make() == make()                                                 # updated_at is data-derived, so reruns match


def test_load_encounters_handles_the_other_sessions_column_names(spark, tmp_path):
    rows = [("E1", "A", "A91", D(2021, 1, 4), D(2021, 1, 5), D(2021, 1, 5), "recovered", "false", "true", "hash1",
             "bh1", dt.datetime(2021, 1, 6, 1, 0))]
    sch = ("encounter_id string, facility_id string, icd10_code string, onset_date date, first_report_date date, "
           "report_date date, outcome string, admitted string, lab_confirmed string, patient_hash string, "
           "bronze_batch_id string, bronze_load_ts timestamp")
    spark.createDataFrame(rows, sch).write.parquet(str(tmp_path / "silver_encounters"))
    df, meta = S.load_encounters(spark, tmp_path / "silver_encounters")
    r = df.first()
    assert r["disease"] == "dengue" and r["batch_id"] == "bh1" and r["lab_confirmed"] is True and r["admitted"] is False
    assert meta["has_batch"] and not meta["first_report_fallback"] and "district_code" not in df.columns
    spark.createDataFrame([("x",)], "foo string").write.parquet(str(tmp_path / "bad"))
    with pytest.raises(S.SilverSchemaError):
        S.load_encounters(spark, tmp_path / "bad")

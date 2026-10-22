"""Gold tests that need no Spark: week arithmetic, ISO-year formula, silver column discovery."""
import datetime as dt
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.gold.silver_io import ENC, QUAR, STOCK, discover_table, resolve   # noqa: E402
from pipeline.gold.weeks import iso_year_week, monday, week_list                # noqa: E402


def test_spark_iso_year_formula_matches_python_for_every_day_2019_to_2031():
    """add_week_cols uses: epi_year = year(Monday + 3 days). Prove it equals the ISO year for all days."""
    d = dt.date(2019, 1, 1)
    while d <= dt.date(2031, 12, 31):
        assert (monday(d) + dt.timedelta(days=3)).year == iso_year_week(d)[0], d
        assert monday(d).weekday() == 0 and 0 <= (d - monday(d)).days <= 6
        d += dt.timedelta(days=1)


def test_known_iso_boundaries():
    assert iso_year_week(dt.date(2021, 1, 1)) == (2020, 53)        # Friday: still ISO week 53 of 2020
    assert iso_year_week(dt.date(2021, 1, 4)) == (2021, 1)
    assert iso_year_week(dt.date(2024, 12, 30)) == (2025, 1)       # Monday in December belongs to 2025-W01
    assert iso_year_week(dt.date(2026, 12, 31)) == (2026, 53)


def test_week_list_is_dense_mondays():
    weeks = week_list(dt.date(2021, 1, 1), dt.date(2021, 1, 20))
    assert weeks[0] == dt.date(2020, 12, 28) and weeks[-1] == dt.date(2021, 1, 18)
    assert all(w.weekday() == 0 for w in weeks)
    assert all((b - a).days == 7 for a, b in zip(weeks, weeks[1:]))
    assert week_list(dt.date(2021, 1, 4), dt.date(2021, 1, 4)) == [dt.date(2021, 1, 4)]


def test_resolve_maps_the_other_sessions_column_names():
    cols = ["encounter_id", "Facility_ID", "icd10_code", "onset_date", "first_report_date", "report_date",
            "outcome", "admitted", "lab_confirmed", "bronze_batch_id", "bronze_load_ts", "patient_hash"]
    m = resolve(cols, ENC)
    assert m["facility_id"] == "Facility_ID" and m["batch_id"] == "bronze_batch_id" and m["load_ts"] == "bronze_load_ts"
    assert m["district_code"] is None and m["disease"] is None and m["icd10_code"] == "icd10_code"
    assert resolve(["snapshot_date", "facility_id", "ns1_kits"], STOCK)["ns1_kits"] == "ns1_kits"
    assert resolve(["reason", "feed"], QUAR) == {"reason_code": "reason", "source_feed": "feed", "batch_id": None}


def test_discover_table_finds_encounters_not_quarantine():
    root = Path(tempfile.mkdtemp())
    for n in ("quarantine", "silver_encounters_quarantine", "silver_encounters", "stock_silver", "dq_results"):
        (root / n).mkdir()
    assert discover_table(root, "encounter").name == "silver_encounters"
    assert discover_table(root, "stock").name == "stock_silver"
    assert discover_table(root, "quarantine", exclude=()).name == "quarantine"
    assert discover_table(root / "missing", "encounter") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)

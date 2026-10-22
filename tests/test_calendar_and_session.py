"""Tests for generator/bd_calendar.py and pipeline/spark_session.py (no Spark needed)."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generator.bd_calendar import HOLIDAYS, is_off_day, shift_table   # noqa: E402
from pipeline.spark_session import default_driver_memory              # noqa: E402


def old_nested_rule(d: dt.date, flag=True) -> dt.date:
    """The previous Spark logic: four times 'if off-day, move one day forward'."""
    r = d
    for _ in range(4):
        if flag and is_off_day(r):
            r += dt.timedelta(days=1)
    return r


def test_calendar_table_equals_old_nested_loop():
    rows = shift_table(dt.date(2021, 1, 1), dt.date(2027, 12, 31))
    assert len(rows) == (dt.date(2027, 12, 31) - dt.date(2021, 1, 1)).days + 1
    for d_iso, s_iso in rows:
        d = dt.date.fromisoformat(d_iso)
        assert dt.date.fromisoformat(s_iso) == old_nested_rule(d)


def test_shift_never_moves_more_than_4_days_or_backwards():
    for d_iso, s_iso in shift_table(dt.date(2021, 1, 1), dt.date(2026, 12, 31)):
        gap = (dt.date.fromisoformat(s_iso) - dt.date.fromisoformat(d_iso)).days
        assert 0 <= gap <= 4


def test_weekend_and_holiday_rules():
    assert is_off_day(dt.date(2023, 6, 16))            # Friday
    assert is_off_day(dt.date(2023, 6, 17))            # Saturday
    assert not is_off_day(dt.date(2023, 6, 18))        # Sunday is a working day
    assert "2022-02-21" in HOLIDAYS and is_off_day(dt.date(2022, 2, 21))   # Language Martyrs' Day (a Monday)
    table = dict(shift_table(dt.date(2023, 6, 14), dt.date(2023, 6, 18)))
    assert table["2023-06-16"] == "2023-06-18" and table["2023-06-17"] == "2023-06-18"


def test_default_driver_memory_scales_with_ram_and_is_clamped():
    gb = 2 ** 30
    assert default_driver_memory(4 * gb) == "2g"       # floor
    assert default_driver_memory(8 * gb) == "3g"
    assert default_driver_memory(16 * gb) == "6g"
    assert default_driver_memory(64 * gb) == "8g"      # ceiling
    assert default_driver_memory(0) == "3g"             # unknown RAM -> safe default


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)

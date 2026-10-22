"""Bangladesh working calendar for the generator: Friday/Saturday weekend plus public holidays.

Pure Python (no Spark) so it can be unit-tested. spark_rows.py joins against shift_table()
instead of nesting a "push to next working day" expression four times, which used to blow
up the Spark query plan (tens of MB of task binary).
"""
from __future__ import annotations

import datetime as dt

_FIXED = ["02-21", "03-26", "04-14", "05-01", "12-16", "12-25"]
_EID = {2021: [("05-12", "05-16"), ("07-20", "07-24")], 2022: [("05-01", "05-05"), ("07-09", "07-13")],
        2023: [("04-20", "04-24"), ("06-27", "07-01")], 2024: [("04-09", "04-13"), ("06-15", "06-19")],
        2025: [("03-29", "04-02"), ("06-05", "06-09")], 2026: [("03-19", "03-23"), ("05-26", "05-30")]}


def _holidays():
    out = []
    for y in range(2021, 2031):
        out += [f"{y}-{d}" for d in _FIXED]
        for a, b in _EID.get(y, []):
            d0, d1 = dt.date.fromisoformat(f"{y}-{a}"), dt.date.fromisoformat(f"{y}-{b}")
            out += [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    return out


HOLIDAYS = frozenset(_holidays())


def is_off_day(d: dt.date) -> bool:
    return d.weekday() in (4, 5) or d.isoformat() in HOLIDAYS      # Fri, Sat, or a holiday


def shift_table(start: dt.date, end: dt.date, max_steps: int = 4):
    """[(date, shifted_date)] as ISO strings: a report that would land on a weekend/holiday
    moves to the next working day, by at most `max_steps` days."""
    rows, d = [], start
    while d <= end:
        s, k = d, 0
        while is_off_day(s) and k < max_steps:
            s += dt.timedelta(days=1)
            k += 1
        rows.append((d.isoformat(), s.isoformat()))
        d += dt.timedelta(days=1)
    return rows

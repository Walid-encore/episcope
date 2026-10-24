from analytics.calendar.preparedness import circ_offset, shift_week, unwrapped_date, week_date


def test_circ_offset_wraps_new_year():
    assert circ_offset(51, 3) == -4
    assert circ_offset(3, 51) == 4
    assert circ_offset(30, 30) == 0


def test_range_around_a_wrapped_peak_is_narrow():
    offs = [circ_offset(w, 3) for w in (3, 51, 5)]
    assert shift_week(3, min(offs)) == 51      # earliest
    assert shift_week(3, max(offs)) == 5       # latest


def test_range_without_wrap_matches_plain_min_max():
    offs = [circ_offset(w, 44) for w in (28, 35, 44)]
    assert (shift_week(44, min(offs)), shift_week(44, max(offs))) == (28, 44)


def test_unwrapped_date_crosses_year_both_ways():
    assert unwrapped_date(2026, 20) == week_date(2026, 20)
    assert unwrapped_date(2026, 0) == week_date(2025, 52)
    assert unwrapped_date(2026, -1) == week_date(2025, 51)
    assert unwrapped_date(2026, 53) == week_date(2027, 1)

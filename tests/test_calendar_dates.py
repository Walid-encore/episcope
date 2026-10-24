import datetime as dt

from analytics.calendar.preparedness import season_date, lead_date, week_date


def test_peak_after_onset_wraps_into_next_year():
    assert season_date(2026, 3, 46) == dt.date(2027, 1, 18)       # ILI: onset Nov 2026, peak Jan 2027
    assert season_date(2026, 3, 46) > week_date(2026, 46)


def test_non_wrapping_season_stays_in_the_same_year():
    assert season_date(2026, 36, 26) == week_date(2026, 36)
    assert season_date(2026, 26, 26) == week_date(2026, 26)       # peak in the onset week itself


def test_actions_counted_back_past_new_year_fall_in_the_previous_year():
    assert lead_date(2026, 51, 3) == dt.date(2025, 12, 15)        # onset week 3, pre-position week 51
    assert lead_date(2026, 22, 26) == week_date(2026, 22)         # normal case unchanged
    assert lead_date(2026, 22, 26) < week_date(2026, 26)

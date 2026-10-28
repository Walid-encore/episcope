from analytics.calendar.preparedness import target_year


def test_data_ending_at_year_end_gives_the_year_just_starting():
    assert target_year("2025-12-22") == 2026
    assert target_year("2025-12-29") == 2026
    assert target_year("2026-01-05") == 2026      # a dataset 14 days longer must not jump to 2027


def test_mid_year_data_gives_next_year():
    assert target_year("2025-07-21") == 2026


def test_data_well_into_the_year_gives_next_year():
    assert target_year("2026-02-23") == 2027

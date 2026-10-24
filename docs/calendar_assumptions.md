# Preparedness calendar assumptions (AN-9)

Method (analytics/calendar/preparedness.py): weekly incidence per division x disease, STL decomposition
(period 52), seasonal index per epi-week, high season = weeks above the season threshold. Divisions with a
weak cycle are reported as "no clear season" (31 of 32 series have one on the real gold data).

## Stated assumptions (not derived from the data)

| Assumption | Value | Where to change |
|---|---|---|
| Pre-position supplies before onset | 4 weeks | lead-time setting in preparedness.py |
| Start prevention campaigns before onset | 8 weeks | lead-time setting in preparedness.py |
| Low-volume flag | mean weekly cases per division < 5 | `LOW_VOLUME` |

The 4-week pre-position lead is deliberately conservative: the synthetic generator restocks facilities in
3 to 10 days. Real procurement lead times differ and should replace these values.

## Reading the columns

- `onset_date`, `peak_date`, `end_date`: one season. A season that starts late in the year and peaks after
  New Year (ILI) has `peak_date` and `end_date` in the following year.
- `prepose_supplies_date`, `start_campaign_date`: counted back from the onset; may fall in the previous year.
- `*_earliest`, `*_latest`: range over the individual years with a clear season (`n_years_clear`), measured as
  circular offsets from the calendar value, so seasons that wrap over New Year give a narrow range.
- `prepose_supplies_date_early`: pre-position date if the season starts as early as in the earliest year.
- `mean_weekly_cases`, `low_volume`: low-volume series (mostly malaria outside the endemic districts) can show
  a strong seasonal cycle from small counts; treat their dates with caution.

## Known limitation

Dhaka dengue: the calendar peak (week 44) is 14 weeks from the raw seasonal peak (week 30), and individual
years peak anywhere from week 28 to week 44. Chattogram dengue differs by 8 weeks. The calendar onset (week 24
for Dhaka) is consistent with the raw data and drives the action dates. One possible cause is the large 2023
season distorting the STL profile; this has not been tested.

## Reading the earliest / latest columns

- `onset_week_earliest` is the earliest onset seen in any clear year, but never later than the calendar onset,
  so `prepose_supplies_date_early` is never later than `prepose_supplies_date`.
- The calendar peak and onset come from the multi-year seasonal profile, the earliest/latest columns from the
  individual years. The calendar peak can therefore lie outside the range of yearly peaks (on the real gold
  data: Chattogram and Mymensingh malaria). This reflects year-to-year variation in the peak and is
  reported by `check_an9_ranges` as a note, not an error.

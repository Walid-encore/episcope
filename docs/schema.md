# EpiScope Schema Contract (v1.0, frozen)

Generator output and the hidden 14-day drop MUST follow this. The pipeline reads columns BY NAME
(never by position), ignores unknown extra columns, and never hard-codes dates, file names or row counts.

## Conventions
- Dates: ISO `YYYY-MM-DD`. Encoding: UTF-8. CSV has a header row, comma separator, no quoting tricks.
- Epi-week: ISO 8601 (Monday start). Gold counts are by `onset_date` week; `report_date` is kept for late-data and as-of logic.
- district_code: 2-digit string "01".."64" from our reference table (S1/S2), the only geography key.
- Diseases from ICD-10: dengue A90,A91 | diarrhoeal A00-A09 | ili J10,J11 | malaria B50-B54 | other = everything else.
- Input location: `data/incoming/<feed>/` (any file name, any number of files, any subfolders). Feeds: `encounters`, `stock`.
  Reference feeds (`facilities`, `districts`, `weather`) are in `data/reference/`.

## S4 encounters (CSV, high volume)
| column | type | rules |
|---|---|---|
| encounter_id | string | unique per encounter; resubmitted records REPEAT the same id (~2%) |
| patient_id | string | synthetic national ID; bronze only; salted-hash before silver |
| facility_id | string | FK to facilities (~0.5% orphans) |
| onset_date | date | <= report_date; not before 2021-01-01 |
| report_date | date | onset_date + delay 0..21 days; fewer on weekends/holidays |
| age_years | int | valid 0..120 (~1% invalid, e.g. 212) |
| sex | string | M or F (invalid values possible) |
| icd10_code | string | pattern `^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$` |
| lab_confirmed | boolean | true/false |
| admitted | boolean | true/false |
| outcome | string | recovered, died, referred, ongoing |

## S5 facilities (CSV, >= 600 rows)
facility_id (string, PK), facility_name, facility_type (hospital | upazila_health_complex | clinic),
district_code (string), latitude (double), longitude (double), beds (int >= 0), icu_beds (int >= 0, <= beds)

## S6 stock and bed snapshots (JSON Lines, incremental, one object per line)
snapshot_date (date), facility_id (string), iv_saline_units (int), ns1_kits (int), ors_sachets (int),
antimalarial_courses (int), occupied_beds (int). All counts >= 0. Key = (snapshot_date, facility_id).

## Reference: districts (CSV, 64 rows, real S1/S2)
district_code, district_name, division (one of 8), population (int), centroid_lat, centroid_lon

## Reference: weather (CSV, real S3 via Open-Meteo, daily)
district_code, date, precip_mm, temp_mean_c, humidity_pct

## Ground truth (evaluation ONLY, detection code must never read it)
`data/truth/ground_truth_outbreaks.csv`: outbreak_id, district_code (or "ALL"), disease, start_date, end_date,
peak_multiplier, kind (local | slow_growth | national_surge)

## Idempotency keys
- encounters: encounter_id (latest report_date wins) | stock: (snapshot_date, facility_id)
- Re-running a day must not change any output. Late records (up to 21 days) rewrite their affected epi-weeks.

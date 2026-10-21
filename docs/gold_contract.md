# Gold Layer Contract

Status: v0.2. FROZEN once all three members approve (target: end of Day 3).
Owner: Data Engineer. Readers: Analytics Engineer, Visualisation Engineer.
Depends on: `docs/schema.md`. If a field here disagrees with schema.md, schema.md wins; fix this file.

## Alignment with schema.md

- **Geography key** is `district_code`: a 2-digit string "01".."64" taken from `data/reference/districts.csv` (the only source of truth). Never use names as keys. Never use "BD-xx" style ids.
- Encounters carry no district. Silver joins `encounters.facility_id` to `facilities.district_code`. Orphan `facility_id` rows (~0.5%) go to quarantine (reason `ORPHAN_FACILITY`) and never reach gold.
- **No `disease` column in S4.** Silver derives it from `icd10_code` via `pipeline/silver/icd_map.csv`:
  dengue = A90, A91 | diarrhoeal = A00-A09 | ili = J10, J11 | malaria = B50-B54 | other = everything else.
  Gold marts cover `dengue` and `diarrhoeal` (the two chosen diseases). Other diseases stay in silver.
- **Epi-week** is ISO 8601 (Monday start), assigned from `onset_date`. `epi_year` is the ISO YEAR, not the calendar year (early January can be week 52/53 of the previous ISO year). Weeks run 1-53.
- A record reported up to 21 days late updates an older epi-week (ING-4). `report_date` is kept for delay analysis, as-of backtests and the silent-facility artefact rule.
- **Dedup key is `encounter_id`; the record with the latest `report_date` wins** (schema.md idempotency rule).
- **Invalid values are quarantined, not repaired**: age outside 0..120 (`BAD_AGE`), `sex` not in M/F (`BAD_SEX`), `onset_date > report_date` (`BAD_DATES`), `onset_date` before 2021-01-01, malformed ICD-10 (`BAD_ICD10`). Decided once here; list in DQ rules.
- Age bands are derived in silver: 0-4, 5-14, 15-24, 25-44, 45-64, 65+.
- `patient_id` exists in bronze only. Silver carries `patient_hash` (salted hash, salt in `.env`, never committed). Gold contains neither.
- **S6 stock snapshots** (JSON Lines) are idempotent on key (`snapshot_date`, `facility_id`). Orphan facility ids are quarantined. Counts must be >= 0. `icu_beds <= beds` is a facility (S5) rule.
- Weather (daily, `data/reference/weather.csv`): `district_code, date, precip_mm, temp_mean_c, humidity_pct`. It covers 2020-01-01 onward so 8-week lags exist for early 2021 weeks.

## General rules

- Storage: Parquet under `data/gold/<table>/`, partitioned by `epi_year`/`epi_week` where the table has those columns.
- `week_start_date` is always supplied so no consumer re-derives it.
- `disease` values: `dengue`, `diarrhoeal` (gold), with `ili` and `malaria` allowed later without a schema change.
- No hard-coded dates, file names or row counts. The hidden 14-day drop must run unchanged.
- Gold holds TRUE counts (it lives in the secure zone). Small-cell suppression is applied only when writing serving exports (see `serving_contract.md`).
- Writes are partition-overwrite by epi-week (or MERGE), so reruns never duplicate rows.
- Detection and forecast code NEVER reads `data/truth/ground_truth_outbreaks.csv`. Only evaluation code may.

## Tables

### gold_weekly_cases (district x disease x epi-week)
| column | type | notes |
|---|---|---|
| district_code | string | key |
| disease | string | key |
| epi_year | int | key (ISO year) |
| epi_week | int | key, 1-53 |
| week_start_date | date | Monday |
| cases | long | deduplicated, valid records only |
| lab_confirmed_cases | long | |
| admitted_cases | long | drives bed demand |
| deaths | long | `outcome = died` |
| population | long | from districts.csv |
| incidence_per_100k | double | cases / population * 100000 |
| reporting_facilities | int | facilities with >= 1 report in the week |
| expected_facilities | int | facilities in the district (S5) |
| last_batch_id | string | latest batch that touched the row (lineage) |
| updated_at | timestamp | |

### gold_weekly_cases_by_age (district x disease x epi-week x age_band) - optional
district_code, disease, epi_year, epi_week, age_band, cases.

### gold_weekly_weather (district x epi-week)
| column | type | notes |
|---|---|---|
| district_code, epi_year, epi_week | string/int/int | key |
| temp_mean_c | double | weekly mean of daily `temp_mean_c` |
| precip_sum_mm | double | weekly sum of daily `precip_mm` |
| humidity_mean_pct | double | weekly mean of daily `humidity_pct` |
| temp_mean_c_lag1..8, precip_sum_mm_lag1..8 | double | optional precomputed lags |

### gold_alerts (district x disease x epi-week x detector)
| column | type | notes |
|---|---|---|
| district_code, disease, epi_year, epi_week | key | |
| detector | string | `ears_c2` or `cusum` |
| score | double | |
| threshold | double | |
| alert_flag | boolean | |
| severity | string | `low`, `medium`, `high` |
| reporting_artefact_flag | boolean | true when a silent facility back-filled; alert is suppressed |
| run_id | string | links to gold_eval_metrics |

### gold_forecast (district x disease x origin week x horizon x model)
| column | type | notes |
|---|---|---|
| district_code, disease | key | |
| origin_epi_year, origin_epi_week | int | last observed week |
| horizon_weeks | int | 1-4 |
| target_epi_year, target_epi_week | int | |
| target_week_start_date | date | |
| model | string | `nb_lagged_weather` or `seasonal_naive` |
| pred_mean | double | |
| q10, q50, q90 | double | 80% interval = q10..q90 |
| is_backtest | boolean | |
| actual_cases | long | backtest rows only |

### gold_stock_weekly (district x epi-week, from S6)
| column | type | notes |
|---|---|---|
| district_code, epi_year, epi_week | key | last snapshot of the week per facility, summed per district |
| iv_saline_units, ns1_kits, ors_sachets | long | |
| antimalarial_courses | long | carried through, unused for the two chosen diseases |
| occupied_beds | long | |
| beds_capacity, icu_beds_capacity | long | sums from S5 (static facility file) |
| facilities_reporting | int | |

### gold_demand (district x disease x target week)
| column | type | notes |
|---|---|---|
| district_code, disease, target_epi_year, target_epi_week | key | |
| beds_needed | double | assumptions in `docs/assumptions.md` |
| iv_saline_units_needed, ns1_kits_needed, ors_sachets_needed | double | same units as S6 |
| beds_needed_q90 etc. | double | upper-quantile versions used by the optimiser (AN-6/AN-7) |

### gold_allocation (budget level x alpha x strategy x resource x district)
| column | type | notes |
|---|---|---|
| budget_level | double | |
| alpha | double | equity floor |
| strategy | string | `optimised` or `population_proportional` |
| district_code | string | |
| resource | string | `iv_saline_units`, `ns1_kits`, `ors_sachets`, `beds` |
| allocated_units | double | |
| unmet_demand | double | max(0, demand - (stock + allocated_units)) |

### gold_eval_metrics (run x metric)
run_id, component (`detection`/`forecast`/`optimiser`), metric (`sensitivity`, `false_alerts_per_district_year`, `median_days_to_detect`, `mae`, `coverage_80`, `wis`), value, model_or_detector, threshold (null unless for the trade-off curve).

### gold_dq_report (batch x rule)
batch_id, rule_id (12+ rules), reason_code (matches quarantine table), rows_checked, rows_failed, pass_rate, run_ts.

### quarantine (silver, not gold)
batch_id, source_feed, reason_code, raw_row (string), quarantined_at.

### gold_lineage (gold row group x batch)
gold_table, epi_year, epi_week, batch_id, source, loaded_at.

## Change process

After freeze, columns may only be ADDED, never renamed or removed. Announce in the group chat and add a note at the bottom of this file.

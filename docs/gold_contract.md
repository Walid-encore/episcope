# Gold Layer Contract

Status: v0.3. FROZEN once all three members approve (target: end of Day 3).
v0.3 changes: added the as-of views (`gold_weekly_reports`, `gold_weekly_cases_by_lag`, `gold_reporting_status`) so the detector can see what was known in each report week; `reporting_facilities` and `expected_facilities` moved out of `gold_weekly_cases`; `gold_alerts` weeks are REPORT weeks; added truth-file section.
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
- **Dedup key is `encounter_id`; the record with the latest `report_date` wins** (schema.md idempotency rule). Silver keeps BOTH `first_report_date` (earliest report of that encounter_id) and `report_date` (latest). The as-of tables below time a case by `first_report_date`: a case became known when it was first reported, and a resubmission must not move it to a later week.
- **Two week axes.** Onset week (from `onset_date`) feeds incidence, forecasting, demand and the dashboard. Report week (ISO week of `first_report_date`) feeds detection. Counts by onset week are revised as late reports arrive; counts by report week are never revised once the week is over, so detection on them is "as known at the time" by construction.
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
| last_batch_id | string | latest batch that touched the row (lineage) |
| updated_at | timestamp | |

Reporting completeness is NOT here (it is a report-week property); see `gold_reporting_status`.

### gold_weekly_cases_by_age (district x disease x epi-week x age_band) - optional
district_code, disease, epi_year, epi_week, age_band, cases.

### gold_weekly_reports (district x disease x REPORT epi-week) - detector input
What the district reported to the system in a given week, regardless of when the illness began.
| column | type | notes |
|---|---|---|
| district_code, disease | string | key |
| report_epi_year, report_epi_week | int | key; ISO week of `first_report_date` |
| report_week_start_date | date | Monday |
| reported_cases | long | distinct valid encounters first reported in the week |
| late_cases_ge8d | long | of those, encounters with `first_report_date - onset_date >= 8` days; a back-fill week shows a high share |
| last_batch_id, updated_at | | lineage |

### gold_weekly_cases_by_lag (district x disease x onset epi-week x report lag) - as-of / nowcast
| column | type | notes |
|---|---|---|
| district_code, disease | string | key |
| epi_year, epi_week | int | key; ONSET week |
| week_start_date | date | Monday of the onset week |
| report_lag_weeks | int | key; 0..4 where 4 means "4 or more": report week minus onset week |
| cases_new | long | encounters first reported at exactly this lag (an increment, not cumulative) |

Properties: the sum of `cases_new` over lags equals `gold_weekly_cases.cases`. Cases known for onset week w when report week t has ended = sum of `cases_new` with lag <= t - w. Rolling-origin forecast backtests must use this as-of count, not the final count.

### gold_reporting_status (district x REPORT epi-week) - disease-agnostic
| column | type | notes |
|---|---|---|
| district_code | string | key |
| report_epi_year, report_epi_week | int | key |
| expected_facilities | int | facilities in the district (S5) |
| reporting_facilities | int | facilities with >= 1 first-reported valid encounter in the week |
| usual_volume_total | double | sum over all district facilities of their usual weekly volume |
| reporting_completeness | double | share of `usual_volume_total` contributed by facilities that reported this week, 0..1; null during warm-up |

Usual weekly volume of a facility = median of its weekly counts of first-reported valid encounters (all diseases) over the previous 12 report weeks, excluding the current week; needs at least 8 prior weeks, otherwise `reporting_completeness` is null. A 1-3 week silence does not zero a 12-week median, so the silent facility still counts at full weight. Weighting by usual volume means a silent large facility lowers completeness a lot, while a small facility with no cases one week hardly moves it. The count columns alone are not enough: one silent facility of eleven gives 10/11 = 91%.

### gold_weekly_weather (district x epi-week)
| column | type | notes |
|---|---|---|
| district_code, epi_year, epi_week | string/int/int | key |
| temp_mean_c | double | weekly mean of daily `temp_mean_c` |
| precip_sum_mm | double | weekly sum of daily `precip_mm` |
| humidity_mean_pct | double | weekly mean of daily `humidity_pct` |
| temp_mean_c_lag1..8, precip_sum_mm_lag1..8 | double | optional precomputed lags |

### gold_alerts (district x disease x REPORT epi-week x detector)
`epi_year` / `epi_week` here are the REPORT week the detector ran on (computed from `gold_weekly_reports`), not the onset week. Evaluation compares them with outbreak onset windows from the truth file.
| column | type | notes |
|---|---|---|
| district_code, disease, epi_year, epi_week | key | report week |
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

## Truth files (evaluation only; generated, not committed; detection and forecast code never read them)

- `data/truth/ground_truth_outbreaks.csv` exactly as in schema.md: outbreak_id, district_code (or "ALL"), disease, start_date, end_date, peak_multiplier, kind. Dates are ONSET dates. The scored diseases are `dengue` and `diarrhoeal`; ILI and malaria outbreaks are in the file but not scored.
- `data/truth/ground_truth_artefacts.csv`, one row per affected district x disease. A silent facility lowers every disease in the raw data, so all four modelled diseases (dengue, diarrhoeal, ili, malaria) have a row; only dengue and diarrhoeal are scored. Columns: district_code (string, keep the leading zero), disease, silent_start, silent_end, backfill_week_start, plus extra column facility_id. All ISO dates. `silent_start` and `silent_end` are the Mondays of the first and last silent week; `backfill_week_start` = `silent_end` + 7 days. Silences last 1 to 3 weeks. The silent facility is one of the largest in its district (usual volume share of about 20% or more) so `reporting_completeness` drops visibly below 0.85. An artefact never overlaps an injected outbreak window for the same district (any scored disease), with a margin of four weeks on each side.
- Detection code applies the artefact rule from `gold_reporting_status` and `gold_weekly_reports` only. It never reads the artefact file.

## Change process

After freeze, columns may only be ADDED, never renamed or removed. Announce in the group chat and add a note at the bottom of this file.

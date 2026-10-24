# Gold Layer Contract

Status: v0.3. FROZEN once all three members approve (target: end of Day 3).
v0.3 changes: added the as-of views (`gold_weekly_reports`, `gold_weekly_cases_by_lag`, `gold_reporting_status`) so the detector can see what was known in each report week; `reporting_facilities` and `expected_facilities` moved out of `gold_weekly_cases`; `gold_alerts` weeks are REPORT weeks; added truth-file section; `reporting_completeness` redefined (capped credit per facility); quarantine reason codes renamed (`AGE_INVALID`, `FACILITY_ORPHAN`, `ICD10_INVALID`, `ONSET_BEFORE_MIN_DATE`, `REPORT_BEFORE_ONSET`, `REPORT_DELAY_TOO_LONG`; `SEX_INVALID` is a warn rule that keeps the row with sex set to `U`); `gold_dq_report` gained `dataset`, `severity`, `description`, `rows_in`, `rows_quarantined`; `gold_weekly_weather` gained `days_observed`.
Owner: Data Engineer. Readers: Analytics Engineer, Visualisation Engineer.
Depends on: `docs/schema.md`. If a field here disagrees with schema.md, schema.md wins; fix this file.

## Alignment with schema.md

- **Geography key** is `district_code`: a 2-digit string "01".."64" taken from `data/reference/districts.csv` (the only source of truth). Never use names as keys. Never use "BD-xx" style ids.
- Encounters carry no district. Silver joins `encounters.facility_id` to `facilities.district_code`. Orphan `facility_id` rows (~0.5%) go to quarantine (reason `FACILITY_ORPHAN`) and never reach gold.
- **No `disease` column in S4.** Silver derives it from `icd10_code` via `pipeline/silver/icd_map.csv`:
  dengue = A90, A91 | diarrhoeal = A00-A09 | ili = J10, J11 | malaria = B50-B54 | other = everything else.
  Gold marts cover `dengue` and `diarrhoeal` (the two chosen diseases). Other diseases stay in silver.
- **Epi-week** is ISO 8601 (Monday start), assigned from `onset_date`. `epi_year` is the ISO YEAR, not the calendar year (early January can be week 52/53 of the previous ISO year). Weeks run 1-53.
- A record reported up to 21 days late updates an older epi-week (ING-4); a later one is quarantined as `REPORT_DELAY_TOO_LONG`. `report_date` is kept for delay analysis, as-of backtests and the silent-facility artefact rule.
- **Dedup key is `encounter_id`; the record with the latest `report_date` wins** (schema.md idempotency rule). Silver keeps BOTH `first_report_date` (earliest report of that encounter_id) and `report_date` (latest). The as-of tables below time a case by `first_report_date`: a case became known when it was first reported, and a resubmission must not move it to a later week.
- **Two week axes.** Onset week (from `onset_date`) feeds incidence, forecasting, demand and the dashboard. Report week (ISO week of `first_report_date`) feeds detection. Counts by onset week are revised as late reports arrive; counts by report week are never revised once the week is over, so detection on them is "as known at the time" by construction.
- **Invalid values are quarantined, not repaired.** Encounter rules with severity `error` (the row goes to quarantine): `AGE_INVALID` (age missing, non-numeric or above 120), `FACILITY_ORPHAN` (`facility_id` not in the facility registry), `ICD10_INVALID` (code missing or not matching the ICD-10 pattern), `ONSET_BEFORE_MIN_DATE` (`onset_date` before 2021-01-01), `REPORT_BEFORE_ONSET` (`report_date` earlier than `onset_date`), `REPORT_DELAY_TOO_LONG` (reporting delay above 21 days), `REPORT_DATE_IN_FUTURE`, `ONSET_DATE_INVALID`, `REPORT_DATE_INVALID`, `ENC_ID_MISSING`, `FACILITY_ID_MISSING`, `PATIENT_ID_MISSING`, `LAB_FLAG_INVALID`, `ADMITTED_FLAG_INVALID`, `OUTCOME_INVALID`. One rule has severity `warn`: `SEX_INVALID` (`sex` not M/F) keeps the row and sets `sex` to `U`; it is counted in `gold_dq_report` but the row is not quarantined. Decided once here; the live list is in `gold_dq_report`.
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
| report_week_start_date | date | Monday of the report week |
| expected_facilities | int | facilities in the district (S5) |
| reporting_facilities | int | facilities with >= 1 first-reported valid encounter in the week |
| usual_volume_total | double | sum over all district facilities of their usual weekly volume (facilities still in warm-up are excluded) |
| reporting_completeness | double | volume-weighted capped credit, 0..1; null when no facility has a usual volume yet (warm-up) |

**Usual weekly volume** of a facility = median of its weekly counts of first-reported valid encounters (all diseases) over the previous 12 report weeks, excluding the current week. It needs at least 8 prior weeks; before that the usual volume is null and the facility is excluded from both the numerator and `usual_volume_total`.

**Completeness** is computed per facility and then summed over the district. For a facility with usual volume `usual > 0` and `n` first-reported valid encounters this week, its credit is `min(usual, 2 x n)`; a facility with `usual = 0` earns 0. Then:

`reporting_completeness = sum(credit) / usual_volume_total`

Why a capped credit and not "reported or not": a facility that sends a trickle (for example n = 1 against a usual of 20) must not count as fully reporting. It earns only `2 x n`, so thin reporting lowers completeness, and a silent facility (n = 0) earns nothing. A facility reporting at half its usual volume or more earns full credit. Weighting by usual volume means a silent large facility lowers completeness a lot, while a small facility with no cases one week hardly moves it. The count columns alone are not enough: one silent facility of eleven gives 10/11 = 91%, however large it is.

Check value: district 30, report week starting 2022-04-18 gives 0.7216 (8 of 11 facilities reporting, `usual_volume_total` 48.5). Across the full table the values range from about 0.32 to 1.0.

### gold_weekly_weather (district x epi-week)
| column | type | notes |
|---|---|---|
| district_code, epi_year, epi_week | string/int/int | key |
| week_start_date | date | Monday |
| temp_mean_c | double | weekly mean of daily `temp_mean_c` |
| precip_sum_mm | double | weekly sum of daily `precip_mm` |
| humidity_mean_pct | double | weekly mean of daily `humidity_pct` |
| temp_mean_c_lag1..8, precip_sum_mm_lag1..8 | double | optional precomputed lags |
| days_observed | int | number of daily rows in the week; under 7 at the edges of the weather file |

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
| week_start_date | date | Monday |
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

Details (as written by `analytics/eval_metrics.py`):
- `run_id` equals the `run_id` of `gold_alerts` for the same run. Rows of different runs are kept, so the table is a history. Re-running a stage replaces only that run's rows for that `component`.
- `forecast` metrics are `mae`, `coverage_80`, `wis` and `wis_ratio_vs_naive` (model WIS divided by the seasonal-naive WIS at the same horizon; written for the non-baseline model only).
- A metric name without a suffix covers all horizons. The suffix `_h1` to `_h4` gives the horizon in weeks, for example `wis_h2` or `wis_ratio_vs_naive_h4`.
- `threshold` is null for `forecast` rows.
- `detection` metrics are `sensitivity`, `false_alerts_per_district_year` and `median_days_to_detect`, one set per detector in `model_or_detector` (`ears_c2`, `cusum`). Rows with a null `threshold` are the chosen operating point of the detector. Rows with a `threshold` are its trade-off curve (conflict C1), taken at the operating point's minimum-case floor. A curve point at which no outbreak is detected has no `median_days_to_detect` row.
- `operating_threshold`, `operating_min_cases` and `operating_point_feasible` (1 when false alerts per district-year are within the limit, else 0) describe the operating point; their `threshold` is null.

### gold_dq_report (batch x rule)
Contract columns: batch_id, rule_id, reason_code (matches the quarantine table), rows_checked, rows_failed, pass_rate, run_ts.
Added columns: dataset, severity (`error` or `warn`), description, rows_in, rows_quarantined.

- `rule_id` is `<dataset>:<reason_code>` (for example `encounters:AGE_INVALID`). Every rule x batch appears, passing rules included (`rows_failed = 0`).
- `run_ts` is silver's run time, so a gold re-run reproduces the same rows.
- `rows_failed` counts the rows failing that one rule. A row can fail several rules, so the sum of `rows_failed` over a dataset can exceed `rows_quarantined`.
- `rows_in` is the number of rows the dataset received in the batch. `rows_quarantined` is the number of rows in the quarantine table for that batch and dataset (exact repeats from the raw feed are not removed), repeated on every rule row of the batch. It is null (unknown) when quarantine and silver share no batch id, never 0.
- Gold-level reconciliation checks (for example, lag counts add up to weekly cases, gold cases equal silver) appear as rows with `batch_id = ALL` and a null `dataset`.

### quarantine (silver, not gold)
The full bronze columns of the rejected row, plus `patient_hash`, `reason_codes` (every error rule the row failed, separated by `;`), `bronze_batch_id` and `quarantined_at`. Rows that only trigger warn rules (`SEX_INVALID`) are not quarantined.

### gold_lineage (gold row group x batch)
gold_table, epi_year, epi_week, batch_id, source, loaded_at.

## Truth files (evaluation only; generated, not committed; detection and forecast code never read them)

- `data/truth/ground_truth_outbreaks.csv` exactly as in schema.md: outbreak_id, district_code (or "ALL"), disease, start_date, end_date, peak_multiplier, kind. Dates are ONSET dates. The scored diseases are `dengue` and `diarrhoeal`; ILI and malaria outbreaks are in the file but not scored.
- `data/truth/ground_truth_artefacts.csv`, one row per affected district x disease. A silent facility lowers every disease in the raw data, so all four modelled diseases (dengue, diarrhoeal, ili, malaria) have a row; only dengue and diarrhoeal are scored. Columns: district_code (string, keep the leading zero), disease, silent_start, silent_end, backfill_week_start, plus extra column facility_id. All ISO dates. `silent_start` and `silent_end` are the Mondays of the first and last silent week; `backfill_week_start` = `silent_end` + 7 days. Silences last 1 to 3 weeks. The silent facility is one of the largest in its district (usual volume share of about 20% or more) so `reporting_completeness` drops visibly below 0.85. An artefact never overlaps an injected outbreak window for the same district (any scored disease), with a margin of four weeks on each side.
- Detection code applies the artefact rule from `gold_reporting_status` and `gold_weekly_reports` only. It never reads the artefact file.

## Change process

After freeze, columns may only be ADDED, never renamed or removed. Announce in the group chat and add a note at the bottom of this file.

## Change log

- v0.3 (2026-10-22): `reporting_completeness` redefined as volume-weighted capped credit, `min(usual, 2n)` per facility (was: share of usual volume from facilities with any report). Quarantine reason codes renamed to `AGE_INVALID`, `FACILITY_ORPHAN`, `REPORT_BEFORE_ONSET`; `ICD10_INVALID`, `ONSET_BEFORE_MIN_DATE` and `REPORT_DELAY_TOO_LONG` documented; `SEX_INVALID` is a warn rule (row kept, sex set to `U`). `gold_dq_report` gained `dataset`, `severity`, `description`, `rows_in`, `rows_quarantined`. `gold_weekly_weather` gained `days_observed`. Quarantine table documented with its real columns.

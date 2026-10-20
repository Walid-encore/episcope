# Gold Layer Contract

Status: DRAFT v0.1. Becomes FROZEN once all three members approve (target: end of Day 3).
Owner: Data Engineer. Readers: Analytics Engineer, Visualisation Engineer.
Depends on: `docs/schema.md` (raw S4-S6 schema). If a field name here disagrees with schema.md, schema.md wins; fix this file.

## Alignment with schema.md (S4 encounters, S5 facilities)

- District key is `district_code` (as in S5 facilities). Silver joins `encounters.facility_id` to `facilities.district_code`; encounters carry no district of their own. Orphan `facility_id` rows (~0.5%) go to quarantine and never reach gold.
- There is no `disease` column in S4. Silver derives `disease` from `icd10_code` (mapping table committed in `pipeline/silver/icd_map.csv`: dengue = A90-A91, diarrhoeal = A00-A09). Codes outside the mapping are kept in silver but excluded from the two-disease gold marts.
- `epi_year` / `epi_week` are assigned from `onset_date` (not `report_date`). A record reported up to 21 days late therefore updates an older epi-week, which is what ING-4 requires. `report_date` is kept for reporting-delay analysis and for the silent-facility artefact rule.
- `encounter_id` is the dedup key (~2% resubmitted duplicates repeat it). Keep the first valid record per `encounter_id`.
- Age bands are derived in silver from `age_years` (valid 0..120). Invalid ages (~1%) are quarantined with a reason code. `sex` is M or F; invalid values are quarantined or set to `U` in silver (decide once and record in the DQ rules).
- `patient_id` exists in bronze only. Silver carries `patient_hash` (salted hash, salt from `.env`). Gold never contains either.
- S6 snapshots are incremental JSON Lines keyed by (`snapshot_date`, `facility_id`); loads are idempotent on that key. Orphan `facility_id` goes to quarantine like S4. Counts must be >= 0 and `icu_beds` <= `beds` (S5); violations are quarantined.
- Reference files: `districts` (64 rows: `district_code`, `district_name`, `division`, `population`, `centroid_lat`, `centroid_lon`) supplies population and the 8 divisions; `weather` is daily per `district_code`.
- Optional extra gold measures available from S4: `lab_confirmed` count, `admitted` count, `died` count (from `outcome`). Demand conversion (beds) should use `admitted`.

## General rules

- Storage: Parquet under `data/gold/<table>/`, partitioned by `epi_year` / `epi_week` where the table has those columns.
- `epi_year` + `epi_week` use ISO-style epi-weeks (week starts Monday). `week_start_date` is always supplied so no consumer has to re-derive it.
- `district_code` is the ADM2 code from the boundary file (64 districts). Never use names as keys.
- `disease` is one of: `dengue`, `diarrhoeal`.
- No hard-coded dates, file names or row counts anywhere. The hidden 14-day drop must run unchanged.
- Gold holds TRUE counts. Privacy suppression (counts 1-4, complementary suppression) is applied only when writing serving exports (see `serving_contract.md`).
- Late data (up to 21 days) updates old epi-weeks. Writes are partition-overwrite by epi-week (or MERGE), so reruns never duplicate rows.
- Detection and forecast code must NEVER read the ground-truth file. Only evaluation code may.

## Tables

### gold_weekly_cases (grain: district x disease x epi-week)
| column | type | notes |
|---|---|---|
| district_code | string | key |
| disease | string | key |
| epi_year | int | key |
| epi_week | int | key, 1-53 |
| week_start_date | date | Monday of the week |
| cases | long | deduplicated, valid records only |
| cases_by_age_band | map<string,long> | optional; bands per schema.md |
| population | long | district population used for rates |
| incidence_per_100k | double | cases / population * 100000 |
| reporting_facilities | int | facilities that reported in the week |
| expected_facilities | int | facilities that should report (for the reporting-artefact rule) |
| last_batch_id | string | latest batch that touched this row (lineage) |
| updated_at | timestamp | last load time |

### gold_weekly_weather (grain: district x epi-week)
| column | type | notes |
|---|---|---|
| district_code, epi_year, epi_week | string/int/int | key |
| temp_mean_c | double | weekly mean of daily `temp_mean_c` (reference weather file) |
| precip_sum_mm | double | weekly sum of daily `precip_mm` |
| humidity_mean_pct | double | weekly mean of daily `humidity_pct` |
| temp_mean_c_lag{1..8}, precip_sum_mm_lag{1..8} | double | optional pre-computed lags for the 4-8 week dengue lag |

### gold_alerts (grain: district x disease x epi-week x detector)
| column | type | notes |
|---|---|---|
| district_code, disease, epi_year, epi_week | key | |
| detector | string | `ears_c2` or `cusum` |
| score | double | detector statistic |
| threshold | double | threshold used for this row |
| alert_flag | boolean | |
| severity | string | `low`, `medium`, `high` |
| reporting_artefact_flag | boolean | true when a silent facility back-filled (alert should not fire) |
| run_id | string | links to gold_eval_metrics |

### gold_forecast (grain: district x disease x origin week x horizon x model)
| column | type | notes |
|---|---|---|
| district_code, disease | key | |
| origin_epi_year, origin_epi_week | int | last observed week |
| horizon_weeks | int | 1-4 |
| target_epi_year, target_epi_week, target_week_start_date | int/int/date | week being predicted |
| model | string | `nb_lagged_weather` or `seasonal_naive` |
| pred_mean | double | |
| q10, q50, q90 | double | 80% interval = q10 to q90 |
| is_backtest | boolean | true for rolling-origin backtest rows |
| actual_cases | long | filled only for backtest rows |

### gold_stock_weekly (grain: district x epi-week, from S6 snapshots)
| column | type | notes |
|---|---|---|
| district_code, epi_year, epi_week | key | snapshot_date mapped to epi-week; last snapshot of the week per facility, summed per district |
| iv_saline_units | long | |
| ns1_kits | long | |
| ors_sachets | long | |
| antimalarial_courses | long | carried through; not used for the two chosen diseases |
| occupied_beds | long | |
| beds_capacity | long | sum of facility `beds` from S5 (point-in-time; facility file is static) |
| icu_beds_capacity | long | sum of facility `icu_beds` from S5 |
| facilities_reporting | int | facilities with a snapshot that week |

### gold_demand (grain: district x disease x target week)
| column | type | notes |
|---|---|---|
| district_code, disease, target_epi_year, target_epi_week | key | |
| beds_needed | double | assumptions documented in `docs/assumptions.md` |
| iv_saline_units_needed | double | same unit as S6 `iv_saline_units` |
| ns1_kits_needed | double | dengue NS1 test kits, same unit as S6 `ns1_kits` |
| ors_sachets_needed | double | same unit as S6 `ors_sachets` (mainly diarrhoeal) |

### gold_allocation (grain: budget level x alpha x resource x district)
| column | type | notes |
|---|---|---|
| budget_level | double | |
| alpha | double | equity floor |
| strategy | string | `optimised` or `population_proportional` |
| district_code | string | |
| resource | string | `iv_saline_units`, `ns1_kits`, `ors_sachets` or `beds` |
| allocated_units | double | |
| unmet_demand | double | max(0, demand - (existing stock from `gold_stock_weekly` + allocated_units)) |

### gold_eval_metrics (grain: run x metric)
| column | type | notes |
|---|---|---|
| run_id | string | |
| component | string | `detection`, `forecast`, `optimiser` |
| metric | string | e.g. `sensitivity`, `false_alerts_per_district_year`, `median_days_to_detect`, `mae`, `coverage_80`, `wis` |
| value | double | |
| model_or_detector | string | |
| threshold | double | for the trade-off curve; null otherwise |

### gold_dq_report (grain: batch x rule)
| column | type | notes |
|---|---|---|
| batch_id | string | |
| rule_id | string | 12+ rules |
| reason_code | string | matches quarantine table |
| rows_checked, rows_failed | long | |
| pass_rate | double | |
| run_ts | timestamp | |

### gold_lineage (grain: gold row group x batch)
| column | type | notes |
|---|---|---|
| gold_table | string | |
| epi_year, epi_week | int | |
| batch_id | string | bronze batch that contributed |
| source | string | |
| loaded_at | timestamp | |

## Change process

After freeze, a column may only be ADDED, never renamed or removed, and the change must be announced in the group chat and committed with a note at the bottom of this file.

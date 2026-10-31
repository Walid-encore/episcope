# Serving Contract (pipeline to dashboard)

Status: v0.2. FROZEN once all three members approve (target: end of Day 3).
Owner: Data Engineer (writes exports). Reader: Visualisation Engineer (dashboard).
Source tables: `docs/gold_contract.md`.

## General rules

- Output folder: `serving/data/`. Hand-made samples with identical shape live in `serving/sample/`. The dashboard reads from one configurable base path, so swapping sample for real needs no code change.
- Dashboard is static D3 v7, served with `python -m http.server`.
- Total size of all files in `serving/data/` is under 10 MB (VIS-4). Budget: cases_weekly < 7.5 MB, everything else < 2.5 MB combined (the 10 MB total is the limit).
- Everything is aggregated. No patient-level data, no hashed ids, no facility-level rows.
- **Geography key is `district_code`**: a 2-digit string "01".."64" (never "BD-01", never a number). It equals the `district_code` property in `serving/geo/bgd_adm2_simplified_coded.geojson`.
- **Privacy (PRV-2):** any count of 1-4 is suppressed. Complementary suppression is applied so suppressed cells cannot be recovered from totals by subtraction. A suppressed count is written as `null` with `"suppressed": true`; its `incidence_per_100k` is also `null`. A true zero is written as `0` and carries no `suppressed` key (absent means false, see v0.4 below). Never write 0 for a suppressed cell.
- Dates are ISO `YYYY-MM-DD`. Weeks carry `epi_year` (ISO year), `epi_week`, `week_start`.
- Every file has top-level `generated_at` (UTC ISO timestamp) and `data_as_of` (latest data date).
- Missing values are `null`, never `""` or `-1`.
- Rows are written as compact JSON (no pretty-printing) to save space.

## Files

### Boundaries (not exported by the pipeline)
`serving/geo/bgd_adm2_simplified_coded.geojson`, GeoJSON, 64 features. Properties: `district_code`, `district_name`, `division`. Population comes from `meta.json`.

### meta.json
```json
{
  "generated_at": "2026-01-01T00:00:00Z",
  "data_as_of": "2026-01-01",
  "diseases": ["dengue", "diarrhoeal"],
  "districts": [{"district_code": "01", "district_name": "Bagerhat", "division": "Khulna", "population": 1427622}],
  "weeks": {"first": {"epi_year": 2021, "epi_week": 1}, "last": {"epi_year": 2025, "epi_week": 52}},
  "batch_ids": ["..."]
}
```

### cases_weekly.json
```json
{"generated_at": "...", "data_as_of": "...",
 "rows": [{"district_code": "01", "disease": "dengue", "epi_year": 2025, "epi_week": 30,
           "week_start": "2025-07-21", "cases": 18, "incidence_per_100k": 1.3}]}
```
`suppressed` is written only when it is `true` (with `cases` and `incidence_per_100k` both `null`); an absent key means `false` (v0.4). Readers must test it for truthiness, never `=== false`.
Used by: choropleth, time series, seasonal heatmap. Size: 64 districts x 2 diseases x ~260 weeks is about 33k rows.

### forecast.json
```json
{"generated_at": "...",
 "rows": [{"district_code": "01", "disease": "dengue", "origin_epi_year": 2025, "origin_epi_week": 30,
           "horizon_weeks": 1, "target_week_start": "2025-08-04", "model": "nb_lagged_weather",
           "pred_mean": 20.4, "q10": 12, "q50": 20, "q90": 31}]}
```
Latest origin only, horizons 1-4. Backtest rows are not served. Forecasts are model output, not counts, so they are not suppressed. The dashboard must always draw the q10-q90 band.

### alerts.json
```json
{"generated_at": "...",
 "rows": [{"district_code": "01", "disease": "dengue", "epi_year": 2025, "epi_week": 30,
           "week_start": "2025-07-21", "detector": "ears_c2", "score": 4.2, "threshold": 3.0,
           "severity": "high", "alert_flag": true, "reporting_artefact_flag": false}]}
```
Last 104 weeks only, and only rows where `alert_flag` is true or `score` is above half the threshold, to stay small. The dashboard sorts by severity then score.

### seasonality.json
```json
{"rows": [{"disease": "dengue", "district_code": "01", "epi_week": 30, "seasonal_index": 1.8, "peak_flag": true}]}
```
`district_code` may be `"ALL"` for national. Used by the seasonal heatmap and preparedness calendar. Calendar rows (division x disease x action x start/end epi-week) go in `calendar.json` with the same header fields.

### allocation.json
```json
{"generated_at": "...", "alpha_values": [0, 0.25, 0.5], "budget_levels": [100, 200, 300, 400, 500],
 "rows": [{"budget_level": 300, "alpha": 0.25, "strategy": "optimised", "district_code": "01",
           "resource": "iv_saline_units", "allocated_units": 12.0, "unmet_demand": 3.0}],
 "curve": [{"budget_level": 300, "alpha": 0.25, "strategy": "optimised", "total_unmet": 140.0, "equity_gini": 0.21}]}
```
At least 5 budget levels and both strategies (`optimised`, `population_proportional`). Also supports the what-if panel (VIS-5) as a precomputed grid. Resources are supply units, not patient counts, so they are not suppressed.

### stock.json
```json
{"generated_at": "...",
 "rows": [{"district_code": "01", "epi_year": 2025, "epi_week": 30, "iv_saline_units": 400,
           "ns1_kits": 120, "ors_sachets": 900, "occupied_beds": 180, "beds_capacity": 240}]}
```
Latest 52 weeks only.

### dq_report.json (DQ-2 shown on the dashboard)
```json
{"generated_at": "...", "batches": [{"batch_id": "...", "rows_in": 0, "rows_out": 0, "rows_quarantined": 0,
  "rules": [{"rule_id": "R01", "reason_code": "BAD_AGE", "pass_rate": 0.99}]}]}
```
Last 14 batches only.

### freshness.json
```json
{"generated_at": "...", "data_as_of": "...", "last_batch_id": "...", "pipeline_version": "v1.0"}
```

## Dashboard behaviour that depends on this contract

- One shared filter state (disease, district set, week range). Every view reads the same loaded files and filters client-side.
- Suppressed cells are drawn with a hatch pattern; tooltip reads "suppressed (fewer than 5 cases)".
- The freshness timestamp comes from `freshness.json`, never from the browser clock.
- Every view shows the data-freshness timestamp, a legend and axis labels, with a colour-blind-safe palette.

## Change process

After freeze, fields may only be ADDED. Never rename or remove. Announce in the group chat and add a note at the bottom of this file.

## Changes after v0.2 (four diseases, announced 2026-10-26)
- Export covers **dengue, diarrhoeal, ili and malaria**. `cases_weekly.json` has the full history for dengue and diarrhoeal and only the last 104 weeks for ili and malaria; earlier weeks are absent, not zero-filled. `meta.diseases` lists the four.
- `seasonality.json` is per **division**: rows have `division` and `district_code: null`; national rows keep `district_code: "ALL"` and `division: "ALL"`. Top level: `geography_level: "division"`, `all_label: "weighted mean of divisions"`. Rows also carry `low_volume`, `peak_week_earliest` and `peak_week_latest`; a range with earliest > latest wraps around the year end. `mean_weekly_cases` is never exported (PRV-2).
- `allocation.json` and `allocation_peak.json`: grid rows where `allocated_units` and `unmet_demand` are both 0 are omitted, and the top-level `grid_zero_rows_omitted: true` says so; a missing row means 0 / 0 only when that flag is true. The `population_proportional` rows have `alpha: null`. `budget_unit: "percent_of_full_need"`.
- `alerts.json`: rows on weeks with a suppressed case count are not exported (their score could be inverted).
- **PRV-2 scope.** The 1-4 suppression applies to counts of patients, which only `cases_weekly.json` carries. Forecast values, alert scores, allocated and unmet supply units, stock units and seasonal indices are modelled or supply quantities and are not suppressed even when a value falls between 1 and 4. `dq_report.json` carries record counts of the data-quality process, not disease cases.

## Final export changes (v0.3)

- **Disease windows in `cases_weekly.json` and `alerts.json`:** dengue and diarrhoeal have their full history (alerts: last 104 weeks). ILI starts 2022-12-19, so both nationwide surges (2023-02 to 05 and 2024-09 to 12) are included. Malaria has the last 52 weeks. `meta.json` has `disease_windows` with `first_week_start` and `last_week_start` per disease, which is the window actually written. A viewer that doesn't find the field derives it from the rows.
- **`stock.json`** holds the last 26 weeks, not 52.
- **Allocation alphas:** `allocation.json` (live) has alpha values 0, 0.2, 0.4, 0.6 and 0.8. `allocation_peak.json` has 0, 0.4 and 0.8. Each file lists its own `alpha_values` and `budget_levels`, and a viewer must use the lists of the file it shows. A grid row that is missing means 0 / 0 (`grid_zero_rows_omitted`), so an alpha that is not in the file's list must not be read as "nothing allocated".
- **Size:** the limit of 10,000,000 bytes for all files in `serving/data/` is binding. The earlier split (2.5 MB for the files other than `cases_weekly.json`, 7.5 MB for `cases_weekly.json`) is a target only, and the exporter prints a warning if one is passed. If the total goes over 9,700,000 bytes the exporter's size valve trims the oldest weeks of ILI and malaria in steps of 4, logs it, never goes below 52 weeks per disease, and never cuts ILI later than 2023-02-13. Measured on the interim data (`data_as_of` 2026-01-12): 9.27 MB.
- **Calendar dates:** `calendar.json` covers the first year ahead of the data (2026 for data ending January 2026). ILI seasons that start in November end in 2027. The week numbers are the real output, and the dates follow from the year.

## Change v0.4 (serving size fix, SF 1 run)

- **`cases_weekly.json`: the `suppressed` key is written only when it is `true`.** An absent key means `false`. Rows with a visible count (including a true `0`) no longer carry `"suppressed": false`. A suppressed row is unchanged: `cases: null`, `incidence_per_100k: null`, `suppressed: true`.
- **Why:** at SF 1 the serving export measured 10.11 MB and failed the 10,000,000-byte limit (VIS-4) even after the size valve had trimmed ILI to its floor. Dropping the default-false flag from about 43,000 rows saved about 0.74 MB (`cases_weekly.json` 7,061 KB to 6,323 KB). The SF 1 total is now 9,368,232 bytes, no history is removed, and ILI again starts 2022-12-19.
- **Readers:** the dashboard reads the flag by truthiness only (`r.suppressed`), so no dashboard change was needed. `scripts/check_serving.py` and the validation in `pipeline/serving_export.py` use `.get("suppressed", False)`, and `suppressed` is no longer a required field of `cases_weekly` rows.
- Older files (for example `serving/sample/`) that carry `"suppressed": false` on every row remain valid.

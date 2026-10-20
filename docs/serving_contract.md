# Serving Contract (pipeline to dashboard)

Status: DRAFT v0.1. Becomes FROZEN once all three members approve (target: end of Day 3).
Owner: Data Engineer (writes exports). Reader: Visualisation Engineer (dashboard).
Source tables: see `docs/gold_contract.md`.

## General rules

- Output folder: `serving/data/`. Dashboard is static D3 v7, served with `python -m http.server`.
- Total size of all JSON files: under 10 MB.
- All files are aggregated. No patient-level data, no hashed IDs.
- Privacy (PRV-2): any cell with a count of 1-4 is suppressed. Complementary suppression is applied so totals cannot be used to recover suppressed cells by subtraction.
- A suppressed value is written as `null` with `"suppressed": true` in the same object. Never write 0 for a suppressed cell.
- `district_code` (same field name as S5 facilities) matches the `id` property of the district TopoJSON. Boundaries live in `serving/data/districts.topo.json`.
- Dates are ISO strings (`YYYY-MM-DD`). Weeks carry `epi_year`, `epi_week` and `week_start`.
- Every file carries a top-level `generated_at` (UTC ISO timestamp) and `data_as_of` (latest data date).
- Missing values are `null`, never `""` or `-1`.
- Hand-made sample files with the same shape go in `serving/sample/` so the dashboard can be built before the real pipeline exists. Real exports replace them without code changes.

## Files

### districts.topo.json
TopoJSON, object name `districts`. Properties per feature: `id` (district_code), `name` (district_name), `population`, `division` (one of 8).

### meta.json
```json
{
  "generated_at": "2026-01-01T00:00:00Z",
  "data_as_of": "2026-01-01",
  "diseases": ["dengue", "diarrhoeal"],
  "districts": [{"id": "BD-01", "name": "...", "division": "...", "population": 123456}],
  "weeks": {"first": {"epi_year": 2021, "epi_week": 1}, "last": {"epi_year": 2025, "epi_week": 52}},
  "batch_ids": ["..."]
}
```

### cases_weekly.json
```json
{
  "generated_at": "...", "data_as_of": "...",
  "rows": [
    {"district_code": "BD-01", "disease": "dengue", "epi_year": 2025, "epi_week": 30,
     "week_start": "2025-07-21", "cases": 18, "incidence_per_100k": 2.1, "suppressed": false}
  ]
}
```
Used by: choropleth, time series, seasonal heatmap.

### forecast.json
```json
{
  "generated_at": "...",
  "rows": [
    {"district_code": "BD-01", "disease": "dengue", "origin_epi_year": 2025, "origin_epi_week": 30,
     "horizon_weeks": 1, "target_week_start": "2025-08-04", "model": "nb_lagged_weather",
     "pred_mean": 20.4, "q10": 12, "q50": 20, "q90": 31}
  ]
}
```
Used by: time series forecast bands. Latest origin only. Backtest results are not served.

### alerts.json
```json
{
  "generated_at": "...",
  "rows": [
    {"district_code": "BD-01", "disease": "dengue", "epi_year": 2025, "epi_week": 30,
     "week_start": "2025-07-21", "detector": "ears_c2", "score": 4.2, "severity": "high",
     "alert_flag": true, "reporting_artefact_flag": false}
  ]
}
```
Used by: ranked alert table, choropleth overlay. The table is sorted by severity then score, done by the dashboard.

### seasonality.json
```json
{
  "rows": [
    {"disease": "dengue", "district_code": "BD-01", "epi_week": 30, "seasonal_index": 1.8, "peak_flag": true}
  ]
}
```
Used by: seasonal heatmap and preparedness calendar. Keys `district_code` may be `"ALL"` for national.

### allocation.json
```json
{
  "generated_at": "...",
  "alpha_values": [0, 0.25, 0.5],
  "budget_levels": [100, 200, 300, 400, 500],
  "rows": [
    {"budget_level": 300, "alpha": 0.25, "strategy": "optimised", "district_code": "BD-01",
     "resource": "iv_saline_units", "allocated_units": 12.0, "unmet_demand": 3.0}
  ],
  "curve": [
    {"budget_level": 300, "alpha": 0.25, "strategy": "optimised", "total_unmet": 140.0, "equity_gini": 0.21}
  ]
}
```
Used by: allocation view and the efficiency-equity curve. Includes at least 5 budget levels and both strategies (`optimised`, `population_proportional`).

### stock.json
```json
{
  "generated_at": "...",
  "rows": [
    {"district_code": "BD-01", "epi_year": 2025, "epi_week": 30, "iv_saline_units": 400,
     "ns1_kits": 120, "ors_sachets": 900, "occupied_beds": 180, "beds_capacity": 240}
  ]
}
```
Used by: allocation view (existing stock versus forecast demand). Latest 52 weeks only, to stay under 10 MB.

### freshness.json
```json
{"generated_at": "...", "data_as_of": "...", "last_batch_id": "...", "pipeline_version": "v1.0"}
```
Used by: the freshness badge shown in the dashboard header.

## Dashboard behaviour that depends on this contract

- One shared filter state (disease, district set, week range). Every view reads from the same loaded files and filters client-side.
- Suppressed cells are drawn with a hatch pattern and the tooltip reads "suppressed (fewer than 5 cases)".
- The freshness timestamp comes from `freshness.json`, never from the browser clock.

## Change process

After freeze, fields may only be ADDED. Never rename or remove. Announce changes in the group chat and note them at the bottom of this file.

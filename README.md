# EpiScope

**Video presentation:**  (chapter timestamps:)

Big-data surveillance, forecasting and response-planning platform for seasonal and epidemic disease in Bangladesh
(CSE 4345 CEP). Encounters, facilities and stock are **synthetic** (seeded generator in `generator/`). Weather is real
(Open-Meteo ERA5 archive, CC BY 4.0, `scripts/s3_download_weather.py`), population is the WorldPop 2020 raster
(`scripts/s2_download_population.py`; a modelled estimate, not the 2022 census) and district boundaries come from
`scripts/s1_download_boundaries.py`. What data each experiment ran on is listed in `docs/experiments/data_kinds.md`.

## Prerequisites
- Python 3.11 and Java 17 (for Spark). Developed and tested on Windows with PowerShell; the commands below are PowerShell.
- GNU Make. On Windows: `winget install ezwinports.make`. Without Make, use the three commands under "Without make".
- `py -3.11 -m venv .venv`, then `.\.venv\Scripts\Activate.ps1`, then `pip install -r requirements.txt` (versions are pinned).
- Secrets: copy `.env.example` to `.env` and set the hashing salt (never committed).
- 16 GB RAM is enough for every scale factor we ran (SF 0.05 to 1).

## One-command run (ENG-1)
```
make all
```
From a clean clone this generates the synthetic data (SF 0.05, seed 42), builds the lake and runs every analytics stage. It took
about 15 minutes on our 16 GB laptop. It does two things in order:
1. `python -m pipeline.run_all`: generate -> bronze -> silver -> gold -> contract -> catalog -> export -> alerts -> demand ->
   allocate -> scenarios -> calendar -> serve. The `serve` stage writes the dashboard data to `serving/data/`
   (about 9 MB, small counts suppressed).
2. `analytics-real`: exports gold to CSV (`data/gold_csv`) and runs `python -m analytics.run_all`, which repeats the
   detection, forecast, allocation and calendar computations on the same data and adds the input-provenance check
   (`data/analytics/input_provenance.json`), the forecast backtest summary and the SEIR scenarios. Pass `--skip-scenarios`
   to `analytics.run_all` to omit the peak-season allocation scenarios.

Every stage prints its time and a summary table is printed at the end. Options of `python -m pipeline.run_all`:
`--only STAGE`, `--retries N`, `--sf SF` (generator scale, only used when it has to generate), `--regenerate` (rebuild
the synthetic data; skipped when `data/incoming` exists) and `--evaluate` (ground-truth evaluation, not part of the delivery
run).

**Without make:**
```
python -m pipeline.run_all
python -m analytics.export_gold --gold data/gold --out data/gold_csv
python -m analytics.run_all --gold data/gold_csv
```
**Dashboard** (from the repository root): `python -m http.server 8000`, then open http://localhost:8000/dashboard/index.html.

## Hidden 14-day test drop
The drop is ingested without any code change and without hard-coded dates, file names or row counts.
1. Copy the drop's files into `data/incoming/`, keeping the layout of the generator output:
   `encounters/batch_week=YYYY-MM-DD/part-*.csv` and `stock/batch_week=YYYY-MM-DD/stock_*.jsonl`.
2. Run the one-command run again: `make all`. The `generate` stage is skipped because `data/incoming` exists.
3. What happens:
   - **bronze** loads only the new files (the log line reads `found N files | new X | already loaded Y`); every file is
     registered with source, load time, batch ID and checksum, so re-running a day creates no duplicates (ING-4).
   - **silver** applies the quality rules, sends failing rows to the quarantine tables with a reason code, drops
     duplicate records and rewrites the epi-weeks touched by late records (`epi-weeks rewritten N`).
   - **gold**, **alerts**, **allocate**, **calendar** and **serve** are regenerated. Alerts, stock and the allocation move to the new last week;
     `serving/data/meta.json` lists the window per disease (`disease_windows`) and `data_as_of`, which move with the data.
   - **The forecast stops at the last day with weather.** `data/reference/weather.csv` ends on 2025-12-31, so the forecast origin
     stays at 2025-W52 even when the drop runs to 2026-01-14 (the log reads `prepare_cases: clipping to last weather week`),
     and demand follows the forecast. The input check accepts a last case week up to 28 days after the last weather day and
     stops the run with `weather covers every case week` beyond that. Weather is fetched by `scripts/s3_download_weather.py`
     (Open-Meteo, needs internet).
4. Check: `python scripts/check_serving.py` ends with `RESULT: all checks passed`.

To rehearse it with our generator (same seed, so the increment is a slice of the same run):
```
Copy-Item data\reference data_drop\reference -Recurse
python -m generator.generate --sf 0.05 --end 2026-01-14 --report-from 2026-01-01 --report-to 2026-01-14 --out-dir data_drop
Copy-Item data_drop\incoming\* data\incoming\ -Recurse -Force
make all
```
Use the same `--sf` as the data in `data/` (default 0.05). Running `make all` a second time without new files changes
nothing.

## Scaling experiment (ENG-2)
Full pipeline at SF 0.25, 0.5 and 1; logs and per-stage timings are in `docs/experiments/scaling_sf*_pipeline.log` and
`docs/experiments/scaling_sf*_analytics_timing.csv`. At SF 1 (about 26 million encounters) the pipeline stages took 16.7 minutes
(23.4 minutes with data generation), inside the 45-minute limit, measured before the gold-stage optimisation in ENG-3. The
SF 1 serve stage first failed the 10 MB dashboard limit (VIS-4, log kept in `scaling_sf1_pipeline.log`) and passed after the fix
(`scaling_sf1_serve_rerun.log`).
<!-- CONFIRM: hardware line for the report and here (CPU model, 16 GB RAM, SSD). SF 0.25 was run by the Data Engineer, on which machine? -->

## Architecture
Medallion lakehouse: bronze (raw, immutable) -> silver (clean, de-identified, quarantine) -> gold (epi-week marts) ->
analytics (detection, forecast, demand, allocation, calendar) -> serving (suppressed JSON) -> D3.js dashboard.
**Storage.** The lake is Parquet on the local file system, partitioned by epi-week, with a Spark SQL catalog
(`data/catalog`, tables `bronze.*`, `silver.*`, `gold.*`). Spark runs in local mode; pandas is used only for small
reference data and the final aggregated exports. **Orchestration.** `pipeline/run_all.py` is a small DAG runner with
per-stage retries, timings and a summary, driven by the `Makefile`; the contract stage fails the run if a gold table has
missing columns or bad keys. **Privacy.** Raw patient IDs exist only in bronze; silver holds salted hashes (the salt is
in `.env`, never committed), and the serving export suppresses every count from 1 to 4 and drops alert rows on
suppressed weeks (PRV-2). **Serving and dashboard.** `pipeline/serving_export.py` writes eleven small JSON files to
`serving/data/`; the total must stay under 10 MB, and a size valve shortens the oldest ILI and malaria weeks if it
would not (every trim is logged; ILI never starts later than 2023-02-13). The windows (ILI from 2022-12-19, malaria the
last 52 weeks) only limit how much history the dashboard files hold; they are options of the exporter, not of the
ingestion or the analytics. The D3.js v7 dashboard in `dashboard/` is static and reads only these files, never
line-level data.

**Analytics** (`analytics/`): EARS C2 and CUSUM detection with reporting-artefact suppression, a negative-binomial forecast
with lagged weather, demand conversion, a linear-programme allocation with an efficiency-equity curve, an STL-based
preparedness calendar and SEIR scenarios. Detection, forecast, optimiser and calendar code never read the ground-truth
files (`tests/test_truth_isolation.py`) or test fixtures (`tests/test_no_fixture_in_delivery.py`).

## Repository map
| Path | Content |
|---|---|
| `generator/`, `scripts/` | seeded synthetic generator (S4-S6); public-data download scripts S1-S3; serving checks |
| `pipeline/` | bronze, silver, gold, quality rules, catalog, serving export, `run_all.py` |
| `analytics/` | detection, forecast, optimise, calendar, scenario, check scripts, `run_all.py` |
| `serving/`, `dashboard/` | suppressed JSON exports and boundaries; the D3.js dashboard |
| `tests/` | automated tests (quality rules, de-identification, optimiser constraints, truth and fixture isolation, README paths) |
| `docs/` | proposal (`docs/proposal.md`), contracts, assumptions, experiment logs (`docs/experiments/`), report PDF <!-- FILL IN: report PDF name when ready --> |
| `AI_USAGE.md` | AI tools, purposes and representative prompts |

## Dashboard (D3.js v7)

An interactive, coordinated-view dashboard for national, division and district decision-makers. It is built with
D3.js v7 only (VIS-1), runs from static files, and loads only the aggregated, privacy-suppressed JSON in `serving/data/`
(VIS-4). It never reads line-level data.

### Run it

The exports in `serving/data/` come from the pipeline (`make all`, `serve` stage) and are committed, so the dashboard also runs from a fresh clone without Spark.
Prerequisite for the dashboard alone: Python 3 (only to serve static files) and a modern browser. No internet is needed: D3 v7.9.0 is vendored in
`dashboard/vendor/d3.v7.min.js`.

```bash
# from the repository root
python -m http.server 8000
# then open
http://localhost:8000/dashboard/
```

Do not open `dashboard/index.html` by double-click: browsers block `fetch` on `file://`.

| URL | Data read |
|---|---|
| `/dashboard/` | real exports in `serving/data/` (default) |
| `/dashboard/?data=sample` | hand-made samples in `serving/sample/` (made by `python scripts/make_sample_data.py`; they cover the main views only, not the allocation plan, calendar or data-quality panels) |

`meta.json`, `freshness.json` and `cases_weekly.json` are required; the dashboard stops with a clear message if one is
missing. The other files are optional and the matching view says so when its file is absent.

### Data folders and files

| Path | Role |
|---|---|
| `serving/geo/bgd_adm2_simplified_coded.geojson` | 64 district boundaries, key `district_code` (2-digit string). Outer rings are re-wound in the browser because d3-geo needs the opposite winding to GeoJSON. |
| `serving/data/` | real exports written by the pipeline (contract: `docs/serving_contract.md`) |
| `serving/sample/` | hand-made samples with the same shape |
| `scripts/check_serving.py` | checks the exports against the contract, PRV-2 and the 10 MB limit (`python scripts/check_serving.py`) |

Which file feeds which view:

| File | Used by |
|---|---|
| `cases_weekly.json` | choropleth, time series, early-warning table (cases column), heatmap week mapping |
| `forecast.json` | time series (median and q10-q90 band) |
| `alerts.json` | alert dots, time series strip, early-warning table, what-if threshold |
| `seasonality.json`, `calendar.json` | seasonal heatmap and preparedness calendar |
| `allocation.json`, `allocation_peak.json` | allocation view and what-if panel (live and peak scenarios) |
| `dq_report.json` | data-quality panel |
| `freshness.json`, `meta.json` | freshness line on every view; districts, diseases, per-disease windows |
| `stock.json` | exported by the pipeline but not drawn: stock and demand per district come from the `plan` in `allocation.json` |

The SEIR scenarios (AN-10) are written to `data/analytics/seir_*.csv` and are not shown on the dashboard.

### Views and requirements

| View | Code | Requirements |
|---|---|---|
| 1. District choropleth of incidence per 100,000, with alert dots | `dashboard/js/views/choropleth.js` | VIS-2 (1), VIS-3, VIS-6, PRV-2 |
| 2. Time series: observed, forecast median, q10-q90 band, alert score vs. threshold, brush/zoom | `views/timeseries.js` | VIS-2 (2), VIS-3, VIS-6 |
| 3. Seasonal heatmap (division x epi-week) and preparedness calendar | `views/seasonal.js` | VIS-2 (3), AN-9 (display) |
| 4. Allocation: forecast demand, current stock, recommended allocation; efficiency-equity curve | `views/allocation.js` | VIS-2 (4), AN-7/AN-8 (display) |
| 5. Ranked early-warning table | `views/warnings.js` | VIS-2 (5), VIS-3 |
| What-if panel: budget, equity floor, detection threshold | `views/whatif.js`, `alerts.js` | VIS-5 |
| Data-quality panel (per load) | `views/dq.js` | DQ-2 |
| Role view (national / district officer demo) | `state.js` (guard), `main.js`, all views | PRV-3 |
| Shared state, loading, freshness | `state.js`, `data.js`, `main.js` | VIS-3, VIS-6 |

VIS-7 (mobile district-officer view or outbreak playback) is not implemented. The layout stacks on narrow screens, but it
has not been tested on phones and is not claimed as a deliverable.

### Linked interaction (VIS-3)

All views read one shared state: disease, district, selected week, week range, budget, equity floor, detection
threshold, scenario, role.

| Action | Effect |
|---|---|
| Click a district on the map, a table row, or an allocation bar | selects the district everywhere (time series switches to it; map outline; table and heatmap row highlight) |
| Drag on the overview chart under the time series | sets the week range (zooms the time series; the week slider follows). Click it to reset |
| Click the time series, or a heatmap column | selects that week; the map, table and what-if cards redraw |
| Change disease | all views redraw; the threshold slider resets |
| Move budget, equity floor or threshold sliders | allocation, alert dots, table, strip and result cards update together |

### Honesty, privacy and accessibility

- **Small-cell suppression (PRV-2).** The exporter hides counts of 1-4. The dashboard draws these cells with a hatch
  pattern and the tooltip reads "suppressed (fewer than 5 cases)". National totals on the time series sum only
  non-suppressed districts and say so on screen. `scripts/check_serving.py` fails if any visible count of 1-4 appears.
  To save space the exporter omits the `suppressed` key when it is false (an absent key means false).
- **Aggregation levels (VIS-4).** Cases are served per district and week, seasonality per division, allocation as a
  precomputed grid. The whole `serving/data/` folder must stay under 10,000,000 bytes (checked by the script).
- **Uncertainty.** The forecast q10-q90 band is always drawn. National forecast bands are sums of district quantiles and are
  labelled approximate.
- **Freshness (VIS-6).** Every panel shows the data date and generation time from `freshness.json`; the allocation panel also
  shows its own date (latest stock snapshot). Nothing uses the browser clock, and no dates or file names are hard-coded, so
  a new export or the hidden test drop is picked up by reloading the page. Each disease uses its own exported week window;
  weeks outside it are shown as "not exported", which is different from "no data".
- **Colour.** Colour-blind-safe scales (ColorBrewer YlOrRd and YlGnBu, viridis, Okabe-Ito). Meaning is never carried by colour
  alone: suppression is hatched, severity uses triangles plus text, and table rows can be reached with the keyboard.
- **Role view (PRV-3).** "District officer (demo)" shows the chosen district in detail and every other district only as its
  division's aggregate (an aggregate needs at least two districts with published counts). This is a front-end demonstration:
  every export file is still downloadable from the browser, so real access control would have to be enforced on a server.
- **What-if detection threshold (VIS-5).** Alerts are re-thresholded in the browser from the exported scores. The slider can go
  down to about half the default because the export keeps only scores above that. Severity labels are not recalculated.
  Allocation figures come from the precomputed grid; nothing is re-optimised in the browser.

### Dashboard layout

```
dashboard/
  index.html
  css/style.css
  js/main.js        load data, create the shared store, controls, role view, freshness
  js/state.js       shared store with subscribe/set and an optional guard
  js/data.js        the only module that knows file names; per-disease windows; geojson rewind
  js/alerts.js      alert logic for the what-if threshold
  js/views/         choropleth, timeseries, seasonal, allocation, warnings, whatif, dq
  vendor/d3.v7.min.js
scripts/make_sample_data.py, scripts/check_serving.py
```

## Requirement traceability
| ID | Where | Report section |
|---|---|---|
| ING-1 generator and bronze with ingestion metadata | `generator/generate.py`, `generator/model.py`, `pipeline/bronze/ingest.py`, `scripts/s1_download_boundaries.py`, `scripts/s2_download_population.py`, `scripts/s3_download_weather.py`; tests `tests/test_generator_model.py`, `tests/test_bronze.py` | §4 |
| ING-2 Spark for all heavy transformations | `pipeline/spark_session.py`, `pipeline/silver/build.py`, `pipeline/gold/build.py`, `pipeline/gold/marts.py` | §3 |
| ING-3 partitioned Parquet, Spark SQL catalog | `pipeline/catalog.py`, `pipeline/silver/build.py`, `pipeline/gold/build.py`; tests `tests/test_silver.py`, `tests/test_gold_marts.py` | §3 |
| ING-4 incremental, idempotent loads, late records | `pipeline/bronze/ingest.py`, `pipeline/silver/build.py`; tests `tests/test_bronze.py`, `tests/test_silver.py` | §4 |
| ING-5 HDFS or object store | not implemented: the lake is on the local file system | §3, §9 |
| DQ-1 at least 12 quality rules, quarantine | `pipeline/quality/rules.py`, `pipeline/silver/build.py`; test `tests/test_silver.py` | §4 |
| DQ-2 per-load quality report, shown on the dashboard | `pipeline/gold/marts.py`, `dashboard/js/views/dq.js`, `serving/data/dq_report.json` | §4, §7 |
| DQ-3 lineage to bronze batch IDs | `pipeline/gold/marts.py`, `pipeline/quality/check_gold_contract.py`, `docs/gold_contract.md` | §4 |
| PRV-1 salted hash, nothing identifying beyond bronze | `pipeline/silver/transforms.py`, `pipeline/silver/build.py`; test `tests/test_silver.py` | §4 |
| PRV-2 small-cell suppression, no recovery by subtraction | `pipeline/serving_export.py`, `scripts/check_serving.py`, `docs/serving_contract.md` | §4, §8 |
| PRV-3 role-based views (front-end demonstration) | `dashboard/js/state.js`, `dashboard/js/main.js` | §7, §8 |
| VIS-1 D3.js v7 only | `dashboard/index.html`, `dashboard/vendor/d3.v7.min.js` | §7 |
| VIS-2 five coordinated views | `dashboard/js/views/choropleth.js`, `dashboard/js/views/timeseries.js`, `dashboard/js/views/seasonal.js`, `dashboard/js/views/allocation.js`, `dashboard/js/views/warnings.js` | §7 |
| VIS-3 linked interaction | `dashboard/js/state.js`, `dashboard/js/main.js` | §7 |
| VIS-4 aggregated data only, at most 10 MB | `pipeline/serving_export.py`, `scripts/check_serving.py`, `docs/serving_contract.md` | §7 |
| VIS-5 what-if panel | `dashboard/js/views/whatif.js`, `dashboard/js/alerts.js` | §7 |
| VIS-6 accessibility, uncertainty, freshness on every view | `dashboard/js/main.js`, `dashboard/js/data.js`, `serving/data/freshness.json` | §7 |
| VIS-7 mobile view or outbreak playback | not implemented | §9 |
| AN-1 outbreak detection (Spark) | `analytics/detection/make_alerts.py`, `analytics/detection/ears_c2.py`, `analytics/detection/cusum.py`, `analytics/detection/variants.py`; tests `tests/test_detectors.py`, `tests/test_applyinpandas.py`, `tests/test_cusum_masking.py` | §5 |
| AN-2 detection evaluation, trade-off curve (C1) | `analytics/detection/run_detection.py`, `analytics/detection/evaluate.py`, `analytics/detection/plot_tradeoff.py`, `analytics/detection/detection_holdout.py`, `docs/experiments/c1_tradeoff.png`; tests `tests/test_evaluate.py`, `tests/test_eval_metrics_detection.py` | §6 |
| AN-3 two methods, reporting-artefact handling | `analytics/detection/variants.py`, `analytics/detection/artefact_demo.py`, `analytics/detection/artefact_stress_test.py`, `docs/experiments/data_kinds.md`; tests `tests/test_artefact_weeks.py`, `tests/test_alerts_assessed.py` | §5, §6 |
| AN-4 forecasting, 1-4 weeks, lagged weather | `analytics/forecast/forecast.py`, `analytics/forecast/features.py`, `analytics/forecast/prep.py`, `analytics/optimise/run_demand.py`, `docs/forecast_assumptions.md`; tests `tests/test_forecast.py`, `tests/test_forecast_leakage.py`, `tests/test_features.py` | §5 |
| AN-5 rolling-origin backtest vs seasonal-naive | `analytics/forecast/backtest_report.py`, `analytics/forecast/ablation.py`, `analytics/forecast/asof_backtest.py`; test `tests/test_eval_metrics.py` | §6 |
| AN-6 demand conversion with uncertainty | `analytics/optimise/demand.py`, `analytics/optimise/demand_sensitivity.py`, `analytics/check_an6.py`, `docs/demand_assumptions.md`, `docs/demand_sensitivity.md`; test `tests/test_demand.py` | §5 |
| AN-7 allocation optimiser vs population baseline | `analytics/optimise/allocate.py`, `analytics/optimise/run_allocation.py`, `analytics/optimise/weights.py`, `analytics/optimise/scenario_demand.py`, `analytics/optimise/allocation_sensitivity.py`, `analytics/optimise/run_scenarios.py`, `docs/allocation_assumptions.md`; tests `tests/test_allocate.py`, `tests/test_allocate_extensions.py`, `tests/test_allocation_sensitivity.py`, `tests/test_weights.py` | §5, §6 |
| AN-8 efficiency-equity curve (C3) | `analytics/optimise/allocate.py` (`efficiency_equity_curve`, `recommend_alpha`), `analytics/optimise/plot_equity_curve.py`, `docs/experiments/c3_efficiency_equity.png` | §6 |
| AN-9 seasonal preparedness calendar | `analytics/calendar/preparedness.py`, `analytics/check_an9.py`; tests `tests/test_season_window.py`, `tests/test_calendar_dates.py` | §5 |
| AN-10 SEIR scenarios (bonus) | `analytics/scenario/seir.py`, `analytics/scenario/make_ili_series.py`, `analytics/scenario/run_seir.py`; test `tests/test_seir.py` | §5 |
| ENG-1 one-command run, pinned dependencies | `Makefile`, `pipeline/run_all.py`, `analytics/run_all.py`, `requirements.txt` | §3 |
| ENG-2 scalability experiment SF 0.25, 0.5, 1 | `docs/experiments/scaling_sf025_pipeline.log`, `docs/experiments/scaling_sf05_pipeline.log`, `docs/experiments/scaling_sf1_pipeline.log`, `docs/experiments/scaling_sf1_serve_rerun.log`, `docs/experiments/scaling_sf05_analytics_timing.csv`, `docs/experiments/scaling_sf1_analytics_timing.csv` | §6 |
| ENG-3 Spark optimisations with before/after evidence | `pipeline/gold/weeks.py`, `pipeline/gold/build.py`, `docs/experiments/eng3_gold_sf05_before.log`, `docs/experiments/eng3_gold_sf05_opt1.log` | §6 |
| ENG-4 tests, isolation guards | `tests/test_truth_isolation.py`, `tests/test_no_fixture_in_delivery.py`, `tests/test_readme_traceability.py`, `analytics/check_inputs.py`, all `tests/test_*.py` | §6 |
| ING-6 | Not implemented | EpiScope is a batch pipeline. New data arrives as files and is ingested incrementally and idempotently (make all); there is no Structured Streaming path. |
| ING-7 | Not implemented | Storage is partitioned Parquet with a Spark SQL catalog. No Delta Lake or Iceberg, so no time travel. Reproducing "what we knew on date t" relies on the bronze file registry and first_report_date. |
| DQ-4 | Partial | Silver removes re-delivered encounter records: one row per encounter ID, keeping the latest delivery (the generator injects about 2% such duplicates). Cross-facility patient de-duplication (same hashed ID and compatible attributes within 7 days) is not implemented, and its precision was not measured. |
| PRV-4 | Not implemented | No k-anonymity check and no written differential-privacy discussion. The related control is PRV-2 small-cell suppression (counts of 1–4 hidden in the exported data). |


## Tests
```
python -m pytest tests -q
```
The Spark tests need Java; the full suite takes about 14 minutes (269 tests passed at the last full run).

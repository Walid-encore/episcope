# EpiScope

**Video presentation:** <PASTE UNLISTED YOUTUBE / DRIVE LINK HERE>  (chapter timestamps: <PASTE>)

Big-data surveillance, forecasting and response-planning platform for seasonal and epidemic disease in Bangladesh
(CSE 4345 CEP). All data are **synthetic** (generator in `generator/`) apart from public boundaries, population and weather.

## Prerequisites
- Python 3.11, Java 17 (for Spark), Windows PowerShell or a POSIX shell
- `py -3.11 -m venv .venv` then `.\.venv\Scripts\Activate.ps1` and `pip install -r requirements.txt`
- Secrets: copy `.env.example` to `.env` and set the hashing salt (never committed)

## One-command run (ENG-1)
```
make all
```
`make all` runs `pipeline.run_all` (generate -> bronze -> silver -> gold -> contract -> catalog -> export -> alerts ->
demand -> allocate -> scenarios -> calendar -> serve), exports gold to CSV (`data/gold_csv`) and then the whole
analytics run. The `serve` stage writes the dashboard data to `serving/data/` (about 9 MB, small counts suppressed).
Every stage prints its time; a summary table at the end lists them. Options of `python -m pipeline.run_all`:
`--only STAGE` (one stage), `--retries N`, `--regenerate` (rebuild the synthetic data; skipped when `data/incoming`
exists) and `--evaluate` (ground-truth evaluation, not part of the delivery run). Pinned dependencies are in
`requirements.txt`. Dashboard (from the repository root): `python -m http.server 8000`, then open
http://localhost:8000/dashboard/index.html.

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
   - **gold**, **alerts**, **demand** (forecast), **allocate**, **calendar** and **serve** are regenerated, so
     `data/analytics/` and `serving/data/` reflect the new last week. `serving/data/meta.json` lists the window per
     disease (`disease_windows`) and `data_as_of`, which move with the data.
4. Check: `python scripts/check_serving.py` ends with `RESULT: all checks passed`.

To rehearse it with our generator (same seed, so the increment is a slice of the same run):
```
Copy-Item data\reference data_drop\reference -Recurse
python -m generator.generate --sf 0.05 --end 2026-01-14 --report-from 2026-01-01 --report-to 2026-01-14 --out-dir data_drop
Copy-Item data_drop\incoming\* data\incoming\ -Recurse -Force
make all
```
Use the same `--sf` as the data in `data/` (default 0.05). Running `make all` a second time without new files changes nothing.

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
| `stock.json`, `seir.json` | exported by the pipeline but not drawn: stock and demand per district come from the `plan` in `allocation.json`, and the SEIR scenarios are not shown on the dashboard |

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

### Usability check (report section 7)

Two non-CS participants, about 20 minutes each, no help given. Fill in after the sessions.

| Task | Participant 1 | Participant 2 |
|---|---|---|
| Which district has the highest dengue incidence in the selected week? | TBD | TBD |
| What does the hatched grey mean on the map? | TBD | TBD |
| Which division should prepare supplies first for dengue next season? | TBD | TBD |
| If the budget doubles, what happens to unmet demand? | TBD | TBD |

Tested in: TBD (browser and version).

## Requirement traceability
| ID | Where | Report section |
|---|---|---|
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
| ENG-4 tests, isolation guards | `tests/test_truth_isolation.py`, `tests/test_no_fixture_in_delivery.py`, `analytics/check_inputs.py`, all `tests/test_*.py` | §6 |
<!-- FILL IN: ING-*, DQ-*, PRV-*, VIS-*, ENG-1..3 rows. -->

## Tests
```
python -m pytest tests -q
```

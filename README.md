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

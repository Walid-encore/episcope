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
`make all` runs `pipeline.run_all` (bronze -> silver -> gold), exports gold to CSV (`data/gold_csv`) and then the whole
analytics run. Pinned dependencies are in `requirements.txt`.
Analytics only, on existing gold CSV (no flags needed):
```
python -m analytics.run_all
```
This runs detection, forecast + demand, backtest summary, allocation, the peak-season allocation scenarios with
sensitivity (`--skip-scenarios` to omit, about 1 minute), and the preparedness calendar. Outputs go to `data/analytics/`.
Nothing is hard-coded: dates, week ranges and the stock cut-off come from the data.
`--gold` must point at the folder holding `gold_weekly_cases.csv` (the CSV export, `data/gold_csv`), not the parquet folder.
Ground-truth evaluation for the report is separate (`make evaluate`) and is not part of the delivery run.

## Hidden 14-day test drop
<!-- FILL IN: where the drop is placed (e.g. data/incoming/...), then the command(s) to ingest it. -->
1. Place the drop files in the incoming folders using the same schema.
2. Run the one-command run above. Ingestion is idempotent, so re-running a day creates no duplicates.
3. Alerts, forecast, demand, allocation and sensitivity regenerate in `data/analytics/`.

## Architecture
Medallion lakehouse: bronze (raw, immutable) -> silver (clean, de-identified, quarantine) -> gold (epi-week marts) ->
analytics (detection, forecast, demand, allocation, calendar) -> serving (suppressed JSON) -> D3.js dashboard.
<!-- FILL IN: one paragraph on storage, orchestration and the dashboard command (e.g. python -m http.server). -->

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

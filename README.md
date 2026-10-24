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
| AN-1 detection | `analytics/detection/make_alerts.py` | 5 |
| AN-2 detection evaluation | `analytics/detection/run_detection.py`, `plot_tradeoff.py` | 6 |
| AN-3 two methods + artefact | `analytics/detection/` (EARS C2, CUSUM), `tests/test_artefact_weeks.py` | 6 |
| AN-4 forecasting | `analytics/forecast/`, `analytics/optimise/run_demand.py` | 5 |
| AN-5 backtest vs seasonal-naive | `analytics/forecast/backtest_report.py` | 6 |
| AN-6 demand conversion | `analytics/optimise/demand.py`, `demand_sensitivity.py`, `analytics/check_an6.py`, `docs/demand_assumptions.md` | 5 |
| AN-7 allocation LP | `analytics/optimise/allocate.py`, `run_allocation.py`, `allocation_sensitivity.py`, `run_scenarios.py`, `docs/allocation_assumptions.md` | 5, 6 |
| AN-8 efficiency-equity curve | `analytics/optimise/allocate.py` (`efficiency_equity_curve`), `data/analytics/allocation_peak/` | 6 |
| AN-9 preparedness calendar | `analytics/calendar/preparedness.py` | 5 |
| ENG-4 tests | `tests/` (`test_allocate.py`, `test_allocation_sensitivity.py`, ...) | 6 |
<!-- FILL IN: ING-*, DQ-*, PRV-*, VIS-*, ENG-1..3 rows. -->

## Tests
```
python -m pytest tests -q
```

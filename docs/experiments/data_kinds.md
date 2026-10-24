# What data each experiment ran on

Every analytics result in this repository starts from the pipeline's own gold tables (`data/gold`, exported to
`data/gold_csv`). Each run records the file hash and checks in `data/analytics/input_provenance.json`
(`python -m analytics.check_inputs`). The kinds of data are:

- **Synthetic by design (brief section 5):** encounters, facilities and stock come from the seeded generator (`generator/`, about SF=0.05 here).
- **Public:** weather is the Open-Meteo ERA5 archive (`scripts/s3_download_weather.py`); population is the WorldPop 2020
  1 km raster, a modelled estimate and not the 2022 census (`scripts/s2_download_population.py`).
- **Generator ground truth:** injected outbreaks and artefacts, used only by evaluation scripts, never by detection, forecast, optimiser or calendar code (`tests/test_truth_isolation.py`).

| Output | Data | Notes |
|---|---|---|
| `c1_tradeoff.png`, `detection_*.csv` (run_detection) | real gold + generator outbreak truth | operating point chosen in-sample |
| `detection_holdout.csv` | real gold + generator outbreak truth | temporal hold-out: threshold chosen on one period, scored on the other |
| `detection_artefact_demo.csv`, `detection_artefact_weekly.csv` | real gold + generator artefact file | the generator's own artefacts; they barely move district counts, so a naive detector also stays quiet |
| `detection_artefact_stress.csv` | **semi-synthetic** | artefacts INJECTED into a copy of the real gold counts in the analytics layer (seeded, no truth read, the lake is unchanged); measures false alert weeks, naive versus pipeline |
| forecast backtests | real gold + ERA5 weather | 52-origin rolling backtest |
| demand, allocation, efficiency-equity curve (`c3_efficiency_equity.png`), sensitivity | real gold forecasts + synthetic stock snapshots + WorldPop population | conversion coefficients are the generator's parameters, unit costs and shipping volumes are stated assumptions (`docs/demand_assumptions.md`, `docs/allocation_assumptions.md`) |
| preparedness calendar | real gold | STL seasonal decomposition, 4 and 8 week lead times are assumptions |
| SEIR scenarios | real gold (the generator's injected novel-respiratory surge) | window found from the data |

Anything named `*_fixture*` is a superseded prototype on a test fixture and must not be cited as a result.

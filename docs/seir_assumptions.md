# AN-10 SEIR scenario module: assumptions and limits

Run: `python -m analytics.scenario.run_seir --gold-csv data/gold_csv` -> data/analytics/seir_scenarios.csv, seir_summary.csv, seir_fit.json.

- Data: national weekly ILI from the real gold (`gold_weekly_cases.csv`), which is itself built from the synthetic generator.
  The stage refuses folders named fixture or sample. seir_fit.json records the gold file, row count and sha256.
- Surge window: found from the data (excess over a leave-one-year-out seasonal median). No outbreak-label file is read.
- Model: SEIR, homogeneous mixing, national population (sum of district populations). Latent period 4 days and infectious period
  6 days are fixed assumptions, not fitted. R0, reporting fraction and the seed are fitted by least squares on sqrt counts.
- Uncertainty: residual bootstrap of the fit. The bands cover fit uncertainty only, not the fixed assumptions or model structure.
- Scenarios: 20% and 40% cut in transmission from an early start and a late start (weeks after the surge window starts).
  They are what-if illustrations, not predictions of what an intervention would do.
- Limits: a national homogeneous model ignores district spread and age structure; the injected surge need not follow SEIR dynamics,
  so report the fit quality (R2, NRMSE) with the scenarios; R0 and the reporting fraction trade off against each other.

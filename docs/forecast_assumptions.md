# AN-4 / AN-5 forecasting assumptions

- Weather input: data/reference/weather.csv (Open-Meteo daily archive, cached in bronze), aggregated to ISO weeks
  by analytics.forecast.features. The gold_weekly_weather table is not read by the forecaster.
- Edge weeks: prepare_cases drops the first week and the newest --trim-last-weeks (default 1) weeks, and never
  runs past the last week with weather. Reports can arrive up to 21 days late, so the newest retained weeks may
  be slightly under-counted; the as-of backtest (docs/experiments/forecast_backtest_asof.csv) shows the effect is small, so a one-week trim is kept.
- Backtest: rolling-origin over the last 52 target weeks. The main backtest uses final counts; the as-of backtest uses only the reports received by each origin (gold_weekly_cases_by_lag, maximum report lag 4 weeks).
  Both are scored against final counts. Interval widths are NB quantiles without coefficient uncertainty.
- Weather lags: the production window (weeks T-4..T-8) is a design choice informed by docs/experiments/lag_correlation.csv,
  which supports it for dengue only; docs/experiments/forecast_ablation*.csv compare it with no weather and with
  shorter lags per disease.
- AN-4/AN-5 evidence (52-week backtest, horizons 1-4, 256 series): the NB model with weather lags 4-8 has WIS 1.548 against 2.205 for persistence and 2.457 for seasonal-naive. With only the reports received by each origin its WIS is 1.572 (+1.5%), still 36% below seasonal-naive and 34% below persistence. Production keeps the uncalibrated intervals because allocation plans on the 90th percentile and a conservative upper quantile is preferred for stock.

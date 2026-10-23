import pandas as pd
from analytics.forecast.features import build_weekly_weather, build_model_table, lag_correlation
c = pd.read_parquet(r"data\gold\gold_weekly_cases")
c["district_code"] = c["district_code"].astype(str).str.zfill(2)
c["week_start_date"] = pd.to_datetime(c["week_start_date"]).dt.round("D")
wx = build_weekly_weather(pd.read_csv(r"data\reference\weather.csv", dtype={"district_code": str}))
m = build_model_table(c, wx, require_lags=True)
out = pd.concat([lag_correlation(m, d).assign(disease=d) for d in m["disease"].unique()])
out.to_csv(r"docs\experiments\lag_correlation.csv", index=False); print(out)

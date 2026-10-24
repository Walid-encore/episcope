import os
import pandas as pd

K = ["district_code", "disease", "epi_year", "epi_week"]
f = pd.read_parquet(r"data\analytics\gold_forecast.parquet")
b = f[f.is_backtest.astype(bool) & (f.model == "seasonal_naive") & (f.horizon_weeks == 1)].copy()
b["district_code"] = b["district_code"].astype(str).str.zfill(2)
b = b.rename(columns={"target_epi_year": "epi_year", "target_epi_week": "epi_week"})[K + ["actual_cases"]]
print("forecast backtest rows:", len(b))

def check(label, c):
    c = c.copy()
    print(f"\n{label}: columns {c.columns.tolist()}")
    col = "cases" if "cases" in c.columns else next(x for x in c.columns if "case" in x)
    c["district_code"] = c["district_code"].astype(str).str.zfill(2)
    c["epi_year"] = c["epi_year"].astype(int)
    c["epi_week"] = c["epi_week"].astype(int)
    c = c.groupby(K, as_index=False)[col].sum()
    m = b.merge(c, on=K, how="left")
    print(f"  matched {m[col].notna().sum()} of {len(b)}, exactly equal {(m[col] == m['actual_cases']).sum()}")

check("data\\gold", pd.read_parquet(r"data\gold\gold_weekly_cases"))

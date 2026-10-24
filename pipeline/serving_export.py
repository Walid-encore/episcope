"""Serving export (PRV-2, VIS-4): gold and analytics outputs -> serving/data/*.json (docs/serving_contract.md v0.2).

    python -m pipeline.serving_export

Writes meta, freshness, cases_weekly, alerts, dq_report, stock, seasonality, calendar and forecast. Run it after the `calendar` stage: it reads the gold
CSVs written by the `export` stage (data/gold_csv), data/analytics/gold_alerts.parquet and data/gold/gold_dq_report.
No Spark needed. allocation is NOT written yet (see NOT_YET below).

Rules taken from the contract and from the visualisation engineer:
  - only the diseases in ALLOWED_DISEASES are exported (gold has four; meta lists only these)
  - cases 1-4 -> cases null, incidence_per_100k null, suppressed true; a true 0 stays 0, suppressed false
  - gold names week_start_date -> serving name week_start
  - compact JSON, district_code is a 2-digit string
  - cases_weekly < 6 MB, all files < 10 MB; the run exits 1 if a check fails
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import pandas as pd

ALLOWED_DISEASES = ["dengue", "diarrhoeal"]
PIPELINE_VERSION = "v1.0"
MB = 1024 * 1024
CASES_BUDGET, TOTAL_BUDGET = 6 * MB, 10 * MB
ALERT_WEEKS = 104
DQ_BATCHES = 14
NOT_YET = ["allocation.json"]


def code2(s):
    return s.astype(str).str.strip().str.zfill(2)


def clean(v):
    """NaN / NA -> None, numpy scalar -> python scalar."""
    if v is None or pd.isna(v):
        return None
    return v.item() if hasattr(v, "item") else v


def write_rows(out: Path, name: str, header: dict, key: str, df: pd.DataFrame) -> None:
    head = json.dumps(header, separators=(",", ":"))
    body = df.to_json(orient="records", double_precision=4)
    (out / name).write_text(f'{head[:-1]},"{key}":{body}}}', encoding="utf-8")


def write_obj(out: Path, name: str, obj: dict) -> None:
    (out / name).write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def require(df, cols, label):
    miss = [c for c in cols if c not in df.columns]
    if miss:
        sys.exit(f"{label}: missing columns {miss}; found {list(df.columns)}")


# ------------------------------------------------------------------ cases_weekly (the only file with counts)
def build_cases(gold_csv: Path, districts: pd.DataFrame, diseases) -> pd.DataFrame:
    path = gold_csv / "gold_weekly_cases.csv"
    if not path.exists():
        sys.exit(f"{path} not found: run the `export` stage first")
    c = pd.read_csv(path, dtype={"district_code": str})
    require(c, ["district_code", "disease", "epi_year", "epi_week", "week_start_date", "cases"], "gold_weekly_cases")
    c["district_code"] = code2(c["district_code"])
    c = c[c["disease"].isin(diseases)].copy()
    pop = districts.set_index("district_code")["population"]
    c["pop"] = c["district_code"].map(pop)
    unknown = sorted(c.loc[c["pop"].isna(), "district_code"].unique())
    if unknown:
        sys.exit(f"district codes not in data/reference/districts.csv: {unknown[:10]}")
    c["week_start"] = pd.to_datetime(c["week_start_date"]).dt.strftime("%Y-%m-%d")
    key = ["district_code", "disease", "epi_year", "epi_week"]
    if c.duplicated(key).any():
        sys.exit(f"gold_weekly_cases has {int(c.duplicated(key).sum())} duplicate keys")
    iso = pd.to_datetime(c["week_start"]).dt.isocalendar()
    bad = int(((iso["year"].astype(int) != c["epi_year"]) | (iso["week"].astype(int) != c["epi_week"])).sum())
    if bad:
        print(f"WARNING: {bad} rows where epi_year/epi_week differ from the ISO week of week_start "
              "(the contract says ISO year); tell the Data Engineer")
    n = c["cases"].astype("int64")
    sup = n.between(1, 4)
    out = pd.DataFrame({
        "district_code": c["district_code"],
        "disease": c["disease"],
        "epi_year": c["epi_year"].astype(int),
        "epi_week": c["epi_week"].astype(int),
        "week_start": c["week_start"],
        "cases": n.where(~sup).astype("Int64"),
        "incidence_per_100k": (n / c["pop"] * 1e5).round(2).where(~sup),
        "suppressed": sup,
    })
    return out.sort_values(["disease", "district_code", "week_start"]).reset_index(drop=True)


# ------------------------------------------------------------------ alerts
def build_alerts(path: Path, diseases, suppressed: pd.DataFrame) -> pd.DataFrame:
    if not path.exists():
        sys.exit(f"{path} not found: run the `alerts` stage first")
    a = pd.read_parquet(path)
    require(a, ["district_code", "disease", "epi_year", "epi_week", "week_start_date", "detector", "score",
                "threshold", "severity", "alert_flag", "reporting_artefact_flag"], "gold_alerts")
    a = a[a["disease"].isin(diseases)].copy()
    a["district_code"] = code2(a["district_code"])
    a["week_start"] = pd.to_datetime(a["week_start_date"]).dt.strftime("%Y-%m-%d")
    cutoff = (pd.to_datetime(a["week_start"]).max() - pd.Timedelta(weeks=ALERT_WEEKS - 1)).strftime("%Y-%m-%d")
    flag = a["alert_flag"].fillna(False).astype(bool)
    keep = (a["week_start"] >= cutoff) & (flag | (a["score"] > a["threshold"] / 2))
    a = a[keep].copy()
    a["alert_flag"] = flag[keep]
    # The score is a function of this week's count and a baseline made of earlier weeks that are published in
    # cases_weekly, so for a suppressed week (1-4 cases) it could be inverted to recover the count: null it.
    key = ["district_code", "disease", "epi_year", "epi_week"]
    a["epi_year"] = a["epi_year"].astype(int)
    a["epi_week"] = a["epi_week"].astype(int)
    sup = suppressed[key].drop_duplicates().assign(_sup=True)
    a = a.merge(sup, on=key, how="left")
    a["_sup"] = a["_sup"].fillna(False).astype(bool)
    a.loc[a["_sup"], "score"] = float("nan")
    print(f"alerts: score nulled on {int(a['_sup'].sum()):,} rows whose week has a suppressed case count")
    a["reporting_artefact_flag"] = a["reporting_artefact_flag"].fillna(False).astype(bool)
    a["score"] = a["score"].round(2)
    a["threshold"] = a["threshold"].round(2)
    a["epi_year"] = a["epi_year"].astype(int)
    a["epi_week"] = a["epi_week"].astype(int)
    cols = ["district_code", "disease", "epi_year", "epi_week", "week_start", "detector", "score", "threshold",
            "severity", "alert_flag", "reporting_artefact_flag"]
    return a.sort_values(["week_start", "score"], ascending=[False, False])[cols].reset_index(drop=True)


# ------------------------------------------------------------------ dq_report
def build_dq(path: Path):
    if not path.exists():
        sys.exit(f"{path} not found: run the `gold` stage first")
    d = pd.read_parquet(path)
    require(d, ["batch_id", "dataset", "rule_id", "reason_code", "pass_rate", "rows_in", "rows_quarantined",
                "run_ts"], "gold_dq_report")
    d = d[d["batch_id"].astype(str) != "ALL"].copy()          # ALL = gold reconciliation rows, not a batch
    order = (d.groupby("batch_id")["run_ts"].max().reset_index().sort_values(["run_ts", "batch_id"]))
    all_ids = [str(b) for b in order["batch_id"]]
    batches = []
    for bid in all_ids[-DQ_BATCHES:]:
        g = d[d["batch_id"].astype(str) == bid]
        per = g.groupby("dataset").agg(rows_in=("rows_in", "max"), rows_q=("rows_quarantined", "max"))
        rows_in = None if per["rows_in"].isna().any() else int(per["rows_in"].sum())
        rows_q = None if per["rows_q"].isna().any() else int(per["rows_q"].sum())
        rows_out = rows_in - rows_q if rows_in is not None and rows_q is not None else None
        rules = [{"rule_id": str(r.rule_id), "reason_code": clean(r.reason_code),
                  "pass_rate": None if pd.isna(r.pass_rate) else round(float(r.pass_rate), 4)}
                 for r in g.sort_values("rule_id").itertuples()]
        batches.append({"batch_id": bid, "rows_in": rows_in, "rows_out": rows_out,
                        "rows_quarantined": rows_q, "rules": rules})
    return batches, all_ids


# ------------------------------------------------------------------ stock (latest 52 weeks, supply units: not suppressed)
def build_stock(gold_csv: Path) -> pd.DataFrame:
    path = gold_csv / "gold_stock_weekly.csv"
    if not path.exists():
        sys.exit(f"{path} not found: run the `export` stage first")
    s = pd.read_csv(path, dtype={"district_code": str})
    cols = ["iv_saline_units", "ns1_kits", "ors_sachets", "occupied_beds", "beds_capacity"]
    require(s, ["district_code", "week_start_date", "epi_year", "epi_week"] + cols, "gold_stock_weekly")
    s["district_code"] = code2(s["district_code"])
    s["week_start"] = pd.to_datetime(s["week_start_date"]).dt.strftime("%Y-%m-%d")
    keep = sorted(s["week_start"].unique())[-52:]
    s = s[s["week_start"].isin(keep)].copy()
    out = pd.DataFrame({"district_code": s["district_code"], "epi_year": s["epi_year"].astype(int),
                        "epi_week": s["epi_week"].astype(int)})
    for c in cols:
        out[c] = s[c].round().astype("Int64")
    return out.sort_values(["district_code", "epi_year", "epi_week"]).reset_index(drop=True)


# ------------------------------------------------------------------ seasonality
def build_seasonality(profile_path: Path, calendar_path: Path, districts: pd.DataFrame, diseases):
    """seasonal_profile.csv is per DIVISION, so every district row carries its division's index (the dashboard
    labels the heatmap division-level). Agreed with the analytics engineer:
      - ALL = mean of the divisions weighted by mean_weekly_cases (preparedness_calendar.csv); mean_weekly_cases
        itself is NOT exported (PRV-2), only the low_volume flag
      - peak_flag = index within 10% of the series maximum AND clear_season; for every clear series the flagged
        weeks must include the calendar's peak_week (a failure makes the run exit 1)
      - peak_week_earliest / peak_week_latest are passed so the dashboard can draw a peak range
    Returns (rows, problems)."""
    for p in (profile_path, calendar_path):
        if not p.exists():
            sys.exit(f"{p} not found: run the `calendar` stage first")
    s = pd.read_csv(profile_path)
    require(s, ["division", "disease", "epi_week", "seasonal_index"], "seasonal_profile")
    cal = pd.read_csv(calendar_path)
    need = ["division", "disease", "clear_season", "peak_week", "peak_week_earliest", "peak_week_latest",
            "mean_weekly_cases", "low_volume"]
    miss = [c for c in need if c not in cal.columns]
    if miss:
        sys.exit(f"preparedness_calendar.csv has no {miss} (found {list(cal.columns)}). The analytics engineer "
                 "must add them to the calendar stage; then re-run `calendar` and this export")
    s = s[s["disease"].isin(diseases)].copy()
    cal = cal[cal["disease"].isin(diseases)].copy()
    unknown = sorted(set(districts["division"]) - set(s["division"]) | set(districts["division"]) - set(cal["division"]))
    if unknown:
        sys.exit(f"divisions in districts.csv missing from seasonal_profile/preparedness_calendar: {unknown}")
    if cal["mean_weekly_cases"].isna().any() or (cal["mean_weekly_cases"] < 0).any():
        sys.exit("preparedness_calendar.csv: mean_weekly_cases has nulls or negative values")
    cal["clear_season"] = cal["clear_season"].fillna(False).astype(bool)
    cal["low_volume"] = cal["low_volume"].fillna(False).astype(bool)

    # division-level peak flag (raw rule) and the assertion against the calendar
    s["peak_raw"] = s["seasonal_index"] >= 0.9 * s.groupby(["division", "disease"])["seasonal_index"].transform("max")
    problems = []
    for r in cal[cal["clear_season"]].itertuples():
        if pd.isna(r.peak_week):
            problems.append(f"calendar: {r.division}/{r.disease} is clear_season but has no peak_week")
            continue
        flagged = set(s[(s["division"] == r.division) & (s["disease"] == r.disease) & s["peak_raw"]]["epi_week"])
        if int(r.peak_week) not in flagged:
            problems.append(f"peak_flag misses the calendar peak_week {int(r.peak_week)} for {r.division}/{r.disease}")

    # district rows
    per = (s.merge(districts[["district_code", "division"]], on="division")
            .merge(cal[["division", "disease", "clear_season", "low_volume", "peak_week_earliest",
                        "peak_week_latest"]], on=["division", "disease"]))
    per["peak_flag"] = per["peak_raw"] & per["clear_season"]

    # ALL = weighted mean of divisions
    nat = s.merge(cal[["division", "disease", "mean_weekly_cases"]], on=["division", "disease"])
    nat["num"] = nat["seasonal_index"] * nat["mean_weekly_cases"]
    g = nat.groupby(["disease", "epi_week"], as_index=False)[["num", "mean_weekly_cases"]].sum()
    if (g["mean_weekly_cases"] <= 0).any():
        sys.exit("preparedness_calendar.csv: total mean_weekly_cases is 0 for a disease, cannot weight ALL")
    g["seasonal_index"] = g["num"] / g["mean_weekly_cases"]
    g["district_code"], g["division"] = "ALL", "ALL"
    g["peak_flag"] = g["seasonal_index"] >= 0.9 * g.groupby("disease")["seasonal_index"].transform("max")

    cols = ["disease", "district_code", "division", "epi_week", "seasonal_index", "peak_flag", "low_volume",
            "peak_week_earliest", "peak_week_latest"]
    out = pd.concat([per[cols], g[[c for c in cols if c in g.columns]]], ignore_index=True)
    out["epi_week"] = out["epi_week"].astype(int)
    out["seasonal_index"] = out["seasonal_index"].round(3)
    out["low_volume"] = out["low_volume"].astype("boolean")
    out["peak_week_earliest"] = pd.to_numeric(out["peak_week_earliest"]).round().astype("Int64")
    out["peak_week_latest"] = pd.to_numeric(out["peak_week_latest"]).round().astype("Int64")
    out["peak_flag"] = out["peak_flag"].astype(bool)
    return out.sort_values(["disease", "district_code", "epi_week"]).reset_index(drop=True), problems


# ------------------------------------------------------------------ calendar (no mean_weekly_cases: PRV-2)
def build_calendar(path: Path, diseases) -> pd.DataFrame:
    if not path.exists():
        sys.exit(f"{path} not found: run the `calendar` stage first")
    c = pd.read_csv(path)
    weeks = ["onset_week", "peak_week", "end_week", "prepose_supplies_week", "start_campaign_week",
             "peak_week_earliest", "peak_week_latest"]
    dates = ["onset_date", "peak_date", "end_date", "prepose_supplies_date", "start_campaign_date"]
    require(c, ["division", "disease", "clear_season", "low_volume", "n_years_clear"] + weeks + dates,
            "preparedness_calendar")
    c = c[c["disease"].isin(diseases)].copy()
    out = pd.DataFrame({"division": c["division"], "disease": c["disease"],
                        "clear_season": c["clear_season"].fillna(False).astype(bool),
                        "low_volume": c["low_volume"].fillna(False).astype(bool),
                        "n_years_clear": pd.to_numeric(c["n_years_clear"]).round().astype("Int64")})
    for col in weeks[:3] + weeks[3:5]:
        out[col] = pd.to_numeric(c[col]).round().astype("Int64")
        out[col.replace("_week", "_date")] = pd.to_datetime(c[col.replace("_week", "_date")]).dt.strftime("%Y-%m-%d")
    for col in weeks[5:]:
        out[col] = pd.to_numeric(c[col]).round().astype("Int64")
    order = ["division", "disease", "clear_season", "low_volume", "n_years_clear",
             "onset_week", "onset_date", "peak_week", "peak_date", "end_week", "end_date",
             "prepose_supplies_week", "prepose_supplies_date", "start_campaign_week", "start_campaign_date",
             "peak_week_earliest", "peak_week_latest"]
    return out[order].sort_values(["division", "disease"]).reset_index(drop=True)


# ------------------------------------------------------------------ forecast (latest origin, horizons 1-4, model output)
def build_forecast(path: Path, diseases, model: str):
    if not path.exists():
        sys.exit(f"{path} not found: run the `demand` stage first")
    f = pd.read_parquet(path)
    require(f, ["district_code", "disease", "origin_epi_year", "origin_epi_week", "horizon_weeks",
                "target_week_start_date", "model", "pred_mean", "q10", "q50", "q90", "is_backtest"], "gold_forecast")
    f = f[(~f["is_backtest"].astype(bool)) & f["disease"].isin(diseases) & f["horizon_weeks"].between(1, 4)].copy()
    if model:
        f = f[f["model"] == model]
    if f.empty:
        sys.exit(f"gold_forecast has no live rows for model {model!r}; models present: "
                 f"{sorted(pd.read_parquet(path)['model'].unique())}")
    origin = f["origin_epi_year"] * 100 + f["origin_epi_week"]
    f = f[origin == origin.max()]
    out = pd.DataFrame({
        "district_code": code2(f["district_code"]), "disease": f["disease"],
        "origin_epi_year": f["origin_epi_year"].astype(int), "origin_epi_week": f["origin_epi_week"].astype(int),
        "horizon_weeks": f["horizon_weeks"].astype(int),
        "target_week_start": pd.to_datetime(f["target_week_start_date"]).dt.strftime("%Y-%m-%d"),
        "model": f["model"],
        "pred_mean": f["pred_mean"].round(1), "q10": f["q10"].round(1), "q50": f["q50"].round(1),
        "q90": f["q90"].round(1)})
    problems = []
    if ((out["q10"] > out["q50"]) | (out["q50"] > out["q90"])).any():
        problems.append("forecast: quantiles are not ordered q10 <= q50 <= q90 in some rows")
    if out[["pred_mean", "q10", "q50", "q90"]].isna().any().any():
        problems.append("forecast: null values in pred_mean/q10/q50/q90")
    return out.sort_values(["disease", "district_code", "horizon_weeks"]).reset_index(drop=True), problems


# ------------------------------------------------------------------ checks on what was written
def validate(out: Path) -> list:
    problems, total = [], 0
    for f in sorted(out.glob("*.json")):
        size = f.stat().st_size
        total += size
        obj = json.loads(f.read_text(encoding="utf-8"))
        if "generated_at" not in obj:
            problems.append(f"{f.name}: no generated_at")
        print(f"  {f.name:<18}{size / 1024:>10,.0f} KB")
    print(f"  {'total':<18}{total / 1024:>10,.0f} KB (limit {TOTAL_BUDGET // MB} MB)")
    if total >= TOTAL_BUDGET:
        problems.append(f"total size {total / MB:.1f} MB is over {TOTAL_BUDGET // MB} MB")
    cases_file = out / "cases_weekly.json"
    if cases_file.exists():
        if cases_file.stat().st_size >= CASES_BUDGET:
            problems.append(f"cases_weekly.json is {cases_file.stat().st_size / MB:.2f} MB, budget is 6 MB")
        rows = json.loads(cases_file.read_text(encoding="utf-8"))["rows"]
        code_ok = re.compile(r"^\d\d$")
        for r in rows:
            if not code_ok.match(r["district_code"]):
                problems.append(f"bad district_code {r['district_code']!r}")
                break
        leaked = [r for r in rows if r["cases"] is not None and 1 <= r["cases"] <= 4]
        mismatch = [r for r in rows if r["suppressed"] != (r["cases"] is None)
                    or (r["incidence_per_100k"] is None) != (r["cases"] is None)]
        if leaked:
            problems.append(f"{len(leaked)} rows publish a count of 1-4 (privacy rule PRV-2)")
        if mismatch:
            problems.append(f"{len(mismatch)} rows where suppressed, cases and incidence disagree")
    for name in ("seasonality.json", "calendar.json"):
        f = out / name
        if f.exists() and "mean_weekly_cases" in f.read_text(encoding="utf-8"):
            problems.append(f"{name} exports mean_weekly_cases (not allowed, PRV-2)")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold-csv", default="data/gold_csv")
    ap.add_argument("--gold", default="data/gold")
    ap.add_argument("--analytics", default="data/analytics")
    ap.add_argument("--reference", default="data/reference")
    ap.add_argument("--out", default="serving/data")
    ap.add_argument("--diseases", nargs="+", default=ALLOWED_DISEASES)
    ap.add_argument("--forecast-model", default="nb_lagged_weather",
                    help="model served in forecast.json (the baseline stays out of the dashboard feed)")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    districts = pd.read_csv(Path(a.reference) / "districts.csv", dtype={"district_code": str})
    require(districts, ["district_code", "district_name", "division", "population"], "districts.csv")
    districts["district_code"] = code2(districts["district_code"])
    districts = districts.sort_values("district_code")

    cases = build_cases(Path(a.gold_csv), districts, a.diseases)
    alerts = build_alerts(Path(a.analytics) / "gold_alerts.parquet", a.diseases,
                          cases.loc[cases["suppressed"], ["district_code", "disease", "epi_year", "epi_week"]])
    dq_batches, batch_ids = build_dq(Path(a.gold) / "gold_dq_report")
    calendar = build_calendar(Path(a.analytics) / "preparedness_calendar.csv", a.diseases)
    stock = build_stock(Path(a.gold_csv))
    forecast, forecast_problems = build_forecast(Path(a.analytics) / "gold_forecast.parquet", a.diseases,
                                                 a.forecast_model)
    seasonality, season_problems = build_seasonality(Path(a.analytics) / "seasonal_profile.csv",
                                                     Path(a.analytics) / "preparedness_calendar.csv",
                                                     districts, a.diseases)

    as_of = cases["week_start"].max()
    first, last = cases.loc[cases["week_start"].idxmin()], cases.loc[cases["week_start"].idxmax()]
    present = [d for d in ALLOWED_DISEASES if d in set(cases["disease"]) and d in a.diseases]

    write_rows(out, "cases_weekly.json", {"generated_at": now, "data_as_of": as_of}, "rows", cases)
    write_rows(out, "alerts.json", {"generated_at": now}, "rows", alerts)
    write_rows(out, "calendar.json", {"generated_at": now, "data_as_of": as_of}, "rows", calendar)
    write_rows(out, "forecast.json", {"generated_at": now}, "rows", forecast)
    write_rows(out, "stock.json", {"generated_at": now}, "rows", stock)
    write_rows(out, "seasonality.json", {"generated_at": now, "data_as_of": as_of, "geography_level": "division",
                                         "all_label": "weighted mean of divisions"}, "rows", seasonality)
    write_obj(out, "dq_report.json", {"generated_at": now, "batches": dq_batches})
    write_obj(out, "freshness.json", {"generated_at": now, "data_as_of": as_of,
                                      "last_batch_id": batch_ids[-1] if batch_ids else None,
                                      "pipeline_version": PIPELINE_VERSION})
    write_obj(out, "meta.json", {
        "generated_at": now, "data_as_of": as_of, "diseases": present,
        "districts": [{"district_code": r.district_code, "district_name": r.district_name,
                       "division": r.division, "population": int(r.population)} for r in districts.itertuples()],
        "weeks": {"first": {"epi_year": int(first["epi_year"]), "epi_week": int(first["epi_week"])},
                  "last": {"epi_year": int(last["epi_year"]), "epi_week": int(last["epi_week"])}},
        "batch_ids": batch_ids})

    n_sup = int(cases["suppressed"].sum())
    print(f"cases_weekly: {len(cases):,} rows, {n_sup:,} suppressed ({n_sup / len(cases):.1%}), diseases {present}")
    print(f"forecast: {len(forecast):,} rows, origin {int(forecast['origin_epi_year'].iloc[0])}-W"
          f"{int(forecast['origin_epi_week'].iloc[0]):02d}, model {a.forecast_model}")
    print(f"calendar: {len(calendar):,} rows")
    print(f"stock: {len(stock):,} rows | seasonality: {len(seasonality):,} rows")
    print(f"alerts: {len(alerts):,} rows (last {ALERT_WEEKS} weeks) | dq_report: {len(dq_batches)} batches")
    print(f"data_as_of {as_of} | wrote to {out}")
    problems = validate(out) + season_problems + forecast_problems
    print("NOT WRITTEN YET:", ", ".join(NOT_YET))
    if problems:
        print("SERVING EXPORT CHECK FAILED:\n  " + "\n  ".join(problems))
        return 1
    print("serving export ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

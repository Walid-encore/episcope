"""Serving export (PRV-2, VIS-4): gold and analytics outputs -> serving/data/*.json (docs/serving_contract.md v0.2).

    python -m pipeline.serving_export

Writes meta, freshness, cases_weekly, alerts, dq_report, stock, seasonality, calendar, forecast and allocation
(allocation.json = live run, allocation_peak.json = peak-season scenario when data/analytics/allocation_peak exists). Run it after the `calendar` stage: it reads the gold
CSVs written by the `export` stage (data/gold_csv), data/analytics/gold_alerts.parquet and data/gold/gold_dq_report.
No Spark needed.

Rules taken from the contract and from the visualisation engineer:
  - all four diseases are exported. dengue and diarrhoeal with the full history; ILI from --ili-start (default
    2022-12-19, so the 2023 and 2024 nationwide surges are inside) and malaria for the last --malaria-weeks (52)
    weeks. No earlier week is filled with zeros (the dashboard reads each disease's own first and last week)
  - space is freed first, always: stock.json holds the last --stock-weeks (26) weeks and allocation_peak.json keeps
    --peak-alphas (0, 0.4, 0.8) of the optimised grid (the live file keeps all alphas for the what-if panel)
  - size valve: reacts to the TOTAL only. If all files in serving/data would pass --target-bytes (9,700,000, a bit
    below the 10,000,000-byte limit), the OLDEST weeks of ILI and malaria are trimmed in steps of 4. Never the latest
    weeks, never below 52 weeks per disease, ILI never later than --ili-floor (2023-02-13, the surge stays in),
    every trim is logged. The 2.5 MB budget for the files other than cases_weekly and the cases_weekly budget are
    warnings only. The windows really written are in meta.json as
    disease_windows: {"ili": {"first_week_start": ..., "last_week_start": ...}, ...}
  - alerts use the same window per disease as cases_weekly (dengue and diarrhoeal: last 104 weeks)
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

ALL_DISEASES = ["dengue", "diarrhoeal", "ili", "malaria"]
DEFAULT_DISEASES = ["dengue", "diarrhoeal"]      # exported with the full cases history
PIPELINE_VERSION = "v1.0"
MB = 1024 * 1024
CASES_BUDGET, TOTAL_BUDGET, OTHERS_BUDGET = 7_500_000, 10_000_000, 2_500_000   # decimal bytes; the total is the VIS-4 limit
ALERT_WEEKS = 104
ILI_START = "2022-12-19"          # first Monday before the OB017 surge (2023-02); OB027 is 2024-09 to 12
MALARIA_WEEKS = 52
MIN_WEEKS = 52                    # the valve never goes below this per disease
TRIM_STEP = 4                     # weeks cut per valve step
TARGET_BYTES = 9_700_000          # valve target: a little below the 10,000,000-byte checker limit
ILI_FLOOR = "2023-02-13"          # the valve never cuts ILI past this week (start of the OB017 surge)
STOCK_WEEKS = 26
PEAK_ALPHAS = [0.0, 0.4, 0.8]     # alpha values kept in allocation_peak.json (live keeps all)
META_RESERVE = 8_000              # bytes kept for meta.json, which is written after the valve has run
DQ_BATCHES = 14
NOT_YET = []


def code2(s):
    return s.astype(str).str.strip().str.zfill(2)


def clean(v):
    """NaN / NA -> None, numpy scalar -> python scalar."""
    if v is None or pd.isna(v):
        return None
    return v.item() if hasattr(v, "item") else v


def _drop_false_flag(js: str) -> str:
    """Omit the default-false `suppressed` key (VIS-4 size budget); an absent key means false.
    `suppressed` is the last column of cases_weekly, so the false form always ends a record."""
    return js.replace(',"suppressed":false}', "}")


def write_rows(out: Path, name: str, header: dict, key: str, df: pd.DataFrame) -> None:
    head = json.dumps(header, separators=(",", ":"))
    body = _drop_false_flag(df.to_json(orient="records", double_precision=4))
    (out / name).write_text(f'{head[:-1]},"{key}":{body}}}', encoding="utf-8")


def write_obj(out: Path, name: str, obj: dict) -> None:
    (out / name).write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def require(df, cols, label):
    miss = [c for c in cols if c not in df.columns]
    if miss:
        sys.exit(f"{label}: missing columns {miss}; found {list(df.columns)}")


# ------------------------------------------------------------------ cases_weekly (the only file with counts)
def build_cases(gold_csv: Path, districts: pd.DataFrame, diseases, ili_start: str = ILI_START,
                malaria_weeks: int = MALARIA_WEEKS) -> pd.DataFrame:
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
    # per-disease windows (size budget): ILI from a fixed start, malaria the last `malaria_weeks` weeks; the
    # latest week is the latest week of the whole data, so a new drop moves the malaria window by itself
    last = pd.to_datetime(out["week_start"]).max()
    starts = {"ili": ili_start,
              "malaria": (last - pd.Timedelta(weeks=malaria_weeks - 1)).strftime("%Y-%m-%d")}
    for d, s in starts.items():
        out = out[(out["disease"] != d) | (out["week_start"] >= s)]
    return out.sort_values(["disease", "district_code", "week_start"]).reset_index(drop=True)


def short_windows(cases: pd.DataFrame) -> dict:
    """First week_start of every disease that is not exported with its full history."""
    first = cases.groupby("disease")["week_start"].min()
    return {d: first[d] for d in first.index if d not in DEFAULT_DISEASES}


def trim_to_budget(cases: pd.DataFrame, alerts: pd.DataFrame, fixed_bytes: int, target: int,
                   ili_floor: str = ILI_FLOOR):
    """Size valve, reacts to the total only. While cases_weekly + alerts + `fixed_bytes` (every other file in
    serving/data) would pass `target`, cut the oldest TRIM_STEP weeks of the disease with the longest window
    (only ILI / malaria). Limits: never below MIN_WEEKS per disease, and ILI never starts later than `ili_floor`.
    Returns (cases, alerts, log lines, fits)."""
    def size(c, a):
        return (len(_drop_false_flag(c.to_json(orient="records", double_precision=4)).encode())
                + len(a.to_json(orient="records", double_precision=4).encode()))

    floors = {"ili": ili_floor}
    cut, fits = {}, True
    while fixed_bytes + size(cases, alerts) > target:
        cand = {}
        for d in cases["disease"].unique():
            if d in DEFAULT_DISEASES:
                continue
            w = sorted(cases.loc[cases["disease"] == d, "week_start"].unique())
            if len(w) - TRIM_STEP < MIN_WEEKS:
                continue
            if d in floors and w[TRIM_STEP] > floors[d]:
                continue
            cand[d] = w
        if not cand:
            fits = False
            break
        d = max(cand, key=lambda k: len(cand[k]))
        first = cand[d][TRIM_STEP]
        cases = cases[(cases["disease"] != d) | (cases["week_start"] >= first)]
        alerts = alerts[(alerts["disease"] != d) | (alerts["week_start"] >= first)]
        cut[d] = cut.get(d, 0) + TRIM_STEP
    log = [f"valve: trimmed {n} oldest weeks of {d}; it now starts {cases.loc[cases['disease'] == d, 'week_start'].min()}"
           for d, n in cut.items()]
    if not fits:
        log.append(f"valve: nothing left to trim (malaria at {MIN_WEEKS} weeks, ILI at {MIN_WEEKS} weeks or at the "
                   f"floor {ili_floor}); still over {target:,} bytes")
    return cases.reset_index(drop=True), alerts.reset_index(drop=True), log, fits


def window_warnings(calendar: pd.DataFrame, windows: dict, cases: pd.DataFrame) -> list:
    """A short window starts in the middle of some division's season if its first epi-week lies inside
    (onset_week, end_week]; then the start of that peak season is cut off. Warning only."""
    def inside(w, on, end):
        if pd.isna(on) or pd.isna(end):
            return False
        return (on < w <= end) if on <= end else (w > on or w <= end)

    out = []
    for d in windows:
        sub = cases[cases["disease"] == d]
        if sub.empty:
            continue
        first_week = int(sub.loc[sub["week_start"].idxmin(), "epi_week"])
        for r in calendar[(calendar["disease"] == d) & calendar["clear_season"]].itertuples():
            if inside(first_week, r.onset_week, r.end_week):
                out.append(f"window: {d} starts in epi-week {first_week}, inside the season of {r.division} "
                           f"(onset W{int(r.onset_week)} to end W{int(r.end_week)}); its first season is cut")
    return out


# ------------------------------------------------------------------ alerts
def build_alerts(path: Path, diseases, suppressed: pd.DataFrame, windows: dict | None = None) -> pd.DataFrame:
    """`windows` = first week_start per short disease (same window as cases_weekly); other diseases get the last
    ALERT_WEEKS weeks."""
    if not path.exists():
        sys.exit(f"{path} not found: run the `alerts` stage first")
    a = pd.read_parquet(path)
    require(a, ["district_code", "disease", "epi_year", "epi_week", "week_start_date", "detector", "score",
                "threshold", "severity", "alert_flag", "reporting_artefact_flag"], "gold_alerts")
    a = a[a["disease"].isin(diseases)].copy()
    a["district_code"] = code2(a["district_code"])
    a["week_start"] = pd.to_datetime(a["week_start_date"]).dt.strftime("%Y-%m-%d")
    default_cut = (pd.to_datetime(a["week_start"]).max() - pd.Timedelta(weeks=ALERT_WEEKS - 1)).strftime("%Y-%m-%d")
    cutoff = a["disease"].map(windows or {}).fillna(default_cut)
    flag = a["alert_flag"].fillna(False).astype(bool)
    keep = (a["week_start"] >= cutoff) & (flag | (a["score"] > a["threshold"] / 2))
    a = a[keep].copy()
    a["alert_flag"] = flag[keep]
    # A row is exported only when it is flagged or its score is above half the threshold, so even a row with the
    # score nulled would reveal that the week's count was high; and the score itself could be inverted from the
    # published neighbouring weeks. Rows on suppressed weeks (1-4 cases) are therefore dropped, not nulled.
    key = ["district_code", "disease", "epi_year", "epi_week"]
    a["epi_year"] = a["epi_year"].astype(int)
    a["epi_week"] = a["epi_week"].astype(int)
    sup = suppressed[key].drop_duplicates().assign(_sup=True)
    a = a.merge(sup, on=key, how="left")
    dropped = int(a["_sup"].notna().sum())
    a = a[a["_sup"].isna()].drop(columns="_sup").reset_index(drop=True)
    print(f"alerts: dropped {dropped:,} rows on weeks with a suppressed case count (PRV-2)")
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


# ------------------------------------------------------------------ stock (latest STOCK_WEEKS weeks, supply units: not suppressed)
def build_stock(gold_csv: Path, weeks: int = STOCK_WEEKS) -> pd.DataFrame:
    path = gold_csv / "gold_stock_weekly.csv"
    if not path.exists():
        sys.exit(f"{path} not found: run the `export` stage first")
    s = pd.read_csv(path, dtype={"district_code": str})
    cols = ["iv_saline_units", "ns1_kits", "ors_sachets", "occupied_beds", "beds_capacity"]
    require(s, ["district_code", "week_start_date", "epi_year", "epi_week"] + cols, "gold_stock_weekly")
    s["district_code"] = code2(s["district_code"])
    s["week_start"] = pd.to_datetime(s["week_start_date"]).dt.strftime("%Y-%m-%d")
    keep = sorted(s["week_start"].unique())[-weeks:]
    s = s[s["week_start"].isin(keep)].copy()
    out = pd.DataFrame({"district_code": s["district_code"], "epi_year": s["epi_year"].astype(int),
                        "epi_week": s["epi_week"].astype(int)})
    for c in cols:
        out[c] = s[c].round().astype("Int64")
    return out.sort_values(["district_code", "epi_year", "epi_week"]).reset_index(drop=True)


# ------------------------------------------------------------------ seasonality
def build_seasonality(profile_path: Path, calendar_path: Path, districts: pd.DataFrame, diseases):
    """One row per DIVISION x disease x epi-week (values are identical for all districts of a division, so a
    district copy would only repeat them 8x): `division` is the key and district_code is null. National rows have
    district_code and division "ALL". Agreed with the analytics and visualisation engineers:
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
    unknown = sorted((set(districts["division"]) - set(s["division"])) | (set(districts["division"]) - set(cal["division"])))
    if unknown:
        sys.exit(f"divisions in districts.csv missing from seasonal_profile/preparedness_calendar: {unknown}")
    if cal["mean_weekly_cases"].isna().any() or (cal["mean_weekly_cases"] < 0).any():
        sys.exit("preparedness_calendar.csv: mean_weekly_cases has nulls or negative values")
    cal["clear_season"] = cal["clear_season"].fillna(False).astype(bool)
    cal["low_volume"] = cal["low_volume"].fillna(False).astype(bool)

    s["peak_raw"] = s["seasonal_index"] >= 0.9 * s.groupby(["division", "disease"])["seasonal_index"].transform("max")
    problems = []
    for r in cal[cal["clear_season"]].itertuples():
        if pd.isna(r.peak_week):
            problems.append(f"calendar: {r.division}/{r.disease} is clear_season but has no peak_week")
            continue
        flagged = set(s[(s["division"] == r.division) & (s["disease"] == r.disease) & s["peak_raw"]]["epi_week"])
        if int(r.peak_week) not in flagged:
            problems.append(f"peak_flag misses the calendar peak_week {int(r.peak_week)} for {r.division}/{r.disease}")

    per = s.merge(cal[["division", "disease", "clear_season", "low_volume", "peak_week_earliest",
                       "peak_week_latest"]], on=["division", "disease"])
    per["peak_flag"] = per["peak_raw"] & per["clear_season"]
    per["district_code"] = None

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
    return out.sort_values(["disease", "division", "epi_week"]).reset_index(drop=True), problems


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


# ------------------------------------------------------------------ allocation (supply units, not patient counts)
STRATEGY = {"optimised": "optimised", "proportional": "population_proportional"}


def build_allocation(folder: Path, scenario: str, label: str, districts: pd.DataFrame, now: str, required: bool,
                     omit_zero: bool = True, repeat_baseline: bool = False, alphas_keep=None):
    """One scenario folder -> allocation.json shape: precomputed grid (what-if, VIS-5), efficiency-equity curve
    (C3), the recommended plan with demand, stock and allocation (VIS-2 view 4) and the recommendation itself.
    budget_level = budget fraction x 100 (percent of the cost of the full unmet need, relative cost units).
    The proportional baseline does not depend on alpha, so by default it is written ONCE with alpha null (rows and
    curve). With repeat_baseline it is repeated for every alpha instead (alpha is then always a number, files are
    about 0.65 MB larger). With omit_zero, grid rows where allocated_units and
    unmet_demand are both 0 are left out (about 70% of rows): a missing row means 0 / 0 (no need, or no budget);
    `plan` and meta.json list every district. Only needed if the 10 MB limit (VIS-4) is at risk."""
    names = ["allocation_grid.csv", "allocation_plan.csv", "efficiency_equity_curve.csv", "recommendation.json"]
    missing = [n for n in names if not (folder / n).exists()]
    if missing:
        if required:
            sys.exit(f"{folder}: missing {missing}. Run the `allocate` stage (analytics.optimise.run_allocation) first")
        return None, []
    g = pd.read_csv(folder / "allocation_grid.csv", dtype={"district_code": str})
    require(g, ["budget_frac", "alpha", "policy", "district_code", "resource", "x", "unmet"], f"{scenario} grid")
    cv = pd.read_csv(folder / "efficiency_equity_curve.csv")
    require(cv, ["budget_frac", "policy", "alpha", "weighted_unmet", "coverage_total", "coverage_min_district",
                 "coverage_gini"], f"{scenario} curve")
    pl = pd.read_csv(folder / "allocation_plan.csv", dtype={"district_code": str})
    require(pl, ["district_code", "resource", "demand", "stock", "x", "unmet"], f"{scenario} plan")
    rec = json.loads((folder / "recommendation.json").read_text(encoding="utf-8"))
    problems = []
    if alphas_keep is not None:
        # fewer alpha values (size): keep the optimised rows of these alphas; the baseline (alpha null) stays
        def keep_alpha(df):
            opt = df["policy"] == "optimised"
            ok = df["alpha"].apply(lambda v: any(abs(v - k) < 1e-9 for k in alphas_keep) if pd.notna(v) else False)
            return df[~opt | ok]
        present = sorted(g.loc[g["policy"] == "optimised", "alpha"].dropna().unique().tolist())
        miss = [k for k in alphas_keep if not any(abs(k - v) < 1e-9 for v in present)]
        if miss:
            problems.append(f"{scenario}: alpha values {miss} are not in the grid (found {present})")
        g, cv = keep_alpha(g), keep_alpha(cv)
        if rec.get("alpha") is not None and not any(abs(rec["alpha"] - k) < 1e-9 for k in alphas_keep):
            print(f"WARNING: {scenario}: the recommendation uses alpha {rec['alpha']}, which is not in {alphas_keep}")

    def level(frac):
        lv = (frac * 100).round(1)
        return lv.astype(int) if (lv % 1 == 0).all() else lv

    bad = sorted(set(g["policy"]) - set(STRATEGY))
    if bad:
        sys.exit(f"{scenario}: unknown policy values {bad}")
    g["district_code"] = code2(g["district_code"])
    if set(g["district_code"]) != set(districts["district_code"]):
        problems.append(f"{scenario}: grid districts differ from districts.csv")
    alphas_all = sorted(g.loc[g["policy"] == "optimised", "alpha"].dropna().unique().tolist())
    if repeat_baseline:
        base = g[g["policy"] == "proportional"]
        g = pd.concat([g[g["policy"] == "optimised"]] + [base.assign(alpha=al) for al in alphas_all],
                      ignore_index=True)
        cbase = cv[cv["policy"] == "proportional"]
        cv = pd.concat([cv[cv["policy"] == "optimised"]] + [cbase.assign(alpha=al) for al in alphas_all],
                       ignore_index=True)
    rows = pd.DataFrame({
        "budget_level": level(g["budget_frac"]), "alpha": g["alpha"], "strategy": g["policy"].map(STRATEGY),
        "district_code": g["district_code"], "resource": g["resource"],
        "allocated_units": g["x"].round(1), "unmet_demand": g["unmet"].round(1)})
    rows = rows.sort_values(["budget_level", "strategy", "alpha", "district_code", "resource"],
                            na_position="first").reset_index(drop=True)
    curve = pd.DataFrame({
        "budget_level": level(cv["budget_frac"]), "alpha": cv["alpha"], "strategy": cv["policy"].map(STRATEGY),
        "total_unmet": cv["weighted_unmet"].round(2), "equity_gini": cv["coverage_gini"].round(4),
        "coverage_total": cv["coverage_total"].round(4), "coverage_min_district": cv["coverage_min_district"].round(4)})
    curve = curve.sort_values(["budget_level", "strategy", "alpha"], na_position="first").reset_index(drop=True)
    plan = pd.DataFrame({
        "district_code": code2(pl["district_code"]), "resource": pl["resource"], "demand": pl["demand"].round(1),
        "stock": pl["stock"].round(1), "allocated_units": pl["x"].round(1), "unmet_demand": pl["unmet"].round(1)})
    levels = sorted(rows["budget_level"].unique().tolist())
    n_full = len(rows)
    if omit_zero:
        rows = rows[(rows["allocated_units"] != 0) | (rows["unmet_demand"] != 0)].reset_index(drop=True)
    print(f"allocation[{scenario}]: {len(rows):,} grid rows"
          + (f" ({n_full - len(rows):,} all-zero rows omitted)" if omit_zero else ""))
    alphas = sorted(rows.loc[rows["strategy"] == "optimised", "alpha"].dropna().unique().tolist())
    if len(levels) < 5 or set(rows["strategy"]) != set(STRATEGY.values()):
        problems.append(f"{scenario}: needs >= 5 budget levels and both strategies (found {levels})")
    # a replay scenario is labelled from what it replays, so a reader does not assume the week had the most cases
    wk, hs = rec.get("as_of_week"), rec.get("horizon_start")
    if scenario == "peak" and wk and hs:
        try:
            d = pd.to_datetime(hs)
            hs = f"{d.day} {d.strftime('%b %Y')}"
        except (ValueError, TypeError):
            hs = str(hs)
        label = f"Replay of the forecast issued for {wk}, covering the four weeks from {hs}"
    obj = {"generated_at": now, "scenario": scenario, "scenario_label": label, "as_of": rec.get("as_of"),
           "budget_unit": "percent_of_full_need", "grid_zero_rows_omitted": omit_zero,
           "alpha_values": alphas, "budget_levels": levels, "recommendation": rec,
           "rows": json.loads(rows.to_json(orient="records")),
           "curve": json.loads(curve.to_json(orient="records")),
           "plan": json.loads(plan.to_json(orient="records"))}
    return obj, problems


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
    print(f"  {'total':<18}{total / 1024:>10,.0f} KB (limit {TOTAL_BUDGET / 1e6:.0f} MB = {TOTAL_BUDGET // 1000:,} KB)")
    if total >= TOTAL_BUDGET:
        problems.append(f"total size {total / 1e6:.2f} MB is over {TOTAL_BUDGET / 1e6:.0f} MB")
    cases_file = out / "cases_weekly.json"
    if cases_file.exists() and total - cases_file.stat().st_size >= OTHERS_BUDGET:
        print(f"WARNING: files other than cases_weekly are {(total - cases_file.stat().st_size) / 1e6:.2f} MB, "
              f"the contract split is {OTHERS_BUDGET / 1e6:.1f} MB (warning only, the total decides)")
    if cases_file.exists():
        if cases_file.stat().st_size >= CASES_BUDGET:
            print(f"WARNING: cases_weekly.json is {cases_file.stat().st_size / 1e6:.2f} MB, "
                  f"the contract split is {CASES_BUDGET / 1e6:.1f} MB (warning only, the total decides)")
        rows = json.loads(cases_file.read_text(encoding="utf-8"))["rows"]
        code_ok = re.compile(r"^\d\d$")
        for r in rows:
            if not code_ok.match(r["district_code"]):
                problems.append(f"bad district_code {r['district_code']!r}")
                break
        leaked = [r for r in rows if r["cases"] is not None and 1 <= r["cases"] <= 4]
        mismatch = [r for r in rows if r.get("suppressed", False) != (r["cases"] is None)
                    or (r["incidence_per_100k"] is None) != (r["cases"] is None)]
        alerts_f = out / "alerts.json"
        if alerts_f.exists():
            sup_keys = {(r["district_code"], r["disease"], r["week_start"]) for r in rows if r.get("suppressed", False)}
            alert_rows = json.loads(alerts_f.read_text(encoding="utf-8"))["rows"]
            on_sup = [r for r in alert_rows if (r["district_code"], r["disease"], r["week_start"]) in sup_keys]
            print(f"  alert rows on suppressed weeks: {len(on_sup)}")
            if on_sup:
                problems.append(f"{len(on_sup)} alert rows sit on suppressed weeks (PRV-2)")
            # windows: meta.json must list what cases_weekly really contains, and no alert may predate its window
            meta_f = out / "meta.json"
            if meta_f.exists():
                dw = json.loads(meta_f.read_text(encoding="utf-8")).get("disease_windows")
                if dw is None:
                    problems.append("meta.json has no disease_windows")
                else:
                    seen = {}
                    for r in rows:
                        lo, hi = seen.get(r["disease"], (r["week_start"], r["week_start"]))
                        seen[r["disease"]] = (min(lo, r["week_start"]), max(hi, r["week_start"]))
                    for d, (lo, hi) in seen.items():
                        w = dw.get(d)
                        if not w or w.get("first_week_start") != lo or w.get("last_week_start") != hi:
                            problems.append(f"meta.json disease_windows[{d}] is {w}, cases_weekly has {lo} to {hi}")
                        if d not in DEFAULT_DISEASES:
                            n = len({r["week_start"] for r in rows if r["disease"] == d})
                            if n < MIN_WEEKS:
                                problems.append(f"{d} has only {n} weeks in cases_weekly (minimum {MIN_WEEKS})")
                    early = [r for r in alert_rows if r["disease"] in dw and r["week_start"] < dw[r["disease"]]["first_week_start"]
                             and r["disease"] not in DEFAULT_DISEASES]
                    if early:
                        problems.append(f"{len(early)} alert rows are older than their disease window")
        if leaked:
            problems.append(f"{len(leaked)} rows publish a count of 1-4 (privacy rule PRV-2)")
        if mismatch:
            problems.append(f"{len(mismatch)} rows where suppressed, cases and incidence disagree")
    fc_f = out / "forecast.json"
    if fc_f.exists() and "actual_cases" in fc_f.read_text(encoding="utf-8"):
        problems.append("forecast.json contains actual_cases (a count; not allowed unless suppressed, PRV-2)")
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
    ap.add_argument("--diseases", nargs="+", default=ALL_DISEASES, choices=ALL_DISEASES,
                    help="diseases to export; ili and malaria get a shorter window in cases_weekly and alerts")
    ap.add_argument("--ili-start", default=ILI_START, help="first week_start exported for ILI (default %(default)s)")
    ap.add_argument("--malaria-weeks", type=int, default=MALARIA_WEEKS,
                    help="malaria: export the last N weeks (default %(default)s)")
    ap.add_argument("--target-bytes", type=int, default=TARGET_BYTES,
                    help="size valve: trim the oldest ILI/malaria weeks until serving/data is under this (default "
                         "%(default)s; the checker limit is 10,000,000)")
    ap.add_argument("--ili-floor", default=ILI_FLOOR,
                    help="the valve never cuts ILI past this week_start (default %(default)s)")
    ap.add_argument("--stock-weeks", type=int, default=STOCK_WEEKS, help="weeks of stock.json (default %(default)s)")
    ap.add_argument("--peak-alphas", nargs="+", type=float, default=PEAK_ALPHAS,
                    help="alpha values kept in allocation_peak.json (default %(default)s); the live file keeps all")
    ap.add_argument("--repeat-baseline", action="store_true",
                    help="repeat the population_proportional baseline for every alpha (default: once, alpha null)")
    ap.add_argument("--keep-zero-grid-rows", action="store_true",
                    help="keep allocation grid rows where allocated_units and unmet_demand are both 0. Default: they "
                         "are omitted (about 70% of rows) and grid_zero_rows_omitted is true: a missing row = 0 / 0")
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

    cases = build_cases(Path(a.gold_csv), districts, a.diseases, a.ili_start, a.malaria_weeks)
    alerts = build_alerts(Path(a.analytics) / "gold_alerts.parquet", a.diseases,
                          cases.loc[cases["suppressed"], ["district_code", "disease", "epi_year", "epi_week"]],
                          short_windows(cases))
    dq_batches, batch_ids = build_dq(Path(a.gold) / "gold_dq_report")
    calendar = build_calendar(Path(a.analytics) / "preparedness_calendar.csv", a.diseases)
    alloc_live, alloc_problems = build_allocation(Path(a.analytics) / "allocation", "live",
                                                  "Current (live) allocation", districts, now, required=True,
                                                  omit_zero=not a.keep_zero_grid_rows,
                                                  repeat_baseline=a.repeat_baseline)
    alloc_peak, peak_problems = build_allocation(Path(a.analytics) / "allocation_peak", "peak",
                                                 "Peak-season scenario (retrospective)", districts, now,
                                                 required=False, omit_zero=not a.keep_zero_grid_rows,
                                                 repeat_baseline=a.repeat_baseline, alphas_keep=a.peak_alphas)
    stock = build_stock(Path(a.gold_csv), a.stock_weeks)
    forecast, forecast_problems = build_forecast(Path(a.analytics) / "gold_forecast.parquet", a.diseases,
                                                 a.forecast_model)
    seasonality, season_problems = build_seasonality(Path(a.analytics) / "seasonal_profile.csv",
                                                     Path(a.analytics) / "preparedness_calendar.csv",
                                                     districts, a.diseases)

    as_of = cases["week_start"].max()      # the latest week is never trimmed, so this does not change below

    write_obj(out, "allocation.json", alloc_live)
    if alloc_peak:
        write_obj(out, "allocation_peak.json", alloc_peak)
    else:
        print("allocation_peak.json: no data/analytics/allocation_peak here; a committed copy is left untouched")
    write_rows(out, "calendar.json", {"generated_at": now, "data_as_of": as_of}, "rows", calendar)
    write_rows(out, "forecast.json", {"generated_at": now}, "rows", forecast)
    write_rows(out, "stock.json", {"generated_at": now}, "rows", stock)
    write_rows(out, "seasonality.json", {"generated_at": now, "data_as_of": as_of, "geography_level": "division",
                                         "all_label": "weighted mean of divisions"}, "rows", seasonality)
    write_obj(out, "dq_report.json", {"generated_at": now, "batches": dq_batches})
    write_obj(out, "freshness.json", {"generated_at": now, "data_as_of": as_of,
                                      "last_batch_id": batch_ids[-1] if batch_ids else None,
                                      "pipeline_version": PIPELINE_VERSION})

    # size valve: everything except cases_weekly, alerts and meta is on disk now, so the real sizes are known
    fixed = sum(f.stat().st_size for f in out.glob("*.json")
                if f.name not in ("cases_weekly.json", "alerts.json", "meta.json")) + META_RESERVE
    cases, alerts, valve_log, fits = trim_to_budget(cases, alerts, fixed, a.target_bytes, a.ili_floor)
    for line in valve_log:
        print(line)
    if not fits:
        print(f"WARNING: size valve could not get under {a.target_bytes:,} bytes within its limits "
              f"({MIN_WEEKS} weeks per disease, ILI from {a.ili_floor} at the latest); the check below fails only "
              "if the total passes 10,000,000 bytes")
    for line in window_warnings(calendar, short_windows(cases), cases):
        print("WARNING:", line)

    first, last = cases.loc[cases["week_start"].idxmin()], cases.loc[cases["week_start"].idxmax()]
    present = [d for d in ALL_DISEASES if d in set(cases["disease"]) and d in a.diseases]
    windows = {d: {"first_week_start": g["week_start"].min(), "last_week_start": g["week_start"].max()}
               for d, g in cases.groupby("disease")}
    write_rows(out, "cases_weekly.json", {"generated_at": now, "data_as_of": as_of}, "rows", cases)
    write_rows(out, "alerts.json", {"generated_at": now}, "rows", alerts)
    write_obj(out, "meta.json", {
        "generated_at": now, "data_as_of": as_of, "diseases": present,
        "districts": [{"district_code": r.district_code, "district_name": r.district_name,
                       "division": r.division, "population": int(r.population)} for r in districts.itertuples()],
        "weeks": {"first": {"epi_year": int(first["epi_year"]), "epi_week": int(first["epi_week"])},
                  "last": {"epi_year": int(last["epi_year"]), "epi_week": int(last["epi_week"])}},
        "disease_windows": {d: windows[d] for d in present},
        "batch_ids": batch_ids})

    for d in present:
        w = windows[d]
        nweeks = int(cases.loc[cases["disease"] == d, "week_start"].nunique())
        print(f"window {d}: {w['first_week_start']} to {w['last_week_start']} ({nweeks} weeks)")
    n_sup = int(cases["suppressed"].sum())
    print(f"cases_weekly: {len(cases):,} rows, {n_sup:,} suppressed ({n_sup / len(cases):.1%}), diseases {present}")
    print(f"forecast: {len(forecast):,} rows, origin {int(forecast['origin_epi_year'].iloc[0])}-W"
          f"{int(forecast['origin_epi_week'].iloc[0]):02d}, model {a.forecast_model}")
    print(f"calendar: {len(calendar):,} rows")
    print(f"stock: {len(stock):,} rows | seasonality: {len(seasonality):,} rows")
    print(f"alerts: {len(alerts):,} rows (same window per disease; dengue/diarrhoeal last {ALERT_WEEKS} weeks) | "
          f"dq_report: {len(dq_batches)} batches")
    print(f"data_as_of {as_of} | wrote to {out}")
    problems = validate(out) + season_problems + forecast_problems + alloc_problems + peak_problems
    if NOT_YET:
        print("NOT WRITTEN YET:", ", ".join(NOT_YET))
    if problems:
        print("SERVING EXPORT CHECK FAILED:\n  " + "\n  ".join(problems))
        return 1
    print("serving export ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

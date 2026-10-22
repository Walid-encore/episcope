"""Stand-in gold tables built from the generated encounters (pandas, small scale only).

Follows docs/gold_contract.md v0.3 so that analytics code can be tuned now and
switched to the Data Engineer's real gold tables later without code changes.
This is NOT the pipeline (ING-2 needs Spark there); it is an analytics test bench.

Usage:
    python -m analytics.standin_gold --encounters data/incoming/encounters \
        --facilities data/reference/facilities.csv --districts data/reference/districts.csv \
        --out data/standin_gold

Writes gold_weekly_cases, gold_weekly_reports, gold_weekly_cases_by_lag,
gold_reporting_status (CSV) and prints how many rows were quarantined per reason.
ASSUMPTION: facilities.csv has columns facility_id and district_code.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DISEASES = {"dengue": ("A90", "A91"), "diarrhoeal": tuple(f"A0{i}" for i in range(10))}
ICD_RE = r"^[A-Z][0-9]{2}"
MIN_ONSET = pd.Timestamp("2021-01-01")


def disease_of(icd3):
    if icd3 in DISEASES["dengue"]:
        return "dengue"
    if icd3 in DISEASES["diarrhoeal"]:
        return "diarrhoeal"
    return "other"


def _week_start(s):
    return (s - pd.to_timedelta(s.dt.weekday, unit="D")).dt.normalize()


def _iso(df, col, prefix=""):
    iso = df[col].dt.isocalendar()
    df[f"{prefix}epi_year"] = iso["year"].astype(int)
    df[f"{prefix}epi_week"] = iso["week"].astype(int)
    return df


def read_encounters(path):
    files = sorted(Path(path).rglob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no CSV files under {path}")
    cols = ["encounter_id", "facility_id", "onset_date", "report_date", "age_years",
            "sex", "icd10_code", "lab_confirmed", "admitted", "outcome"]
    return pd.concat((pd.read_csv(f, usecols=cols, dtype=str) for f in files), ignore_index=True)


def to_silver(enc, facilities):
    """Quarantine invalid rows (reason codes as in the contract), dedup by encounter_id."""
    enc = enc.copy()
    enc["onset_date"] = pd.to_datetime(enc["onset_date"], errors="coerce")
    enc["report_date"] = pd.to_datetime(enc["report_date"], errors="coerce")
    age = pd.to_numeric(enc["age_years"], errors="coerce")
    icd3 = enc["icd10_code"].fillna("").str.upper().str[:3]
    reason = pd.Series("", index=enc.index)
    rules = [("ORPHAN_FACILITY", ~enc["facility_id"].isin(facilities["facility_id"])),
             ("BAD_AGE", age.isna() | (age < 0) | (age > 120)),
             ("BAD_SEX", ~enc["sex"].isin(["M", "F"])),
             ("BAD_DATES", enc["onset_date"].isna() | enc["report_date"].isna()
              | (enc["onset_date"] > enc["report_date"])),
             ("BAD_ONSET", enc["onset_date"] < MIN_ONSET),
             ("BAD_ICD10", ~icd3.str.match(ICD_RE))]
    for code, mask in rules:  # first failing rule wins
        reason = reason.where(reason != "", np.where(mask, code, ""))
    bad = reason != ""
    print("quarantined:", reason[bad].value_counts().to_dict(), f"of {len(enc):,}")
    ok = enc[~bad].copy()
    ok["icd3"] = icd3[~bad]
    g = ok.groupby("encounter_id")["report_date"]
    ok["first_report_date"] = g.transform("min")
    ok = ok.sort_values("report_date").drop_duplicates("encounter_id", keep="last")
    ok = ok.merge(facilities[["facility_id", "district_code"]], on="facility_id")
    ok["disease"] = ok["icd3"].map(disease_of)
    return ok


def _grid(keys_df, weeks, diseases):
    idx = pd.MultiIndex.from_product([sorted(keys_df), diseases, weeks],
                                     names=["district_code", "disease", "week_start_date"])
    return idx.to_frame(index=False)


def build_gold(silver, facilities, districts):
    sel = silver[silver["disease"].isin(DISEASES)].copy()
    sel["onset_ws"] = _week_start(sel["onset_date"])
    sel["report_ws"] = _week_start(sel["first_report_date"])
    sel["lag_days"] = (sel["first_report_date"] - sel["onset_date"]).dt.days
    all_ws = pd.date_range(sel["onset_ws"].min(), sel["report_ws"].max(), freq="7D")
    dist = districts["district_code"].tolist()
    diseases = list(DISEASES)

    # onset-week cases (forecast / demand / dashboard)
    sel["died"] = (sel["outcome"] == "died").astype(int)
    sel["lab"] = (sel["lab_confirmed"] == "true").astype(int)
    sel["adm"] = (sel["admitted"] == "true").astype(int)
    c = (sel.groupby(["district_code", "disease", "onset_ws"])
            .agg(cases=("encounter_id", "size"), lab_confirmed=("lab", "sum"),
                 admitted=("adm", "sum"), deaths=("died", "sum")).reset_index()
            .rename(columns={"onset_ws": "week_start_date"}))
    onset_weeks = all_ws[all_ws <= sel["onset_ws"].max()]
    cases = _grid(dist, onset_weeks, diseases).merge(c, how="left",
                                                     on=["district_code", "disease", "week_start_date"])
    cases = cases.fillna({"cases": 0, "lab_confirmed": 0, "admitted": 0, "deaths": 0})
    cases = cases.rename(columns={"lab_confirmed": "lab_confirmed_cases", "admitted": "admitted_cases"})
    for col in ["cases", "lab_confirmed_cases", "admitted_cases", "deaths"]:
        cases[col] = cases[col].astype("int64")
    cases = cases.merge(districts[["district_code", "population"]], on="district_code")
    cases["incidence_per_100k"] = cases["cases"] / cases["population"] * 1e5
    cases = _iso(cases, "week_start_date")
    cases["last_batch_id"] = "standin"
    cases["updated_at"] = pd.Timestamp.now().floor("s").isoformat()

    # report-week table (detector input)
    r = (sel.groupby(["district_code", "disease", "report_ws"])
            .agg(reported_cases=("encounter_id", "size"),
                 late_cases_ge8d=("lag_days", lambda s: int((s >= 8).sum()))).reset_index()
            .rename(columns={"report_ws": "report_week_start_date"}))
    rep = _grid(dist, all_ws, diseases).rename(columns={"week_start_date": "report_week_start_date"})
    rep = rep.merge(r, how="left", on=["district_code", "disease", "report_week_start_date"])
    rep = rep.fillna({"reported_cases": 0, "late_cases_ge8d": 0})
    rep[["reported_cases", "late_cases_ge8d"]] = rep[["reported_cases", "late_cases_ge8d"]].astype("int64")
    rep = _iso(rep, "report_week_start_date", "report_")

    # as-of table: onset week x report lag (0..4, 4 = four or more)
    sel["lag_w"] = ((sel["report_ws"] - sel["onset_ws"]).dt.days // 7).clip(0, 4)
    lag = (sel.groupby(["district_code", "disease", "onset_ws", "lag_w"]).size()
              .rename("cases_new").reset_index()
              .rename(columns={"onset_ws": "week_start_date", "lag_w": "report_lag_weeks"}))
    lag = _iso(lag, "week_start_date")

    # reporting status (all diseases, report weeks)
    fac = facilities[["facility_id", "district_code"]]
    per = (silver.assign(report_ws=_week_start(silver["first_report_date"]))
           .groupby(["facility_id", "report_ws"]).size().unstack(fill_value=0)
           .reindex(index=fac["facility_id"], columns=all_ws, fill_value=0))
    usual = per.T.shift(1).rolling(12, min_periods=8).median().T  # previous 12 weeks, excl. current
    reported = (per > 0)
    d = fac.set_index("facility_id")["district_code"]
    u_tot = usual.groupby(d).sum(min_count=1)
    # volume-aware credit: a facility counts in proportion to min(1, n / (0.5 * usual)), so a facility
    # that reports 1 case against a usual 13 no longer counts as "reporting" (contract change proposal)
    credit = (per / (0.5 * usual.where(usual > 0))).clip(upper=1).fillna(0)
    u_rep = (usual * credit).groupby(d).sum(min_count=1)
    comp = (u_rep.fillna(0) / u_tot).where(u_tot.notna())
    status = pd.DataFrame({"expected_facilities": d.value_counts(),
                           }).rename_axis("district_code").reset_index()
    def _long(wide, name):  # version-independent wide -> long, keeps NaN
        w = wide.rename_axis("district_code").reset_index()
        w = w.melt(id_vars="district_code", var_name="report_week_start_date", value_name=name)
        return w.set_index(["district_code", "report_week_start_date"])[name]

    long = pd.concat([_long(reported.groupby(d).sum(), "reporting_facilities"),
                      _long(u_tot, "usual_volume_total"),
                      _long(comp, "reporting_completeness")], axis=1).reset_index()
    status = long.merge(status, on="district_code")
    status = _iso(status, "report_week_start_date", "report_")
    return {"gold_weekly_cases": cases, "gold_weekly_reports": rep,
            "gold_weekly_cases_by_lag": lag, "gold_reporting_status": status}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--encounters", default="data/incoming/encounters")
    ap.add_argument("--facilities", default="data/reference/facilities.csv")
    ap.add_argument("--districts", default="data/reference/districts.csv")
    ap.add_argument("--out", default="data/standin_gold")
    a = ap.parse_args()
    fac = pd.read_csv(a.facilities, dtype={"district_code": str, "facility_id": str})
    if "district_code" not in fac.columns:
        raise SystemExit(f"facilities.csv has no district_code; columns: {list(fac.columns)}")
    fac["district_code"] = fac["district_code"].str.zfill(2)
    dis = pd.read_csv(a.districts, dtype={"district_code": str})
    silver = to_silver(read_encounters(a.encounters), fac)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, df in build_gold(silver, fac, dis).items():
        df.to_csv(out / f"{name}.csv", index=False)
        print(f"{name}: {len(df):,} rows")


if __name__ == "__main__":
    main()

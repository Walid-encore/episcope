"""Why was (or wasn't) each artefact's back-fill week flagged?  python -m analytics.diag_artefact

For each artefact in data/truth/ground_truth_artefacts.csv (diagnostic use only, not detection):
  * reporting_completeness and facility counts for the district around the silence
  * the silent facility's share of its district's first-reported volume in the weeks before
"""
import pandas as pd

from analytics.standin_gold import _week_start, read_encounters, to_silver

ROOT = "data/"
art = pd.read_csv(ROOT + "truth/ground_truth_artefacts.csv", dtype={"district_code": str})
art = art.drop_duplicates(["district_code", "facility_id"])
fac = pd.read_csv(ROOT + "reference/facilities.csv", dtype={"district_code": str, "facility_id": str})
fac["district_code"] = fac["district_code"].str.zfill(2)
st = pd.read_csv(ROOT + "standin_gold/gold_reporting_status.csv", dtype={"district_code": str})
st["report_week_start_date"] = pd.to_datetime(st["report_week_start_date"])

silver = to_silver(read_encounters(ROOT + "incoming/encounters"), fac)
silver["ws"] = _week_start(silver["first_report_date"])
per = silver.groupby(["facility_id", "ws"]).size().rename("n").reset_index()

pd.set_option("display.width", 200)
for _, a in art.iterrows():
    d, f = a["district_code"], a["facility_id"]
    s0, b0 = pd.Timestamp(a["silent_start"]), pd.Timestamp(a["backfill_week_start"])
    lo, hi = s0 - pd.Timedelta(weeks=4), b0 + pd.Timedelta(weeks=1)
    print(f"\n=== district {d}, facility {f}: silent {s0.date()}..{a['silent_end']}, back-fill {b0.date()}")
    rows = st[(st["district_code"] == d) & (st["report_week_start_date"].between(lo, hi))]
    print(rows[["report_week_start_date", "reporting_facilities", "expected_facilities",
                "usual_volume_total", "reporting_completeness"]].round(3).to_string(index=False))
    ids = fac.loc[fac["district_code"] == d, "facility_id"]
    dist = per[per["facility_id"].isin(ids) & per["ws"].between(lo, hi)]
    mine = dist[dist["facility_id"] == f].set_index("ws")["n"]
    tot = dist.groupby("ws")["n"].sum()
    out = pd.DataFrame({"silent_facility_n": mine, "district_total_n": tot}).fillna(0).astype(int)
    out["share"] = (out["silent_facility_n"] / out["district_total_n"]).round(3)
    print(out.to_string())

"""Export gold_alerts.parquet to the dashboard's alerts.json (same layout as serving/sample/alerts.json).

    python -m analytics.export_alerts_json [--alerts data/analytics/gold_alerts.parquet]
                                           [--out data/outputs/alerts.json] [--weeks 26] [--alerts-only]

Reads ONLY the detector output (gold_alerts), never ground truth. Contains scores, flags and severity, no case counts,
so no small-cell (PRV-2) issue. Every disease in gold_alerts is exported; nothing is hard-coded.

Rows: every ASSESSED district x disease x report week in the last --weeks report weeks (--alerts-only keeps just the
alert weeks). Severity, threshold and flags are copied from the parquet, never recomputed, so the dashboard shows
exactly what the evaluated detector produced.

File layout:
  {"generated_at": ..., "data_through": <latest week_start>, "detector": ..., "threshold": ..., "min_cases": ...,
   "diseases": [...], "weeks": N, "rows": [{district_code, disease, epi_year, epi_week, week_start, detector, score,
   threshold, severity, alert_flag, reporting_artefact_flag}, ...]}
The per-row keys are exactly the ones in the sample; the extra top-level keys are additive.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ROW_KEYS = ["district_code", "disease", "epi_year", "epi_week", "week_start", "detector", "score", "threshold",
            "severity", "alert_flag", "reporting_artefact_flag"]


def build(alerts, weeks, alerts_only):
    a = alerts.copy()
    a["district_code"] = a["district_code"].astype(str).str.zfill(2)
    a["week_start_date"] = pd.to_datetime(a["week_start_date"])
    last = a["week_start_date"].max()
    cutoff = last - pd.Timedelta(weeks=weeks - 1)
    a = a[(a["week_start_date"] >= cutoff) & a["assessed"]]
    if alerts_only:
        a = a[a["alert_flag"]]
    a = a.sort_values(["district_code", "disease", "week_start_date"], ascending=[True, True, False])
    rows = []
    for r in a.itertuples(index=False):
        rows.append({
            "district_code": r.district_code, "disease": r.disease, "epi_year": int(r.epi_year),
            "epi_week": int(r.epi_week), "week_start": r.week_start_date.strftime("%Y-%m-%d"),
            "detector": r.detector, "score": None if pd.isna(r.score) else round(float(r.score), 3),
            "threshold": float(r.threshold), "severity": None if pd.isna(r.severity) else str(r.severity),
            "alert_flag": bool(r.alert_flag), "reporting_artefact_flag": bool(r.reporting_artefact_flag)})
    return rows, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alerts", default=str(ROOT / "data" / "analytics" / "gold_alerts.parquet"))
    ap.add_argument("--out", default=str(ROOT / "data" / "outputs" / "alerts.json"))
    ap.add_argument("--weeks", type=int, default=26, help="most recent report weeks to export")
    ap.add_argument("--alerts-only", action="store_true", help="export only the alert weeks")
    a = ap.parse_args()

    al = pd.read_parquet(a.alerts)
    rows, last = build(al, a.weeks, a.alerts_only)
    doc = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "data_through": last.strftime("%Y-%m-%d"),
           "detector": str(al["detector"].iloc[0]), "threshold": float(al["threshold"].iloc[0]),
           "min_cases": int(al["min_cases"].iloc[0]) if "min_cases" in al.columns else None,
           "diseases": sorted(al["disease"].unique().tolist()), "weeks": a.weeks, "rows": rows}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False), encoding="utf-8")

    df = pd.DataFrame(rows)
    print(f"read {a.alerts}: {len(al):,} rows, diseases {doc['diseases']}, threshold {doc['threshold']}, "
          f"min_cases {doc['min_cases']}")
    print(f"wrote {out}: {len(rows):,} rows, last {a.weeks} report weeks up to {doc['data_through']}, "
          f"{out.stat().st_size / 1e6:.2f} MB (dashboard budget: all outputs together <= 10 MB)")
    print("rows by disease:", df.groupby("disease").size().to_dict())
    print("alert weeks by disease:", df[df["alert_flag"]].groupby("disease").size().to_dict())
    print("severity of alert weeks:", df[df["alert_flag"]]["severity"].value_counts().to_dict())
    n_art = int(df["reporting_artefact_flag"].sum())
    print(f"artefact-flagged weeks in the export: {n_art}")
    missing = set(al["disease"].unique()) - set(df["disease"].unique()) if len(df) else set(al["disease"].unique())
    if missing:
        print(f"WARNING: no rows for {sorted(missing)} in this window (no assessed weeks, or --alerts-only with no alerts)")


if __name__ == "__main__":
    main()

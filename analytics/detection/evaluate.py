"""Detection evaluation harness (AN-2, conflict C1).

THIS IS THE ONLY CODE ALLOWED TO READ THE GROUND-TRUTH FILE. Detectors must never import it
or open data/truth/*.

Input `alerts` must be the FULL scored table (one row per district x disease x week, flagged or not),
because district and year counts for the false-alert rate come from it. Columns used:
    district_code (str), disease, week_start_date (or epi_year + epi_week), alert_flag (bool),
    reporting_artefact_flag (bool, optional), score (float, only needed for threshold_sweep)

Definitions
  effective alert   alert_flag AND NOT reporting_artefact_flag
  outbreak window   [start_date, end_date + window_grace_days] from the truth file. The grace period
                    (default 0) lets report-week alerts that arrive after onset ends count as in-window,
                    because cases are reported up to 21 days late (gold contract v0.3). An alert week overlaps the window
                    if week_start <= end_date and week_start + 6 days >= start_date.
                    A truth district_code of "ALL" matches every district (national surge).
  detected          at least one effective alert, same disease, in the window
  days to detect    max(first alert week_start, start_date) - start_date, in days
  false alert       effective alert outside every matching window. Consecutive alert weeks
                    (gap <= 7 days) in the same district-disease count as ONE episode.
  false alerts per district-year
                    false episodes / (n_districts x years covered). Diseases are summed, so with
                    two diseases this is per district-year across both.
"""
import datetime as dt

import numpy as np
import pandas as pd


def load_truth(path):
    t = pd.read_csv(path, dtype={"district_code": str})
    t["start_date"] = pd.to_datetime(t["start_date"])
    t["end_date"] = pd.to_datetime(t["end_date"])
    return t


def _prepare(df):
    df = df.copy()
    df["district_code"] = df["district_code"].astype(str).str.zfill(2)
    if "week_start_date" not in df.columns:
        df["week_start_date"] = [
            dt.date.fromisocalendar(int(y), int(w), 1) for y, w in zip(df["epi_year"], df["epi_week"])
        ]
    df["week_start_date"] = pd.to_datetime(df["week_start_date"])
    flag = df["alert_flag"].astype(bool)
    if "reporting_artefact_flag" in df.columns:
        flag = flag & ~df["reporting_artefact_flag"].astype(bool)
    df["_effective"] = flag
    return df


def _in_window(df, outbreak, grace_days=0):
    m = (df["disease"] == outbreak["disease"]) & \
        (df["week_start_date"] <= outbreak["end_date"] + pd.Timedelta(days=grace_days)) & \
        (df["week_start_date"] + pd.Timedelta(days=6) >= outbreak["start_date"])
    if outbreak["district_code"] != "ALL":
        m &= df["district_code"] == outbreak["district_code"]
    return m


def per_outbreak(alerts, truth, window_grace_days=0):
    """One row per truth outbreak: detected (bool) and days_to_detect (NaN if missed)."""
    df = _prepare(alerts)
    eff = df[df["_effective"]]
    rows = []
    for _, o in truth.iterrows():
        hit = eff[_in_window(eff, o, window_grace_days)]
        if hit.empty:
            rows.append(dict(outbreak_id=o["outbreak_id"], detected=False, days_to_detect=np.nan))
        else:
            first = max(hit["week_start_date"].min(), o["start_date"])
            rows.append(dict(outbreak_id=o["outbreak_id"], detected=True,
                             days_to_detect=float((first - o["start_date"]).days)))
    return pd.DataFrame(rows)


def evaluate(alerts, truth, window_grace_days=0):
    """Return the AN-2 metrics as a dict."""
    df = _prepare(alerts)
    eff = df[df["_effective"]]
    po = per_outbreak(alerts, truth, window_grace_days)

    in_any = pd.Series(False, index=eff.index)
    for _, o in truth.iterrows():
        in_any |= _in_window(eff, o, window_grace_days)
    false = eff[~in_any].sort_values(["district_code", "disease", "week_start_date"])
    episodes = 0
    if not false.empty:
        gap = false.groupby(["district_code", "disease"])["week_start_date"].diff()
        episodes = int(gap.isna().sum() + (gap > pd.Timedelta(days=7)).sum())

    years = ((df["week_start_date"].max() - df["week_start_date"].min()).days + 7) / 365.25
    district_years = df["district_code"].nunique() * years
    n_out = len(po)
    n_det = int(po["detected"].sum())
    return dict(
        n_outbreaks=n_out,
        n_detected=n_det,
        sensitivity=n_det / n_out if n_out else float("nan"),
        false_alert_episodes=episodes,
        false_alerts_per_district_year=episodes / district_years if district_years else float("nan"),
        median_days_to_detect=float(po["days_to_detect"].median()) if n_det else float("nan"),
    )


def threshold_sweep(scored, truth, thresholds, window_grace_days=0, min_cases=0):
    """Trade-off curve for conflict C1: rerun evaluate() with alert_flag = score >= t.
    min_cases > 0 adds a count floor: an alert also needs cases >= min_cases that week
    (guards against tiny counts producing large z-scores). `scored` then needs a `cases` column."""
    if min_cases and "cases" not in scored.columns:
        raise ValueError("min_cases needs a 'cases' column in the scored table")
    rows = []
    for t in thresholds:
        s = scored.copy()
        s["alert_flag"] = s["score"] >= t
        if min_cases:
            s["alert_flag"] &= s["cases"] >= min_cases
        m = evaluate(s, truth, window_grace_days)
        m["threshold"] = float(t)
        m["min_cases"] = int(min_cases)
        rows.append(m)
    cols = ["threshold", "sensitivity", "false_alerts_per_district_year",
            "median_days_to_detect", "n_detected", "n_outbreaks", "false_alert_episodes", "min_cases"]
    return pd.DataFrame(rows)[cols]


def per_outbreak_context(alerts, truth, window_grace_days=0, pre_weeks=4):
    """Diagnostic: per outbreak, was the detector already alerting just BEFORE the outbreak began?
    pre_alert_weeks > 0 means a 'detection' may be a carry-over of an earlier alert, not a real catch."""
    df = _prepare(alerts)
    eff = df[df["_effective"]]
    base = per_outbreak(alerts, truth, window_grace_days)
    pre = []
    for _, o in truth.iterrows():
        m = (eff["disease"] == o["disease"]) & (eff["week_start_date"] < o["start_date"]) & \
            (eff["week_start_date"] >= o["start_date"] - pd.Timedelta(weeks=pre_weeks))
        if o["district_code"] != "ALL":
            m &= eff["district_code"] == o["district_code"]
        pre.append(int(m.sum()))
    base["pre_alert_weeks"] = pre
    return base


def load_artefacts(path):
    a = pd.read_csv(path, dtype={"district_code": str})
    a["district_code"] = a["district_code"].str.zfill(2)
    for c in ("silent_start", "silent_end", "backfill_week_start"):
        a[c] = pd.to_datetime(a[c])
    return a


def artefact_check(alerts, artefacts, diseases=("dengue", "diarrhoeal")):
    """AN-3: what the reporting-artefact rule does in the silent weeks and the back-fill week.
    Per artefact (district x disease): raw alert weeks (what a naive detector would act on),
    effective alert weeks (after suppression), and whether the rule flagged the back-fill week.
    Artefacts never overlap an outbreak window, so every raw alert here is a false alert."""
    df = _prepare(alerts)
    if "reporting_artefact_flag" not in df.columns:
        df["reporting_artefact_flag"] = False
    df["_raw"] = df["alert_flag"].astype(bool)
    rows = []
    for _, a in artefacts[artefacts["disease"].isin(diseases)].iterrows():
        m = (df["district_code"] == a["district_code"]) & (df["disease"] == a["disease"]) & \
            (df["week_start_date"] >= a["silent_start"]) & (df["week_start_date"] <= a["backfill_week_start"])
        w = df[m]
        flagged = df[(df["district_code"] == a["district_code"]) & (df["disease"] == a["disease"]) &
                     (df["week_start_date"] == a["backfill_week_start"])]["reporting_artefact_flag"]
        rows.append(dict(district_code=a["district_code"], disease=a["disease"],
                         backfill_week_start=a["backfill_week_start"].date(),
                         raw_alert_weeks=int(w["_raw"].sum()), effective_alert_weeks=int(w["_effective"].sum()),
                         backfill_week_flagged=bool(flagged.any())))
    return pd.DataFrame(rows)

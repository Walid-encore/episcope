"""gold_alerts keeps every week and says why a week was not assessed; the usable-baseline variant
(the baseline make_alerts uses) equals the calendar ears_c2 when nothing is masked, scores more weeks when
reporting is patchy, and leaves only warm-up weeks unscored (no 'baseline_masked' weeks)."""
import numpy as np
import pandas as pd

from analytics.detection.ears_c2 import ears_c2_pandas
from analytics.detection.make_alerts import WARM_UP_WEEKS, build_alerts
from analytics.detection.variants import ears_c2_usable_pandas


def _data(n=40):
    weeks = pd.date_range("2022-01-03", periods=n, freq="7D")
    rng = np.random.default_rng(0)
    rows = []
    for dist, under in (("01", []), ("02", [15, 17, 19, 21])):     # series 02: patchy reporting
        for i, w in enumerate(weeks):
            comp = 0.5 if i in under else 1.0
            iso = w.isocalendar()
            rows.append(dict(district_code=dist, disease="dengue", epi_year=int(iso.year),
                             epi_week=int(iso.week), week_start_date=w, cases=int(rng.poisson(10)),
                             late_cases_ge8d=0, reporting_completeness=comp))
    return pd.DataFrame(rows)


def test_every_week_has_exactly_one_row():
    data = _data()
    out = build_alerts(data, engine="pandas")
    assert len(out) == len(data)
    assert not out.duplicated(["district_code", "disease", "week_start_date"]).any()


def test_assessed_flag_matches_score():
    out = build_alerts(_data(), engine="pandas")
    assert (out["assessed"] == out["score"].notna()).all()
    assert out.loc[out["assessed"], "not_assessed_reason"].isna().all()
    assert out.loc[~out["assessed"], "not_assessed_reason"].notna().all()


def test_warm_up_is_exactly_the_documented_length_when_nothing_is_masked():
    out = build_alerts(_data(), engine="pandas")
    s = out[out["district_code"] == "01"].sort_values("week_start_date").reset_index(drop=True)
    assert list(s.index[~s["assessed"]]) == list(range(WARM_UP_WEEKS))
    assert (s.loc[~s["assessed"], "not_assessed_reason"] == "warm_up").all()


def test_masked_stretch_is_reported_not_dropped_and_leaves_no_holes():
    """Usable-weeks baseline: a silent stretch keeps its rows, and after warm-up every week is scored.
    'baseline_masked' (the calendar baseline's blind weeks) must no longer occur."""
    data = _data()
    out = build_alerts(data, engine="pandas")
    s = out[out["district_code"] == "02"].sort_values("week_start_date").reset_index(drop=True)
    assert len(s) == (data["district_code"] == "02").sum()              # nothing dropped
    assert not (out["not_assessed_reason"] == "baseline_masked").any()
    assert list(s.index[~s["assessed"]]) == list(range(WARM_UP_WEEKS))   # only warm-up is unscored
    assert (s.loc[~s["assessed"], "not_assessed_reason"] == "warm_up").all()
    assert s.loc[~s["assessed"], "severity"].isna().all()
    assert not s.loc[~s["assessed"], "alert_flag"].any()


def test_reporting_artefact_weeks_never_alert():
    """With a threshold nobody can miss, every assessed week alerts EXCEPT those inside the artefact."""
    out = build_alerts(_data(), threshold=-1e9, min_cases=0, engine="pandas")
    s = out[out["district_code"] == "02"].sort_values("week_start_date").reset_index(drop=True)
    assert s["reporting_artefact_flag"].any()                            # the silent stretch is flagged
    assert not s.loc[s["reporting_artefact_flag"], "alert_flag"].any()   # ... and suppressed
    assert (s["alert_flag"] == (s["assessed"] & ~s["reporting_artefact_flag"])).all()
    assert s.loc[s["reporting_artefact_flag"], "severity"].isna().all()


def test_usable_baseline_equals_ears_c2_when_nothing_is_masked():
    data = _data()
    data = data[data["district_code"] == "01"]
    a = ears_c2_pandas(data, threshold=3.0).sort_values("week_start_date").reset_index(drop=True)
    b = ears_c2_usable_pandas(data, threshold=3.0).sort_values("week_start_date").reset_index(drop=True)
    assert a["score"].isna().equals(b["score"].isna())
    np.testing.assert_allclose(a["score"].dropna().to_numpy(), b["score"].dropna().to_numpy(), atol=1e-9)


def test_usable_baseline_scores_weeks_the_calendar_baseline_cannot():
    data = _data()
    data = data[data["district_code"] == "02"]
    a = ears_c2_pandas(data, threshold=3.0)
    b = ears_c2_usable_pandas(data, threshold=3.0)
    assert b["score"].notna().sum() > a["score"].notna().sum()

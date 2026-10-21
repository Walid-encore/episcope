"""Tests for analytics/detection/evaluate.py using tiny hand-made tables."""
import pandas as pd

from analytics.detection.evaluate import evaluate, per_outbreak, threshold_sweep


def _weeks(n=52, start="2022-01-03"):
    return [pd.Timestamp(start) + pd.Timedelta(weeks=i) for i in range(n)]


def _table(flag_weeks, artefact_weeks=(), district="01", disease="dengue", n=52):
    rows = []
    for i, w in enumerate(_weeks(n)):
        rows.append(dict(district_code=district, disease=disease, week_start_date=w,
                         alert_flag=i in flag_weeks, reporting_artefact_flag=i in artefact_weeks,
                         score=3.0 if i in flag_weeks else 0.0))
    return pd.DataFrame(rows)


def _truth(start_idx=10, weeks=4, district="01"):
    s = _weeks()[start_idx]
    return pd.DataFrame([dict(outbreak_id="T1", district_code=district, disease="dengue",
                              start_date=s, end_date=s + pd.Timedelta(days=7 * weeks - 1),
                              peak_multiplier=3.0, kind="local")])


def test_detected_with_delay():
    m = evaluate(_table({12}), _truth())
    assert m["sensitivity"] == 1.0
    assert m["median_days_to_detect"] == 14.0
    assert m["false_alert_episodes"] == 0


def test_missed_outbreak():
    m = evaluate(_table(set()), _truth())
    assert m["sensitivity"] == 0.0
    assert pd.isna(m["median_days_to_detect"])


def test_false_alerts_collapse_into_episodes():
    # weeks 30,31,32 are one episode; week 40 is another. Both are outside the outbreak window.
    m = evaluate(_table({11, 30, 31, 32, 40}), _truth())
    assert m["sensitivity"] == 1.0
    assert m["false_alert_episodes"] == 2


def test_artefact_flag_suppresses_alert():
    t = _table({30}, artefact_weeks={30})
    m = evaluate(t, _truth())
    assert m["false_alert_episodes"] == 0


def test_national_surge_matches_any_district():
    rows = pd.concat([_table({12}, district="01"), _table(set(), district="02")])
    truth = _truth(district="ALL")
    assert per_outbreak(rows, truth)["detected"].iloc[0]


def test_threshold_sweep_is_monotone_in_false_alerts():
    t = _table({11, 30, 40})
    t.loc[t["alert_flag"], "score"] = [5.0, 2.0, 1.0]
    sweep = threshold_sweep(t, _truth(), [0.5, 1.5, 3.0, 6.0])
    fa = sweep["false_alerts_per_district_year"].tolist()
    assert fa == sorted(fa, reverse=True)
    assert sweep["sensitivity"].iloc[0] == 1.0 and sweep["sensitivity"].iloc[-1] == 0.0

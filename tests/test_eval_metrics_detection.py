import numpy as np
import pandas as pd

from analytics.eval_metrics import COLS, detection_rows, forecast_rows, upsert


def _inputs():
    ops = pd.DataFrame([dict(detector="ears_c2", feasible=True, threshold=7.0, min_cases=15,
                             sensitivity=0.8, false_per_district_year=0.4, median_days_to_detect=5.0)])

    def pt(thr, floor, s, fa, d):
        return dict(detector="ears_c2", threshold=thr, min_cases=floor, sensitivity=s,
                    false_alerts_per_district_year=fa, median_days_to_detect=d)

    curve = pd.DataFrame([pt(6.0, 15, 0.85, 0.6, 4.0), pt(7.0, 15, 0.8, 0.4, 5.0),
                          pt(30.0, 15, 0.0, 0.0, np.nan),      # nothing detected: no median
                          pt(7.0, 0, 0.9, 3.0, 2.0)])          # other count floor: must be ignored
    return ops, {"ears_c2": curve}


def _forecast_summary():
    return pd.DataFrame([
        dict(model="nb_lagged_weather", horizon_weeks="all", n=10, mae=2.4, coverage_80=0.9, wis=1.5, wis_ratio_vs_naive=0.63),
        dict(model="seasonal_naive", horizon_weeks="all", n=10, mae=3.5, coverage_80=0.83, wis=2.4, wis_ratio_vs_naive=1.0),
    ])


def test_operating_point_and_curve_rows():
    ops, curves = _inputs()
    r = detection_rows(ops, curves, "r1")
    assert list(r.columns) == COLS
    assert set(r.component) == {"detection"}
    assert not r["value"].isna().any()
    opr = r[r["threshold"].isna()]
    assert set(opr["metric"]) == {"sensitivity", "false_alerts_per_district_year", "median_days_to_detect",
                                  "operating_threshold", "operating_min_cases", "operating_point_feasible"}
    cv = r[r["threshold"].notna()]
    assert set(cv["threshold"]) == {6.0, 7.0, 30.0}                      # only the operating floor
    assert len(cv) == 8                                                  # 3 + 3 + 2 (no median at 30)
    assert not ((cv["threshold"] == 30.0) & (cv["metric"] == "median_days_to_detect")).any()
    assert len(r) == 14


def test_upsert_idempotent_and_keeps_forecast_rows(tmp_path):
    ops, curves = _inputs()
    upsert(forecast_rows(_forecast_summary(), "r1"), tmp_path)
    upsert(detection_rows(ops, curves, "r1"), tmp_path)
    n1 = len(pd.read_parquet(tmp_path / "gold_eval_metrics.parquet"))
    upsert(detection_rows(ops, curves, "r1"), tmp_path)                  # re-run
    t = pd.read_parquet(tmp_path / "gold_eval_metrics.parquet")
    assert len(t) == n1
    assert (t.component == "forecast").sum() > 0
    assert (t.component == "detection").sum() == 14

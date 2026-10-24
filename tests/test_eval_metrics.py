import pandas as pd

from analytics.eval_metrics import COLS, forecast_rows, upsert


def _summary():
    return pd.DataFrame([
        dict(model="nb_lagged_weather", horizon_weeks="all", n=10, mae=2.4, coverage_80=0.9, wis=1.5, wis_ratio_vs_naive=0.63),
        dict(model="seasonal_naive", horizon_weeks="all", n=10, mae=3.5, coverage_80=0.83, wis=2.4, wis_ratio_vs_naive=1.0),
        dict(model="nb_lagged_weather", horizon_weeks="1", n=5, mae=2.3, coverage_80=0.89, wis=1.4, wis_ratio_vs_naive=0.6),
        dict(model="seasonal_naive", horizon_weeks="1", n=5, mae=3.5, coverage_80=0.83, wis=2.4, wis_ratio_vs_naive=1.0),
    ])


def test_rows_and_contract_columns():
    r = forecast_rows(_summary(), "r1")
    assert list(r.columns) == COLS
    assert set(r.component) == {"forecast"}
    assert {"mae", "coverage_80", "wis", "mae_h1", "wis_ratio_vs_naive"} <= set(r.metric)
    assert r.threshold.isna().all()


def test_upsert_idempotent_and_keeps_other_components(tmp_path):
    det = pd.DataFrame([dict(run_id="r1", component="detection", metric="sensitivity", value=0.8,
                             model_or_detector="ears_c2", threshold=3.0)])
    upsert(det, tmp_path)
    upsert(forecast_rows(_summary(), "r1"), tmp_path)
    n1 = len(pd.read_parquet(tmp_path / "gold_eval_metrics.parquet"))
    upsert(forecast_rows(_summary(), "r1"), tmp_path)   # re-run
    t = pd.read_parquet(tmp_path / "gold_eval_metrics.parquet")
    assert len(t) == n1
    assert (t.component == "detection").sum() == 1

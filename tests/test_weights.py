import pandas as pd
from analytics.optimise.weights import severity_weights

CODES = ["01", "02", "03", "04"]


def _cases():
    return pd.DataFrame({
        "district_code": ["01", "02", "03"],
        "cases": [10000, 10000, 3],
        "admitted_cases": [1000, 3000, 3],
        "deaths": [10, 60, 3]})


def test_mean_one_and_all_districts_present():
    w = severity_weights(_cases(), CODES)
    assert list(w.index) == CODES
    assert abs(w.mean() - 1) < 1e-9


def test_sicker_district_weighs_more_and_missing_is_neutral_ish():
    w = severity_weights(_cases(), CODES)
    assert w["02"] > w["01"]
    assert w["04"] > 0


def test_tiny_district_is_shrunk_not_extreme():
    w = severity_weights(_cases(), CODES, lo=0.0, hi=100.0)
    assert w["03"] < 2 * w["02"]      # 3 cases, all admitted and dead, must not dominate


def test_clip_bounds_before_rescale():
    w = severity_weights(_cases(), CODES)
    assert w.max() / w.min() <= 2.0 / 0.5 + 1e-9


def test_no_events_gives_equal_weights():
    c = _cases(); c["deaths"] = 0
    assert (severity_weights(c, CODES) == 1.0).all()

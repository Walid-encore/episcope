"""CUSUM must not add a back-fill spike to its running sum (AN-3). Uses the tests/fixtures data."""
from pathlib import Path

import pandas as pd
import pytest

from analytics.detection.cusum import cusum_pandas
from analytics.detection.evaluate import evaluate, load_truth

FIX = Path(__file__).resolve().parent / "fixtures"
K = 1.0  # the default allowance


@pytest.fixture(scope="module")
def cases():
    return pd.read_csv(FIX / "gold_weekly_cases_fixture.csv", dtype={"district_code": str})


@pytest.fixture(scope="module")
def artefact():
    return pd.read_csv(FIX / "artefact_fixture.csv", dtype={"district_code": str}).iloc[0]


def _flagged_and_previous(alerts, art):
    a = alerts[(alerts.district_code == art.district_code) & (alerts.disease == art.disease)]
    a = a.sort_values("week_start_date").reset_index(drop=True)
    i = int(a.index[a.reporting_artefact_flag][0])
    return a.loc[i, "score"], a.loc[i - 1, "score"]


def test_flagged_week_adds_nothing_to_the_sum(cases, artefact):
    now, prev = _flagged_and_previous(cusum_pandas(cases, k=K), artefact)
    assert now == pytest.approx(max(0.0, prev - K))


def test_naive_variant_adds_the_spike(cases, artefact):
    masked_now, _ = _flagged_and_previous(cusum_pandas(cases, k=K), artefact)
    naive_now, _ = _flagged_and_previous(cusum_pandas(cases, k=K, mask=False), artefact)
    assert naive_now > masked_now


def test_masking_does_not_cost_sensitivity(cases):
    truth = load_truth(FIX / "ground_truth_fixture.csv")
    assert evaluate(cusum_pandas(cases), truth)["sensitivity"] == 1.0

"""AN-10: SEIR calibration on a gold-shaped table. The table here is built in the test (known parameters) to check the
code; the real run uses data/gold_csv via `python -m analytics.scenario.run_seir`."""
import numpy as np
import pandas as pd
import pytest

from analytics.scenario.run_seir import run, simulate

N = 170e6


def _gold(path, with_surge=True, seed=1):
    rng = np.random.default_rng(seed)
    weeks = pd.date_range("2021-01-04", "2025-12-29", freq="7D")
    surge = np.zeros(len(weeks))
    if with_surge:
        surge[150:190] = simulate(N, 2.2, 0.02, 2e-6, 40)
    rows = []
    for k, w in enumerate(weeks):
        iso = w.isocalendar()
        mu = (900 + 700 * np.cos(2 * np.pi * (iso.week - 3) / 52.0) + surge[k]) / 8
        for d in range(8):
            rows.append(dict(district_code=f"{d + 1:02d}", disease="ili", epi_year=int(iso.year),
                             epi_week=int(iso.week), week_start_date=w.date(), cases=int(rng.poisson(mu))))
    path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path / "gold_weekly_cases.csv", index=False)


def test_recovers_parameters_and_orders_scenarios(tmp_path):
    _gold(tmp_path / "gold_csv")
    info, s = run(tmp_path / "gold_csv", tmp_path / "out", population=N, n_boot=3)
    assert info["fitted"]["R0"] == pytest.approx(2.2, rel=0.1)
    assert info["fitted"]["reporting_fraction"] == pytest.approx(0.02, rel=0.25)
    t = s.set_index("scenario")["total_cases"]
    assert t["contact_reduction_40pct_early"] < t["contact_reduction_20pct_early"] < t["baseline_no_intervention"]
    assert t["contact_reduction_40pct_early"] < t["contact_reduction_40pct_late"]   # starting earlier helps more
    assert (tmp_path / "out" / "seir_scenarios.csv").exists() and (tmp_path / "out" / "seir_fit.json").exists()


def test_refuses_when_there_is_no_surge(tmp_path):
    _gold(tmp_path / "gold_csv", with_surge=False)
    with pytest.raises(SystemExit):
        run(tmp_path / "gold_csv", tmp_path / "out", population=N, n_boot=1)


def test_refuses_fixture_or_sample_folders(tmp_path):
    _gold(tmp_path / "fixture_gold")
    with pytest.raises(SystemExit):
        run(tmp_path / "fixture_gold", tmp_path / "out", population=N, n_boot=1)


def test_no_intervention_scenario_is_unchanged_by_start_day():
    a = simulate(N, 2.0, 0.05, 1e-6, 30)
    b = simulate(N, 2.0, 0.05, 1e-6, 30, reduction=0.0, start_day=40.0)
    assert np.allclose(a, b)

import numpy as np

from analytics.scenario.seir import fit, predict, run_scenarios, simulate, weekly_new

N = 2_000_000.0


def test_population_is_conserved():
    sol = simulate(2.5, 100, N, 200)
    assert np.allclose(sol[:, :4].sum(axis=1), N, rtol=1e-6)


def test_no_epidemic_below_one():
    wk = weekly_new(simulate(0.8, 100, N, 140))
    assert wk[-1] < wk[0]


def test_intervention_lowers_peak_and_attack_rate():
    base = simulate(2.5, 100, N, 300)
    cut = simulate(2.5, 100, N, 300, intervention=(40, 0.5))
    assert weekly_new(cut).max() < weekly_new(base).max() and cut[-1, 4] < base[-1, 4]


def test_fit_recovers_r0_from_simulated_surge():
    rho, b = 0.05, 20.0
    rng = np.random.default_rng(3)
    obs = rng.poisson(predict(2.2, 300, N, 24, rho, b)).astype(float)
    f = fit(obs, N, rho, b)
    assert abs(f["R0"] - 2.2) / 2.2 < 0.15 and f["r2_sqrt"] > 0.9


def test_scenario_table_has_unmitigated_and_nine_scenarios():
    scen, curves, peak_w = run_scenarios(2.2, 300, N, horizon_weeks=40, capacity=50_000)
    assert len(scen) == 10 and scen.iloc[0]["scenario"] == "unmitigated"
    assert scen["peak_weekly_symptomatic"].iloc[1:].max() <= scen["peak_weekly_symptomatic"].iloc[0] + 1e-6
    assert "peak_beds_vs_capacity" in scen.columns


def test_growth_phase_lets_a_small_seed_produce_the_surge():
    rho, b, pre = 0.01, 30.0, 12
    rng = np.random.default_rng(5)
    obs = rng.poisson(predict(1.8, 500, N, 26, rho, b, pre)).astype(float)
    f = fit(obs, N, rho, b, pre=pre)
    assert abs(f["R0"] - 1.8) / 1.8 < 0.2 and f["I0"] <= 1e5 + 1


def test_scenarios_report_start_day_and_clipping():
    scen, _, peak_w = run_scenarios(2.0, 200, N, horizon_weeks=52)
    assert {"start_day", "start_clipped"} <= set(scen.columns)
    late = scen[scen["scenario"] != "unmitigated"]
    assert (late["start_day"] >= 0).all()

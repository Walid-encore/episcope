"""Build a tiny, deterministic fixture in the gold_weekly_cases format (docs/gold_contract.md).

Run from the repo root:   python tests/fixtures/make_fixture.py

Outputs (all in tests/fixtures/):
  gold_weekly_cases_fixture.csv   the fake gold table (3 districts x 2 diseases x 156 ISO weeks)
  ground_truth_fixture.csv        outbreak truth. EVALUATION CODE ONLY. Detectors must never read it.
  artefact_fixture.csv            where the silent-then-backfill artefact is. TESTS ONLY.

What is injected
  O001 sharp dengue outbreak, O004 second sharp dengue outbreak (district 01)
  O002 slow-growth diarrhoeal outbreak (district 02)
  O003 local diarrhoeal outbreak (district 03)
  ARTEFACT: district 03 dengue. A big facility is silent for 3 weeks (reporting_facilities drops,
  cases fall), then back-fills, so the back-fill week shows a spike that is NOT an outbreak.
  This is the "as-of" view of the data: counts as they looked in the week they were reported.

District codes are real codes from data/reference/districts.csv (first three: 01, 02, 03).
Population is read from that file when it exists, else a fallback is used.
"""
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEED = 42
CODES = ["01", "02", "03"]
DISEASES = ["dengue", "diarrhoeal"]
FIRST_MONDAY = pd.Timestamp("2021-01-04")  # Monday of ISO week 2021-W01
N_WEEKS = 156                              # 3 ISO years
EXPECTED_FACILITIES = 8

# weekly baseline per 100k, peak epi-week, seasonal amplitude, NB dispersion (smaller = noisier)
DISEASE_PARAMS = {
    "dengue": dict(rate=1.5, peak_week=36, amp=0.8, disp=12.0, admit=0.15),
    "diarrhoeal": dict(rate=6.0, peak_week=28, amp=0.4, disp=20.0, admit=0.05),
}
DISTRICT_SCALE = {"01": 1.0, "02": 0.7, "03": 1.3}

# (id, district, disease, kind, first Monday, weeks, peak multiplier)
OUTBREAKS = [
    ("O001", "01", "dengue", "local", "2021-08-02", 5, 4.0),
    ("O002", "02", "diarrhoeal", "slow_growth", "2022-03-07", 12, 2.5),
    ("O003", "03", "diarrhoeal", "local", "2022-09-05", 4, 3.5),
    ("O004", "01", "dengue", "local", "2023-08-14", 6, 3.0),
]
# silent facility on district 03 dengue: week indexes (0-based) of the silence; back-fill is the week after
ARTEFACT = dict(district_code="03", disease="dengue", silent_first=80, silent_len=3)
SILENT_SHARE = 0.6  # share of the district's reports that the silent facility normally supplies


def load_population():
    path = ROOT / "data" / "reference" / "districts.csv"
    if path.exists():
        d = pd.read_csv(path, dtype={"district_code": str})
        return dict(zip(d["district_code"], d["population"]))
    return {c: 1_000_000 for c in CODES}


def outbreak_multiplier(kind, n, peak):
    """Multiplier curve over n weeks. Sharp: triangle peaking early. Slow: steady ramp."""
    idx = np.arange(n)
    if kind == "slow_growth":
        return 1 + (peak - 1) * (idx + 1) / n
    top = max(1, n // 3)
    shape = np.where(idx <= top, (idx + 1) / (top + 1), (n - idx) / (n - top))
    return 1 + (peak - 1) * np.clip(shape, 0, 1)


def build():
    rng = np.random.default_rng(SEED)
    pop = load_population()
    week_starts = [FIRST_MONDAY + pd.Timedelta(weeks=i) for i in range(N_WEEKS)]
    iso = [w.isocalendar() for w in week_starts]
    epi_year = np.array([i.year for i in iso])
    epi_week = np.array([i.week for i in iso])

    mult = {}  # (district, disease) -> array of outbreak multipliers
    truth_rows = []
    for oid, dist, dis, kind, start, n, peak in OUTBREAKS:
        m = mult.setdefault((dist, dis), np.ones(N_WEEKS))
        s = week_starts.index(pd.Timestamp(start))
        m[s:s + n] = outbreak_multiplier(kind, n, peak)
        end = pd.Timestamp(start) + pd.Timedelta(days=7 * n - 1)
        truth_rows.append(dict(outbreak_id=oid, district_code=dist, disease=dis,
                               start_date=start, end_date=end.date().isoformat(),
                               peak_multiplier=peak, kind=kind))

    rows = []
    for dist in CODES:
        for dis in DISEASES:
            p = DISEASE_PARAMS[dis]
            base = pop[dist] * p["rate"] / 1e5 * DISTRICT_SCALE[dist]
            season = 1 + p["amp"] * np.cos(2 * np.pi * (epi_week - p["peak_week"]) / 52.0)
            mu = base * season * mult.get((dist, dis), np.ones(N_WEEKS))
            reporting = np.full(N_WEEKS, EXPECTED_FACILITIES)

            art = ARTEFACT["district_code"] == dist and ARTEFACT["disease"] == dis
            if art:
                s, k = ARTEFACT["silent_first"], ARTEFACT["silent_len"]
                mu = mu.copy()
                normal_mu = mu.copy()
                mu[s:s + k] = normal_mu[s:s + k] * (1 - SILENT_SHARE)
                reporting[s:s + k] = EXPECTED_FACILITIES - 2
                mu[s + k] = normal_mu[s + k] + SILENT_SHARE * normal_mu[s:s + k].sum()  # back-fill week

            r = p["disp"]
            cases = rng.negative_binomial(r, r / (r + mu))
            lab = rng.binomial(cases, 0.4)
            adm = rng.binomial(cases, p["admit"])
            deaths = rng.binomial(adm, 0.01)
            for i in range(N_WEEKS):
                rows.append(dict(
                    district_code=dist, disease=dis,
                    epi_year=int(epi_year[i]), epi_week=int(epi_week[i]),
                    week_start_date=week_starts[i].date().isoformat(),
                    cases=int(cases[i]), lab_confirmed_cases=int(lab[i]),
                    admitted_cases=int(adm[i]), deaths=int(deaths[i]),
                    population=int(pop[dist]),
                    incidence_per_100k=float(cases[i]) / pop[dist] * 1e5,
                    reporting_facilities=int(reporting[i]),
                    expected_facilities=EXPECTED_FACILITIES,
                    last_batch_id="fixture", updated_at="2026-01-01T00:00:00",
                ))
    cases_df = pd.DataFrame(rows)
    truth_df = pd.DataFrame(truth_rows)
    s, k = ARTEFACT["silent_first"], ARTEFACT["silent_len"]
    art_df = pd.DataFrame([dict(
        district_code=ARTEFACT["district_code"], disease=ARTEFACT["disease"],
        silent_start=week_starts[s].date().isoformat(),
        silent_end=week_starts[s + k - 1].date().isoformat(),
        backfill_week_start=week_starts[s + k].date().isoformat())])
    return cases_df, truth_df, art_df


if __name__ == "__main__":
    cases_df, truth_df, art_df = build()
    cases_df.to_csv(HERE / "gold_weekly_cases_fixture.csv", index=False)
    truth_df.to_csv(HERE / "ground_truth_fixture.csv", index=False)
    art_df.to_csv(HERE / "artefact_fixture.csv", index=False)
    print(f"cases: {len(cases_df)} rows, truth: {len(truth_df)} outbreaks, artefact rows: {len(art_df)}")

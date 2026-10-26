"""Write hand-made sample JSONs to serving/sample/ following docs/serving_contract.md (v0.2).
Run from the repo root:  python scripts/make_sample_data.py
Numbers are invented (synthetic). Edge cases included on purpose:
  - suppressed cells (cases 1-4 -> null + suppressed:true)
  - the last district has NO rows at all (no data)
  - one forecast with very wide q10-q90 bands
  - one alert row with reporting_artefact_flag = true
"""
import csv, json, math, random, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "serving" / "sample"
OUT.mkdir(parents=True, exist_ok=True)
random.seed(7)

NOW = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
DISEASES = ["dengue", "diarrhoeal"]
RESOURCES = ["iv_saline_units", "ns1_kits", "ors_sachets"]

with open(ROOT / "data" / "reference" / "districts.csv", newline="", encoding="utf-8") as f:
    D = [dict(district_code=r["district_code"].zfill(2), district_name=r["district_name"],
              division=r["division"], population=int(r["population"])) for r in csv.DictReader(f)]
NO_DATA = D[-1]["district_code"]            # district with no rows anywhere
ACTIVE = [d for d in D if d["district_code"] != NO_DATA]

WEEKS = []
for y in (2024, 2025):
    for w in range(1, 53):
        WEEKS.append((y, w, dt.date.fromisocalendar(y, w, 1).isoformat()))
DATA_AS_OF = WEEKS[-1][2]


def dump(name, obj):
    with open(OUT / name, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))


def season(w, peak):
    return 1 + 0.9 * math.sin(2 * math.pi * (w - peak + 13) / 52)


PEAK = {"dengue": 31, "diarrhoeal": 22}
SCALE = {"dengue": 9.0, "diarrhoeal": 14.0}

# ---- cases_weekly
rows, last_cases = [], {}
for i, d in enumerate(ACTIVE):
    pop_m = d["population"] / 1e6
    for dis in DISEASES:
        for (y, w, ws) in WEEKS:
            lam = SCALE[dis] * pop_m * season(w, PEAK[dis]) * (0.4 + (i % 7) / 7)
            if dis == "dengue" and i == 5 and ws >= WEEKS[-4][2]:
                lam *= 4                      # outbreak in the last 4 weeks
            c = max(0, round(random.gauss(lam, math.sqrt(lam) + 1)))
            sup = 1 <= c <= 4
            rows.append({"district_code": d["district_code"], "disease": dis, "epi_year": y,
                         "epi_week": w, "week_start": ws, "cases": None if sup else c,
                         "incidence_per_100k": None if sup else round(c / d["population"] * 1e5, 2),
                         "suppressed": sup})
            if (y, w) == WEEKS[-1][:2]:
                last_cases[(d["district_code"], dis)] = c
dump("cases_weekly.json", {"generated_at": NOW, "data_as_of": DATA_AS_OF, "rows": rows})

# ---- forecast (latest origin, horizons 1-4)
oy, ow, _ = WEEKS[-1]
frows, wide_done = [], False
for d in ACTIVE:
    for dis in DISEASES:
        m = max(last_cases[(d["district_code"], dis)], 1)
        wide = (dis == "dengue" and not wide_done)
        wide_done = wide_done or wide
        for h in range(1, 5):
            tws = (dt.date.fromisoformat(DATA_AS_OF) + dt.timedelta(weeks=h)).isoformat()
            lo, hi = (0.2, 3.5) if wide else (0.7, 1.45)
            spread = 1 + 0.08 * h
            frows.append({"district_code": d["district_code"], "disease": dis,
                          "origin_epi_year": oy, "origin_epi_week": ow, "horizon_weeks": h,
                          "target_week_start": tws, "model": "nb_lagged_weather",
                          "pred_mean": round(m * 1.05, 1), "q10": round(m * lo / spread),
                          "q50": m, "q90": round(m * hi * spread)})
dump("forecast.json", {"generated_at": NOW, "rows": frows})

# ---- alerts
arows = []
for dis in DISEASES:
    top = sorted(ACTIVE, key=lambda d: -last_cases[(d["district_code"], dis)])[:7]
    base = 6.5 if dis == "dengue" else 4.8
    for k, d in enumerate(top):
        for o in range(5):                  # 5 weeks of alert history per district
            score = round(base - k * 0.6 - o * 1.1, 1)
            if score <= 1.5:                # contract: only rows above half the threshold
                continue
            y_, w_, ws_ = WEEKS[-1 - o]
            arows.append({"district_code": d["district_code"], "disease": dis, "epi_year": y_,
                          "epi_week": w_, "week_start": ws_, "detector": "ears_c2",
                          "score": score, "threshold": 3.0,
                          "severity": "high" if score >= 5 else "medium" if score >= 3 else "low",
                          "alert_flag": score >= 3, "reporting_artefact_flag": dis == "dengue" and k == 6 and o == 0})
dump("alerts.json", {"generated_at": NOW, "rows": arows})

# ---- seasonality (district_code "ALL" = national)
srows = []
for dis in DISEASES:
    for code in ["ALL"] + [d["district_code"] for d in ACTIVE]:
        for w in range(1, 53):
            idx = round(season(w, PEAK[dis]), 2)
            srows.append({"disease": dis, "district_code": code, "epi_week": w,
                          "seasonal_index": idx, "peak_flag": idx > 1.7})
dump("seasonality.json", {"generated_at": NOW, "data_as_of": DATA_AS_OF, "rows": srows})

# ---- allocation (precomputed grid + curve)
BUDGETS, ALPHAS = [100, 200, 300, 400, 500], [0, 0.25, 0.5]
tot_pop = sum(d["population"] for d in ACTIVE)
arows2, curve = [], []
for b in BUDGETS:
    for a in ALPHAS:
        for strat in ("optimised", "population_proportional"):
            unmet_total = 0.0
            for d in ACTIVE:
                share = d["population"] / tot_pop
                for r in RESOURCES:
                    demand = share * 600 * (1 + random.random())
                    alloc = share * b * (1.0 if strat == "population_proportional" else 0.8 + 0.4 * random.random())
                    unmet = max(0.0, demand - alloc)
                    unmet_total += unmet
                    arows2.append({"budget_level": b, "alpha": a, "strategy": strat,
                                   "district_code": d["district_code"], "resource": r,
                                   "allocated_units": round(alloc, 1), "unmet_demand": round(unmet, 1)})
            curve.append({"budget_level": b, "alpha": a, "strategy": strat,
                          "total_unmet": round(unmet_total, 1),
                          "equity_gini": round(0.35 - a * 0.2, 2)})
dump("allocation.json", {"generated_at": NOW, "alpha_values": ALPHAS, "budget_levels": BUDGETS,
                         "rows": arows2, "curve": curve})

# ---- stock (latest 52 weeks)
strows = []
for d in ACTIVE:
    cap = max(40, d["population"] // 8000)
    for (y, w, _) in WEEKS[-52:]:
        strows.append({"district_code": d["district_code"], "epi_year": y, "epi_week": w,
                       "iv_saline_units": random.randint(100, 900), "ns1_kits": random.randint(30, 300),
                       "ors_sachets": random.randint(200, 2000),
                       "occupied_beds": int(cap * random.uniform(0.4, 0.95)), "beds_capacity": cap})
dump("stock.json", {"generated_at": NOW, "rows": strows})

# ---- dq_report, freshness, meta
dump("dq_report.json", {"generated_at": NOW, "batches": [
    {"batch_id": f"sample-{i}", "rows_in": 100000, "rows_out": 98000 - i * 100,
     "rows_quarantined": 2000 + i * 100,
     "rules": [{"rule_id": "R01", "reason_code": "BAD_AGE", "pass_rate": 0.99}]} for i in range(3)]})
dump("freshness.json", {"generated_at": NOW, "data_as_of": DATA_AS_OF,
                        "last_batch_id": "sample-0", "pipeline_version": "sample"})
dump("meta.json", {"generated_at": NOW, "data_as_of": DATA_AS_OF, "diseases": DISEASES,
                   "districts": D,
                   "weeks": {"first": {"epi_year": WEEKS[0][0], "epi_week": WEEKS[0][1]},
                             "last": {"epi_year": oy, "epi_week": ow}},
                   "batch_ids": ["sample-0"]})
print("wrote", len(list(OUT.glob("*.json"))), "files to", OUT, "| no-data district:", NO_DATA)

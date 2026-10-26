"""Check serving/data/*.json against docs/serving_contract.md and the PRV-2 privacy rule.
Run from the repo root:  python scripts/check_serving.py [serving/data]
Prints OK / FAIL lines and exits with code 1 if anything fails."""
import json, re, sys
from pathlib import Path

D = Path(sys.argv[1] if len(sys.argv) > 1 else "serving/data")
fails = 0


def ok(msg): print("  OK  ", msg)


def bad(msg):
    global fails; fails += 1; print("  FAIL", msg)


def load(name):
    p = D / f"{name}.json"
    if not p.exists():
        print(f"  --   {name}.json missing"); return None
    return json.loads(p.read_text(encoding="utf-8"))


def need(rows, keys, label):
    miss = [k for k in keys if rows and k not in rows[0]]
    (bad if miss else ok)(f"{label}: fields " + (f"missing {miss}" if miss else "present"))


print(f"Checking {D.resolve()}")
total = sum(f.stat().st_size for f in D.glob("**/*") if f.is_file())
(ok if total < 10_000_000 else bad)(f"total size {total / 1e6:.2f} MB decimal = {total / 2**20:.2f} MiB (strict limit: 10,000,000 bytes)")

meta = load("meta")
codes, diseases = set(), set()
if meta:
    codes = {d["district_code"] for d in meta["districts"]}
    diseases = set(meta["diseases"])
    (ok if all(re.fullmatch(r"\d{2}", c) for c in codes) else bad)(f"meta: {len(codes)} district codes are 2-digit strings")
    ok(f"meta: diseases = {sorted(diseases)}")

fr = load("freshness")
if fr: need([fr], ["generated_at", "data_as_of", "last_batch_id", "pipeline_version"], "freshness")

cw = load("cases_weekly")
if cw:
    r = cw["rows"]
    need(r, ["district_code", "disease", "epi_year", "epi_week", "week_start", "cases", "incidence_per_100k", "suppressed"], "cases_weekly")
    small = [x for x in r if x["cases"] is not None and 1 <= x["cases"] <= 4]
    (bad if small else ok)(f"PRV-2: {len(small)} rows with a visible count of 1-4")
    inc = [x for x in r if x["suppressed"] and (x["cases"] is not None or x["incidence_per_100k"] is not None)]
    (bad if inc else ok)(f"suppressed rows with non-null cases/incidence: {len(inc)}")
    nul = [x for x in r if x["cases"] is None and not x["suppressed"]]
    (bad if nul else ok)(f"null cases without suppressed flag: {len(nul)}")
    (ok if all(isinstance(x["district_code"], str) for x in r) else bad)("district_code is a string everywhere")
    if codes: (ok if {x["district_code"] for x in r} <= codes else bad)("all district codes exist in meta")
    seen = {x["disease"] for x in r}
    (ok if seen <= diseases or not diseases else bad)(f"diseases in cases_weekly {sorted(seen)} vs meta")
    print(f"       {len(r)} rows, suppressed share {sum(x['suppressed'] for x in r) / max(len(r), 1):.1%}")

fc = load("forecast")
if fc:
    r = fc["rows"]
    need(r, ["district_code", "disease", "horizon_weeks", "target_week_start", "pred_mean", "q10", "q50", "q90"], "forecast")
    badq = [x for x in r if not (x["q10"] <= x["q50"] <= x["q90"])]
    (bad if badq else ok)(f"forecast quantiles ordered (violations: {len(badq)})")
    print(f"       diseases {sorted({x['disease'] for x in r})}, horizons {sorted({x['horizon_weeks'] for x in r})}")

al = load("alerts")
if al:
    r = al["rows"]
    need(r, ["district_code", "disease", "week_start", "detector", "score", "threshold", "severity", "alert_flag", "reporting_artefact_flag"], "alerts")
    import collections
    print(f"       diseases {sorted({x['disease'] for x in r})}")
    print("       severity counts:", dict(collections.Counter(str(x['severity']) for x in r)))
    nosev = [x for x in r if x["alert_flag"] and x["severity"] is None]
    (bad if nosev else ok)(f"flagged alerts without a severity: {len(nosev)}")
    print("       alert_flag true:", sum(1 for x in r if x["alert_flag"]), "| artefact flagged:", sum(1 for x in r if x["reporting_artefact_flag"]))

ac = load("allocation")
if ac:
    r = ac["rows"]
    need(r, ["budget_level", "alpha", "strategy", "district_code", "resource", "allocated_units", "unmet_demand"], "allocation")
    badnull = [x for x in r if (x["alpha"] is None) != (x["strategy"] == "population_proportional")]
    (bad if badnull else ok)(f"alpha null only on population_proportional rows (violations: {len(badnull)})")
    print(f"       budgets {ac.get('budget_levels')}, alphas {ac.get('alpha_values')}, unit {ac.get('budget_unit')}")

for name in ("seasonality", "stock", "dq_report", "calendar"):
    x = load(name)
    if x: ok(f"{name}.json loads ({len(x.get('rows', x.get('batches', [])))} entries)")

print("\nRESULT:", "all checks passed" if fails == 0 else f"{fails} check(s) failed")
sys.exit(1 if fails else 0)

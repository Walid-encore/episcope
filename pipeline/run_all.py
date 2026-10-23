"""One-command pipeline run (ENG-1): runs each stage in order, retries a failed stage, logs timings (ENG-4).

    python -m pipeline.run_all                      # everything (generates data first if data/incoming is missing)
    python -m pipeline.run_all --sf 0.25            # generator scale factor, used only when it has to generate
    python -m pipeline.run_all --only gold          # one stage
    python -m pipeline.run_all --from detect        # from a stage onward
    python -m pipeline.run_all --regenerate         # force the generator to run again
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def _has_incoming():
    d = Path("data/incoming/encounters")
    return d.exists() and any(d.iterdir())


# (name, python args, skip_if) ; skip_if(args) -> reason string to skip, or None to run
GOLD_CSV = "data/gold_csv"
OPTIONAL = {"catalog"}  # a failure here is reported as a warning and the run continues
STAGES = [
    ("generate", ["-m", "generator.generate"], lambda a: None if (a.regenerate or not _has_incoming())
     else "data/incoming already exists (use --regenerate to rebuild it)"),
    ("bronze",   ["-m", "pipeline.bronze.ingest"], None),
    ("silver",   ["-m", "pipeline.silver.build"], None),
    ("gold",     ["-m", "pipeline.gold.build"], None),
    ("contract", ["-m", "pipeline.quality.check_gold_contract"], None),
    ("catalog",  ["-m", "pipeline.catalog"], None),
    ("export",   ["-m", "analytics.export_gold", "--gold", "data/gold", "--out", GOLD_CSV], None),
    ("alerts",   ["-m", "analytics.detection.make_alerts", "--gold", GOLD_CSV], None),
    ("demand",   ["-m", "analytics.optimise.run_demand", "--cases", GOLD_CSV + "/gold_weekly_cases.csv"], None),
    ("allocate", ["-m", "analytics.optimise.run_allocation"], None),
    ("calendar", ["-m", "analytics.calendar.preparedness", "--gold", GOLD_CSV], None),
    # evaluation needs ground truth, so it is not part of the delivery run (same rule as `make evaluate`)
    ("evaluate", ["-m", "analytics.detection.run_detection", "--gold", GOLD_CSV],
     lambda a: None if a.evaluate else "needs data/truth; add --evaluate to run it"),
]


def run_stage(name, args, retries, wait):
    for attempt in range(1, retries + 2):
        t0 = time.time()
        print(f"\n=== {name} (attempt {attempt}/{retries + 1}) ===", flush=True)
        code = subprocess.call([sys.executable, *args])
        secs = time.time() - t0
        if code == 0:
            return {"stage": name, "status": "ok", "attempts": attempt, "seconds": round(secs, 1)}
        print(f"stage {name} failed with exit code {code}", flush=True)
        if attempt <= retries:
            time.sleep(wait)
    return {"stage": name, "status": "failed", "attempts": retries + 1, "seconds": round(secs, 1)}


def main():
    names = [n for n, _, _ in STAGES]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=names)
    ap.add_argument("--from", dest="start", choices=names)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--wait", type=int, default=10, help="seconds between retries")
    ap.add_argument("--sf", default="0.05", help="generator scale factor (1 = ~25M encounters)")
    ap.add_argument("--regenerate", action="store_true", help="run the generator even if data/incoming exists")
    ap.add_argument("--evaluate", action="store_true", help="also run detection evaluation (needs data/truth)")
    a = ap.parse_args()

    todo = [s for s in STAGES
            if (a.only is None or s[0] == a.only) and (a.start is None or names.index(s[0]) >= names.index(a.start))]

    results = []
    for name, args, skip in todo:
        reason = skip(a) if skip else None
        if reason:
            print(f"\n=== {name}: skipped ({reason}) ===", flush=True)
            results.append({"stage": name, "status": "skipped", "attempts": 0, "seconds": 0.0})
            continue
        if name == "generate":
            args = args + ["--sf", str(a.sf)]
        res = run_stage(name, args, a.retries, a.wait)
        if res["status"] != "ok" and name in OPTIONAL:
            print(f"WARNING: optional stage {name} failed; continuing", flush=True)
            res["status"] = "warned"
        results.append(res)
        if res["status"] not in ("ok", "warned"):
            break

    log = Path("logs"); log.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (log / f"run_all_{stamp}.json").write_text(json.dumps(results, indent=2))
    print("\nSummary")
    for r in results:
        print(f"  {r['stage']:<10} {r['status']:<8} {r['seconds']:>8.1f}s  attempts={r['attempts']}")
    sys.exit(0 if len(results) == len(todo) and all(r["status"] in ("ok", "skipped", "warned") for r in results) else 1)


if __name__ == "__main__":
    main()

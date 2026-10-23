"""Register the lake in a persistent Spark SQL (Hive) catalog (ING-3).

    python -m pipeline.catalog

Creates databases bronze, silver and gold in data/catalog and one external Parquet table per dataset:
    bronze.<dataset>, silver.<dataset>, silver.quarantine_<dataset>, silver.dq_report, gold.<table>
The files stay where they are; only metadata is registered, so the stage is cheap and idempotent
(each table is dropped and recreated, which never deletes data for an external table).
Self-check: every table's row count through the catalog must equal a direct Parquet read, otherwise exit 1.
"""
import sys
from pathlib import Path

from pipeline.spark_session import get_spark

DATA = Path("data")
CATALOG_DIR = DATA / "catalog"
LAYERS = {"bronze": DATA / "bronze", "silver": DATA / "silver", "gold": DATA / "gold"}


def has_parquet(p: Path) -> bool:
    return p.is_dir() and any(p.rglob("*.parquet"))


def discover(layer: str, root: Path) -> dict:
    found = {}
    if not root.exists():
        return found
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        if layer == "silver" and d.name == "quarantine":
            for q in sorted(d.iterdir()):
                if has_parquet(q):
                    found[f"quarantine_{q.name}"] = q
        elif has_parquet(d):
            found[d.name] = d
    return found


def main() -> int:
    spark = get_spark(catalog_dir=CATALOG_DIR, app_name="episcope-catalog")
    problems, registered = [], 0
    print(f"{'table':<40} {'rows':>12}  check")
    for layer, root in LAYERS.items():
        spark.sql(f"CREATE DATABASE IF NOT EXISTS {layer}")
        for name, path in discover(layer, root).items():
            fq = f"{layer}.{name}"
            spark.sql(f"DROP TABLE IF EXISTS {fq}")
            spark.sql(f"CREATE TABLE {fq} USING parquet LOCATION '{path.resolve().as_uri()}'")
            try:
                spark.sql(f"MSCK REPAIR TABLE {fq}")      # discover partition folders
            except Exception:
                pass                                       # not a partitioned table
            via_catalog = spark.table(fq).count()
            direct = spark.read.parquet(str(path)).count()
            ok = via_catalog == direct
            registered += 1
            print(f"{fq:<40} {via_catalog:>12,}  {'ok' if ok else f'MISMATCH (direct {direct:,})'}")
            if not ok:
                problems.append(fq)
    if registered == 0:
        problems.append("no tables found under data/bronze, data/silver, data/gold")
    if problems:
        print("CATALOG CHECK FAILED:", ", ".join(problems))
        return 1
    print(f"catalog ready: {registered} tables in data/catalog (query with spark.sql, e.g. SELECT * FROM gold.gold_weekly_cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
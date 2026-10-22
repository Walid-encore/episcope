"""Bronze layer: raw, immutable, idempotent ingestion (ING-1, ING-4, DQ-3).

    python -m pipeline.bronze.ingest                    # default input folders
    python -m pipeline.bronze.ingest --input some/dir   # one or more folders (repeatable)
    python -m pipeline.bronze.ingest --dry-run          # show what would be loaded

What it does
- Walks the input folders recursively. Any file name, any depth. Hidden (.x) and underscore (_x)
  files/folders are skipped (Spark's .crc files and _SUCCESS markers).
- Recognises each file's dataset from its CONTENT (columns / JSON keys), never from its name:
    encounters (S4), facilities (S5), stock (S6), districts (S1/S2), weather (S3), boundaries (.geojson)
- Keeps every value exactly as received (all columns are strings) and adds 4 metadata columns:
    bronze_source_file, bronze_load_ts, bronze_batch_id, bronze_checksum (sha256 of the file)
- Idempotent: a ledger of file checksums means a file that was already loaded is skipped, even if
  it is renamed or moved. The batch id is a hash of the files' checksums, so a crash between the
  data write and the ledger write is repaired by simply re-running (same batch dir is overwritten).
- Bronze never deduplicates or fixes anything. Late records and resubmissions are appended as new
  batches; silver resolves them.

Output
    data/bronze/<dataset>/bronze_batch_id=<id>/part-*.parquet
    data/bronze/_ledger/files.jsonl      one line per ingested file
    data/bronze/_ledger/batches.jsonl    one line per batch (dataset, rows, files, load time)
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUTS = [ROOT / "data" / "incoming", ROOT / "data" / "reference", ROOT / "reference"]
DEFAULT_BRONZE = ROOT / "data" / "bronze"
DATA_EXTS = {".csv", ".jsonl", ".ndjson", ".json", ".geojson"}
TARGET_FILE_BYTES = 128 * 1024 * 1024  # aim for ~128 MB parquet files


# --------------------------------------------------------------------------------------
# File discovery, hashing, classification (pure Python, no Spark needed)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FileInfo:
    path: Path
    ext: str            # csv | jsonl | geojson
    dataset: str
    header: tuple       # exact CSV header (ordered); () for non-CSV
    checksum: str
    size: int


def _ext_of(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".gz"):
        name = name[:-3]
    return Path(name).suffix


def _opener(path: Path):
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return open(path, "r", encoding="utf-8-sig", newline="")


def discover(inputs) -> list[Path]:
    """All candidate data files under the input folders (sorted, de-duplicated)."""
    seen, out = set(), []
    for root in inputs:
        root = Path(root)
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel_parts = p.relative_to(root).parts
            if any(part.startswith((".", "_")) for part in rel_parts):
                continue
            if p.name.lower().endswith(".topo.json"):      # derived file, not a source
                continue
            if _ext_of(p) not in DATA_EXTS:
                continue
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                out.append(rp)
    return out


def sha256_file(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def classify_columns(cols) -> str | None:
    c = {str(x).strip().lower() for x in cols}
    if "encounter_id" in c:
        return "encounters"
    if "snapshot_date" in c and "facility_id" in c:
        return "stock"
    if "facility_id" in c and "facility_name" in c:
        return "facilities"
    if {"district_code", "date", "precip_mm"} <= c:
        return "weather"
    if {"district_code", "district_name", "population", "centroid_lat"} <= c:
        return "districts"
    return None


def classify_file(path: Path):
    """Return (dataset, ext, header) or None if the file is empty/unrecognised."""
    ext = _ext_of(path)
    if path.stat().st_size == 0:
        return None
    if ext == ".geojson":
        return "boundaries", "geojson", ()
    if ext == ".csv":
        with _opener(path) as f:
            first = f.readline()
        if not first.strip():
            return None
        header = tuple(x.strip() for x in next(csv.reader(io.StringIO(first))))
        ds = classify_columns(header)
        return (ds, "csv", header) if ds else None
    if ext in (".jsonl", ".ndjson", ".json"):
        with _opener(path) as f:
            for line in f:
                if line.strip():
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        return None
                    ds = classify_columns(obj.keys()) if isinstance(obj, dict) else None
                    return (ds, "jsonl", ()) if ds else None
        return None
    return None


def batch_id_for(checksums) -> str:
    return "b_" + hashlib.sha1("|".join(sorted(checksums)).encode()).hexdigest()[:12]


def key_of(path: Path) -> str:
    """Normalised path key; must match _norm_key_col() on the Spark side."""
    return path.resolve().as_posix().lstrip("/").lower()


def rel_source(path: Path) -> str:
    p = path.resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


# --------------------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------------------
def load_ledger(ledger_dir: Path) -> set[str]:
    f = ledger_dir / "files.jsonl"
    done = set()
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["checksum"])
    return done


def append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


# --------------------------------------------------------------------------------------
# Spark part (imports are lazy so the helpers above can be unit-tested without PySpark)
# --------------------------------------------------------------------------------------
def get_spark():
    """The project's shared session (memory and settings live in pipeline/spark_session.py)."""
    from pipeline.spark_session import get_spark as _get_spark
    return _get_spark(app_name="episcope-bronze")


def _norm_key_col(F, col):
    c = F.regexp_replace(col, r"^file:/+", "")
    c = F.regexp_replace(c, "%20", " ")
    c = F.regexp_replace(c, r"\\", "/")
    c = F.regexp_replace(c, r"^/+", "")
    return F.lower(c)


def _read_group(spark, ext, files):
    paths = [str(f.path) for f in files]
    if ext == "csv":
        return (spark.read.option("header", True).option("inferSchema", False)
                .option("multiLine", False).option("escape", '"').csv(paths))
    if ext == "jsonl":
        return spark.read.option("primitivesAsString", True).json(paths)
    if ext == "geojson":
        return spark.read.option("wholetext", True).text(paths).withColumnRenamed("value", "content")
    raise ValueError(ext)


def ingest_dataset(spark, dataset: str, files: list[FileInfo], bronze_root: Path, load_ts: str):
    from pyspark.sql import functions as F

    # 1. read (one read per distinct ordered CSV header, then union by name)
    groups: dict = {}
    for fi in files:
        groups.setdefault((fi.ext, fi.header), []).append(fi)
    dfs = [_read_group(spark, ext, grp) for (ext, _h), grp in groups.items()]
    df = dfs[0]
    for other in dfs[1:]:
        df = df.unionByName(other, allowMissingColumns=True)

    # 2. metadata: source file + checksum via a small broadcast lookup
    mapping = spark.createDataFrame(
        [(key_of(fi.path), rel_source(fi.path), fi.checksum) for fi in files],
        "bronze_key string, bronze_source_file string, bronze_checksum string")
    batch_id = batch_id_for(fi.checksum for fi in files)
    df = (df.withColumn("bronze_key", _norm_key_col(F, F.input_file_name()))
            .join(F.broadcast(mapping), "bronze_key", "left")
            .drop("bronze_key")
            .withColumn("bronze_load_ts", F.lit(load_ts).cast("timestamp")))

    # 3. write: one directory per batch; deterministic id => safe to overwrite on re-run
    total = sum(fi.size for fi in files)
    n_out = max(1, math.ceil(total / TARGET_FILE_BYTES))
    out_dir = bronze_root / dataset / f"bronze_batch_id={batch_id}"
    df.coalesce(n_out).write.mode("overwrite").parquet(str(out_dir))

    # 4. verify before the ledger is touched
    written = spark.read.parquet(str(out_dir))
    rows = written.count()
    orphan = written.filter(F.col("bronze_checksum").isNull()).count()
    if orphan:
        raise RuntimeError(f"{dataset}: {orphan} rows lost their source-file mapping; not recording batch")
    return batch_id, rows


def run(inputs, bronze_root: Path, dry_run: bool = False) -> dict:
    t0 = time.time()
    ledger_dir = bronze_root / "_ledger"
    done = load_ledger(ledger_dir)

    candidates = discover(inputs)
    new_files, seen_now = [], set()
    skipped_loaded = skipped_unknown = skipped_dupe = 0
    for p in candidates:
        cls = classify_file(p)
        if cls is None:
            skipped_unknown += 1
            print(f"  skip (empty or unrecognised): {rel_source(p)}")
            continue
        cs = sha256_file(p)
        if cs in done:
            skipped_loaded += 1
            continue
        if cs in seen_now:
            skipped_dupe += 1
            print(f"  skip (identical content already in this run): {rel_source(p)}")
            continue
        seen_now.add(cs)
        dataset, ext, header = cls
        new_files.append(FileInfo(p, ext, dataset, header, cs, p.stat().st_size))

    by_ds: dict[str, list[FileInfo]] = {}
    for fi in new_files:
        by_ds.setdefault(fi.dataset, []).append(fi)

    print(f"found {len(candidates)} files | new {len(new_files)} | already loaded {skipped_loaded} "
          f"| unrecognised {skipped_unknown} | duplicate content {skipped_dupe}")
    for ds, fis in sorted(by_ds.items()):
        print(f"  {ds}: {len(fis)} files, {sum(f.size for f in fis) / 1e6:,.1f} MB")

    summary = {"new_files": len(new_files), "already_loaded": skipped_loaded, "batches": []}
    if dry_run or not new_files:
        if not new_files:
            print("nothing to do (idempotent re-run)")
        return summary

    spark = get_spark()
    load_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for ds, fis in sorted(by_ds.items()):
        t1 = time.time()
        batch_id, rows = ingest_dataset(spark, ds, fis, bronze_root, load_ts)
        append_jsonl(ledger_dir / "files.jsonl", [
            {"checksum": f.checksum, "path": rel_source(f.path), "dataset": ds,
             "batch_id": batch_id, "bytes": f.size, "load_ts": load_ts} for f in fis])
        append_jsonl(ledger_dir / "batches.jsonl", [
            {"batch_id": batch_id, "dataset": ds, "rows": rows, "files": len(fis),
             "bytes": sum(f.size for f in fis), "load_ts": load_ts}])
        summary["batches"].append({"dataset": ds, "batch_id": batch_id, "rows": rows})
        print(f"  [{time.time() - t1:6.1f}s] {ds}: {rows:,} rows -> batch {batch_id}")
    print(f"bronze done in {time.time() - t0:.1f}s")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", action="append", help="input folder (repeatable); default: data/incoming, data/reference, reference")
    ap.add_argument("--bronze-root", default=str(DEFAULT_BRONZE))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    inputs = [Path(x) for x in a.input] if a.input else DEFAULT_INPUTS
    run(inputs, Path(a.bronze_root), a.dry_run)


if __name__ == "__main__":
    sys.exit(main())

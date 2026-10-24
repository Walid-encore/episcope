"""Tiny loader shared by the AN-6 scripts: reads <dir>/<name>.parquet, a parquet folder <dir>/<name>, or <name>.csv."""
from pathlib import Path

import pandas as pd


def load_table(folder, name):
    folder = Path(folder)
    for cand in (folder / f"{name}.parquet", folder / name):
        if cand.exists():
            return pd.read_parquet(cand)
    csv = folder / f"{name}.csv"
    if csv.exists():
        return pd.read_csv(csv, dtype={"district_code": str})
    raise FileNotFoundError(f"{name} not found in {folder}")

"""EpiScope synthetic data generator (S4 encounters, S5 facilities, S6 stock) with ground truth.

Run from the repo root:
  python -m generator.generate --sf 0.05                        # small, 1.3M encounters
  python -m generator.generate --sf 1 --driver-memory 6g        # 25M encounters (benchmark)

A hidden-drop style increment (late records included) is a slice of one deterministic run:
  python -m generator.generate --sf 0.05 --end 2026-01-14 --report-from 2026-01-01 \
         --report-to 2026-01-14 --out-dir data_drop

Outputs under --out-dir (default data/):
  incoming/encounters/batch_week=YYYY-MM-DD/part-*.csv   S4, one folder per report week
  incoming/stock/batch_week=YYYY-MM-DD/stock_*.jsonl     S6
  reference/facilities.csv                                S5
  ground_truth/outbreaks.csv, outbreak_days.csv           EVALUATION ONLY
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from generator import model as M

FAC_COLS = ["facility_id", "facility_name", "facility_type", "district_code", "latitude",
            "longitude", "beds", "icu_beds"]
STOCK_COLS = ["iv_saline_units", "ns1_kits", "ors_sachets", "antimalarial_courses", "occupied_beds"]


def stage(name, t0):
    print(f"[{time.perf_counter() - t0:7.1f}s] {name}", flush=True)


def write_reference_and_truth(model, out_dir, ref_dir):
    ref_dir.mkdir(parents=True, exist_ok=True)
    model.fac[FAC_COLS].to_csv(ref_dir / "facilities.csv", index=False, encoding="utf-8")
    gt = out_dir / "ground_truth"
    gt.mkdir(parents=True, exist_ok=True)
    model.outbreaks.to_csv(gt / "outbreaks.csv", index=False, encoding="utf-8")
    model.outbreak_days.to_csv(gt / "outbreak_days.csv", index=False, encoding="utf-8")
    (gt / "README.txt").write_text(
        "EVALUATION ONLY. Detection and forecasting code must never read this folder.\n", encoding="utf-8")


def write_stock(model, stock, out_dir, seed, report_from, report_to):
    """S6: JSON Lines, one folder per ISO week, with a few realistic defects."""
    dates, fac_ids = model.dates, model.fac["facility_id"].to_numpy()
    F = len(fac_ids)
    week = dates - pd.to_timedelta(dates.weekday, unit="D")
    n_rows = 0
    for wk in week.unique():
        sel = np.nonzero(week == wk)[0]
        sel = sel[[(report_from is None or dates[i].date() >= report_from)
                   and (report_to is None or dates[i].date() <= report_to) for i in sel]]
        if len(sel) == 0:
            continue
        t_i, f_i = np.repeat(sel, F), np.tile(np.arange(F), len(sel))
        df = pd.DataFrame({"snapshot_date": dates[t_i].strftime("%Y-%m-%d"), "facility_id": fac_ids[f_i],
                           **{c: stock[c][t_i, f_i] for c in STOCK_COLS}})
        r = np.random.default_rng([seed, 4242, int(wk.strftime("%Y%m%d"))])
        df = df[r.random(len(df)) >= 0.003].reset_index(drop=True)                 # ~0.3% missing snapshots
        neg = r.random(len(df)) < 0.002                                             # ~0.2% invalid negatives
        col = r.integers(0, len(STOCK_COLS), len(df))
        for k, c in enumerate(STOCK_COLS):
            df.loc[neg & (col == k), c] = -1
        orph = r.random(len(df)) < 0.005                                            # ~0.5% orphan facility ids
        df.loc[orph, "facility_id"] = ["FX%05d" % x for x in r.integers(10000, 100000, orph.sum())]
        df = pd.concat([df, df[r.random(len(df)) < 0.01]], ignore_index=True)       # ~1% duplicate lines
        folder = out_dir / "incoming" / "stock" / f"batch_week={wk.strftime('%Y-%m-%d')}"
        folder.mkdir(parents=True, exist_ok=True)
        df.to_json(folder / f"stock_{wk.strftime('%Y-%m-%d')}.jsonl", orient="records", lines=True)
        n_rows += len(df)
    return n_rows


def write_work_tables(model, work):
    work.mkdir(parents=True, exist_ok=True)
    codes = model.dist["district_code"].to_numpy()
    t_i, d_i, g_i = np.nonzero(model.counts)
    cells = pd.DataFrame({
        "district_code": codes[d_i],
        "onset_date": model.dates.strftime("%Y-%m-%d").to_numpy()[t_i],
        "disease": np.array(M.GROUPS)[g_i],
        "n": model.counts[t_i, d_i, g_i].astype("int32")})
    cells.to_parquet(work / "cells.parquet", index=False)
    arr = []
    for code, g in model.fac.groupby("district_code", sort=True):
        cum = np.cumsum(g["weight"].to_numpy())
        cum[-1] = 1.0
        arr.append({"district_code": code, "fac_ids": g["facility_id"].tolist(), "cum_w": cum.tolist()})
    pd.DataFrame(arr).to_parquet(work / "fac_arrays.parquet", index=False)
    return len(cells), int(model.counts.sum())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sf", type=float, default=0.05, help="scale factor; 1 = ~25M encounters")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2021, 1, 1))
    ap.add_argument("--end", type=dt.date.fromisoformat, default=dt.date(2025, 12, 31))
    ap.add_argument("--report-from", type=dt.date.fromisoformat, default=None)
    ap.add_argument("--report-to", type=dt.date.fromisoformat, default=None)
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--ref-dir", default=None, help="default: <out-dir>/reference")
    ap.add_argument("--facilities", type=int, default=700)
    ap.add_argument("--driver-memory", default="3g")
    ap.add_argument("--shuffle-partitions", type=int, default=64)
    ap.add_argument("--verify", action=argparse.BooleanOptionalAction, default=None,
                    help="read the output back and print defect rates (default: on when sf <= 0.1)")
    ap.add_argument("--keep-work", action="store_true")
    a = ap.parse_args(argv)

    out_dir = Path(a.out_dir)
    ref_dir = Path(a.ref_dir) if a.ref_dir else out_dir / "reference"
    if not (ref_dir / "districts.csv").exists() or not (ref_dir / "weather.csv").exists():
        sys.exit(f"Missing {ref_dir}/districts.csv or weather.csv. Run scripts S1-S3 (or git pull) first.")
    cfg = M.Config(seed=a.seed, sf=a.sf, start=a.start, end=a.end, n_facilities=a.facilities)
    t0 = time.perf_counter()

    model = M.build(cfg, ref_dir)
    stage(f"model built: {len(model.fac)} facilities, "
          f"{int((model.outbreaks.kind != 'reporting_artefact').sum())} outbreaks, "
          f"{int((model.outbreaks.kind == 'reporting_artefact').sum())} reporting artefacts", t0)

    if a.report_from is None and a.report_to is None:        # full run: start from a clean slate
        for sub in ("encounters", "stock"):
            shutil.rmtree(out_dir / "incoming" / sub, ignore_errors=True)
    write_reference_and_truth(model, out_dir, ref_dir)
    stage(f"wrote {ref_dir / 'facilities.csv'} and ground_truth/", t0)

    n_stock = write_stock(model, M.simulate_stock(model), out_dir, cfg.seed, a.report_from, a.report_to)
    stage(f"wrote stock snapshots: {n_stock:,} JSON lines", t0)

    work = out_dir / "_work"
    n_cells, n_rows = write_work_tables(model, work)
    stage(f"work tables: {n_cells:,} day/district/disease cells -> {n_rows:,} base encounters", t0)

    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    from generator import spark_rows as S                    # imported late: pyspark only needed here
    spark = S.get_spark(a.driver_memory, a.shuffle_partitions, work / "spark_tmp")
    enc = S.build_encounters(spark, work, cfg.seed, cfg.end.isoformat(), model.artefacts,
                             a.report_from.isoformat() if a.report_from else None,
                             a.report_to.isoformat() if a.report_to else None)
    enc_dir = out_dir / "incoming" / "encounters"
    S.write_encounters(enc, enc_dir)
    stage(f"wrote encounters to {enc_dir}", t0)

    verify = a.verify if a.verify is not None else a.sf <= 0.1
    if verify:
        S.verify(spark, enc_dir, ref_dir / "facilities.csv")
        stage("verified", t0)
    spark.stop()
    if not a.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    stage("done", t0)


if __name__ == "__main__":
    main()

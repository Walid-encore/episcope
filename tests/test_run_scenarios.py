"""Peak-scenario labels and the atomic swap of allocation_peak (analytics/optimise/run_scenarios.py)."""
import json
from datetime import date

import pytest

from analytics.optimise import run_scenarios as rs

META = {"origin": 202527, "as_of": "2025-07-06", "model": "nb_lagged_weather"}


def test_peak_metadata_labels():
    m = rs.peak_metadata(META)
    assert m["as_of_week"] == "2025-W27"
    assert m["peak_week_start"] == "2025-06-30"            # the Monday of the origin week
    assert m["horizon_start"] == "2025-07-07"              # first forecast week
    assert m["origin_epi_week"] == 202527 and m["retrospective"] is True
    assert date.fromisoformat(m["peak_week_start"]).weekday() == 0


def test_annotate_keeps_as_of_and_adds_labels(tmp_path):
    (tmp_path / "recommendation.json").write_text(json.dumps({"as_of": "2025-07-06", "min_cover": 0.25}))
    rec = rs.annotate(tmp_path, META)
    on_disk = json.loads((tmp_path / "recommendation.json").read_text())
    assert on_disk == rec
    assert rec["as_of"] == "2025-07-06" and rec["min_cover"] == 0.25      # existing fields untouched
    assert rec["as_of_week"] == "2025-W27" and rec["peak_week_start"] == "2025-06-30"


def test_labels_follow_the_data_not_a_constant():
    m = rs.peak_metadata({"origin": 202431, "as_of": "2024-08-04"})
    assert m["as_of_week"] == "2024-W31" and m["peak_week_start"] == "2024-07-29"


def test_failed_build_leaves_no_peak_folder(tmp_path, monkeypatch):
    (tmp_path / "allocation_peak").mkdir()
    (tmp_path / "allocation_peak" / "recommendation.json").write_text("{}")   # an older, complete run

    def boom(*args):
        raise RuntimeError("allocation failed")
    monkeypatch.setattr(rs, "sh", boom)
    with pytest.raises(RuntimeError):
        rs.build_served_peak(tmp_path, ["x"], META)
    assert (tmp_path / "allocation_peak").exists()          # the old folder is only replaced after success


def test_successful_build_swaps_in_a_labelled_folder(tmp_path, monkeypatch):
    def fake_sh(*args):
        out = args[args.index("--out") + 1]
        from pathlib import Path
        Path(out).mkdir(parents=True)
        for f in ("allocation_grid.csv", "allocation_plan.csv", "efficiency_equity_curve.csv"):
            (Path(out) / f).write_text("x")
        (Path(out) / "recommendation.json").write_text(json.dumps({"as_of": "2025-07-06"}))
    monkeypatch.setattr(rs, "sh", fake_sh)
    rs.build_served_peak(tmp_path, ["x"], META)
    assert not (tmp_path / "allocation_peak.tmp").exists()
    rec = json.loads((tmp_path / "allocation_peak" / "recommendation.json").read_text())
    assert rec["as_of_week"] == "2025-W27"
    assert sorted(p.name for p in (tmp_path / "allocation_peak").iterdir()) == [
        "allocation_grid.csv", "allocation_plan.csv", "efficiency_equity_curve.csv", "recommendation.json"]

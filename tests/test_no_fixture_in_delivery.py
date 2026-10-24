"""The code run_all executes must never read a test fixture or sample file (companion to test_truth_isolation).

Static check: no non-docstring string constant in the delivery modules may contain "fixture". Evaluation and
demonstration scripts that are not part of run_all are exempt."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIRS = ["analytics/detection", "analytics/forecast", "analytics/optimise", "analytics/calendar"]
EXEMPT = {"detection_holdout.py", "evaluate.py", "run_detection.py", "artefact_demo.py", "plot_tradeoff.py"}


def _files():
    out = [p for d in DIRS for p in sorted((ROOT / d).glob("*.py"))
           if p.name not in EXEMPT and "stress" not in p.name]
    return out + [ROOT / "analytics" / "run_all.py"]


def _docstrings(tree):
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) and isinstance(b[0].value.value, str):
                ids.add(id(b[0].value))
    return ids


def fixture_refs(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    skip = _docstrings(tree)
    return [(n.lineno, n.value[:60]) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip
            and "fixture" in n.value.lower()]


def test_scan_covers_the_delivery_modules():
    names = {p.name for p in _files()}
    assert {"make_alerts.py", "forecast.py", "run_demand.py", "run_allocation.py", "preparedness.py", "run_all.py"} <= names


@pytest.mark.parametrize("path", _files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_fixture_reference(path):
    assert not fixture_refs(path), f"{path.name} refers to a test fixture: {fixture_refs(path)}"

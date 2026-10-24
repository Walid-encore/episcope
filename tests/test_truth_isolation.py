"""Detection, forecast, optimiser and calendar code must never read the ground-truth
outbreak files (gold_contract.md, General rules). Only evaluation code may.

The check is static: it parses each source file and fails on any non-docstring string
constant, identifier or import containing "truth".
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_DIRS = ["analytics/detection", "analytics/forecast", "analytics/optimise",
                  "analytics/calendar"]
# Evaluation code is allowed to read the truth files.
EVALUATION_FILES = {"evaluate.py", "run_detection.py", "artefact_demo.py"}


def _protected_files():
    files = []
    for d in PROTECTED_DIRS:
        files += [p for p in sorted((ROOT / d).glob("*.py")) if p.name not in EVALUATION_FILES]
    return files


def _docstring_node_ids(tree):
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = n.body
            if (b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant)
                    and isinstance(b[0].value.value, str)):
                ids.add(id(b[0].value))
    return ids


def _violations(path):
    # "utf-8-sig" strips a leading byte-order mark (added by Notepad / PowerShell
    # Out-File on Windows) so that ast.parse does not fail on U+FEFF. Without a BOM
    # it behaves exactly like "utf-8".
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    skip = _docstring_node_ids(tree)
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip:
            if "truth" in n.value.lower():
                bad.append((n.lineno, f"string {n.value[:50]!r}"))
        elif isinstance(n, ast.Name) and "truth" in n.id.lower():
            bad.append((n.lineno, f"name {n.id}"))
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in n.names] + [getattr(n, "module", None) or ""]
            if any("truth" in x.lower() for x in names):
                bad.append((n.lineno, "import " + ", ".join(names)))
    return bad


def test_scan_covers_the_expected_modules():
    names = {p.name for p in _protected_files()}
    assert {"ears_c2.py", "cusum.py", "common.py", "forecast.py", "demand.py"} <= names


@pytest.mark.parametrize("path", _protected_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_truth_access(path):
    assert not _violations(path), f"{path.name} refers to ground truth: {_violations(path)}"

"""Appends a horizon-guard fixture + guard test to tests/test_demand.py.
Run once from C:\\episcope:  python fix_tests.py
Safe to re-run: it refuses if already applied."""
from pathlib import Path

p = Path("tests/test_demand.py")
src = p.read_text(encoding="utf-8")
MARK = "_relax_horizon_guard"
if MARK in src:
    raise SystemExit("already applied, nothing to do")
for need in ("compute_demand", "_fc", "_rates"):
    if need not in src:
        raise SystemExit(f"tests/test_demand.py has no '{need}'; send me the file")

block = '''

# --- AN-6 live-horizon guard: the by-hand fixtures use 1-2 horizons on purpose ---
import pytest
from analytics.optimise import demand as _demand_mod


@pytest.fixture(autouse=True)
def _relax_horizon_guard(request, monkeypatch):
    if request.node.name == "test_guard_rejects_short_horizons":
        return
    real = _demand_mod._require_full_horizons
    monkeypatch.setattr(_demand_mod, "_require_full_horizons",
                        lambda f, max_horizon=4: real(f, 1))


def test_guard_rejects_short_horizons():
    fc = _fc([dict(district_code="01", disease="dengue", horizon_weeks=h, pred_mean=10.0, q90=12.0)
              for h in (1, 2)])
    with pytest.raises(ValueError, match="lack all 4 live horizons"):
        compute_demand(fc, _rates())
'''
nl = "\r\n" if "\r\n" in src else "\n"
p.write_text(src.rstrip() + block.replace("\n", nl) + nl, encoding="utf-8", newline="")
print("patched tests/test_demand.py")

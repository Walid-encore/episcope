"""Every file path named in the README requirement-traceability table must exist (H1 asks for ID -> file -> section).
Paths under data/ are generated outputs and are not checked; command snippets (with spaces) are skipped."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = (".py", ".md", ".csv", ".png", ".json", ".yml", ".yaml", ".txt")


def missing_paths(readme_text, root):
    missing = []
    for line in readme_text.splitlines():
        m = re.match(r"\|\s*([A-Z]+-\d+)\b", line)
        if not m:
            continue
        for tok in re.findall(r"`([^`]+)`", line):
            if " " in tok or "*" in tok or tok.startswith("data/"):
                continue
            if "/" in tok or tok.endswith(EXT):
                if not (root / tok).exists():
                    missing.append((m.group(1), tok))
    return missing


def test_traceability_paths_exist():
    text = (ROOT / "README.md").read_text(encoding="utf-8-sig")
    assert "| AN-1" in text, "README has no traceability rows"
    assert missing_paths(text, ROOT) == []

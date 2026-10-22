"""Privacy helpers (PRV-1): the hashing salt lives in .env or the environment, never in the repo."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_KEY = "EPISCOPE_SALT"
MIN_LEN = 16
PLACEHOLDER = "change-me"


def _usable(value) -> bool:
    return bool(value) and len(value) >= MIN_LEN and not value.lower().startswith(PLACEHOLDER)


def load_salt(env_path=None) -> str:
    """Return the salt. Order: environment variable, then .env.

    If neither holds a real value (missing, too short, or still the .env.example placeholder),
    a random salt is generated and written to .env, so a clean clone works without a manual step
    and never ends up with a publicly known salt. The value is never printed or logged.
    """
    v = os.environ.get(ENV_KEY)
    if _usable(v):
        return v
    path = Path(env_path) if env_path else ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for line in lines:
        s = line.strip()
        if s.startswith(ENV_KEY + "="):
            val = s.split("=", 1)[1].strip().strip('"').strip("'")
            if _usable(val):
                return val
    salt = secrets.token_hex(32)
    out, replaced = [], False
    for line in lines:
        if line.strip().startswith(ENV_KEY + "="):
            if not replaced:
                out.append(f"{ENV_KEY}={salt}")
                replaced = True
            continue
        out.append(line)
    if not replaced:
        out.append(f"{ENV_KEY}={salt}")
    path.write_text("\n".join(out) + "\n", encoding="ascii")
    return salt

"""Shared helpers for S1-S3: district name matching, divisions, district_code assignment.

district_code is a 2-digit string "01".."64", assigned by sorting the 64 canonical
district names alphabetically. The order is arbitrary but deterministic. After S2 writes
data/reference/districts.csv, that table is the only source of truth for codes.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_BOUNDS = ROOT / "data" / "raw" / "boundaries"
REFERENCE = ROOT / "data" / "reference"

# Canonical district name -> division (8 divisions, 64 districts).
DIVISIONS = {
    "Barishal": ["Barguna", "Barishal", "Bhola", "Jhalokati", "Patuakhali", "Pirojpur"],
    "Chattogram": ["Bandarban", "Brahmanbaria", "Chandpur", "Chattogram", "Cumilla",
                   "Cox's Bazar", "Feni", "Khagrachhari", "Lakshmipur", "Noakhali", "Rangamati"],
    "Dhaka": ["Dhaka", "Faridpur", "Gazipur", "Gopalganj", "Kishoreganj", "Madaripur",
              "Manikganj", "Munshiganj", "Narayanganj", "Narsingdi", "Rajbari",
              "Shariatpur", "Tangail"],
    "Khulna": ["Bagerhat", "Chuadanga", "Jashore", "Jhenaidah", "Khulna", "Kushtia",
               "Magura", "Meherpur", "Narail", "Satkhira"],
    "Mymensingh": ["Jamalpur", "Mymensingh", "Netrokona", "Sherpur"],
    "Rajshahi": ["Bogura", "Chapainawabganj", "Joypurhat", "Naogaon", "Natore", "Pabna",
                 "Rajshahi", "Sirajganj"],
    "Rangpur": ["Dinajpur", "Gaibandha", "Kurigram", "Lalmonirhat", "Nilphamari",
                "Panchagarh", "Rangpur", "Thakurgaon"],
    "Sylhet": ["Habiganj", "Moulvibazar", "Sunamganj", "Sylhet"],
}


def slug(name: str) -> str:
    s = re.sub(r"\s+district$", "", str(name).strip(), flags=re.I).lower()
    s = s.replace("'", "").replace("\u2019", "")
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


_CANON = {slug(n): (n, div) for div, names in DIVISIONS.items() for n in names}

# Alternative spellings seen in different sources -> canonical slug.
_ALIASES = {
    "barisal": "barishal", "bogra": "bogura", "comilla": "cumilla", "chittagong": "chattogram",
    "jessore": "jashore", "jhalakathi": "jhalokati", "jhalokathi": "jhalokati",
    "jhalakati": "jhalokati", "khagrachari": "khagrachhari", "khagrachhari": "khagrachhari",
    "maulvibazar": "moulvibazar", "maulavibazar": "moulvibazar", "nawabganj": "chapainawabganj",
    "chapai_nawabganj": "chapainawabganj", "chapai_nababganj": "chapainawabganj",
    "netrakona": "netrokona", "laxmipur": "lakshmipur", "hobiganj": "habiganj",
    "cox_s_bazar": "coxs_bazar", "coxsbazar": "coxs_bazar", "bagherhat": "bagerhat",
    "narsinghdi": "narsingdi", "munshigonj": "munshiganj", "brahamanbaria": "brahmanbaria",
    "jaipurhat": "joypurhat", "jhenidah": "jhenaidah", "kishorganj": "kishoreganj",
    "maymansingh": "mymensingh", "mymensingh": "mymensingh", "sirajgonj": "sirajganj",
    "netrokona": "netrokona", "naogoan": "naogaon", "rajbari": "rajbari",
}


def resolve(name: str):
    """Return (canonical_name, division) for a source district name, or None."""
    key = slug(name)
    key = _ALIASES.get(key, key)
    return _CANON.get(key)


def code_table() -> dict:
    """canonical name -> district_code ('01'..'64'), alphabetical."""
    names = sorted(n for n, _ in _CANON.values())
    return {n: f"{i:02d}" for i, n in enumerate(names, 1)}

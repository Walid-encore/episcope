"""S3: cached daily weather per district from the Open-Meteo historical (ERA5) API.

Writes the reference table exactly as schema.md defines it:

  data/reference/weather.csv
  district_code, date, precip_mm, temp_mean_c, humidity_pct

One request per district for the whole date range. Daily temperature and precipitation
come straight from the API; daily humidity is the mean of the 24 hourly relative-humidity
values (Asia/Dhaka local days). Each response is cached in data/raw/weather/cache/, so the
script is resumable: if Open-Meteo's hourly or daily limit is hit it stops cleanly, and a
rerun fetches only the missing districts.

Run from the repo root:
  python scripts/s3_download_weather.py
  python scripts/s3_download_weather.py --start 2020-01-01 --end 2025-12-31
  python scripts/s3_download_weather.py --merge-only

Needs S1 first (districts_base.csv). Source: Open-Meteo (CC BY 4.0), ERA5 reanalysis.
The default start is 2020-01-01, one year before the first onset_date, for lagged-weather features.
"""
import argparse
import csv
import json
import sys
import time
from collections import defaultdict

import requests

from geo_common import RAW_BOUNDS, REFERENCE, ROOT

OUT = ROOT / "data" / "raw" / "weather"
CACHE = OUT / "cache"
URL = "https://archive-api.open-meteo.com/v1/archive"
PAUSE_SECONDS = 2.0


class LimitReached(Exception):
    pass


def fetch(lat, lon, start, end):
    params = {
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
        "daily": "temperature_2m_mean,precipitation_sum",
        "hourly": "relative_humidity_2m",
        "timezone": "Asia/Dhaka",
    }
    for attempt in range(1, 6):
        try:
            r = requests.get(URL, params=params, timeout=120)
        except requests.RequestException as e:
            print(f"    network error ({e}); retry {attempt}/5")
            time.sleep(10 * attempt)
            continue
        if r.status_code == 200:
            return r.json()
        reason = ""
        try:
            reason = r.json().get("reason", "")
        except ValueError:
            pass
        if r.status_code == 429:
            low = reason.lower()
            if "hourly" in low or "daily" in low:
                raise LimitReached(reason or "hourly/daily limit reached")
            print(f"    minute limit; waiting 65 s (retry {attempt}/5)")
            time.sleep(65)
            continue
        if r.status_code >= 500:
            print(f"    server error {r.status_code}; retry {attempt}/5")
            time.sleep(10 * attempt)
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {reason or r.text[:200]}")
    raise RuntimeError("gave up after 5 attempts")


def load_districts():
    path = RAW_BOUNDS / "districts_base.csv"
    if not path.exists():
        sys.exit("Run scripts/s1_download_boundaries.py first (districts_base.csv missing).")
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def download(start, end):
    CACHE.mkdir(parents=True, exist_ok=True)
    districts = load_districts()
    todo = []
    for d in districts:
        p = CACHE / f"{d['district_code']}.json"
        if p.exists():
            try:
                if json.loads(p.read_text(encoding="utf-8")).get("_range") == [start, end]:
                    continue
            except ValueError:
                pass
        todo.append(d)
    print(f"{len(districts) - len(todo)} cached, {len(todo)} to fetch ({start} to {end})")
    try:
        for i, d in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {d['district_code']} {d['district_name']}")
            data = fetch(float(d["centroid_lat"]), float(d["centroid_lon"]), start, end)
            data["_range"] = [start, end]
            tmp = CACHE / f"{d['district_code']}.json.part"
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(CACHE / f"{d['district_code']}.json")
            time.sleep(PAUSE_SECONDS)
    except LimitReached as e:
        print(f"\nStopped: Open-Meteo limit reached ({e}).")
        print("Progress is saved. Rerun in an hour (or tomorrow if it was the daily limit).")


def mean_or_blank(vals, nd=1):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), nd) if vals else ""


def merge():
    files = sorted(CACHE.glob("*.json"))
    if not files:
        sys.exit("No cached weather files to merge.")
    REFERENCE.mkdir(parents=True, exist_ok=True)
    out = REFERENCE / "weather.csv"
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["district_code", "date", "precip_mm", "temp_mean_c", "humidity_pct"])
        for p in files:
            j = json.loads(p.read_text(encoding="utf-8"))
            daily, hourly = j["daily"], j["hourly"]
            hum = defaultdict(list)
            for t, v in zip(hourly["time"], hourly["relative_humidity_2m"]):
                hum[t[:10]].append(v)
            for day, temp, rain in zip(daily["time"], daily["temperature_2m_mean"],
                                       daily["precipitation_sum"]):
                w.writerow([p.stem, day,
                            "" if rain is None else rain,
                            "" if temp is None else temp,
                            mean_or_blank(hum.get(day, []))])
                n += 1
    print(f"Wrote {out}: {n:,} rows from {len(files)} districts")
    if len(files) < 64:
        print(f"NOTE: only {len(files)}/64 districts cached. Rerun to finish the rest.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--merge-only", action="store_true")
    a = ap.parse_args()
    if not a.merge_only:
        download(a.start, a.end)
    merge()


if __name__ == "__main__":
    main()

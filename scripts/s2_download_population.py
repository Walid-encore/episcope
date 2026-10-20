"""S2: district population from the WorldPop 1 km population-count raster.

Downloads the Bangladesh raster once, sums the pixels inside each district polygon, and
writes the reference table exactly as schema.md defines it:

  data/reference/districts.csv
  district_code, district_name, division, population, centroid_lat, centroid_lon

Needs S1 first.  Run from the repo root:  python scripts/s2_download_population.py

If the download URL returns 404, fetch the Bangladesh 2020 1 km raster from
https://www.worldpop.org, save it as data/raw/population/bgd_ppp_2020_1km.tif and rerun
(the script skips the download when the file exists).
"""
import csv
import sys

import geopandas as gpd
import requests
from rasterstats import zonal_stats

from geo_common import RAW_BOUNDS, REFERENCE, ROOT

POP_DIR = ROOT / "data" / "raw" / "population"
TIF = POP_DIR / "bgd_ppp_2020_1km.tif"
URL = ("https://data.worldpop.org/GIS/Population/Global_2000_2020_1km/"
       "2020/BGD/bgd_ppp_2020_1km_Aggregated.tif")


def download(url, dest):
    if dest.exists() and dest.stat().st_size > 0:
        print(f"Raster already present: {dest.name}")
        return
    print(f"Downloading {url}")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        tmp.replace(dest)
    print(f"  saved {dest.stat().st_size / 1e6:.1f} MB")


def main():
    coded = RAW_BOUNDS / "bgd_adm2_coded.geojson"
    base_csv = RAW_BOUNDS / "districts_base.csv"
    if not coded.exists() or not base_csv.exists():
        print("Run scripts/s1_download_boundaries.py first.")
        return 1
    POP_DIR.mkdir(parents=True, exist_ok=True)
    REFERENCE.mkdir(parents=True, exist_ok=True)
    download(URL, TIF)

    gdf = gpd.read_file(coded)
    print("Computing zonal sums (about a minute) ...")
    stats = zonal_stats(gdf, str(TIF), stats=["sum"], nodata=-99999)
    pop = {c: int(round(s["sum"] or 0)) for c, s in zip(gdf["district_code"], stats)}

    with open(base_csv, encoding="utf-8") as f:
        base = {r["district_code"]: r for r in csv.DictReader(f)}

    out = REFERENCE / "districts.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["district_code", "district_name", "division", "population",
                    "centroid_lat", "centroid_lon"])
        for code in sorted(base):
            b = base[code]
            w.writerow([code, b["district_name"], b["division"], pop[code],
                        b["centroid_lat"], b["centroid_lon"]])

    total = sum(pop.values())
    print(f"Wrote {out}: {len(pop)} districts, total population {total:,}")
    if not (140e6 < total < 190e6):
        print("WARNING: total is far from Bangladesh's ~170 million. Check the raster and nodata.")
    if any(v == 0 for v in pop.values()):
        print("WARNING: some districts have zero population:", [c for c, v in pop.items() if v == 0])
    print("Note: WorldPop is a modelled 2020 estimate, not the 2022 census. State this in the report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

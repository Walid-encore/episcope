"""S1: Bangladesh district boundaries (geoBoundaries gbOpen ADM2) with our district_code.

Outputs (data/raw/boundaries/):
  bgd_adm2_coded.geojson             full resolution (used by S2 zonal statistics)
  bgd_adm2_simplified_coded.geojson  light file for the D3 choropleth
  districts_base.csv                 district_code, district_name, division, centroid_lat, centroid_lon

Every feature carries district_code, district_name and division, so the dashboard can
join on district_code. S2 adds population and writes data/reference/districts.csv.

Run from the repo root:  python scripts/s1_download_boundaries.py
Cite www.geoboundaries.org (CC BY 4.0) in the report.
"""
import sys

import geopandas as gpd
import requests

from geo_common import RAW_BOUNDS, code_table, resolve

API = "https://www.geoboundaries.org/api/current/gbOpen/BGD/ADM2/"


def download(url, dest):
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  skip (exists): {dest.name}")
        return
    print(f"  downloading {dest.name} ...")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)


def add_codes(gdf):
    name_col = "shapeName" if "shapeName" in gdf.columns else gdf.columns[0]
    resolved = gdf[name_col].map(resolve)
    bad = gdf.loc[resolved.isna(), name_col].tolist()
    if bad:
        sys.exit("These source names did not match our 64 districts. Add them to _ALIASES in "
                 f"scripts/geo_common.py and rerun:\n  {bad}")
    codes = code_table()
    out = gdf[["geometry"]].copy()
    out["district_name"] = [r[0] for r in resolved]
    out["division"] = [r[1] for r in resolved]
    out["district_code"] = out["district_name"].map(codes)
    if out["district_code"].duplicated().any() or len(out) != 64:
        dup = out[out["district_code"].duplicated(keep=False)]["district_name"].tolist()
        sys.exit(f"Expected 64 unique districts, got {len(out)} rows (duplicates: {dup}).")
    return out.sort_values("district_code")[["district_code", "district_name", "division", "geometry"]]


def main():
    RAW_BOUNDS.mkdir(parents=True, exist_ok=True)
    print("Querying geoBoundaries API ...")
    r = requests.get(API, timeout=60)
    r.raise_for_status()
    meta = r.json()
    if isinstance(meta, list):
        meta = meta[0]
    print(f"  {meta.get('boundaryName')} {meta.get('boundaryType')}, build date {meta.get('buildDate')}")

    src_full = RAW_BOUNDS / "_src_full.geojson"
    src_simple = RAW_BOUNDS / "_src_simplified.geojson"
    download(meta["gjDownloadURL"], src_full)
    if meta.get("simplifiedGeometryGeoJSON"):
        download(meta["simplifiedGeometryGeoJSON"], src_simple)

    full = add_codes(gpd.read_file(src_full).to_crs(4326))
    full.to_file(RAW_BOUNDS / "bgd_adm2_coded.geojson", driver="GeoJSON")

    if src_simple.exists():
        simple = add_codes(gpd.read_file(src_simple).to_crs(4326))
        simple.to_file(RAW_BOUNDS / "bgd_adm2_simplified_coded.geojson", driver="GeoJSON")

    # Representative point lies inside the polygon (a true centroid can fall outside it).
    pts = full.geometry.representative_point()
    base = full.drop(columns="geometry").copy()
    base["centroid_lat"] = pts.y.round(4).values
    base["centroid_lon"] = pts.x.round(4).values
    base.to_csv(RAW_BOUNDS / "districts_base.csv", index=False, encoding="utf-8")

    print(f"\nOK: 64 districts coded 01..64. Wrote files to {RAW_BOUNDS}")
    print(base.head(5).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

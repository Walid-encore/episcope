// The ONLY module that knows file names. Default = hand-made samples.
// Real exports: open http://localhost:8000/dashboard/?data=real  (reads /serving/data/)
const params = new URLSearchParams(location.search);
export const BASE = params.get("data") === "real" ? "/serving/data/" : "/serving/sample/";
const GEO = "/serving/geo/bgd_adm2_simplified_coded.geojson";
const FILES = ["meta", "freshness", "cases_weekly", "forecast", "alerts",
               "seasonality", "allocation", "stock", "dq_report"];
const REQUIRED = new Set(["meta", "freshness", "cases_weekly"]);

export async function loadAll() {
  const [geo, ...loaded] = await Promise.all([
    d3.json(GEO),
    ...FILES.map(f => d3.json(BASE + f + ".json").catch(() => null)),
  ]);
  const data = { geo: rewind(geo) };
  FILES.forEach((f, i) => {
    if (loaded[i] === null && REQUIRED.has(f)) throw new Error(`Missing ${BASE}${f}.json`);
    data[f] = loaded[i];
  });

  // district_code is always a string ("01"), never a number.
  data.districtInfo = new Map(data.meta.districts.map(d => [d.district_code, d]));
  data.cases = new Map(data.cases_weekly.rows.map(r => [`${r.disease}|${r.week_start}|${r.district_code}`, r]));
  data.weeks = [...new Set(data.cases_weekly.rows.map(r => r.week_start))].sort();
  return data;
}

function rewind(geo) {
  const flip = ring => ring.slice().reverse();
  for (const f of geo.features) {
    if (d3.geoArea(f) > 2 * Math.PI) {      // polygon is "inside out" for D3
      const g = f.geometry;
      if (g.type === "Polygon") g.coordinates = g.coordinates.map(flip);
      else if (g.type === "MultiPolygon") g.coordinates = g.coordinates.map(p => p.map(flip));
    }
  }
  return geo;
}

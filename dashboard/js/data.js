// The ONLY module that knows file names. Default = real exports in /serving/data/.
// Hand-made samples: open http://localhost:8000/dashboard/?data=sample
const params = new URLSearchParams(location.search);
export const BASE = params.get("data") === "sample" ? "/serving/sample/" : "/serving/data/";
const GEO = "/serving/geo/bgd_adm2_simplified_coded.geojson";
const FILES = ["meta", "freshness", "cases_weekly", "forecast", "alerts",
               "seasonality", "calendar", "allocation", "allocation_peak", "stock", "dq_report"];
const REQUIRED = new Set(["meta", "freshness", "cases_weekly"]);

export async function loadAll() {
  const [geo, ...loaded] = await Promise.all([
    d3.json(GEO),
    ...FILES.map(f => d3.json(BASE + f + ".json").catch(() => null)),
  ]);
  const data = { geo: rewind(geo) };
  FILES.forEach((f, i) => {
    // Fail loudly: a required file that is missing must not leave a half-empty dashboard.
    if (loaded[i] === null && REQUIRED.has(f)) {
      throw new Error(`Missing ${BASE}${f}.json - run the pipeline export, or open ?data=sample`);
    }
    data[f] = loaded[i];
  });

  // district_code is always a string ("01"), never a number.
  data.districtInfo = new Map(data.meta.districts.map(d => [d.district_code, d]));
  data.cases = new Map(data.cases_weekly.rows.map(r => [`${r.disease}|${r.week_start}|${r.district_code}`, r]));
  data.weeks = [...new Set(data.cases_weekly.rows.map(r => r.week_start))].sort();

  // Per-disease export window [first, last] week_start. Prefer meta.disease_windows if the
  // exporter provides it; otherwise derive it from the rows (ISO dates compare as strings).
  const win = new Map();
  for (const r of data.cases_weekly.rows) {
    const w = win.get(r.disease);
    if (!w) win.set(r.disease, [r.week_start, r.week_start]);
    else { if (r.week_start < w[0]) w[0] = r.week_start; if (r.week_start > w[1]) w[1] = r.week_start; }
  }
  for (const [d, v] of Object.entries(data.meta.disease_windows ?? {})) {
    if (v && v.first_week_start && v.last_week_start) win.set(d, [v.first_week_start, v.last_week_start]);
  }
  data.diseaseWindow = win;
  return data;
}

// The boundary file has counter-clockwise outer rings (GeoJSON standard); d3-geo needs the
// opposite winding, so flip any feature that d3 sees as "the whole globe minus the district".
function rewind(geo) {
  const flip = ring => ring.slice().reverse();
  for (const f of geo.features) {
    if (d3.geoArea(f) > 2 * Math.PI) {
      const g = f.geometry;
      if (g.type === "Polygon") g.coordinates = g.coordinates.map(flip);
      else if (g.type === "MultiPolygon") g.coordinates = g.coordinates.map(p => p.map(flip));
    }
  }
  return geo;
}

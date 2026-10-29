// View 1 (VIS-2): district choropleth of incidence per 100,000 with alert markers.
// Encodings: sequential YlOrRd (colour-blind-safe); hatch = suppressed; plain grey = no data.
const W = 700, H = 760;

export function mountChoropleth(el, data, store, tooltip) {
  const root = d3.select(el);
  const svg = root.append("svg").attr("viewBox", `0 0 ${W} ${H}`)
    .attr("role", "img").attr("aria-label", "Choropleth map of incidence per 100,000 by district");

  const defs = svg.append("defs");
  defs.append("pattern").attr("id", "hatch").attr("width", 6).attr("height", 6)
    .attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)")
    .call(p => {
      p.append("rect").attr("width", 6).attr("height", 6).attr("fill", "#e6e6e6");
      p.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 6)
        .attr("stroke", "#555").attr("stroke-width", 2);
    });
  const grad = defs.append("linearGradient").attr("id", "legendGrad");
  d3.range(0, 1.01, 0.1).forEach(t =>
    grad.append("stop").attr("offset", t).attr("stop-color", d3.interpolateYlOrRd(t)));

  const projection = d3.geoMercator().fitSize([W, H - 70], data.geo);
  const path = d3.geoPath(projection);
  const centroid = new Map(data.geo.features.map(f => [f.properties.district_code, path.centroid(f)]));

  const paths = svg.append("g").selectAll("path").data(data.geo.features).join("path")
    .attr("class", "district").attr("d", path)
    .on("click", (_, f) => {
      const c = f.properties.district_code;
      store.set({ district: store.get().district === c ? null : c });
    })
    .on("mousemove", (ev, f) => tooltip.show(ev, tipHtml(f)))
    .on("mouseleave", () => tooltip.hide());
  const markers = svg.append("g");

  // Legend (drawn once; domain label updated in render)
  const lg = svg.append("g").attr("class", "legend").attr("transform", `translate(20,${H - 50})`);
  lg.append("rect").attr("width", 180).attr("height", 10).attr("fill", "url(#legendGrad)");
  const lgMin = lg.append("text").attr("y", 24).text("0");
  const lgMax = lg.append("text").attr("x", 180).attr("y", 24).attr("text-anchor", "end");
  lg.append("text").attr("y", -6).text("Incidence per 100,000 (weekly)");
  lg.append("rect").attr("x", 230).attr("width", 14).attr("height", 14).attr("fill", "url(#hatch)").attr("stroke", "#555");
  lg.append("text").attr("x", 250).attr("y", 11).text("suppressed (<5 cases)");
  lg.append("rect").attr("x", 230).attr("y", 20).attr("width", 14).attr("height", 14).attr("fill", "#bdbdbd");
  lg.append("text").attr("x", 250).attr("y", 31).text("no data");
  lg.append("rect").attr("x", 400).attr("y", 20).attr("width", 14).attr("height", 14).attr("fill", "#c9d3e0").attr("stroke", "#8a96a6");
  lg.append("text").attr("x", 420).attr("y", 31).text("not exported for this disease");
  lg.append("circle").attr("cx", 400).attr("cy", 7).attr("r", 5).attr("fill", "#111").attr("stroke", "#fff");
  lg.append("text").attr("x", 410).attr("y", 11).text("alert");

  // Weeks outside a disease's export window are "not exported", which is not the same as "no data".
  const outside = s => {
    const w = data.diseaseWindow?.get(s.disease), wk = data.weeks[s.week];
    return !!w && (wk < w[0] || wk > w[1]);
  };
  // PRV-3: in the district role, other districts are drawn as their division's aggregate (own district excluded).
  // An aggregate needs at least MIN_AGG districts with published counts, otherwise it could equal one district's count.
  const MIN_AGG = 2;
  const isOther = (code, s) => s.role === "district" && code !== s.roleDistrict;
  let aggKey = "", aggMap = new Map();
  function getAgg(s) {
    const key = `${s.disease}|${s.week}|${s.roleDistrict}`;
    if (key === aggKey) return aggMap;
    aggKey = key; aggMap = new Map();
    for (const f of data.geo.features) {
      const c = f.properties.district_code;
      if (c === s.roleDistrict) continue;
      const r = rowFor(c, s);
      if (!r || r.suppressed || r.cases === null) continue;
      const o = aggMap.get(f.properties.division) ?? { cases: 0, pop: 0, n: 0, inc: null };
      o.cases += r.cases; o.pop += data.districtInfo.get(c)?.population ?? 0; o.n++;
      aggMap.set(f.properties.division, o);
    }
    for (const o of aggMap.values()) o.inc = o.n >= MIN_AGG && o.pop > 0 ? (o.cases / o.pop) * 1e5 : null;
    return aggMap;
  }
  const rowFor = (code, s) => data.cases.get(`${s.disease}|${data.weeks[s.week]}|${code}`);
  const alertSet = s => new Set((data.alerts?.rows ?? [])
    .filter(a => a.alert_flag && a.disease === s.disease && a.week_start === data.weeks[s.week])
    .map(a => a.district_code));

  function tipHtml(f) {
    const s = store.get(), code = f.properties.district_code, r = rowFor(code, s);
    if (isOther(code, s)) {
      const a = getAgg(s).get(f.properties.division);
      return `<b>${f.properties.division} division</b> (other districts, aggregate)<br>` +
        (a && a.inc !== null ? `${a.inc.toFixed(2)} per 100,000<br>district detail hidden in this role`
                             : "aggregate not available (fewer than 2 districts with published counts)");
    }
    const name = `<b>${f.properties.district_name}</b> (${f.properties.division})`;
    if (!r) return outside(s) ? `${name}<br>not exported for this disease before ${data.diseaseWindow.get(s.disease)[0]}` : `${name}<br>no data for this week`;
    if (r.suppressed) return `${name}<br>suppressed (fewer than 5 cases)`;
    return `${name}<br>${r.cases} cases<br>${r.incidence_per_100k} per 100,000`;
  }

  function render(s) {
    const vals = data.cases_weekly.rows
      .filter(r => r.disease === s.disease && r.incidence_per_100k !== null)
      .map(r => r.incidence_per_100k);
    const max = d3.quantile(vals.sort(d3.ascending), 0.95) || 1;   // robust to outliers
    const color = d3.scaleSequential(d3.interpolateYlOrRd).domain([0, max]).clamp(true);
    lgMax.text(`${max.toFixed(1)}+`); lgMin.text("0");

    paths.attr("fill", f => {
      if (isOther(f.properties.district_code, s)) {
        const a = getAgg(s).get(f.properties.division);
        return a && a.inc !== null ? color(a.inc) : (outside(s) ? "#c9d3e0" : "#bdbdbd");
      }
      const r = rowFor(f.properties.district_code, s);
      if (!r) return outside(s) ? "#c9d3e0" : "#bdbdbd";
      if (r.suppressed) return "url(#hatch)";
      return color(r.incidence_per_100k);
    }).attr("fill-opacity", f => (isOther(f.properties.district_code, s) ? 0.8 : 1))
      .classed("selected", f => f.properties.district_code === s.district)
      .classed("own", f => s.role === "district" && f.properties.district_code === s.roleDistrict);

    const alerts = [...alertSet(s)].filter(c => centroid.has(c) && !isOther(c, s));   // others' alerts are not shown in detail
    markers.selectAll("circle").data(alerts, c => c).join("circle")
      .attr("r", 5).attr("fill", "#111").attr("stroke", "#fff").attr("stroke-width", 1.5)
      .attr("pointer-events", "none")
      .attr("cx", c => centroid.get(c)[0]).attr("cy", c => centroid.get(c)[1]);
  }

  store.subscribe(render);
  render(store.get());
}

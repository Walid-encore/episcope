// View 3 (VIS-2, VIS-3): seasonal heatmap (division x epi-week) + preparedness calendar (AN-9).
// Colour = seasonal_index for the selected disease. Outlined cells = peak weeks. Hatched = low volume.
// For divisions with a clear season: bar = season window, triangle = pre-position supplies, circle = start campaign.
// The selected week's epi-week is outlined; clicking a column jumps to the latest week with that epi-week.
const W = 900, H = 330, ML = 92, MR = 12, MT = 34, MB = 54;
const fmt2 = d3.format(".2f");

export function mountSeasonal(el, data, store, tooltip) {
  const root = d3.select(el);
  root.append("h2").attr("class", "panel-title").text("Seasonal pattern and preparedness calendar");
  if (!data.seasonality) {
    root.append("div").attr("class", "panel-note").text("seasonality.json not found in the data folder.");
    return;
  }
  const note = root.append("div").attr("class", "panel-note");
  const svg = root.append("svg").attr("class", "ts sea").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img")
    .attr("aria-label", "Heatmap of seasonal index by division and epi-week, with preparedness calendar markers");
  const calBox = root.append("div").attr("class", "table-wrap").style("max-height", "none").style("margin-top", "8px");
  root.append("div").attr("class", "panel-note").style("margin-top", "6px").text(
    "The seasonal index shows how busy each epi-week usually is for the chosen disease, based on past years. " +
    "The calendar dates are for the next season and come from the seasonal profile; they are guidance, not a forecast.");

  const weekNo = new Map();                           // week_start -> epi_week (from cases_weekly)
  for (const r of data.cases_weekly.rows) if (!weekNo.has(r.week_start)) weekNo.set(r.week_start, r.epi_week);
  const calRows = data.calendar?.rows ?? [];

  function render(s) {
    const rows = data.seasonality.rows.filter(r => r.disease === s.disease);
    svg.selectAll("*").remove();
    calBox.html("");
    const asOf = data.seasonality.data_as_of ?? "n/a";
    note.text(`${s.disease} · seasonal index by division · seasonality as of ${asOf}`);
    if (!rows.length) {
      svg.append("text").attr("x", W / 2).attr("y", H / 2).attr("text-anchor", "middle").attr("class", "muted")
        .text("No seasonality rows for this disease.");
      return;
    }
    const area = r => r.division ?? r.district_code;
    const byKey = new Map(rows.map(r => [`${area(r)}|${r.epi_week}`, r]));
    const areas = ["ALL", ...[...new Set(rows.map(area))].filter(a => a !== "ALL").sort()];
    const weeks = [...new Set(rows.map(r => r.epi_week))].sort((a, b) => a - b);
    const maxWeek = weeks.at(-1);
    const x = d3.scaleBand(weeks, [ML, W - MR]).paddingInner(0.05);
    const y = d3.scaleBand(areas, [MT, H - MB]).paddingInner(0.08);
    const maxIdx = d3.max(rows, r => r.seasonal_index) || 1;
    const color = d3.scaleSequential(d3.interpolateYlGnBu).domain([0, maxIdx]).clamp(true);
    const selDiv = data.districtInfo.get(s.district)?.division;
    const curEpi = weekNo.get(data.weeks[s.week]);
    const label = a => (a === "ALL" ? "National" : a);

    const defs = svg.append("defs");
    defs.append("pattern").attr("id", "hatchSea").attr("width", 5).attr("height", 5).attr("patternUnits", "userSpaceOnUse")
      .attr("patternTransform", "rotate(45)").call(p => {
        p.append("rect").attr("width", 5).attr("height", 5).attr("fill", "#e6e6e6").attr("fill-opacity", 0.55);
        p.append("line").attr("y2", 5).attr("stroke", "#444").attr("stroke-width", 1.6);
      });
    const grad = defs.append("linearGradient").attr("id", "seaGrad");
    d3.range(0, 1.01, 0.1).forEach(t => grad.append("stop").attr("offset", t).attr("stop-color", d3.interpolateYlGnBu(t)));

    // axis: epi-week labels every 4th week
    svg.append("g").selectAll("text").data(weeks.filter(w => (w - 1) % 4 === 0)).join("text")
      .attr("x", w => x(w) + x.bandwidth() / 2).attr("y", MT - 8).attr("text-anchor", "middle").attr("font-size", 10).text(w => w);
    svg.append("text").attr("x", ML).attr("y", 12).attr("font-weight", 600).text("Epi-week");
    svg.append("g").selectAll("text").data(areas).join("text")
      .attr("x", ML - 6).attr("y", a => y(a) + y.bandwidth() / 2 + 4).attr("text-anchor", "end")
      .attr("font-weight", a => (a === selDiv ? 700 : 400)).text(label);

    // cells
    const tip = (a, r, w) => {
      if (!r) return `<b>${label(a)}</b> · week ${w}<br>no data`;
      let h = `<b>${label(a)}</b> · epi-week ${w}<br>seasonal index ${fmt2(r.seasonal_index)}`;
      if (r.peak_flag) h += "<br>peak week";
      if (r.peak_week_earliest != null) h += `<br>usual peak: weeks ${r.peak_week_earliest}–${r.peak_week_latest}`;
      if (r.low_volume === true) h += "<br>low volume: interpret with caution";
      return h;
    };
    for (const a of areas) for (const w of weeks) {
      const r = byKey.get(`${a}|${w}`);
      const g = svg.append("g").style("cursor", "pointer")
        .on("mousemove", ev => tooltip.show(ev, tip(a, r, w))).on("mouseleave", () => tooltip.hide())
        .on("click", () => {
          const [lo, hi] = store.get().range;
          for (let i = hi; i >= lo; i--) if (weekNo.get(data.weeks[i]) === w) { store.set({ week: i }); break; }
        });
      g.append("rect").attr("x", x(w)).attr("y", y(a)).attr("width", x.bandwidth()).attr("height", y.bandwidth())
        .attr("fill", r ? color(r.seasonal_index) : "#bdbdbd");
      if (r && r.low_volume === true)
        g.append("rect").attr("x", x(w)).attr("y", y(a)).attr("width", x.bandwidth()).attr("height", y.bandwidth()).attr("fill", "url(#hatchSea)");
      if (r && r.peak_flag)
        g.append("rect").attr("x", x(w) + 1).attr("y", y(a) + 1).attr("width", x.bandwidth() - 2).attr("height", y.bandwidth() - 2)
          .attr("fill", "none").attr("stroke", "currentColor").attr("stroke-width", 1.6);
    }

    // calendar markers per division (clear seasons only)
    const cal = new Map(calRows.filter(c => c.disease === s.disease).map(c => [c.division, c]));
    const cx = w => x(w) + x.bandwidth() / 2;
    for (const a of areas) {
      const c = cal.get(a);
      if (!c || !c.clear_season || c.onset_week == null) continue;
      const segs = c.onset_week <= c.end_week ? [[c.onset_week, c.end_week]] : [[c.onset_week, maxWeek], [weeks[0], c.end_week]];
      for (const [a0, a1] of segs)
        svg.append("rect").attr("x", x(a0)).attr("y", y(a) + y.bandwidth() - 4).attr("width", x(a1) + x.bandwidth() - x(a0))
          .attr("height", 3).attr("fill", "currentColor").attr("pointer-events", "none");
      if (c.prepose_supplies_week != null && x(c.prepose_supplies_week) !== undefined)
        svg.append("path").attr("transform", `translate(${cx(c.prepose_supplies_week)},${y(a) + 6})`)
          .attr("d", d3.symbol(d3.symbolTriangle, 40)()).attr("fill", "#d95f02").attr("stroke", "#fff").attr("stroke-width", 0.8)
          .attr("pointer-events", "none");
      if (c.start_campaign_week != null && x(c.start_campaign_week) !== undefined)
        svg.append("circle").attr("cx", cx(c.start_campaign_week)).attr("cy", y(a) + 6).attr("r", 3.2)
          .attr("fill", "#CC79A7").attr("stroke", "#fff").attr("stroke-width", 0.8).attr("pointer-events", "none");
    }

    // selected week column
    if (curEpi != null && x(curEpi) !== undefined)
      svg.append("rect").attr("x", x(curEpi) - 1).attr("y", MT - 2).attr("width", x.bandwidth() + 2).attr("height", H - MB - MT + 4)
        .attr("fill", "none").attr("stroke", "#d95f02").attr("stroke-width", 2).attr("stroke-dasharray", "4 2").attr("pointer-events", "none");

    // legend
    const lg = svg.append("g").attr("transform", `translate(${ML},${H - 36})`).attr("class", "legend");
    lg.append("rect").attr("width", 160).attr("height", 10).attr("fill", "url(#seaGrad)");
    lg.append("text").attr("y", 24).attr("font-size", 10).text("0");
    lg.append("text").attr("x", 160).attr("y", 24).attr("font-size", 10).attr("text-anchor", "end").text(`${fmt2(maxIdx)} (busier)`);
    lg.append("text").attr("y", -4).attr("font-size", 10).text("Seasonal index");
    const items = [
      ["peak week", g => g.append("rect").attr("width", 12).attr("height", 12).attr("y", -6).attr("fill", "none").attr("stroke", "currentColor").attr("stroke-width", 1.6)],
      ["low volume", g => g.append("rect").attr("width", 12).attr("height", 12).attr("y", -6).attr("fill", "url(#hatchSea)").attr("stroke", "#555")],
      ["season window", g => g.append("rect").attr("width", 14).attr("height", 3).attr("y", -1).attr("fill", "currentColor")],
      ["pre-position supplies", g => g.append("path").attr("transform", "translate(6,0)").attr("d", d3.symbol(d3.symbolTriangle, 40)()).attr("fill", "#d95f02")],
      ["start campaign", g => g.append("circle").attr("cx", 6).attr("r", 3.5).attr("fill", "#CC79A7")],
      ["selected week", g => g.append("rect").attr("width", 12).attr("height", 12).attr("y", -6).attr("fill", "none").attr("stroke", "#d95f02").attr("stroke-width", 2).attr("stroke-dasharray", "3 2")],
    ];
    let xx = 210;
    for (const [text, draw] of items) {
      const g = lg.append("g").attr("transform", `translate(${xx},6)`);
      draw(g);
      g.append("text").attr("x", 18).attr("y", 4).attr("font-size", 10).text(text);
      xx += 26 + text.length * 5.6;
    }

    // calendar table
    const dRows = calRows.filter(c => c.disease === s.disease);
    const wk = (w, d) => (w == null ? "—" : `week ${w}${d ? ` (${d})` : ""}`);
    const body = dRows.map(c => {
      const cls = c.division === s.district ? "" : "";
      if (!c.clear_season) return `<tr class="${cls}"><td>${c.division}</td><td colspan="4" class="empty" style="text-align:left;padding:5px 6px">No clear seasonal pattern${c.low_volume ? " (low volume)" : ""}: no dates recommended</td></tr>`;
      const range = c.peak_week_earliest != null ? ` · usual peak weeks ${c.peak_week_earliest}–${c.peak_week_latest}` : "";
      return `<tr class="${c.division === selDiv ? "selected" : ""}"><td>${c.division}</td>` +
        `<td>${wk(c.onset_week, c.onset_date)} to ${wk(c.end_week, c.end_date)}</td>` +
        `<td>${wk(c.peak_week, c.peak_date)}${range}</td>` +
        `<td>${wk(c.prepose_supplies_week, c.prepose_supplies_date)}</td>` +
        `<td>${wk(c.start_campaign_week, c.start_campaign_date)}${c.low_volume ? " · low volume" : ""}</td></tr>`;
    }).join("");
    calBox.html(dRows.length
      ? `<table class="warn"><thead><tr><th>Division</th><th>Season window</th><th>Peak</th><th>Pre-position supplies by</th><th>Start prevention campaign by</th></tr></thead><tbody>${body}</tbody></table>`
      : `<div class="panel-note">No calendar rows for this disease.</div>`);
  }

  store.subscribe(render);
  render(store.get());
}

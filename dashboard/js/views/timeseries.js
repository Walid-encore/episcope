// View 2 (VIS-2, VIS-3): time series with brush/zoom.
// Focus chart: observed cases, forecast median, q10-q90 band, suppressed weeks (hatch).
// Strip: alert score vs. threshold (different units from cases, so shown separately).
// Context chart: d3.brushX sets store.range = [firstWeekIdx, lastWeekIdx].
// No district selected -> national total (non-suppressed districts only).
const W = 900, H = 490, M = { l: 52, r: 20 };
const F = { t: 40, b: 250 }, S = { t: 282, b: 328 }, C = { t: 372, b: 420 };
const WEEK_MS = 7 * 864e5;
const utc = s => new Date(s + "T00:00:00Z");

export function mountTimeSeries(el, data, store, tooltip) {
  const n = data.weeks.length;
  const dates = data.weeks.map(utc);
  const innerW = W - M.l - M.r;
  const x2 = d3.scaleUtc().domain([dates[0], dates[n - 1]]).range([M.l, W - M.r]);
  let cur = null; // state shared between render() and the tooltip handlers

  const svg = d3.select(el).append("svg").attr("class", "ts").attr("viewBox", `0 0 ${W} ${H}`)
    .attr("role", "img")
    .attr("aria-label", "Weekly cases with forecast and 80% band, alert scores, and a range selector");
  const defs = svg.append("defs");
  defs.append("pattern").attr("id", "hatchTS").attr("width", 5).attr("height", 5)
    .attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)")
    .call(p => {
      p.append("rect").attr("width", 5).attr("height", 5).attr("fill", "#e6e6e6");
      p.append("line").attr("y2", 5).attr("stroke", "#555").attr("stroke-width", 2);
    });
  defs.append("clipPath").attr("id", "clipF").append("rect")
    .attr("x", M.l).attr("y", F.t).attr("width", innerW).attr("height", F.b - F.t);

  const title = svg.append("text").attr("x", M.l).attr("y", 16).attr("font-weight", 600);
  const sub = svg.append("text").attr("x", M.l).attr("y", 31).attr("class", "muted");
  const gx = svg.append("g").attr("transform", `translate(0,${F.b})`);
  const gy = svg.append("g").attr("transform", `translate(${M.l},0)`);
  svg.append("text").attr("transform", `translate(13,${(F.t + F.b) / 2}) rotate(-90)`)
    .attr("text-anchor", "middle").text("Cases per week");

  const gF = svg.append("g").attr("clip-path", "url(#clipF)");
  const band = gF.append("path").attr("fill", "#4c78a8").attr("fill-opacity", 0.25);
  const fcLine = gF.append("path").attr("fill", "none").attr("stroke", "#4c78a8")
    .attr("stroke-width", 2).attr("stroke-dasharray", "6 4");
  const obsLine = gF.append("path").attr("fill", "none").attr("stroke", "currentColor").attr("stroke-width", 1.8);
  const supG = gF.append("g");
  const selLine = gF.append("line").attr("y1", F.t).attr("y2", F.b)
    .attr("stroke", "#d95f02").attr("stroke-width", 1.5).attr("stroke-dasharray", "3 3");
  const msg = svg.append("text").attr("x", W / 2).attr("y", (F.t + F.b) / 2)
    .attr("text-anchor", "middle").attr("class", "muted");

  // Alert strip
  const gS = svg.append("g");
  const gSy = svg.append("g").attr("transform", `translate(${M.l},0)`);
  svg.append("text").attr("x", M.l).attr("y", S.t - 6).attr("class", "muted")
    .text("Alert score vs. threshold (max across districts if national)");
  const thrLine = svg.append("line").attr("stroke", "currentColor").attr("stroke-dasharray", "4 3");
  const thrText = svg.append("text").attr("text-anchor", "start").attr("font-size", 10);

  // Context + brush
  const ctxPath = svg.append("path").attr("fill", "none").attr("stroke", "currentColor").attr("stroke-width", 1);
  svg.append("g").attr("transform", `translate(0,${C.b})`).call(d3.axisBottom(x2).ticks(8));
  svg.append("text").attr("x", M.l).attr("y", C.t - 6).attr("class", "muted")
    .text("Drag to choose the date range · click to reset");
  const brush = d3.brushX().extent([[M.l, C.t], [W - M.r, C.b]]).on("end", ev => {
    if (!ev.sourceEvent) return;                       // ignore programmatic moves
    if (!ev.selection) { store.set({ range: [0, n - 1] }); return; }
    let a = d3.bisectCenter(dates, x2.invert(ev.selection[0]));
    let b = d3.bisectCenter(dates, x2.invert(ev.selection[1]));
    if (b - a < 3) b = Math.min(n - 1, a + 3), a = Math.max(0, b - 3);
    const w = Math.min(Math.max(store.get().week, a), b);
    store.set({ range: [a, b], week: w });
  });
  const brushG = svg.append("g").attr("class", "brush").call(brush);

  // Hover / click on focus chart
  const overlay = svg.append("rect").attr("x", M.l).attr("y", F.t).attr("width", innerW)
    .attr("height", F.b - F.t).attr("fill", "transparent").style("cursor", "crosshair")
    .on("mousemove", ev => { const h = tipHtml(d3.pointer(ev, svg.node())[0]); if (h) tooltip.show(ev, h); })
    .on("mouseleave", () => tooltip.hide())
    .on("click", ev => store.set({ week: weekAt(d3.pointer(ev, svg.node())[0]) }));

  // Legend (two rows)
  const legend = svg.append("g").attr("class", "legend");
  function legendRow(y, items) {
    let xx = M.l;
    for (const it of items) {
      const g = legend.append("g").attr("transform", `translate(${xx},${y})`);
      it.draw(g);
      g.append("text").attr("x", 24).attr("y", 4).attr("font-size", 11).text(it.label);
      xx += 24 + it.label.length * 6.1 + 18;
    }
  }
  const line = (g, dash, col = "currentColor") => g.append("line").attr("x2", 18)
    .attr("stroke", col).attr("stroke-width", 2).attr("stroke-dasharray", dash);
  legendRow(H - 20, [
    { label: "Observed", draw: g => line(g, null) },
    { label: "Suppressed week (<5 cases)", draw: g => g.append("rect").attr("y", -5).attr("width", 18).attr("height", 10).attr("fill", "url(#hatchTS)").attr("stroke", "#555") },
    { label: "Forecast median", draw: g => line(g, "5 3", "#4c78a8") },
    { label: "Forecast 80% band (q10-q90)", draw: g => g.append("rect").attr("y", -5).attr("width", 18).attr("height", 10).attr("fill", "#4c78a8").attr("fill-opacity", .3) },
    { label: "Selected week", draw: g => line(g, "3 3", "#d95f02") },
  ]);
  legendRow(H - 5, [
    { label: "Alert (score at/above threshold)", draw: g => g.append("rect").attr("y", -5).attr("width", 18).attr("height", 10).attr("fill", "#d95f02") },
    { label: "Below threshold", draw: g => g.append("rect").attr("y", -5).attr("width", 18).attr("height", 10).attr("fill", "#9e9e9e") },
    { label: "Possible reporting artefact", draw: g => g.append("rect").attr("y", -5).attr("width", 18).attr("height", 10).attr("fill", "url(#hatchTS)").attr("stroke", "#d95f02") },
  ]);

  // ---------- data helpers ----------
  const natCache = new Map();
  function national(disease) {
    if (!natCache.has(disease)) {
      const m = new Map();
      for (const r of data.cases_weekly.rows) {
        if (r.disease !== disease) continue;
        const o = m.get(r.week_start) ?? { v: 0, n: 0, sup: 0 };
        if (r.suppressed) o.sup++; else { o.v += r.cases; o.n++; }
        m.set(r.week_start, o);
      }
      natCache.set(disease, m);
    }
    return natCache.get(disease);
  }
  function observed(s) {
    if (s.district) return data.weeks.map(w => {
      const r = data.cases.get(`${s.disease}|${w}|${s.district}`);
      return r ? { v: r.cases, sup: r.suppressed } : { v: null, sup: false, nodata: true };
    });
    const m = national(s.disease);
    return data.weeks.map(w => {
      const o = m.get(w);
      return o && o.n > 0 ? { v: o.v, sup: false, nsup: o.sup } : { v: null, sup: false, nodata: true };
    });
  }
  function forecast(s) {
    const by = new Map();
    for (const r of data.forecast?.rows ?? []) {
      if (r.disease !== s.disease || (s.district && r.district_code !== s.district)) continue;
      const o = by.get(r.target_week_start) ?? { d: r.target_week_start, q10: 0, q50: 0, q90: 0 };
      o.q10 += r.q10; o.q50 += r.q50; o.q90 += r.q90;
      by.set(r.target_week_start, o);
    }
    return [...by.values()].sort((a, b) => a.d.localeCompare(b.d));
  }
  function alertMap(s) {
    const by = new Map();
    for (const a of data.alerts?.rows ?? []) {
      if (a.disease !== s.disease || (s.district && a.district_code !== s.district)) continue;
      const o = by.get(a.week_start);
      if (!o || a.score > o.score) by.set(a.week_start, a);
    }
    return by;
  }
  const weekAt = px => {
    const i = d3.bisectCenter(dates, cur.x.invert(px));
    return Math.min(Math.max(i, cur.i0), cur.i1);
  };
  function tipHtml(px) {
    if (!cur) return null;
    const t = cur.x.invert(px);
    if (cur.showFc && cur.fc.length && t > new Date(+dates[n - 1] + WEEK_MS / 2)) {
      const f = cur.fc.reduce((a, b) => Math.abs(utc(b.d) - t) < Math.abs(utc(a.d) - t) ? b : a);
      return `<b>${f.d}</b> (forecast)<br>median ${f.q50}<br>80% interval ${f.q10}–${f.q90}`;
    }
    const i = weekAt(px), o = cur.obs[i], w = data.weeks[i];
    let h = `<b>week of ${w}</b><br>`;
    h += o.nodata ? "no data" : o.sup ? "suppressed (fewer than 5 cases)" : `${o.v} cases`;
    if (o.nsup) h += `<br>${o.nsup} suppressed districts excluded from total`;
    const a = cur.alerts.get(w);
    if (a) h += `<br>alert score ${a.score} (threshold ${a.threshold})` + (a.reporting_artefact_flag ? "<br>possible reporting artefact" : "");
    return h;
  }

  // ---------- render ----------
  function render(s) {
    const [i0, i1] = s.range;
    const obs = observed(s), fc = forecast(s), alerts = alertMap(s);
    const showFc = i1 === n - 1 && fc.length > 0;
    const x = d3.scaleUtc().domain([dates[i0], showFc ? utc(fc.at(-1).d) : dates[i1]]).range([M.l, W - M.r]);
    cur = { x, i0, i1, obs, fc, alerts, showFc };

    const info = s.district ? data.districtInfo.get(s.district) : null;
    title.text(`${s.disease[0].toUpperCase() + s.disease.slice(1)} · ${info ? info.district_name : "National total"}`);
    sub.text(info
      ? `${info.division} division`
      : "Sum of districts with non-suppressed counts (suppressed cells excluded); forecast band = sum of district quantiles (approximate)");

    const vis = obs.map((o, i) => ({ ...o, i, d: dates[i] })).filter(o => o.i >= i0 && o.i <= i1);
    const ymax = d3.max([...vis.map(o => o.v), ...(showFc ? fc.map(f => f.q90) : [])]) || 1;
    const y = d3.scaleLinear().domain([0, ymax * 1.1]).nice().range([F.b, F.t]);
    gx.call(d3.axisBottom(x).ticks(8));
    gy.call(d3.axisLeft(y).ticks(5));

    const hasAny = vis.some(o => o.v !== null || o.sup);
    msg.text(hasAny ? "" : "No data for this district / disease");

    obsLine.attr("d", d3.line().defined(o => o.v !== null).x(o => x(o.d)).y(o => y(o.v))(vis));

    const last = obs[n - 1];
    const origin = last.v !== null ? [{ d: dates[n - 1], q10: last.v, q50: last.v, q90: last.v }] : [];
    const fpts = showFc ? [...origin, ...fc.map(f => ({ ...f, d: utc(f.d) }))] : [];
    band.attr("d", d3.area().x(p => x(p.d)).y0(p => y(p.q10)).y1(p => y(p.q90))(fpts));
    fcLine.attr("d", d3.line().x(p => x(p.d)).y(p => y(p.q50))(fpts));

    const bw = Math.max(2, x(new Date(+dates[i0] + WEEK_MS)) - x(dates[i0]) - 1);
    supG.selectAll("rect").data(vis.filter(o => o.sup), o => o.i).join("rect")
      .attr("x", o => x(o.d) - bw / 2).attr("width", bw).attr("y", F.b - 9).attr("height", 9)
      .attr("fill", "url(#hatchTS)").attr("stroke", "#555").attr("stroke-width", .5);

    const sx = x(dates[s.week]);
    selLine.attr("x1", sx).attr("x2", sx).attr("display", s.week >= i0 && s.week <= i1 ? null : "none");

    // alert strip
    const al = [...alerts.values()].filter(a => { const d = utc(a.week_start); return d >= dates[i0] && d <= dates[i1]; });
    const thr = [...alerts.values()][0]?.threshold ?? null;
    const ys = d3.scaleLinear().domain([0, Math.max(thr ? thr * 1.5 : 1, d3.max(al, a => a.score) || 0) * 1.1]).range([S.b, S.t]);
    gSy.call(d3.axisLeft(ys).ticks(3));
    gS.selectAll("rect").data(al, a => a.week_start).join("rect")
      .attr("x", a => x(utc(a.week_start)) - bw / 2).attr("width", bw)
      .attr("y", a => ys(a.score)).attr("height", a => S.b - ys(a.score))
      .attr("fill", a => a.reporting_artefact_flag ? "url(#hatchTS)" : a.alert_flag ? "#d95f02" : "#9e9e9e")
      .attr("stroke", a => a.reporting_artefact_flag ? "#d95f02" : null);
    thrLine.attr("display", thr === null ? "none" : null)
      .attr("x1", M.l).attr("x2", W - M.r).attr("y1", thr === null ? 0 : ys(thr)).attr("y2", thr === null ? 0 : ys(thr));
    thrText.attr("display", thr === null ? "none" : null).attr("x", M.l + 6).attr("y", thr === null ? 0 : ys(thr) - 3)
      .text(`threshold ${thr}`);

    // context chart (full period) + brush position
    const yc = d3.scaleLinear().domain([0, d3.max(obs, o => o.v) || 1]).range([C.b, C.t]);
    ctxPath.attr("d", d3.line().defined(o => o.v !== null).x((o, i) => x2(dates[i])).y(o => yc(o.v))(obs));
    // Full range = no brush box, so the user can drag a new one (a full-width box cannot be dragged).
    const curSel = d3.brushSelection(brushG.node());
    if (i0 === 0 && i1 === n - 1) {
      if (curSel) brushG.call(brush.move, null);
    } else {
      const target = [x2(dates[i0]), x2(dates[i1])];
      if (!curSel || Math.abs(curSel[0] - target[0]) > 1 || Math.abs(curSel[1] - target[1]) > 1) brushG.call(brush.move, target);
    }
  }

  store.subscribe(render);
  render(store.get());
}

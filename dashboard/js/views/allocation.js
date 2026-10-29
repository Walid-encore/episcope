// View 4 (VIS-2, VIS-3; groundwork for the VIS-5 what-if panel): allocation.
// Left: per district, forecast demand vs current stock vs recommended allocation
//       (optimised and population-proportional baseline) for the chosen resource.
// Right: efficiency-equity curve (weighted unmet demand vs coverage Gini) across budgets and alphas.
// Data: allocation.json -> plan (demand, stock), rows (grid of allocations), curve, recommendation.
// alpha is null on population_proportional rows, so baseline rows are selected by strategy + budget only.
const KEYS = ["demand", "stock", "opt", "base"];
const COLORS = { demand: "#0072B2", stock: "#56B4E9", opt: "#D55E00", base: "#CC79A7" };   // Okabe-Ito
const LABELS = { demand: "Forecast demand", stock: "Current stock", opt: "Optimised allocation", base: "Population-proportional" };
const RES = { iv_saline_units: "IV saline (units)", ns1_kits: "NS1 test kits", ors_sachets: "ORS sachets" };
const TOP_N = 12;
const f1 = d3.format(",.1f");
const fmt = v => (v === null || v === undefined ? "n/a" : f1(v));

export function mountAllocation(el, data, store, tooltip) {
  const root = d3.select(el);
  const scenarios = { live: data.allocation, peak: data.allocation_peak ?? null };
  const live = scenarios.live;
  const act = () => scenarios[store.get().scenario] ?? live;   // each scenario has its own budget/alpha lists
  const nearest = (arr, v) => arr.reduce((b, x) => (Math.abs(x - v) < Math.abs(b - v) ? x : b));
  const cache = {};
  const build = a => ({
    grid: new Map(a.rows.map(r => [`${r.strategy}|${r.budget_level}|${r.alpha}|${r.district_code}|${r.resource}`, r])),
    plan: a.plan ?? [],
    zeroOmitted: !!a.grid_zero_rows_omitted,       // true: a missing grid row means allocated 0 and unmet 0
  });
  const ix = key => (cache[key] ??= build(scenarios[key]));
  const alloc = (key, strat, b, alpha, code, res) => {
    const r = ix(key).grid.get(`${strat}|${b}|${alpha}|${code}|${res}`);
    return r ?? (ix(key).zeroOmitted ? { allocated_units: 0, unmet_demand: 0 } : null);
  };

  // ---------- controls ----------
  root.append("h2").attr("class", "panel-title").text("Allocation: demand, stock and recommended allocation");
  const bar = root.append("div").attr("class", "controls-row");
  const lab = t => { const l = bar.append("label"); l.append("span").text(t + " "); return l; };
  let selScen = null;
  if (scenarios.peak) {
    selScen = lab("Scenario").append("select").on("change", ev => store.set({ scenario: ev.target.value }));
    // Short names in the dropdown; the full scenario_label from the file is shown in the summary line.
    selScen.append("option").attr("value", "live").attr("title", live.scenario_label ?? "").text("Current (live) allocation");
    selScen.append("option").attr("value", "peak").attr("title", scenarios.peak.scenario_label ?? "").text("Peak season (replay)");
  }
  const resources = [...new Set((live.plan ?? []).map(p => p.resource))];
  const selRes = lab("Resource").append("select").on("change", ev => store.set({ resource: ev.target.value }));
  (resources.length ? resources : Object.keys(RES)).forEach(r => selRes.append("option").attr("value", r).text(RES[r] ?? r));
  const bLab = lab("Budget").append("input").attr("type", "range").attr("min", 0).attr("max", live.budget_levels.length - 1)
    .attr("step", 1).on("input", ev => store.set({ budget: act().budget_levels[+ev.target.value] }));
  const bTxt = bar.append("span").attr("class", "val");
  const aLab = lab("Equity floor α").append("input").attr("type", "range").attr("min", 0).attr("max", live.alpha_values.length - 1)
    .attr("step", 1).on("input", ev => store.set({ alpha: act().alpha_values[+ev.target.value] }));
  const aTxt = bar.append("span").attr("class", "val");
  const summary = root.append("div").attr("class", "panel-note");
  const wrap = root.append("div").attr("class", "alloc-wrap");
  const svg1 = wrap.append("svg").attr("class", "ts bars").attr("role", "img")
    .attr("aria-label", "Grouped bars of forecast demand, stock and allocation by district");
  const svg2 = wrap.append("svg").attr("class", "ts curve").attr("role", "img")
    .attr("aria-label", "Efficiency-equity curve: weighted unmet demand against coverage Gini");
  root.append("div").attr("class", "panel-note").style("margin-top", "6px").html(
    `Equity floor α: every district receives at least α × its population share of each resource (α = 0 is pure efficiency; higher α spreads supplies more evenly). ` +
    `Bars show the ${TOP_N} districts with the highest demand, plus the selected district. Demand and stock come from the recommended plan; allocations follow the budget and α controls. ` +
    `Demand is modelled for dengue and diarrhoeal supplies only (ILI and malaria have no unit costs yet).`);

  // ---------- bars ----------
  const W1 = 640, H1 = 540, ML = 128, MR = 16, MT = 44, MB = 40;
  function drawBars(s, key, a) {
    const I = ix(key);
    svg1.attr("viewBox", `0 0 ${W1} ${H1}`); svg1.selectAll("*").remove();
    const plan = I.plan.filter(p => p.resource === s.resource).sort((p, q) => q.demand - p.demand);
    if (!plan.length) {
      svg1.append("text").attr("x", W1 / 2).attr("y", H1 / 2).attr("text-anchor", "middle").attr("class", "muted")
        .text("No plan rows for this resource.");
      return;
    }
    const top = plan.slice(0, TOP_N);
    if (s.district && !top.some(p => p.district_code === s.district)) {
      const sel = plan.find(p => p.district_code === s.district);
      if (sel) top[top.length - 1] = sel;
    }
    const calc = p => {
      const o = alloc(key, "optimised", s.budget, s.alpha, p.district_code, s.resource);
      const b = alloc(key, "population_proportional", s.budget, null, p.district_code, s.resource);
      return { code: p.district_code, demand: p.demand, stock: p.stock,
               opt: o ? o.allocated_units : null, optUnmet: o ? o.unmet_demand : null,
               base: b ? b.allocated_units : null, baseUnmet: b ? b.unmet_demand : null };
    };
    let items;
    if (s.role === "district") {
      // PRV-3: own district in detail, every other district only as its division's aggregate.
      const own = plan.find(p => p.district_code === s.roleDistrict);
      const groups = d3.group(plan.filter(p => p.district_code !== s.roleDistrict),
        p => data.districtInfo.get(p.district_code)?.division ?? "Unknown");
      const sumN = (rs, k) => (rs.some(r => r[k] === null || r[k] === undefined) ? null : d3.sum(rs, r => r[k]));
      const agg = [...groups].map(([division, ps]) => {
        const rs = ps.map(calc);
        return { code: "agg:" + division, aggregate: true, division, label: `${division} · others`,
                 demand: d3.sum(rs, r => r.demand), stock: d3.sum(rs, r => r.stock),
                 opt: sumN(rs, "opt"), optUnmet: sumN(rs, "optUnmet"), base: sumN(rs, "base"), baseUnmet: sumN(rs, "baseUnmet") };
      }).sort((p, q) => q.demand - p.demand);
      items = [...(own ? [calc(own)] : []), ...agg];
    } else {
      items = top.map(calc);
    }
    const xmax = d3.max(items, d => d3.max([d.demand, d.stock, d.opt, d.base])) || 1;
    const x = d3.scaleLinear().domain([0, xmax]).nice().range([ML, W1 - MR]);
    const y = d3.scaleBand(items.map(d => d.code), [MT, H1 - MB]).paddingInner(0.18);
    const yi = d3.scaleBand(KEYS, [0, y.bandwidth()]).paddingInner(0.08);

    svg1.append("g").attr("transform", `translate(0,${H1 - MB})`).call(d3.axisBottom(x).ticks(6));
    svg1.append("text").attr("x", (ML + W1 - MR) / 2).attr("y", H1 - 6).attr("text-anchor", "middle")
      .text(`${RES[s.resource] ?? s.resource}`);
    KEYS.forEach((k, i) => {
      const g = svg1.append("g").attr("transform", `translate(${ML + i * 135 - 90},16)`);
      g.append("rect").attr("width", 11).attr("height", 11).attr("fill", COLORS[k]);
      g.append("text").attr("x", 15).attr("y", 10).attr("font-size", 10).text(LABELS[k]);
    });

    const tip = d => {
      const info = d.aggregate ? null : data.districtInfo.get(d.code);
      return `<b>${d.label ?? info?.district_name ?? d.code}</b>${info ? " (" + info.division + ")" : ""}<br>` +
        `Forecast demand: ${fmt(d.demand)}<br>Current stock: ${fmt(d.stock)}<br>` +
        `Optimised allocation: ${fmt(d.opt)} (unmet ${fmt(d.optUnmet)})<br>` +
        `Population-proportional: ${fmt(d.base)} (unmet ${fmt(d.baseUnmet)})`;
    };
    items.forEach(d => {
      const g = svg1.append("g").attr("transform", `translate(0,${y(d.code)})`).style("cursor", "pointer")
        .on("click", () => { if (!d.aggregate) store.set({ district: store.get().district === d.code ? null : d.code }); })
        .on("mousemove", ev => tooltip.show(ev, tip(d))).on("mouseleave", () => tooltip.hide());
      g.append("rect").attr("x", 2).attr("width", W1 - 4).attr("height", y.bandwidth())
        .attr("fill", d.code === s.district ? "var(--accent)" : "transparent").attr("fill-opacity", 0.18);
      g.append("text").attr("x", ML - 6).attr("y", y.bandwidth() / 2 + 4).attr("text-anchor", "end")
        .attr("font-weight", d.code === s.district ? 700 : 400).text(d.label ?? data.districtInfo.get(d.code)?.district_name ?? d.code);
      KEYS.forEach(k => {
        if (d[k] === null || d[k] === undefined) return;
        g.append("rect").attr("x", ML).attr("y", yi(k)).attr("height", yi.bandwidth())
          .attr("width", Math.max(0, x(d[k]) - ML)).attr("fill", COLORS[k]);
      });
    });
  }

  // ---------- curve ----------
  const W2 = 440, H2 = 440, MLc = 56, MRc = 34, MTc = 34, MBc = 52;
  function drawCurve(s, a) {
    svg2.attr("viewBox", `0 0 ${W2} ${H2}`); svg2.selectAll("*").remove();
    const cv = a.curve ?? [];
    const opt = cv.filter(c => c.strategy === "optimised");
    const base = cv.filter(c => c.strategy === "population_proportional");
    if (!opt.length) { svg2.append("text").attr("x", W2 / 2).attr("y", H2 / 2).attr("text-anchor", "middle").attr("class", "muted").text("No curve data."); return; }
    const ge = d3.extent([...opt, ...base], c => c.equity_gini);
    const x = d3.scaleLinear().domain([Math.max(0, ge[0] - 0.03), ge[1] + 0.03]).nice().range([MLc, W2 - MRc]);
    const y = d3.scaleLinear().domain([0, d3.max(cv, c => c.total_unmet) * 1.06]).nice().range([H2 - MBc, MTc]);
    const n = a.budget_levels.length;
    const col = b => d3.interpolateViridis(0.12 + 0.8 * a.budget_levels.indexOf(b) / Math.max(1, n - 1));

    svg2.append("g").attr("transform", `translate(0,${H2 - MBc})`).call(d3.axisBottom(x).ticks(5));
    svg2.append("g").attr("transform", `translate(${MLc},0)`).call(d3.axisLeft(y).ticks(5));
    svg2.append("text").attr("x", (MLc + W2 - MRc) / 2).attr("y", H2 - 10).attr("text-anchor", "middle")
      .text("Coverage Gini (0 = every district equally covered)");
    svg2.append("text").attr("transform", `translate(13,${(MTc + H2 - MBc) / 2}) rotate(-90)`).attr("text-anchor", "middle")
      .text("Weighted unmet demand");
    svg2.append("text").attr("x", MLc).attr("y", 16).attr("font-weight", 600).text("Efficiency vs. equity (lower-left is better)");

    const line = d3.line().x(c => x(c.equity_gini)).y(c => y(c.total_unmet));
    const tipO = c => `<b>Optimised</b><br>budget ${c.budget_level}% of full need<br>equity floor α = ${c.alpha}<br>weighted unmet ${fmt(c.total_unmet)}<br>coverage Gini ${c.equity_gini}`;
    a.budget_levels.forEach(b => {
      const pts = opt.filter(c => c.budget_level === b).sort((p, q) => p.alpha - q.alpha);
      const sel = b === s.budget;
      svg2.append("path").attr("d", line(pts)).attr("fill", "none").attr("stroke", col(b))
        .attr("stroke-width", sel ? 3 : 1.4).attr("stroke-opacity", sel ? 1 : 0.65);
      svg2.selectAll(null).data(pts).join("circle").attr("cx", c => x(c.equity_gini)).attr("cy", c => y(c.total_unmet))
        .attr("r", 3.5).attr("fill", col(b)).style("cursor", "pointer")
        .on("click", (_, c) => store.set({ budget: c.budget_level, alpha: c.alpha }))
        .on("mousemove", (ev, c) => tooltip.show(ev, tipO(c))).on("mouseleave", () => tooltip.hide());
      const last = pts.at(-1);
      if (last) svg2.append("text").attr("x", x(last.equity_gini) + 6).attr("y", y(last.total_unmet) + 3)
        .attr("font-size", 10).attr("fill", col(b)).text(`${b}%`);
      const bp = base.find(c => c.budget_level === b);
      if (bp) svg2.append("path").attr("transform", `translate(${x(bp.equity_gini)},${y(bp.total_unmet)})`)
        .attr("d", d3.symbol(d3.symbolDiamond, 70)()).attr("fill", "none").attr("stroke", col(b)).attr("stroke-width", 2)
        .on("mousemove", ev => tooltip.show(ev, `<b>Population-proportional baseline</b><br>budget ${bp.budget_level}% of full need<br>weighted unmet ${fmt(bp.total_unmet)}<br>coverage Gini ${bp.equity_gini}`))
        .on("mouseleave", () => tooltip.hide());
    });
    const cur = opt.find(c => c.budget_level === s.budget && c.alpha === s.alpha);
    if (cur) svg2.append("circle").attr("cx", x(cur.equity_gini)).attr("cy", y(cur.total_unmet)).attr("r", 8)
      .attr("fill", "none").attr("stroke", "currentColor").attr("stroke-width", 2).attr("pointer-events", "none");
    const note = svg2.append("g").attr("transform", `translate(${W2 - 150},${MTc + 6})`);
    note.append("path").attr("d", d3.symbol(d3.symbolDiamond, 60)()).attr("fill", "none").attr("stroke", "currentColor").attr("stroke-width", 2);
    note.append("text").attr("x", 10).attr("y", 4).attr("font-size", 10).text("proportional baseline");
    note.append("circle").attr("cy", 16).attr("r", 4).attr("fill", "currentColor");
    note.append("text").attr("x", 10).attr("y", 20).attr("font-size", 10).text("optimised (click to set)");
  }

  // ---------- render ----------
  function render(s) {
    const key = scenarios[s.scenario] ? s.scenario : "live";
    const a = scenarios[key];
    // If this scenario's grid lacks the chosen budget/alpha, snap to the nearest available one first.
    const sb = nearest(a.budget_levels, s.budget), sa = nearest(a.alpha_values, s.alpha);
    if (sb !== s.budget || sa !== s.alpha) { store.set({ budget: sb, alpha: sa }); return; }
    if (selScen) selScen.property("value", key);
    selRes.property("value", s.resource);
    bLab.attr("max", a.budget_levels.length - 1).property("value", a.budget_levels.indexOf(s.budget));
    aLab.attr("max", a.alpha_values.length - 1).property("value", a.alpha_values.indexOf(s.alpha));
    bTxt.text(`${s.budget}% of full need`);
    aTxt.text(`α = ${s.alpha}`);

    const rec = a.recommendation;
    const o = a.curve?.find(c => c.strategy === "optimised" && c.budget_level === s.budget && c.alpha === s.alpha);
    const p = a.curve?.find(c => c.strategy === "population_proportional" && c.budget_level === s.budget);
    let txt = `${a.scenario_label ?? "Allocation"} · as of ${a.as_of ?? "n/a"} (latest stock snapshot). `;
    if (o && p) txt += `At ${s.budget}% budget and α = ${s.alpha}: weighted unmet demand ${fmt(o.total_unmet)} (optimised) vs ${fmt(p.total_unmet)} (population-proportional). `;
    if (rec && rec.recommended_alpha !== undefined) txt += `Recommended: α = ${rec.recommended_alpha} at ${Math.round((rec.budget_frac ?? 0) * 100)}% of full need.`;
    summary.text(txt);

    drawBars(s, key, a);
    drawCurve(s, a);
  }

  store.subscribe(render);
  render(store.get());
}

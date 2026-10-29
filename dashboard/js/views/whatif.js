// VIS-5 what-if panel: sliders for budget, equity floor (alpha) and detection threshold.
// Allocation numbers come from the precomputed grid in allocation.json (nothing is re-optimised in the browser).
// Alerts are re-thresholded client-side from the exported scores (see ../alerts.js).
// The sliders write to the shared store, so the map, table, time series and allocation view update together.
import { baseThreshold, isAlert } from "../alerts.js";
const f1 = d3.format(",.1f");
const sign = v => (v > 0 ? "+" : v < 0 ? "−" : "±") + f1(Math.abs(v));

export function mountWhatIf(el, data, store) {
  const root = d3.select(el);
  root.append("h2").attr("class", "panel-title").text("What-if: budget, equity floor and detection threshold");
  const scen = { live: data.allocation, peak: data.allocation_peak ?? null };
  const nearest = (arr, v) => arr.reduce((b, x) => (Math.abs(x - v) < Math.abs(b - v) ? x : b));
  const active = () => scen[store.get().scenario] ?? scen.live;

  const bar = root.append("div").attr("class", "controls-row");
  const lab = t => { const l = bar.append("label"); l.append("span").text(t + " "); return l; };
  const slider = l => l.append("input").attr("type", "range").attr("step", 1);
  const bIn = slider(lab("Budget")).on("input", ev => { const a = active(); if (a) store.set({ budget: a.budget_levels[+ev.target.value] }); });
  const bTxt = bar.append("span").attr("class", "val");
  const aIn = slider(lab("Equity floor α")).on("input", ev => { const a = active(); if (a) store.set({ alpha: a.alpha_values[+ev.target.value] }); });
  const aTxt = bar.append("span").attr("class", "val");
  const tIn = slider(lab("Detection threshold")).on("input", ev => store.set({ threshold: +ev.target.value }));
  const tTxt = bar.append("span").attr("class", "val").style("min-width", "150px");
  bar.append("button").attr("class", "link").text("Reset to recommended").on("click", () => {
    const a = active(), rec = a?.recommendation;
    store.set({
      threshold: null,
      ...(a ? { budget: nearest(a.budget_levels, (rec?.budget_frac ?? 0.5) * 100), alpha: nearest(a.alpha_values, rec?.recommended_alpha ?? 0) } : {}),
    });
  });
  const out = root.append("div").attr("class", "whatif-out");
  root.append("div").attr("class", "panel-note").style("margin-top", "6px").text(
    "Allocation figures come from a precomputed grid of optimiser runs, so only the grid values can be chosen. " +
    "The detection threshold re-filters the exported alert scores in the browser: it can go down to about half the default, " +
    "because the export keeps only scores above that. Severity labels are the pipeline's and are not recalculated.");

  function render(s) {
    const key = scen[s.scenario] ? s.scenario : "live", a = scen[key];

    // allocation sliders
    if (a) {
      const b = nearest(a.budget_levels, s.budget), al = nearest(a.alpha_values, s.alpha);
      bIn.attr("min", 0).attr("max", a.budget_levels.length - 1).property("value", a.budget_levels.indexOf(b)).attr("disabled", null);
      aIn.attr("min", 0).attr("max", a.alpha_values.length - 1).property("value", a.alpha_values.indexOf(al)).attr("disabled", null);
      bTxt.text(`${b}% of full need`); aTxt.text(`α = ${al}`);
    } else { bIn.attr("disabled", true); aIn.attr("disabled", true); bTxt.text("n/a"); aTxt.text("n/a"); }

    // threshold slider (per disease)
    const base = baseThreshold(data, s.disease);
    if (base != null) {
      const lo = Math.ceil((base / 2) * 4) / 4, hi = base * 2;
      tIn.attr("min", lo).attr("max", hi).attr("step", 0.25).property("value", s.threshold ?? base).attr("disabled", null);
      tTxt.text(`${(s.threshold ?? base).toFixed(2)} (default ${base})`);
    } else { tIn.attr("disabled", true); tTxt.text("no alert data"); }

    // result cards
    const cards = [];
    if (a?.curve) {
      const b = nearest(a.budget_levels, s.budget), al = nearest(a.alpha_values, s.alpha);
      const o = a.curve.find(c => c.strategy === "optimised" && c.budget_level === b && c.alpha === al);
      const p = a.curve.find(c => c.strategy === "population_proportional" && c.budget_level === b);
      const rec = a.recommendation;
      const rc = rec ? a.curve.find(c => c.strategy === "optimised" && c.budget_level === nearest(a.budget_levels, (rec.budget_frac ?? 0.5) * 100)
        && c.alpha === nearest(a.alpha_values, rec.recommended_alpha ?? 0)) : null;
      if (o && p) {
        const red = p.total_unmet > 0 ? ((p.total_unmet - o.total_unmet) / p.total_unmet) * 100 : 0;
        cards.push(`<div class="wcard"><h3>Allocation (${b}% budget, α = ${al})</h3>` +
          `<p class="big">${f1(o.total_unmet)} <small>weighted unmet demand</small></p>` +
          `<p>Population-proportional baseline: ${f1(p.total_unmet)} → optimised is ${red.toFixed(1)}% lower.</p>` +
          `<p>Coverage Gini ${o.equity_gini} (0 = every district equally covered)` +
          (rc && rc !== o ? `<br>Versus the recommended setting: unmet ${sign(o.total_unmet - rc.total_unmet)}, Gini ${sign(o.equity_gini - rc.equity_gini)}` : "") +
          `</p></div>`);
      }
    }
    if (base != null) {
      const rows = (data.alerts?.rows ?? []).filter(r => r.disease === s.disease);
      const wk = data.weeks[s.week], last52 = new Set(data.weeks.slice(-52));
      const count = st => ({
        week: rows.filter(r => r.week_start === wk && isAlert(data, r, st)).length,
        year: rows.filter(r => last52.has(r.week_start) && isAlert(data, r, st)).length,
      });
      const cur = count(s), def = count({ ...s, threshold: null });
      cards.push(`<div class="wcard"><h3>Alerts (${s.disease}, threshold ${(s.threshold ?? base).toFixed(2)})</h3>` +
        `<p class="big">${cur.week} <small>district${cur.week === 1 ? "" : "s"} alerting in the week of ${wk}</small></p>` +
        `<p>Default threshold: ${def.week}. Alert-weeks over the last 52 weeks: ${cur.year} (default ${def.year}).</p>` +
        `<p>A lower threshold warns earlier but raises false alarms (alert fatigue); a higher one does the opposite.</p></div>`);
    }
    out.html(cards.join("") || `<div class="panel-note">No allocation or alert data to explore.</div>`);
  }

  store.subscribe(render);
  render(store.get());
}

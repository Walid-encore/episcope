// DQ-2: per-load data-quality report shown on the dashboard.
// Left: one row per load (batch): rows in, accepted, quarantined. Click a row to see its rules.
// Right: rule pass rates for the selected load, worst first. Failing share is shown as a bar AND a number.
// Source: dq_report.json -> batches[{batch_id, rows_in, rows_out, rows_quarantined, rules[{rule_id, reason_code, pass_rate}]}]
const SOURCES = {
  facilities: "Facility registry", districts: "Districts (reference)", stock: "Stock and bed occupancy",
  weather: "Weather", encounters: "Encounters",
};
const fmtN = d3.format(",");
const pct = (v, d = 2) => `${(v * 100).toFixed(d)}%`;

export function mountDq(el, data) {
  const root = d3.select(el);
  root.append("h2").attr("class", "panel-title").text("Data quality (per load)");
  const dq = data.dq_report;
  if (!dq || !dq.batches?.length) {
    root.append("div").attr("class", "panel-note").text("dq_report.json not found or empty.");
    return;
  }
  const batches = dq.batches.map(b => ({ ...b, source: (b.rules?.[0]?.rule_id ?? "").split(":")[0] || "unknown" }));
  const sum = k => d3.sum(batches, b => b[k]);
  let selected = (batches.filter(b => b.rows_quarantined > 0).sort((a, b) => b.rows_in - a.rows_in)[0] ?? batches[0]).batch_id;

  root.append("div").attr("class", "panel-note").text(
    `${batches.length} loads · ${fmtN(sum("rows_in"))} rows in · ${fmtN(sum("rows_out"))} accepted · ` +
    `${fmtN(sum("rows_quarantined"))} quarantined (${pct(sum("rows_quarantined") / Math.max(1, sum("rows_in")))}). ` +
    `Rows that fail a rule are quarantined with a reason code and are not used in any chart. Report generated ${dq.generated_at}.`);

  const wrap = root.append("div").attr("class", "alloc-wrap");
  const left = wrap.append("div").style("flex", "3 1 420px").append("div").attr("class", "table-wrap").style("max-height", "none");
  const right = wrap.append("div").style("flex", "2 1 320px");

  const t1 = left.append("table").attr("class", "warn");
  t1.append("thead").append("tr").selectAll("th").data(["Source", "Load", "Rows in", "Accepted", "Quarantined"]).join("th").text(d => d);
  const body1 = t1.append("tbody");
  const rulesHead = right.append("div").attr("class", "panel-note");
  const t2 = right.append("div").attr("class", "table-wrap").append("table").attr("class", "warn");
  t2.append("thead").append("tr").selectAll("th").data(["Rule (reason code)", "Pass rate", "Failing share"]).join("th").text(d => d);
  const body2 = t2.append("tbody");

  function render() {
    body1.selectAll("tr").data(batches, b => b.batch_id).join("tr")
      .attr("tabindex", 0)
      .classed("selected", b => b.batch_id === selected)
      .attr("aria-selected", b => b.batch_id === selected)
      .on("click", (_, b) => { selected = b.batch_id; render(); })
      .on("keydown", (ev, b) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); selected = b.batch_id; render(); } })
      .html(b => `<td>${SOURCES[b.source] ?? b.source}</td><td>${b.batch_id}</td><td>${fmtN(b.rows_in)}</td>` +
        `<td>${fmtN(b.rows_out)}</td><td>${fmtN(b.rows_quarantined)} (${pct(b.rows_quarantined / Math.max(1, b.rows_in))})</td>`);

    const b = batches.find(x => x.batch_id === selected);
    const rules = (b.rules ?? []).slice().sort((p, q) => p.pass_rate - q.pass_rate || p.reason_code.localeCompare(q.reason_code));
    const maxFail = d3.max(rules, r => 1 - r.pass_rate) || 0;
    rulesHead.text(`${SOURCES[b.source] ?? b.source} · load ${b.batch_id} · ${rules.length} rules, worst first`);
    body2.selectAll("tr").data(rules, r => r.rule_id).join("tr")
      .html(r => {
        const fail = 1 - r.pass_rate;
        const w = maxFail > 0 ? (fail / maxFail) * 100 : 0;
        return `<td>${r.reason_code}</td><td>${pct(r.pass_rate)}</td>` +
          `<td><span class="dqbar"><span style="width:${w}%"></span></span> ${pct(fail)}</td>`;
      });
  }
  render();
}

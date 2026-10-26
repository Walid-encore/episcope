// View 5 (VIS-2, VIS-3): ranked early-warning table.
// Shows alerts.json rows for the selected disease and week, sorted by severity, then score.
// Clicking a row (or pressing Enter on it) selects that district in the shared state.
const SEV_ORDER = { high: 0, medium: 1, low: 2 };
const SEV_MARK = { high: "▲▲▲", medium: "▲▲", low: "▲" };   // shape + text, never colour alone

export function mountWarnings(el, data, store) {
  const root = d3.select(el);
  root.append("h2").attr("class", "panel-title").text("Early-warning ranking");
  const note = root.append("div").attr("class", "panel-note");
  const table = root.append("div").attr("class", "table-wrap").append("table").attr("class", "warn");

  const cols = ["#", "District", "Division", "Severity", "Score / threshold", "Status", "Cases this week"];
  table.append("thead").append("tr").selectAll("th").data(cols).join("th")
    .attr("scope", "col").text(d => d);
  const tbody = table.append("tbody");

  const select = code => store.set({ district: store.get().district === code ? null : code });

  function render(s) {
    const week = data.weeks[s.week];
    const rows = (data.alerts?.rows ?? [])
      .filter(a => a.disease === s.disease && a.week_start === week)
      .sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9) || b.score - a.score);

    note.text(`${s.disease} · week of ${week} · ${rows.length} district${rows.length === 1 ? "" : "s"} ` +
      `on the list (alerts, plus watch-list districts above half the threshold)`);

    if (!rows.length) {
      tbody.selectAll("tr").data([null]).join("tr").attr("class", null).attr("tabindex", null)
        .on("click", null).on("keydown", null)
        .html(`<td colspan="${cols.length}" class="empty">No alerts or watch-list districts for this week.</td>`);
      return;
    }

    tbody.selectAll("tr").data(rows, a => a.district_code).join("tr")
      .attr("tabindex", 0)
      .classed("selected", a => a.district_code === s.district)
      .attr("aria-selected", a => a.district_code === s.district)
      .on("click", (_, a) => select(a.district_code))
      .on("keydown", (ev, a) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(a.district_code); } })
      .html((a, i) => {
        const info = data.districtInfo.get(a.district_code);
        const c = data.cases.get(`${s.disease}|${week}|${a.district_code}`);
        const cases = !c ? "no data" : c.suppressed ? "suppressed (&lt;5)" : c.cases;
        const status = (a.alert_flag ? "Alert" : "Watch") +
          (a.reporting_artefact_flag ? " · possible reporting artefact" : "");
        return `<td>${i + 1}</td><td>${info ? info.district_name : a.district_code}</td>` +
          `<td>${info ? info.division : ""}</td>` +
          `<td><span class="sev sev-${a.severity}">${SEV_MARK[a.severity] ?? ""}</span> ${a.severity}</td>` +
          `<td>${a.score} / ${a.threshold}</td><td>${status}</td><td>${cases}</td>`;
      });
  }

  store.subscribe(render);
  render(store.get());
}

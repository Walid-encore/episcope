// View 5 (VIS-2, VIS-3): ranked early-warning table.
// Shows alerts.json rows for the selected disease and week, sorted by severity, then score.
// Clicking a row (or pressing Enter on it) selects that district in the shared state.
import { isAlert, effectiveThreshold } from "../alerts.js";
const SEV_ORDER = { high: 0, medium: 1, low: 2 };
const SEV_MARK = { high: "▲▲▲", medium: "▲▲", low: "▲" };   // shape + text, never colour alone

export function mountWarnings(el, data, store) {
  const root = d3.select(el);
  root.append("h2").attr("class", "panel-title").text("Early-warning ranking");
  const note = root.append("div").attr("class", "panel-note");
  const table = root.append("div").attr("class", "table-wrap").append("table").attr("class", "warn");
  root.append("div").attr("class", "panel-note").style("margin-top", "6px").text(
    "Alerts need at least 15 cases in the week. District alerts and the national total can differ during a national surge. " +
    "Rows marked 'below alert threshold' are watch-list weeks or weeks without enough history.");

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

    // PRV-3: in the district role, show the own district in detail and the others only as division aggregates.
    let list = rows;
    if (s.role === "district") {
      const own = rows.filter(a => a.district_code === s.roleDistrict);
      const byDiv = d3.group(rows.filter(a => a.district_code !== s.roleDistrict),
        a => data.districtInfo.get(a.district_code)?.division ?? "Unknown");
      const agg = [...byDiv].map(([division, rs]) => ({
        aggregate: true, key: "agg:" + division, division, n: rs.length, nAlert: rs.filter(a => isAlert(data, a, s)).length,
        severity: rs.map(a => a.severity).filter(Boolean).sort((p, q) => SEV_ORDER[p] - SEV_ORDER[q])[0] ?? null,
      })).sort((p, q) => (SEV_ORDER[p.severity] ?? 9) - (SEV_ORDER[q.severity] ?? 9) || q.nAlert - p.nAlert);
      list = [...own, ...agg];
    }

    note.text(s.role === "district"
      ? `${s.disease} · week of ${week} · own district in detail; other districts only as division aggregates`
      : `${s.disease} · week of ${week} · ${rows.length} district${rows.length === 1 ? "" : "s"} ` +
        `on the list (alerts, plus watch-list districts above half the threshold)`);

    if (!list.length) {
      const latest = (data.alerts?.rows ?? []).filter(a => a.disease === s.disease && isAlert(data, a, s))
        .map(a => a.week_start).sort().at(-1);
      const idx = latest ? data.weeks.indexOf(latest) : -1;
      tbody.selectAll("tr").data([null]).join("tr").attr("class", null).attr("tabindex", null).attr("aria-selected", null)
        .on("click", null).on("keydown", null)
        .html(`<td colspan="${cols.length}" class="empty">No alerts or watch-list districts for this week.` +
          (idx >= 0 ? ` <button class="link" id="jumpAlert">Go to the latest week with an alert (${latest})</button>` : "") + `</td>`);
      tbody.select("#jumpAlert").on("click", () => {
        const r = store.get().range;
        store.set({ week: idx, range: [Math.min(r[0], idx), Math.max(r[1], idx)] });
      });
      return;
    }

    // The key function also runs on existing rows; the empty-state row carries null data, so guard it.
    tbody.selectAll("tr").data(list, a => (a ? (a.aggregate ? a.key : a.district_code) : "__empty__")).join("tr")
      .attr("tabindex", 0)
      .classed("selected", a => !a.aggregate && a.district_code === s.district)
      .attr("aria-selected", a => !a.aggregate && a.district_code === s.district)
      .on("click", (_, a) => { if (!a.aggregate) select(a.district_code); })
      .on("keydown", (ev, a) => { if (!a.aggregate && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); select(a.district_code); } })
      .html((a, i) => {
        if (a.aggregate) {
          return `<td>${i + 1}</td><td>Other districts</td><td>${a.division}</td>` +
            `<td><span class="sev sev-${a.severity ?? "none"}">${SEV_MARK[a.severity] ?? ""}</span> ${a.severity ?? "—"} (highest)</td>` +
            `<td>—</td><td>${a.n} on the list, ${a.nAlert} alert${a.nAlert === 1 ? "" : "s"} (aggregate)</td><td>—</td>`;
        }
        const info = data.districtInfo.get(a.district_code);
        const c = data.cases.get(`${s.disease}|${week}|${a.district_code}`);
        const cases = !c ? "no data" : c.suppressed ? "suppressed (&lt;5)" : c.cases;
        const status = (isAlert(data, a, s) ? "Alert" : "Below alert threshold") +
          (a.reporting_artefact_flag ? " · possible reporting artefact" : "");
        return `<td>${i + 1}</td><td>${info ? info.district_name : a.district_code}</td>` +
          `<td>${info ? info.division : ""}</td>` +
          `<td><span class="sev sev-${a.severity ?? "none"}">${SEV_MARK[a.severity] ?? ""}</span> ${a.severity ?? "—"}</td>` +
          `<td>${a.score} / ${effectiveThreshold(data, a.disease, s)}</td><td>${status}</td><td>${cases}</td>`;
      });
  }

  store.subscribe(render);
  render(store.get());
}

import { loadAll, BASE } from "./data.js";
import { createStore } from "./state.js";
import { mountChoropleth } from "./views/choropleth.js";
import { mountTimeSeries } from "./views/timeseries.js";
import { mountWarnings } from "./views/warnings.js";
import { mountAllocation } from "./views/allocation.js";
import { mountSeasonal } from "./views/seasonal.js";
import { mountDq } from "./views/dq.js";

const tip = document.getElementById("tooltip");
const tooltip = {
  show(ev, html) {
    tip.innerHTML = html; tip.style.opacity = 1;
    tip.style.left = ev.clientX + 14 + "px"; tip.style.top = ev.clientY + 14 + "px";
  },
  hide() { tip.style.opacity = 0; },
};

try {
  const data = await loadAll();
  const n = data.weeks.length;

  // Freshness (VIS-6): from freshness.json, never the browser clock. Shown in header and under every panel.
  const fr = data.freshness;
  const freshText = `Data as of ${fr.data_as_of} · generated ${fr.generated_at} · ${fr.pipeline_version}`;
  document.getElementById("freshness").textContent = `${freshText} (${BASE})`;
  // The allocation view has its own date (latest stock snapshot), so it gets its own caption.
  const A = data.allocation;
  document.querySelectorAll(".panel").forEach(p => {
    const text = p.id === "allocation" && A
      ? `Allocation as of ${A.as_of ?? "n/a"} (latest stock snapshot) · generated ${A.generated_at} · other views: data as of ${fr.data_as_of}`
      : freshText;
    p.insertAdjacentHTML("beforeend", `<div class="fresh">${text}</div>`);
  });
  const nearest = (arr, v) => arr.reduce((b, x) => (Math.abs(x - v) < Math.abs(b - v) ? x : b));

  const store = createStore({
    district: null,
    disease: data.meta.diseases[0],
    week: n - 1,                 // index into data.weeks; default = latest
    range: [0, n - 1],           // [first, last] week index, set by the brush
    metric: "incidence",
    // allocation view (VIS-5 what-if groundwork): defaults to the recommended scenario
    scenario: "live",
    resource: A ? ([...new Set((A.plan ?? []).map(p => p.resource))][0] ?? "iv_saline_units") : null,
    budget: A ? nearest(A.budget_levels, (A.recommendation?.budget_frac ?? 0.5) * 100) : null,
    alpha: A ? nearest(A.alpha_values, A.recommendation?.recommended_alpha ?? 0) : null,
  });

  const dSel = document.getElementById("disease");
  data.meta.diseases.forEach(d => dSel.add(new Option(d, d)));
  dSel.onchange = () => store.set({ disease: dSel.value });

  const wInp = document.getElementById("week");
  wInp.oninput = () => store.set({ week: +wInp.value });

  const sel = document.getElementById("selection");
  store.subscribe(s => {
    wInp.min = s.range[0]; wInp.max = s.range[1]; wInp.value = s.week;   // slider follows the brush
    document.getElementById("weekLabel").textContent = `week of ${data.weeks[s.week]}`;
    const info = data.districtInfo.get(s.district);
    sel.innerHTML = info ? `Selected: <b>${info.district_name}</b> <button class="link" id="clr">clear</button>` : "";
    if (info) document.getElementById("clr").onclick = () => store.set({ district: null });
  });

  mountChoropleth(document.getElementById("choropleth"), data, store, tooltip);
  mountTimeSeries(document.getElementById("timeseries"), data, store, tooltip);
  mountWarnings(document.getElementById("warnings"), data, store);
  mountSeasonal(document.getElementById("seasonal"), data, store, tooltip);
  if (A) mountAllocation(document.getElementById("allocation"), data, store, tooltip);
  mountDq(document.getElementById("dq"), data);
  if (!A) document.getElementById("allocation").insertAdjacentHTML("afterbegin", "<p class='panel-note'>allocation.json not found in " + BASE + "</p>");
  store.set({}); // initial notify so labels render
} catch (e) {
  const err = document.getElementById("error");
  err.hidden = false; err.textContent = "Could not load data: " + e.message;
  console.error(e);
}

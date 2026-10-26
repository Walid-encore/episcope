import { loadAll, BASE } from "./data.js";
import { createStore } from "./state.js";
import { mountChoropleth } from "./views/choropleth.js";
import { mountTimeSeries } from "./views/timeseries.js";
import { mountWarnings } from "./views/warnings.js";

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
  document.querySelectorAll(".panel").forEach(p =>
    p.insertAdjacentHTML("beforeend", `<div class="fresh">${freshText}</div>`));

  const store = createStore({
    district: null,
    disease: data.meta.diseases[0],
    week: n - 1,                 // index into data.weeks; default = latest
    range: [0, n - 1],           // [first, last] week index, set by the brush
    metric: "incidence",
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
  store.set({}); // initial notify so labels render
} catch (e) {
  const err = document.getElementById("error");
  err.hidden = false; err.textContent = "Could not load data: " + e.message;
  console.error(e);
}

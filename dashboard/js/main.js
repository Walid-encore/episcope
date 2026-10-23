import { loadAll, BASE } from "./data.js";
import { createStore } from "./state.js";
import { mountChoropleth } from "./views/choropleth.js";

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

  // Freshness badge (VIS-6): from freshness.json, never the browser clock.
  const fr = data.freshness;
  document.getElementById("freshness").textContent =
    `Data as of ${fr.data_as_of} · generated ${fr.generated_at} · ${fr.pipeline_version} (${BASE})`;

  const store = createStore({
    district: null,
    disease: data.meta.diseases[0],
    week: data.weeks.length - 1,      // index into data.weeks; default = latest
    metric: "incidence",
  });

  // Controls
  const dSel = document.getElementById("disease");
  data.meta.diseases.forEach(d => dSel.add(new Option(d, d)));
  dSel.onchange = () => store.set({ disease: dSel.value });

  const wInp = document.getElementById("week");
  wInp.max = data.weeks.length - 1; wInp.value = store.get().week;
  wInp.oninput = () => store.set({ week: +wInp.value });

  const sel = document.getElementById("selection");
  store.subscribe(s => {
    document.getElementById("weekLabel").textContent = `week of ${data.weeks[s.week]}`;
    const info = data.districtInfo.get(s.district);
    sel.innerHTML = info ? `Selected: <b>${info.district_name}</b> <button class="link" id="clr">clear</button>` : "";
    if (info) document.getElementById("clr").onclick = () => store.set({ district: null });
  });

  mountChoropleth(document.getElementById("choropleth"), data, store, tooltip);
  store.set({}); // initial notify so labels render
} catch (e) {
  const err = document.getElementById("error");
  err.hidden = false; err.textContent = "Could not load data: " + e.message;
  console.error(e);
}

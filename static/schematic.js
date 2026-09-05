"use strict";
// 2D schematic, Rotax 915 iS: FLAT-FOUR BOXER, plan view. Crank axis runs
// fore-aft; two jugs oppose left, two right. Drawn anatomically rather than
// as an upright inline-four, for the same reason the four jugs share one
// tint: the picture must not imply hardware the build does not have.
const SCHEM_SVG = `
<svg id="schem" viewBox="0 0 600 306" preserveAspectRatio="xMidYMid meet">
 <defs><linearGradient id="sc" x1="0" x2="1">
  <stop offset="0" stop-color="#3fb950"/>
  <stop offset=".5" stop-color="#e3b341"/>
  <stop offset="1" stop-color="#f85149"/></linearGradient></defs>

 <path id="z-jacket" d="M150 58 H450 V242 H150 Z" fill="none"
   stroke="#30363d" stroke-width="3"/>
 <text x="155" y="52" class="sl">COOLANT JACKET</text>
 <text id="t-cool" x="450" y="52" class="sv" text-anchor="end">--</text>

 <rect id="z-intake" x="215" y="14" width="170" height="15" rx="2"
   fill="#1f2937" stroke="#30363d"/>
 <path d="M300 29 V58" stroke="#30363d" stroke-width="2" fill="none"/>
 <text x="220" y="11" class="sl">INTAKE / FUEL PATH</text>
 <text id="t-fuel" x="385" y="11" class="sv" text-anchor="end">--</text>

 <rect x="270" y="58" width="60" height="184" fill="#0e1524" stroke="#30363d"/>
 <circle id="z-hub" cx="300" cy="176" r="17" fill="none" stroke="#8b949e"
   stroke-width="2"/>
 <text id="t-rpm" x="300" y="180" class="sv" text-anchor="middle">--</text>
 <text x="300" y="238" class="sl" text-anchor="middle">CRANK (no residual)</text>

 <rect id="z-jug-1" x="182" y="74" width="88" height="42" rx="3"
   fill="#1f2937" stroke="#30363d"/>
 <rect id="z-jug-3" x="182" y="184" width="88" height="42" rx="3"
   fill="#1f2937" stroke="#30363d"/>
 <rect id="z-jug-2" x="330" y="74" width="88" height="42" rx="3"
   fill="#1f2937" stroke="#30363d"/>
 <rect id="z-jug-4" x="330" y="184" width="88" height="42" rx="3"
   fill="#1f2937" stroke="#30363d"/>
 <rect x="166" y="68" width="16" height="54" fill="#111827" stroke="#30363d"/>
 <rect x="166" y="178" width="16" height="54" fill="#111827" stroke="#30363d"/>
 <rect x="418" y="68" width="16" height="54" fill="#111827" stroke="#30363d"/>
 <rect x="418" y="178" width="16" height="54" fill="#111827" stroke="#30363d"/>
 <text x="226" y="99" class="sn" text-anchor="middle">1</text>
 <text x="226" y="209" class="sn" text-anchor="middle">3</text>
 <text x="374" y="99" class="sn" text-anchor="middle">2</text>
 <text x="374" y="209" class="sn" text-anchor="middle">4</text>

 <path id="z-exh-1" d="M174 122 C 174 200 300 244 454 252" fill="none"
   stroke="#30363d" stroke-width="2"/>
 <path id="z-exh-3" d="M174 232 C 210 258 330 262 454 252" fill="none"
   stroke="#30363d" stroke-width="2"/>
 <path id="z-exh-2" d="M426 122 C 462 176 474 214 474 236" fill="none"
   stroke="#30363d" stroke-width="2"/>
 <path id="z-exh-4" d="M426 232 C 452 242 470 244 474 236" fill="none"
   stroke="#30363d" stroke-width="2"/>
 <circle cx="474" cy="252" r="15" fill="#0e1524" stroke="#30363d"/>
 <text x="496" y="256" class="sl">TURBO</text>
 <text x="300" y="134" class="sl" text-anchor="middle">EGT MEAN (all 4) - ONE SENSOR</text>
 <text id="t-egt" x="300" y="146" class="sv" text-anchor="middle">--</text>

 <rect id="z-sump" x="248" y="252" width="104" height="26" rx="2"
   fill="#1f2937" stroke="#30363d"/>
 <text x="300" y="250" class="sl" text-anchor="middle">OIL SUMP</text>
 <text id="t-oil" x="300" y="270" class="sv" text-anchor="middle">--</text>
 <path id="z-oilline" d="M248 265 H160 V176 H270" fill="none"
   stroke="#30363d" stroke-width="2"/>
 <text x="96" y="252" class="sl">OIL PRESS</text>
 <text id="t-oilp" x="96" y="264" class="sv">--</text>

 <g class="ph-svg">
  <circle cx="174" cy="95" r="6" class="phc"/><circle cx="174" cy="205" r="6" class="phc"/>
  <circle cx="426" cy="95" r="6" class="phc"/><circle cx="426" cy="205" r="6" class="phc"/>
  <text x="196" y="60" class="pht">CHT PROBES - PLANNED</text>
  <circle cx="222" cy="168" r="5" class="phc"/><circle cx="238" cy="252" r="5" class="phc"/>
  <circle cx="452" cy="176" r="5" class="phc"/><circle cx="452" cy="240" r="5" class="phc"/>
  <text x="440" y="296" class="pht" text-anchor="middle">EGT 1-4 - PLANNED</text>
  <rect x="276" y="200" width="48" height="15" class="phr"/>
  <text x="300" y="211" class="pht" text-anchor="middle">VIB</text>
 </g>

 <rect x="180" y="290" width="110" height="6" fill="url(#sc)"/>
 <text x="20" y="296" class="sl">RESIDUAL MAGNITUDE (display scale, not a limit)</text>

 <g id="sch-veil" style="display:none">
  <rect x="0" y="0" width="600" height="300" fill="#0a0e1a" opacity=".82"/>
  <text id="sch-veil-t" x="300" y="146" class="veil" text-anchor="middle">NO CLAIM</text>
  <text id="sch-veil-r" x="300" y="164" class="sl" text-anchor="middle"></text>
 </g>
</svg>`;

// Tint by RESIDUAL magnitude (measured vs physics), never by raw temperature:
// a hot day is not a fault. Five channels carry residuals; rpm does not, so
// the crank hub shows its number without colour.
const SCH_SPAN = { EGT_mean_C: 120, coolant_temp_C: 20, oil_temperature_C: 20,
                   oil_pressure_bar: 1.0, fuelflow_kgh: 4.0 };

function schColour(res, span) {
  if (typeof res !== "number" || !isFinite(res)) return "#30363d";
  const t = Math.max(0, Math.min(1, Math.abs(res) / span));
  if (t < 0.5) return "#3fb950";
  if (t < 0.85) return "#e3b341";
  return "#f85149";
}

function paintSchematic(f) {
  const host = document.getElementById("p-schem");
  if (!host) return;
  if (!host.querySelector("#schem")) host.innerHTML = SCHEM_SVG;
  const g = (id) => document.getElementById(id);
  const res = f.residuals || {}, x = f.features || {};

  const cEgt  = schColour(res.EGT_mean_C, SCH_SPAN.EGT_mean_C);
  const cCool = schColour(res.coolant_temp_C, SCH_SPAN.coolant_temp_C);
  const cOilT = schColour(res.oil_temperature_C, SCH_SPAN.oil_temperature_C);
  const cOilP = schColour(res.oil_pressure_bar, SCH_SPAN.oil_pressure_bar);
  const cFuel = schColour(res.fuelflow_kgh, SCH_SPAN.fuelflow_kgh);

  // All four jugs take ONE colour: there is one EGT sensor, not four.
  [1, 2, 3, 4].forEach((n) => {
    const j = g("z-jug-" + n), e = g("z-exh-" + n);
    if (j) { j.setAttribute("stroke", cEgt); j.setAttribute("fill", cEgt + "22"); }
    if (e) e.setAttribute("stroke", cEgt);
  });
  const set = (id, col) => { const n = g(id); if (n) n.setAttribute("stroke", col); };
  set("z-jacket", cCool);
  set("z-sump", cOilT);
  set("z-oilline", cOilP);
  set("z-intake", cFuel);

  const sig = (k) => {
    const m = x[k], e = (f.expected || {})[k];
    if (typeof m !== "number" || typeof e !== "number") return "--";
    const d = m - e;
    return m.toFixed(1) + "  " + (d > 0 ? "+" : "") + d.toFixed(1);
  };
  const put = (id, s) => { const n = g(id); if (n) n.textContent = s; };
  put("t-egt", sig("EGT_mean_C"));
  put("t-cool", sig("coolant_temp_C"));
  put("t-oil", sig("oil_temperature_C"));
  put("t-oilp", sig("oil_pressure_bar"));
  put("t-fuel", sig("fuelflow_kgh"));
  put("t-rpm", typeof x.rpm === "number" ? x.rpm.toFixed(0) : "--");

  // Refused frame: the physics baseline is extrapolated, so the whole picture
  // makes no claim. Grey it and say why rather than showing a stale verdict.
  const refused = f.refusal_class !== null && f.refusal_class !== undefined;
  const veil = g("sch-veil");
  if (veil) {
    veil.style.display = refused ? "block" : "none";
    if (refused) {
      const why = f.admit_reason ||
        (f.envelope_violations || []).join("; ") || String(f.refusal_class);
      put("sch-veil-t", "NO CLAIM");
      put("sch-veil-r", why.length > 76 ? why.slice(0, 73) + "..." : why);
    }
  }
  const svg = g("schem");
  if (svg) svg.classList.toggle("greyed", refused);
}

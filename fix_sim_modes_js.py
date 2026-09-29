import io, shutil
P = r"static\sim.js"
shutil.copy2(P, P + ".bak_modes")
block = """

// ---- simulator mode chooser (fault / condition / mission) ----------------
(function () {
  var MODES = {
    fault: ["FAULT SIMULATOR",
      "you choose the failure; the engine shows how it behaves under it"],
    condition: ["CONDITION SIMULATOR",
      "no fault is injected; wear and failures emerge from the conditions"],
    mission: ["MISSION SIMULATOR",
      "full surveillance sortie -- not built yet"]
  };
  function el(id) { return document.getElementById(id); }

  function showFleetAge() {
    var sel = el("sm-eng"), out = el("sm-age-now");
    if (!sel || !out || !sel.value) return;
    fetch("/sim/fleet").then(function (r) { return r.json(); }).then(function (j) {
      var list = Array.isArray(j) ? j : (j.fleet || j.engines || j.items || []);
      for (var i = 0; i < list.length; i++) {
        var e = list[i];
        if (e.serial !== sel.value) continue;
        out.textContent = e.serial + " -- " + e.hours.toFixed(0) + " h" +
          "  oil " + e.oil_pump_health.toFixed(3) +
          "  coolant " + e.coolant_pump_health.toFixed(3) +
          "  bearing " + e.bearing_wear.toFixed(3);
        return;
      }
    }).catch(function () { out.textContent = "fleet unavailable"; });
  }

  function enter(mode) {
    var m = MODES[mode] || MODES.fault;
    el("sm-choose").style.display = "none";
    el("sm-workspace").style.display = "";
    el("sm-mode-title").textContent = m[0];
    el("sm-mode-sub").textContent = m[1];
    var fault = el("sm-card-fault"), age = el("sm-card-age");
    if (mode === "condition") {
      if (fault) fault.style.display = "none";
      if (age) age.style.display = "";
      var f = el("sm-fault");            // sim.js still reads this
      if (f) f.value = "none";
      showFleetAge();
    } else {
      if (fault) fault.style.display = "";
      if (age) age.style.display = "none";
    }
    if (mode === "mission" && typeof smLog === "function")
      smLog("mission simulator is not built yet -- running as a plain sortie");
  }

  document.addEventListener("click", function (ev) {
    var box = ev.target.closest && ev.target.closest(".sm-box");
    if (box) { enter(box.getAttribute("data-mode")); return; }
    if (ev.target.id === "sm-back") {
      el("sm-workspace").style.display = "none";
      el("sm-choose").style.display = "";
    }
  });
  document.addEventListener("change", function (ev) {
    if (ev.target.id === "sm-eng") showFleetAge();
  });
})();
"""
io.open(P, "a", encoding="utf-8", newline="\n").write(block)
print("appended mode chooser to %s" % P)

import io, shutil
P = r"static\sim.js"
shutil.copy2(P, P + ".bak_agebtn")
block = """

// ---- pre-age controls on the condition simulator ------------------------
(function () {
  function el(id) { return document.getElementById(id); }
  function log(msg) { var o = el("sm-age-log"); if (o) o.textContent = msg; }
  function refresh() {
    document.getElementById("sm-eng")
      .dispatchEvent(new Event("change", { bubbles: true }));
  }

  document.addEventListener("click", function (ev) {
    var serial = (el("sm-eng") || {}).value;
    if (!serial) return;

    if (ev.target.id === "sm-age-go") {
      var h = parseFloat((el("sm-agehrs") || {}).value || "0");
      if (!(h > 0)) { log("enter a positive number of hours"); return; }
      log("aging " + serial + " by " + h + " h ...");
      fetch("/sim/age?serial=" + encodeURIComponent(serial) + "&hours=" + h,
            { method: "POST" })
        .then(function (r) { return r.json().then(function (j) {
          return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok) { log("refused: " + (res.j.detail || "error")); return; }
          var b = res.j.before, a = res.j.after;
          log("+" + h + " h  oil " + b.oil_pump_health.toFixed(4) + " -> " +
              a.oil_pump_health.toFixed(4) + "   bearing " +
              b.bearing_wear.toFixed(4) + " -> " + a.bearing_wear.toFixed(4));
          refresh();
        })
        .catch(function (e) { log("failed: " + e); });
    }

    if (ev.target.id === "sm-age-reset") {
      log("restoring " + serial + " to factory ...");
      fetch("/sim/age/reset?serial=" + encodeURIComponent(serial),
            { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function () { log("back to factory"); refresh(); })
        .catch(function (e) { log("failed: " + e); });
    }
  });
})();
"""
io.open(P, "a", encoding="utf-8", newline="\n").write(block)
print("appended age handlers to %s" % P)

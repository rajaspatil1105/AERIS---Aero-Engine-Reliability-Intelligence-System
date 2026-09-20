function smSpeed() {
  var s = document.getElementById('sm-map-speed');
  return s ? Number(s.value) : 0;
}
(function () {
  function el(id) { return document.getElementById(id); }
  var poll = null;
  function watch(sid, out, t0) {
    if (poll) clearInterval(poll);
    poll = setInterval(function () {
      fetch("/sim/run/" + sid).then(function (r) { return r.json(); })
        .then(function (s) {
          var secs = ((Date.now() - t0) / 1000).toFixed(0);
          if (!s.done) {
            out.textContent = "flying session " + sid + " -- " + s.frames +
              " frames, " + s.faults + " faults, " + secs + " s elapsed";
            return;
          }
          clearInterval(poll); poll = null;
          out.textContent = "session " + sid + " finished: " + s.frames +
            " frames, " + s.faults + " faults in " + secs + " s" +
            (s.error ? " -- ERROR: " + s.error : "") +
            ". switch to TACTICAL or REPLAY to see it.";
        })
        .catch(function () { clearInterval(poll); poll = null; });
    }, 1000);
  }
  window.smFlyIt = function (plan) {
    var out = el("sm-plan-out");
    if (!plan || !plan.setpoints || plan.setpoints.length < 2) {
      out.textContent = "plan a sortie first"; return;
    }
    var body = {
      engine_serial: (el("sm-eng") || {}).value || "RTX915-0003",
      setpoints: plan.setpoints.map(function (s) {
        return {t_s: s.t_s, throttle_pct: s.throttle_pct,
                altitude_ft: s.altitude_ft, oat_c: s.oat_c};
      }),
      stress_enabled: true, emit_cruise_s: 300.0,
      emit_event_s: 1.0, speed: smSpeed()
    };
    out.textContent = "starting " + plan.total_h + " h sortie -- " +
      plan.setpoints.length + " setpoints, this takes a minute or two";
    el("sm-map-fly").disabled = true;
    fetch("/sim/run", {method: "POST",
                       headers: {"Content-Type": "application/json"},
                       body: JSON.stringify(body)})
      .then(function (r) {
        return r.json().then(function (j) { return {s: r.status, j: j}; }); })
      .then(function (o) {
        el("sm-map-fly").disabled = false;
        if (o.s !== 201 && o.s !== 200) {
          out.textContent = "run refused (" + o.s + "): " +
            (typeof o.j.detail === "string" ? o.j.detail
                                            : JSON.stringify(o.j.detail || o.j));
          return;
        }
        var sid = o.j.session_id || o.j.sid || o.j.id;
        out.textContent = "session " + sid + " started";
        watch(sid, out, Date.now());
      })
      .catch(function (e) {
        el("sm-map-fly").disabled = false;
        out.textContent = "run failed: " + e;
      });
  };
  document.addEventListener("click", function (ev) {
    if (ev.target.id === "sm-map-fly") window.smFlyIt(window.smLastPlan);
  });
})();

// SM_FLY_ENABLER
setInterval(function () {
  var b = document.getElementById('sm-map-fly');
  if (b) b.disabled = !window.smLastPlan;
}, 500);

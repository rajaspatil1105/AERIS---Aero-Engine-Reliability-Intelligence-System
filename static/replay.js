"use strict";
// AERIS REPLAY -- steps stored frames through the same panels as /live.
// /frames?session_id=N&limit=1000 is the thin projection, newest first.
// /frames/{seq}?session_id=N is that row plus a nested `frame` object holding
// the full stored response, which is the same shape /live returns.
var RP = { on:false, frozen:false, ses:null, rows:[], i:-1,
           playing:false, timer:null, cache:{} };

function $r(id) { return document.getElementById(id); }

function rpTickClass(row) {
  if (row.status === "HEALTHY") return "ok";
  if (row.status === "FAULT") return "bad";
  if (row.status === "UNAVAILABLE")
    return row.refusal_class === "transient" ? "wait" : "void";
  return "";
}

function rpStrip() {
  var h = "", i, row;
  for (i = 0; i < RP.rows.length; i++) {
    row = RP.rows[i];
    h += '<b class="' + rpTickClass(row) + '" data-i="' + i +
         '" title="seq ' + row.seq + "  " + row.status +
         (row.refusal_class ? "  " + row.refusal_class : "") +
         (row.p_anom === null || row.p_anom === undefined
            ? "" : "  p=" + Number(row.p_anom).toFixed(3)) +
         '"></b>';
  }
  $r("rp-strip").innerHTML = h;
}

function rpMark() {
  var t = $r("rp-strip").children, i;
  for (i = 0; i < t.length; i++) {
    if (i === RP.i) t[i].classList.add("cur");
    else t[i].classList.remove("cur");
  }
}

async function rpDetail(row) {
  var key = RP.ses + "/" + row.seq;
  if (RP.cache[key]) return RP.cache[key];
  var r = await fetch("/frames/" + row.seq + "?session_id=" + RP.ses,
                      { cache:"no-store" });
  if (!r.ok) throw new Error("detail HTTP " + r.status);
  var d = await r.json();
  RP.cache[key] = d;
  return d;
}

async function rpGoto(i) {
  if (!RP.rows.length) return;
  RP.i = Math.max(0, Math.min(RP.rows.length - 1, i));
  var row = RP.rows[RP.i];
  $r("rp-pos").textContent = (RP.i + 1) + " / " + RP.rows.length;
  $r("rp-scrub").value = String(RP.i);
  rpMark();
    var hs = $r("h-ses"); if (hs) hs.textContent = String(RP.ses);

  try {
    var d = await rpDetail(row);
    render(d.frame || d);
    // wall clock from the stored row, not the nested epoch float
    $r("rp-stamp").textContent =
      "seq " + row.seq + "  " + String(row.ts_utc || "--").replace("T", " ");
  } catch (e) {
    $r("rp-stamp").textContent = "detail failed: " + e.message;
  }
}

async function rpLoad(sid) {
  RP.ses = sid;
  RP.cache = {};
  var r = await fetch("/frames?session_id=" + sid + "&limit=1000",
                      { cache:"no-store" });
  var d = await r.json();
  RP.rows = (d.frames || []).slice().reverse();   // newest-first -> chronological
  $r("rp-scrub").max = String(Math.max(0, RP.rows.length - 1));
  rpStrip();
  if (!RP.rows.length) {
    $r("rp-pos").textContent = "0 / 0";
    $r("rp-stamp").textContent = "session " + sid + " has no frames";
    return;
  }
  await rpGoto(RP.rows.length - 1);
}

function rpPlay(on) {
  RP.playing = on;
  $r("rp-play").innerHTML = on ? "&#10074;&#10074;" : "&#9654;";
  if (RP.timer) { clearInterval(RP.timer); RP.timer = null; }
  if (!on) return;
  RP.timer = setInterval(function () {
    if (RP.i >= RP.rows.length - 1) { rpPlay(false); return; }
    rpGoto(RP.i + 1);
  }, 400);
}

async function rpEnter() {
  RP.on = true;
  RP.frozen = true;                    // stops the 500 ms /live poll
  $r("rp-bar").style.display = "flex";
    document.body.classList.add("replay");
  var hm = $r("h-mode"); if (hm) hm.textContent = "REPLAY";

  var r = await fetch("/sessions", { cache:"no-store" });
  var d = await r.json();
  var list = d.sessions || [], h = "", i, s;
  for (i = 0; i < list.length; i++) {
    s = list[i];
    h += '<option value="' + s.id + '">' + s.id + "  " +
         String(s.started_utc).slice(0, 19).replace("T", " ") +
         (s.ended_utc ? "" : "  (open)") + "</option>";
  }
  $r("rp-ses").innerHTML = h;
  if (list.length) await rpLoad(list[0].id);
}

function rpExit() {
  rpPlay(false);
  RP.on = false;
  RP.frozen = false;
  $r("rp-bar").style.display = "none";
    document.body.classList.remove("replay");
  var hm = $r("h-mode"); if (hm) hm.textContent = "LIVE";

  if (typeof tick === "function") tick();
}

(function rpWire() {
  $r("rp-first").onclick = function () { rpPlay(false); rpGoto(0); };
  $r("rp-prev").onclick  = function () { rpPlay(false); rpGoto(RP.i - 1); };
  $r("rp-next").onclick  = function () { rpPlay(false); rpGoto(RP.i + 1); };
  $r("rp-last").onclick  = function () {
    rpPlay(false); rpGoto(RP.rows.length - 1);
  };
  $r("rp-play").onclick  = function () { rpPlay(!RP.playing); };
  $r("rp-scrub").oninput = function () {
    rpPlay(false); rpGoto(parseInt(this.value, 10));
  };
  $r("rp-ses").onchange  = function () {
    rpPlay(false); rpLoad(parseInt(this.value, 10));
  };
  $r("rp-strip").onclick = function (e) {
    var i = e.target && e.target.getAttribute
          ? e.target.getAttribute("data-i") : null;
    if (i !== null) { rpPlay(false); rpGoto(parseInt(i, 10)); }
  };
  var tabs = document.querySelectorAll(".tab"), i, txt;
  for (i = 0; i < tabs.length; i++) {
    txt = tabs[i].textContent || "";
    if (txt.indexOf("REPLAY") >= 0) {
      tabs[i].disabled = false;
      tabs[i].classList.remove("ph");
      tabs[i].onclick = function () { if (RP.on) rpExit(); else rpEnter(); };
    } else if (txt.indexOf("TACTICAL") >= 0) {
      tabs[i].onclick = function () { if (RP.on) rpExit(); };
    }
  }
  document.addEventListener("keydown", function (e) {
    if (!RP.on) return;
    if (e.key === "ArrowLeft")  { rpPlay(false); rpGoto(RP.i - 1); }
    if (e.key === "ArrowRight") { rpPlay(false); rpGoto(RP.i + 1); }
    if (e.key === " ") { e.preventDefault(); rpPlay(!RP.playing); }
  });
})();

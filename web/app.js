/* DubStage web player - lets phones join a room, claim a character and
 * record their lines in the browser. Talks to the same relay as the desktop
 * app; takes are uploaded as 16-bit / 44.1 kHz mono WAV, exactly what the
 * desktop host assembles. No build step, no dependencies. */
(function () {
  "use strict";

  // ------------------------------------------------------------ i18n
  var DE = (navigator.language || "").toLowerCase().indexOf("de") === 0;
  var T = {
    title: ["DubStage", "DubStage"],
    room: ["Raum %s", "Room %s"],
    your_name: ["Dein Name", "Your name"],
    join: ["Beitreten", "Join"],
    joining: ["Trete bei ...", "Joining ..."],
    need_name: ["Bitte einen Namen eingeben.", "Please enter a name."],
    no_room: ["Diesen Raum gibt es nicht (mehr). Ist der Host noch online?",
              "This room doesn't exist (any more). Is the host still online?"],
    bad_link: ["Der Link ist unvollstaendig - frag den Host nach einem neuen.",
               "This link is incomplete - ask the host for a new one."],
    full: ["Der Raum ist voll.", "The room is full."],
    characters: ["Figuren", "Characters"],
    tap_claim: ["Tippe eine Figur an, um sie zu uebernehmen.",
                "Tap a character to take it."],
    open: ["frei", "open"],
    you: ["du", "you"],
    host: ["Host", "Host"],
    players: ["Spieler", "Players"],
    none_yet: ["noch keine Figur", "no character yet"],
    record_mine: ["●  Meine Zeilen aufnehmen (%s)", "●  Record my lines (%s)"],
    pick_first: ["Erst eine Figur waehlen", "Pick a character first"],
    leave: ["Raum verlassen", "Leave room"],
    leave_q: ["Raum wirklich verlassen? Deine Figur wird frei.",
              "Leave the room? Your character will be freed."],
    left: ["Du hast den Raum verlassen.", "You left the room."],
    rejoin: ["Wieder beitreten", "Rejoin"],
    closed: ["Der Host hat den Raum beendet.", "The host closed the room."],
    lost: ["Verbindung weg - versuche es weiter ...",
           "Connection lost - retrying ..."],
    line_of: ["Zeile %s / %s", "Line %s / %s"],
    hear: ["▶  Original", "▶  Original"],
    rec: ["●  Aufnehmen", "●  Record"],
    stop: ["■  Stopp", "■  Stop"],
    mine_play: ["▶  Meine Aufnahme", "▶  My take"],
    back: ["‹ Zurueck", "‹ Back"],
    next: ["Weiter ›", "Next ›"],
    done: ["Fertig → Lobby", "Done → Lobby"],
    to_lobby: ["‹ Lobby", "‹ Lobby"],
    go: ["Los!", "Go!"],
    recording: ["● REC", "● REC"],
    st_none: ["noch nicht aufgenommen", "not recorded yet"],
    st_up: ["wird hochgeladen ...", "uploading ..."],
    st_ok: ["✓ gespeichert", "✓ saved"],
    st_bad: ["✗ Hochladen fehlgeschlagen", "✗ upload failed"],
    retry: ["Erneut senden", "Retry upload"],
    music: ["Musik beim Aufnehmen abspielen (mit Kopfhoerern)",
            "Play the music while recording (use headphones)"],
    loading_music: ["Musik wird geladen ...", "Loading music ..."],
    mic_denied: ["Kein Mikrofon-Zugriff. Erlaube das Mikrofon fuer diese Seite " +
                 "in den Browser-Einstellungen und lade neu.",
                 "No microphone access. Allow the microphone for this site in " +
                 "your browser settings, then reload."],
    mic_none: ["Dieser Browser kann hier nicht aufnehmen (HTTPS noetig).",
               "This browser can't record here (HTTPS is required)."],
    no_video: ["Dieses Video kann dein Handy nicht abspielen - du kannst trotzdem " +
               "aufnehmen.", "Your phone can't play this video - you can still " +
               "record."],
    hint_tap: ["Erst ▶ Original anhoeren, dann aufnehmen. Beliebig oft.",
               "Hear the original first, then record. As often as you like."],
    no_lines: ["Deine Figur hat keine Zeilen.", "Your character has no lines."],
    reconnected: ["Wieder verbunden.", "Reconnected."]
  };
  function t(k) {
    var s = (T[k] || [k, k])[DE ? 0 : 1];
    for (var i = 1; i < arguments.length; i++) s = s.replace("%s", arguments[i]);
    return s;
  }

  // ------------------------------------------------------------ helpers
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function $(sel) { return document.querySelector(sel); }
  function store(k, v) {
    try {
      if (v === undefined) return localStorage.getItem(k);
      if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v);
    } catch (e) { /* private mode etc. */ }
    return null;
  }
  function randHex(n) {
    var a = new Uint8Array(n);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (b) {
      return ("0" + b.toString(16)).slice(-2);
    }).join("");
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  var m = location.pathname.match(/^(.*)\/r\/([A-Za-z0-9]+)\/?$/);
  var CODE = m ? m[2].toUpperCase() : "";
  var BASE = location.origin + (m ? m[1] : "");
  var API = BASE + "/rooms/" + CODE;
  var TARGET_SR = 44100;           // what the desktop host renders at
  var TAIL = 0.7;                  // same recording tail as the desktop app

  var S = {
    pid: null, token: null, name: "",
    info: null, state: null, poll: null, lost: false,
    screen: "join", mine: [], idx: 0,
    status: {},                    // clip -> "up" | "ok" | "bad"
    takes: {},                     // clip -> Float32Array @ TARGET_SR
    phase: "idle"                  // idle | countdown | record | play
  };

  function deviceKey() {
    var k = store("ds_key");
    if (!k) { k = randHex(16); store("ds_key", k); }
    return k;
  }

  function api(method, path, body, ctype) {
    var h = {};
    if (S.token) h["X-DS-Token"] = S.token;
    var data = body;
    if (body != null && !(body instanceof Blob)) {
      h["Content-Type"] = "application/json";
      data = JSON.stringify(body);
    }
    if (ctype) h["Content-Type"] = ctype;
    return fetch(API + path, { method: method, headers: h, body: data,
                               cache: "no-store" })
      .then(function (r) {
        return r.json().catch(function () { return null; }).then(function (j) {
          if (!r.ok) {
            var e = new Error((j && j.error) || ("HTTP " + r.status));
            e.status = r.status;
            throw e;
          }
          return j;
        });
      });
  }

  // ------------------------------------------------------------ audio
  var AC = null, micStream = null, capNode = null, sinkGain = null;
  var capturing = false, chunks = [], meterEl = null;

  function audioCtx() {
    if (!AC) {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      AC = new Ctx();
    }
    if (AC.state === "suspended") AC.resume();
    return AC;
  }

  var WORKLET = "class R extends AudioWorkletProcessor{process(i){var c=i[0]&&i[0][0];" +
    "if(c)this.port.postMessage(c.slice(0));return true;}}registerProcessor('ds-rec',R);";

  function onChunk(f32) {
    if (!capturing) return;
    chunks.push(f32);
    if (meterEl) {
      var s = 0;
      for (var i = 0; i < f32.length; i += 8) s += f32[i] * f32[i];
      var rms = Math.sqrt(s / Math.max(1, f32.length / 8));
      meterEl.style.width = Math.min(100, rms * 400) + "%";
    }
  }

  function ensureMic() {
    if (capNode) return Promise.resolve();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return Promise.reject(new Error(t("mic_none")));
    }
    var ac = audioCtx();
    return navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true,
               autoGainControl: false, channelCount: 1 }
    }).catch(function () {
      throw new Error(t("mic_denied"));
    }).then(function (stream) {
      micStream = stream;
      var src = ac.createMediaStreamSource(stream);
      sinkGain = ac.createGain();
      sinkGain.gain.value = 0;               // keep the graph running, silently
      sinkGain.connect(ac.destination);
      var useWorklet = !!(ac.audioWorklet && window.AudioWorkletNode);
      var p = Promise.resolve(false);
      if (useWorklet) {
        var url = URL.createObjectURL(new Blob([WORKLET],
                                               { type: "application/javascript" }));
        p = ac.audioWorklet.addModule(url).then(function () {
          capNode = new AudioWorkletNode(ac, "ds-rec");
          capNode.port.onmessage = function (e) { onChunk(e.data); };
          return true;
        }).catch(function () { return false; });
      }
      return p.then(function (ok) {
        if (!ok) {                             // older Safari: ScriptProcessor
          capNode = ac.createScriptProcessor(4096, 1, 1);
          capNode.onaudioprocess = function (e) {
            onChunk(new Float32Array(e.inputBuffer.getChannelData(0)));
          };
        }
        src.connect(capNode);
        capNode.connect(sinkGain);
      });
    });
  }

  function joinChunks() {
    var n = 0, i;
    for (i = 0; i < chunks.length; i++) n += chunks[i].length;
    var out = new Float32Array(n), o = 0;
    for (i = 0; i < chunks.length; i++) { out.set(chunks[i], o); o += chunks[i].length; }
    return out;
  }

  function resample(f32, from, to) {
    if (from === to) return f32;
    var n = Math.round(f32.length * to / from);
    var out = new Float32Array(n), ratio = from / to;
    for (var i = 0; i < n; i++) {
      var x = i * ratio, a = Math.floor(x), f = x - a;
      var v0 = f32[a] || 0, v1 = f32[a + 1] !== undefined ? f32[a + 1] : v0;
      out[i] = v0 + (v1 - v0) * f;
    }
    return out;
  }

  function wavBlob(f32, sr) {
    var n = f32.length, buf = new ArrayBuffer(44 + n * 2), dv = new DataView(buf);
    function str(o, s) { for (var i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); }
    str(0, "RIFF"); dv.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
    str(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true);
    dv.setUint16(22, 1, true); dv.setUint32(24, sr, true);
    dv.setUint32(28, sr * 2, true); dv.setUint16(32, 2, true);
    dv.setUint16(34, 16, true); str(36, "data"); dv.setUint32(40, n * 2, true);
    for (var i = 0, o = 44; i < n; i++, o += 2) {
      var s = Math.max(-1, Math.min(1, f32[i]));
      dv.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  var backingBuf = null, backingLoading = null, playingSrc = null;

  function loadBacking() {
    if (backingBuf) return Promise.resolve(backingBuf);
    if (backingLoading) return backingLoading;
    var ac = audioCtx();
    backingLoading = fetch(API + "/files/" + encodeURIComponent(S.info.backing))
      .then(function (r) { return r.arrayBuffer(); })
      .then(function (ab) {
        return new Promise(function (res, rej) { ac.decodeAudioData(ab, res, rej); });
      })
      .then(function (b) { backingBuf = b; return b; })
      .catch(function () { backingLoading = null; return null; });
    return backingLoading;
  }

  function playBuffer(buf, offset, dur) {
    stopBuffer();
    var ac = audioCtx(), s = ac.createBufferSource();
    s.buffer = buf;
    s.connect(ac.destination);
    s.start(0, Math.max(0, offset || 0), dur || buf.duration);
    playingSrc = s;
  }
  function stopBuffer() {
    if (playingSrc) { try { playingSrc.stop(); } catch (e) { } playingSrc = null; }
  }

  // ------------------------------------------------------------ video
  var video = null, stopAt = null, onSegEnd = null, segTimer = null;

  function getVideo() {
    if (video) return video;
    video = document.createElement("video");
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "");
    video.preload = "auto";
    video.muted = true;
    if (S.info && S.info.video) {
      video.src = API + "/files/" + encodeURIComponent(S.info.video);
    }
    video.addEventListener("error", function () {
      var el = $("#vmsg");
      if (el) { el.textContent = t("no_video"); }
    });
    (function tick() {
      if (stopAt !== null && video.currentTime >= stopAt) endSegment();
      requestAnimationFrame(tick);
    })();
    return video;
  }

  function endSegment() {
    if (stopAt === null) return;
    stopAt = null;
    clearTimeout(segTimer);
    try { video.pause(); } catch (e) { }
    var cb = onSegEnd; onSegEnd = null;
    if (cb) cb();
  }

  /* Play [start, start+dur]. Called synchronously from a tap where sound
   * matters, so iOS allows unmuted playback. */
  function playSegment(start, dur, muted, done) {
    var v = getVideo();
    endSegment();
    v.muted = !!muted;
    try { v.currentTime = start; } catch (e) { }
    stopAt = start + dur;
    onSegEnd = done || null;
    var p = v.play();
    if (p && p.catch) p.catch(function () { /* still let the timer end it */ });
    segTimer = setTimeout(endSegment, (dur + 1.5) * 1000);
  }

  function showFrame(sec) {
    var v = getVideo();
    endSegment();
    try { v.pause(); v.currentTime = sec; } catch (e) { }
  }

  // ------------------------------------------------------------ session
  function startPolling() {
    stopPolling();
    var tick = function () {
      api("GET", "/state").then(function (st) {
        S.state = st;
        if (S.lost) { S.lost = false; }
        if (S.pid && !st.players.some(function (p) { return p.id === S.pid; })) {
          // we were pruned (screen locked too long) -> quietly rejoin
          return doJoin(true);
        }
        refresh();
      }).catch(function (e) {
        if (e.status === 404) { stopPolling(); return renderEnd(t("closed")); }
        S.lost = true;
        refresh();
      });
    };
    tick();
    S.poll = setInterval(tick, 1500);
  }
  function stopPolling() { if (S.poll) { clearInterval(S.poll); S.poll = null; } }

  function doJoin(silent) {
    return api("POST", "/join", { name: S.name, key: deviceKey() })
      .then(function (res) {
        S.pid = res.player_id;
        S.token = res.token;
        S.state = res.state;
        store("ds_name", S.name);
        store("ds_joined_" + CODE, "1");
        if (!S.info) return api("GET", "/info").then(function (i) { S.info = i; });
      })
      .then(function () {
        if (!silent) { renderLobby(); }
        if (!S.poll) startPolling();
      });
  }

  function myChars() {
    var a = (S.state && S.state.assignments) || {}, out = [];
    Object.keys(a).forEach(function (c) { if (a[c] === S.pid) out.push(c); });
    return out;
  }
  function myLines() {
    if (!S.info) return [];
    var mine = myChars();
    return S.info.lines.filter(function (l) { return mine.indexOf(l.character) >= 0; });
  }
  function recordedSet() {
    var s = {};
    ((S.state && S.state.recorded_clips) || []).forEach(function (c) { s[c] = 1; });
    Object.keys(S.status).forEach(function (c) { if (S.status[c] === "ok") s[c] = 1; });
    return s;
  }

  function refresh() {
    if (S.screen === "lobby") renderLobby();
    else if (S.screen === "record") updateRecordBits();
  }

  // ------------------------------------------------------------ screens
  var app = $("#app");

  function renderJoin(msg, isErr) {
    S.screen = "join";
    var name = store("ds_name") || "";
    app.innerHTML =
      '<div class="card">' +
      '<h1>' + esc(t("title")) + '</h1>' +
      '<p class="sub">' + esc(t("room", CODE || "?")) + '</p>' +
      '<label for="name">' + esc(t("your_name")) + '</label>' +
      '<input id="name" type="text" maxlength="40" autocomplete="nickname" value="' + esc(name) + '">' +
      '<div class="row"><button id="join" class="go">' + esc(t("join")) + '</button></div>' +
      '<p id="msg" class="msg' + (isErr ? " err" : "") + '">' + esc(msg || "") + '</p>' +
      '</div>';
    var go = function () {
      var n = ($("#name").value || "").trim();
      if (!n) { $("#msg").className = "msg err"; $("#msg").textContent = t("need_name"); return; }
      audioCtx();                       // unlock audio inside the tap
      getVideoUnlock();
      S.name = n;
      $("#join").disabled = true;
      $("#msg").className = "msg"; $("#msg").textContent = t("joining");
      joinWithRetry().catch(function (e) {
        $("#join").disabled = false;
        $("#msg").className = "msg err";
        $("#msg").textContent = e.status === 404 ? t("no_room")
          : e.status === 403 ? t("full") : e.message;
      });
    };
    $("#join").onclick = go;
    $("#name").onkeydown = function (e) { if (e.key === "Enter") go(); };
  }

  // a fresh host tunnel can take a moment to resolve; retry quietly
  function joinWithRetry() {
    var deadline = Date.now() + 60000;
    var attempt = function () {
      return doJoin(false).catch(function (e) {
        if (e.status || Date.now() > deadline) throw e;
        return sleep(3000).then(attempt);
      });
    };
    return attempt();
  }

  function getVideoUnlock() {
    // Touch the video element inside the tap so later (timer-driven,
    // muted) playback is allowed on iOS.
    if (!S.info) return;
    var v = getVideo();
    var p = v.play();
    if (p && p.then) p.then(function () { v.pause(); }).catch(function () { });
  }

  function renderLobby() {
    S.screen = "lobby";
    var st = S.state || {}, a = st.assignments || {};
    var pmap = {};
    (st.players || []).forEach(function (p) { pmap[p.id] = p; });
    var chars = (S.info && S.info.characters) || st.characters || [];
    var html = '<div class="topbar"><div><h1>' + esc(st.pack_name || t("title")) +
      '</h1><span class="code">' + esc(CODE) + '</span></div>' +
      '<button class="flat" id="leave">' + esc(t("leave")) + '</button></div>';
    if (S.lost) html += '<div class="banner">' + esc(t("lost")) + '</div>';

    html += '<h2>' + esc(t("characters")) + '</h2><p class="sub">' +
      esc(t("tap_claim")) + '</p><div class="chars">';
    chars.forEach(function (c, i) {
      var owner = a[c], mine = owner === S.pid;
      var who = !owner ? t("open") : ((pmap[owner] || {}).name || "?") +
        (mine ? " (" + t("you") + ")" : "");
      var cls = "char" + (!owner ? " open" : "") + (mine ? " mine" : "");
      var dis = owner && !mine ? " disabled" : "";
      html += '<button class="' + cls + '" data-i="' + i + '"' + dis + '><span>' +
        esc(c) + '</span><span class="who">' + esc(who) + '</span></button>';
    });
    html += '</div>';

    html += '<h2>' + esc(t("players")) + '</h2><div class="players">';
    (st.players || []).forEach(function (p) {
      var tags = [];
      if (p.is_host) tags.push(t("host"));
      if (p.id === S.pid) tags.push(t("you"));
      var f = p.assigned ? p.recorded / p.assigned : 0;
      html += '<div class="player"><div class="top"><span class="dot' +
        (p.online === false ? " away" : "") + '"></span><span class="name">' +
        esc(p.name) + '</span><span class="tag">' + esc(tags.join(" · ")) +
        '</span></div><div class="chs">' +
        esc((p.characters || []).join(", ") || t("none_yet")) + '</div>' +
        '<div class="bar"><i class="' + (p.assigned && p.recorded >= p.assigned ? "done" : "") +
        '" style="width:' + Math.round(f * 100) + '%"></i></div>' +
        '<div class="count">' + p.recorded + ' / ' + p.assigned + '</div></div>';
    });
    html += '</div>';

    var n = myLines().length;
    html += '<div class="row" style="margin-top:20px"><button id="rec" class="rec"' +
      (n ? "" : " disabled") + '>' + esc(n ? t("record_mine", n) : t("pick_first")) +
      '</button></div>';
    app.innerHTML = html;

    Array.prototype.forEach.call(app.querySelectorAll(".char"), function (b) {
      b.onclick = function () {
        var c = chars[+b.getAttribute("data-i")];
        var mine = a[c] === S.pid;
        b.disabled = true;
        api("POST", "/assign", { character: c, player_id: mine ? null : S.pid })
          .then(function (st2) { S.state = st2; renderLobby(); })
          .catch(function () { renderLobby(); });
      };
    });
    $("#rec").onclick = function () { audioCtx(); getVideoUnlock(); enterRecord(); };
    $("#leave").onclick = function () {
      if (!confirm(t("leave_q"))) return;
      stopPolling();
      api("POST", "/leave").catch(function () { });
      store("ds_joined_" + CODE, null);
      S.pid = S.token = null;
      renderEnd(t("left"), true);
    };
  }

  function renderEnd(msg, canRejoin) {
    S.screen = "end";
    stopBuffer();
    if (video) { try { video.pause(); } catch (e) { } }
    app.innerHTML = '<div class="card center"><h1>' + esc(t("title")) +
      '</h1><p class="sub">' + esc(msg) + '</p>' +
      (canRejoin ? '<div class="row"><button id="again" class="go">' +
        esc(t("rejoin")) + '</button></div>' : '') + '</div>';
    if (canRejoin) $("#again").onclick = function () { renderJoin(); };
  }

  // --- record screen ---------------------------------------------------
  function enterRecord() {
    S.mine = myLines();
    if (!S.mine.length) { alert(t("no_lines")); return; }
    var rec = recordedSet();
    S.idx = 0;
    for (var i = 0; i < S.mine.length; i++) { if (!rec[S.mine[i].file]) { S.idx = i; break; } }
    renderRecord();
  }

  function cur() { return S.mine[S.idx]; }

  function renderRecord() {
    S.screen = "record";
    var hasBacking = !!(S.info && S.info.backing);
    app.innerHTML =
      '<div class="topbar"><button class="flat" id="lobby">' + esc(t("to_lobby")) +
      '</button><span class="code">' + esc(CODE) + '</span></div>' +
      '<div class="stage" id="stage"><div class="overlay" id="ov"></div></div>' +
      '<p class="sub" id="vmsg" style="margin:6px 0 0"></p>' +
      '<div class="lineinfo"><span class="n" id="ln"></span><span class="st" id="st"></span></div>' +
      '<div class="caption" id="cap"></div>' +
      '<div class="meter"><i id="meter"></i></div>' +
      '<div class="row"><button id="hear">' + esc(t("hear")) + '</button>' +
      '<button id="mine">' + esc(t("mine_play")) + '</button></div>' +
      '<div class="row"><button id="rec" class="rec">' + esc(t("rec")) + '</button></div>' +
      '<div class="row" id="retryrow" style="display:none"><button id="retry">' +
      esc(t("retry")) + '</button></div>' +
      '<div class="row"><button id="prev">' + esc(t("back")) + '</button>' +
      '<button id="next" class="go">' + esc(t("next")) + '</button></div>' +
      (hasBacking ? '<label class="check"><input type="checkbox" id="music">' +
        esc(t("music")) + '</label>' : '') +
      '<div class="dots" id="dots"></div>' +
      '<p class="sub foot">' + esc(t("hint_tap")) + '</p>';
    var v = getVideo();
    $("#stage").insertBefore(v, $("#ov"));
    meterEl = $("#meter");

    $("#lobby").onclick = function () { abortAll(); meterEl = null; renderLobby(); };
    $("#hear").onclick = function () {
      if (S.phase !== "idle") return;
      var l = cur(); stopBuffer();
      S.phase = "play"; updateRecordBits();
      playSegment(l.start, l.dur, false, function () { S.phase = "idle"; updateRecordBits(); });
    };
    $("#mine").onclick = function () {
      if (S.phase !== "idle") return;
      var l = cur(), take = S.takes[l.file];
      if (!take) return;
      var ac = audioCtx(), buf = ac.createBuffer(1, take.length, TARGET_SR);
      buf.getChannelData(0).set(take);
      S.phase = "play"; updateRecordBits();
      playSegment(l.start, l.dur + TAIL, true, function () {
        stopBuffer(); S.phase = "idle"; updateRecordBits();
      });
      playBuffer(buf, 0);
    };
    $("#rec").onclick = function () {
      if (S.phase === "record") { finishRecord(); return; }
      if (S.phase !== "idle") return;
      audioCtx();
      startRecord();
    };
    $("#retry").onclick = function () { upload(cur().file); };
    $("#prev").onclick = function () { if (S.phase === "idle" && S.idx > 0) { S.idx--; showLine(); } };
    $("#next").onclick = function () {
      if (S.phase !== "idle") return;
      if (S.idx < S.mine.length - 1) { S.idx++; showLine(); }
      else { meterEl = null; renderLobby(); }
    };
    var mus = $("#music");
    if (mus) {
      mus.checked = store("ds_music") === "1";
      mus.onchange = function () {
        store("ds_music", mus.checked ? "1" : "0");
        if (mus.checked) { $("#vmsg").textContent = t("loading_music");
          loadBacking().then(function () { $("#vmsg").textContent = ""; }); }
      };
      if (mus.checked) loadBacking();
    }
    showLine();
  }

  function showLine() {
    var l = cur();
    showFrame(l.start);
    updateRecordBits();
  }

  function updateRecordBits() {
    if (S.screen !== "record" || !$("#ln")) return;
    var l = cur(), rec = recordedSet(), st = S.status[l.file];
    $("#ln").textContent = t("line_of", S.idx + 1, S.mine.length) + " · " + l.character;
    $("#cap").textContent = l.caption || "";
    var se = $("#st");
    if (st === "up") { se.textContent = t("st_up"); se.className = "st"; }
    else if (st === "bad") { se.textContent = t("st_bad"); se.className = "st bad"; }
    else if (st === "ok" || rec[l.file]) { se.textContent = t("st_ok"); se.className = "st ok"; }
    else { se.textContent = t("st_none"); se.className = "st"; }
    $("#retryrow").style.display = st === "bad" ? "" : "none";
    var busy = S.phase !== "idle";
    $("#rec").textContent = S.phase === "record" ? t("stop") : t("rec");
    $("#rec").disabled = S.phase === "countdown" || S.phase === "play";
    $("#hear").disabled = busy;
    $("#mine").disabled = busy || !S.takes[l.file];
    $("#prev").disabled = busy || S.idx === 0;
    $("#next").disabled = busy;
    $("#next").textContent = S.idx < S.mine.length - 1 ? t("next") : t("done");
    var dots = "";
    S.mine.forEach(function (x, i) {
      dots += '<span class="' + (rec[x.file] ? "ok" : "") + (i === S.idx ? " cur" : "") + '"></span>';
    });
    $("#dots").innerHTML = dots;
  }

  var countTimer = null, recTimer = null, recLine = null, recMusic = false;

  function overlay(text, cls) {
    var ov = $("#ov"); if (!ov) return;
    ov.textContent = text || "";
    ov.className = "overlay" + (text ? " on" : "") + (cls ? " " + cls : "");
  }

  function startRecord() {
    var l = cur();
    S.phase = "countdown"; updateRecordBits();
    var mus = $("#music");
    recMusic = !!(mus && mus.checked);
    var musicReady = recMusic ? loadBacking() : Promise.resolve(null);
    ensureMic().then(function () { return musicReady; }).then(function () {
      showFrame(l.start);
      var n = 3;
      var step = function () {
        if (S.phase !== "countdown") return;
        if (n > 0) { overlay(String(n)); n--; countTimer = setTimeout(step, 650); }
        else { overlay(t("go")); countTimer = setTimeout(begin, 300); }
      };
      step();
    }).catch(function (e) {
      S.phase = "idle"; overlay(""); updateRecordBits();
      $("#vmsg").textContent = e.message;
    });

    function begin() {
      if (S.phase !== "countdown") return;
      recLine = l;
      chunks = [];
      capturing = true;
      S.phase = "record";
      overlay(t("recording"), "rec");
      updateRecordBits();
      var dur = l.dur + TAIL;
      playSegment(l.start, dur, true, null);
      if (recMusic && backingBuf) playBuffer(backingBuf, l.start, dur);
      recTimer = setTimeout(finishRecord, dur * 1000);
    }
  }

  function finishRecord() {
    if (S.phase !== "record") return;
    clearTimeout(recTimer);
    capturing = false;
    endSegment(); stopBuffer();
    overlay("");
    if (meterEl) meterEl.style.width = "0";
    var ac = audioCtx();
    var take = resample(joinChunks(), ac.sampleRate, TARGET_SR);
    chunks = [];
    S.phase = "idle";
    var l = recLine;
    if (take.length > TARGET_SR * 0.15) {
      S.takes[l.file] = take;
      upload(l.file);
    }
    updateRecordBits();
  }

  function upload(clip) {
    var take = S.takes[clip];
    if (!take) return;
    S.status[clip] = "up"; updateRecordBits();
    var blob = wavBlob(take, TARGET_SR);
    var attempt = function (n) {
      return api("POST", "/takes/" + encodeURIComponent(clip), blob, "audio/wav")
        .catch(function (e) {
          if (n >= 3 || (e.status && e.status < 500)) throw e;
          return sleep(1500 * n).then(function () { return attempt(n + 1); });
        });
    };
    attempt(1).then(function () {
      S.status[clip] = "ok"; updateRecordBits();
    }).catch(function (e) {
      S.status[clip] = "bad"; updateRecordBits();
      var vm = $("#vmsg"); if (vm) vm.textContent = e.message;
    });
  }

  function abortAll() {
    clearTimeout(countTimer); clearTimeout(recTimer);
    capturing = false; chunks = [];
    endSegment(); stopBuffer(); overlay("");
    S.phase = "idle";
  }

  // phone locked / app switched: stop cleanly, refresh the session on return
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (S.phase === "record") finishRecord();
      else if (S.phase !== "idle") { abortAll(); updateRecordBits(); }
    } else if (S.pid) {
      doJoin(true).then(refresh).catch(function () { });
    }
  });

  // ------------------------------------------------------------ boot
  if (!CODE) { renderEnd(t("bad_link")); return; }
  S.name = store("ds_name") || "";
  if (S.name && store("ds_joined_" + CODE)) {
    // reconnect straight into the room we were in
    doJoin(false).catch(function (e) {
      renderJoin(e.status === 404 ? t("no_room") : "", e.status === 404);
    });
  } else {
    renderJoin();
  }
})();

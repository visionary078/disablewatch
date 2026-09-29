(function () {
  const LIVE_MS = 2500;
  const SPEAK_GAP_MS = 4000;
  const DEFAULT_QUESTION = "请判断当前是否安全，并告诉我目标在哪个方向。";
  const SAFETY =
    "朝前看，我说哪边、近不近。手杖照拿，过马路别靠我。";

  const els = {
    header: document.getElementById("header"),
    status: document.getElementById("status"),
    speech: document.getElementById("speech"),
    meta: document.getElementById("meta"),
    preview: document.getElementById("preview"),
    gate: document.getElementById("gate"),
    settings: document.getElementById("settings"),
    startBtn: document.getElementById("start-btn"),
    gateSettings: document.getElementById("gate-settings"),
    talkBtn: document.getElementById("talk-btn"),
    preciseBtn: document.getElementById("precise-btn"),
    stopBtn: document.getElementById("stop-btn"),
    apiInput: document.getElementById("api-input"),
    tokenInput: document.getElementById("token-input"),
    saveSettings: document.getElementById("save-settings"),
    closeSettings: document.getElementById("close-settings"),
  };

  const state = {
    running: false,
    inferring: false,
    recording: false,
    holding: false,
    stream: null,
    timer: null,
    sessionId: sessionId(),
    sensors: { camera: false, tof: false },
    tofMm: "",
    question: DEFAULT_QUESTION,
    mainTask: "",
    lastResult: null,
    lastSpeakAt: 0,
    holdLiveSpeech: 0,
    audio: null,
    mediaRecorder: null,
    chunks: [],
    pressAt: 0,
  };

  hydrateSettingsFromUrl();

  if (els.startBtn) els.startBtn.addEventListener("click", startAssist);
  if (els.gateSettings) els.gateSettings.addEventListener("click", () => openSettings(true));
  if (els.saveSettings) els.saveSettings.addEventListener("click", saveSettings);
  if (els.closeSettings) els.closeSettings.addEventListener("click", () => closeSettings(false));
  if (els.preciseBtn) els.preciseBtn.addEventListener("click", () => captureAndInfer("precise"));
  if (els.stopBtn) els.stopBtn.addEventListener("click", stopAssist);
  bindHold(els.talkBtn, beginTalk, endTalk);
  if (els.header) bindLongPress(els.header, () => openSettings(false));
  document.addEventListener("keydown", onKeyDown);
  document.addEventListener("keyup", onKeyUp);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopLiveTimer();
    } else if (state.running && !state.recording) {
      startLiveTimer(true);
    }
  });

  function settings() {
    return {
      apiBaseUrl: (localStorage.getItem("glassesApi") || "").replace(/\/$/, ""),
      appToken: localStorage.getItem("glassesToken") || "",
    };
  }

  function hydrateSettingsFromUrl() {
    const params = new URLSearchParams(location.search);
    if (params.get("token")) {
      localStorage.setItem("glassesToken", params.get("token"));
    }
    if (params.get("api")) {
      localStorage.setItem("glassesApi", params.get("api").replace(/\/$/, ""));
    }
  }

  function userId() {
    let id = localStorage.getItem("glassesUserId");
    if (!id) {
      id = `u${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
      localStorage.setItem("glassesUserId", id);
    }
    return id;
  }

  const NOTE_TTL_MS = 24 * 60 * 60 * 1000;

  function readStoredNotes() {
    let prev = [];
    try {
      prev = JSON.parse(localStorage.getItem("memoryHighlights") || "[]");
    } catch (error) {
      prev = [];
    }
    const now = Date.now();
    const fresh = (Array.isArray(prev) ? prev : [])
      .map((item) => {
        if (item && typeof item === "object" && item.text) {
          return { text: String(item.text).trim(), at: Number(item.at) || 0 };
        }
        return null;
      })
      .filter((item) => item && item.text && now - item.at <= NOTE_TTL_MS)
      .slice(0, 20);
    localStorage.setItem("memoryHighlights", JSON.stringify(fresh));
    return fresh;
  }

  function saveHighlights(items) {
    const incoming = Array.isArray(items) ? items.map((item) => String(item || "").trim()).filter(Boolean) : [];
    const now = Date.now();
    const stored = readStoredNotes();
    incoming.forEach((text) => {
      const found = stored.find((item) => item.text === text);
      if (found) found.at = now;
      else stored.unshift({ text, at: now });
    });
    localStorage.setItem("memoryHighlights", JSON.stringify(stored.filter((item) => now - item.at <= NOTE_TTL_MS).slice(0, 20)));
    refreshHighlights();
  }

  function fillMemoryList(id, items) {
    const list = document.getElementById(id);
    if (!list) return;
    const rows = Array.isArray(items) ? items.map((item) => String(item || "").trim()).filter(Boolean) : [];
    list.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = "还没有。";
      list.appendChild(empty);
      return;
    }
    rows.slice(0, 20).forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      list.appendChild(li);
    });
  }

  function renderMemory(memory, mainTask) {
    const groups = memory || {};
    fillMemoryList("kept-list", groups.kept);
    fillMemoryList("find-list", groups.finds);
    fillMemoryList("fix-list", groups.corrections);
    const now = document.getElementById("memory-now");
    if (now) {
      const goal = String(mainTask || "").trim();
      now.textContent = goal ? `正在找：${goal}` : "正在找：还没有";
    }
  }

  async function refreshHighlights() {
    try {
      const res = await fetch(`${apiUrl("/highlights")}?user_id=${encodeURIComponent(userId())}`, {
        headers: headers(),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) return;
      renderMemory(payload.memory, state.mainTask);
    } catch (error) {
      /* 后台暂时读不到时，保留刚才画上的重点。 */
    }
  }

  function sessionId() {
    let id = localStorage.getItem("glassesSessionId");
    if (!id) {
      id = `g${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
      localStorage.setItem("glassesSessionId", id);
    }
    return id;
  }

  function apiUrl(path) {
    const base = settings().apiBaseUrl || location.origin;
    return `${base}${path}`;
  }

  function headers(extra) {
    const token = settings().appToken;
    const next = extra ? Object.assign({}, extra) : {};
    if (token) {
      next["X-App-Token"] = token;
    }
    return next;
  }

  function setStatus(text, speech) {
    els.status.textContent = text;
    if (speech) {
      els.speech.textContent = speech;
    }
  }

  function shortGoal(mainTask) {
    return String(mainTask || "")
      .replace(/请|帮我|一下/g, "")
      .replace(/找|买|拿/g, "")
      .replace(/[：:，,。；;]/g, "")
      .trim()
      .slice(0, 12);
  }

  function compactSpeech(text) {
    return String(text || "")
      .replace(/\s+/g, "")
      .replace(/[。．，,、！!？?；;：:]/g, "");
  }

  function shouldSpeak(previous, next, lastSpeakAt, now) {
    if (!next) return false;
    const nextSpeech = compactSpeech(next.speech || next.action || "");
    if (!nextSpeech) return false;
    if (!previous) return true;
    if (next.risk_level === "high" && previous.risk_level !== "high") return true;
    if (nextSpeech !== compactSpeech(previous.speech || previous.action || "")) return true;
    const stillUncertain = next.direction === "未确定" || nextSpeech.indexOf("继续观察") >= 0;
    return stillUncertain && now - lastSpeakAt >= SPEAK_GAP_MS;
  }

  function wrapTaskSpeech(decision, mainTask, speech) {
    const spoken = String(speech || "").trim();
    const goal = shortGoal(mainTask);
    if (!goal || /正在帮你找|帮你找/.test(spoken)) return spoken;
    if (decision === "switch") return `好，改成找${goal}。${spoken}`;
    if (decision === "refine") return `继续帮你找${goal}。${spoken}`;
    return spoken;
  }

  function vibrate(level) {
    if (!navigator.vibrate) return;
    if (level === "high") navigator.vibrate([80, 60, 220]);
    else if (level === "medium") navigator.vibrate(80);
  }

  async function speak(text) {
    const spoken = String(text || "").trim();
    if (!spoken) return;
    stopSpeak();
    try {
      const res = await fetch(apiUrl("/tts"), {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ text: spoken.slice(0, 200) }),
      });
      if (!res.ok) throw new Error("tts");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      state.audio = new Audio(url);
      state.audio.onended = () => URL.revokeObjectURL(url);
      await state.audio.play();
      return;
    } catch (error) {
      if (!window.speechSynthesis) return;
      const utterance = new SpeechSynthesisUtterance(spoken);
      utterance.lang = "zh-CN";
      utterance.rate = 0.95;
      window.speechSynthesis.speak(utterance);
    }
  }

  function stopSpeak() {
    if (state.audio) {
      state.audio.pause();
      state.audio = null;
    }
    if (window.speechSynthesis) window.speechSynthesis.cancel();
  }

  async function startAssist() {
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: true,
      });
    } catch (error) {
      setStatus("无法打开摄像头", "请允许摄像头和麦克风后，再点开始。");
      await speak("请允许摄像头和麦克风后，再点开始。");
      return;
    }
    els.preview.srcObject = state.stream;
    if (els.gate) {
      els.gate.classList.add("hidden");
      els.gate.setAttribute("hidden", "");
      els.gate.setAttribute("aria-hidden", "true");
    }
    els.talkBtn.disabled = false;
    if (els.preciseBtn) els.preciseBtn.disabled = false;
    if (els.stopBtn) els.stopBtn.disabled = false;
    state.running = true;
    setStatus("正在看", SAFETY);
    els.meta.textContent = "可以说要找什么";
    await speak("开始看前方。手杖照拿着。可以说要找什么。");
    startLiveTimer(false);
  }

  function stopAssist() {
    state.running = false;
    stopLiveTimer();
    stopSpeak();
    if (state.recording) endTalk(true);
    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
    }
    els.preview.srcObject = null;
    if (els.preciseBtn) els.preciseBtn.disabled = true;
    if (els.stopBtn) els.stopBtn.disabled = true;
    if (els.gate) {
      els.gate.classList.remove("hidden");
      els.gate.removeAttribute("hidden");
      els.gate.removeAttribute("aria-hidden");
    }
    document.body.className = "";
    setStatus("已停下", "需要时再说开始看。");
  }

  function startLiveTimer(skipImmediate) {
    stopLiveTimer();
    if (!skipImmediate) captureAndInfer("live");
    state.timer = setInterval(() => captureAndInfer("live"), LIVE_MS);
  }

  function stopLiveTimer() {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  function grabFrame(quality) {
    const video = els.preview;
    const canvas = document.createElement("canvas");
    const w = video.videoWidth || 960;
    const h = video.videoHeight || 540;
    const scale = Math.min(1, 960 / Math.max(w, 1));
    canvas.width = Math.max(320, Math.round(w * scale));
    canvas.height = Math.max(180, Math.round(h * scale));
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve, reject) => {
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error("拍照失败"))),
        "image/jpeg",
        quality
      );
    });
  }

  async function captureAndInfer(mode, spokenText) {
    const spoken = spokenText || "";
    const forceLook = mode === "precise" && !spoken;
    if (!state.running || state.recording) return;
    if (state.inferring && !(mode === "precise" && spoken)) return;
    if (!spoken && !forceLook && !state.sensors.camera) return;
    if (!els.preview.videoWidth) return;
    state.inferring = true;
    const hunting = shortGoal(state.mainTask);
    if (mode === "precise") {
      setStatus(hunting ? `正在帮你找${hunting}` : "再看一眼");
    }
    try {
      const blob = await grabFrame(mode === "precise" ? 0.86 : 0.7);
      const body = new FormData();
      body.append("image", blob, "scene.jpg");
      body.append("question", state.question || DEFAULT_QUESTION);
      body.append("mode", mode);
      body.append("session_id", state.sessionId);
      body.append("user_id", userId());
      body.append("spoken_text", spoken);
      body.append("client", "glasses");
      if (state.sensors.tof && state.tofMm) {
        body.append("tof_mm", state.tofMm);
      }
      const res = await fetch(apiUrl("/infer"), {
        method: "POST",
        headers: headers(),
        body,
      });
      if (res.status === 429) return;
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.detail || `识别失败 ${res.status}`);
      handlePayload(payload, mode);
    } catch (error) {
      if (mode === "precise") {
        const message = error.message || "识别失败";
        setStatus("识别失败", message);
        await speak(message);
      }
    } finally {
      state.inferring = false;
    }
  }

  function handlePayload(payload, mode) {
    if (!payload || payload.status === "error") {
      throw new Error((payload && payload.error) || "远程模型调用失败");
    }
    const result = payload.result || null;
    const task = payload.task || {};
    const mainTask = task.main_task || (result && result.main_task) || state.mainTask || "";
    state.mainTask = mainTask;
    renderMemory(payload.memory, mainTask);
    saveHighlights(payload.highlights);
    const now = Date.now();
    if (payload.sensors && typeof payload.sensors.camera === "boolean") {
      state.sensors = payload.sensors;
    }
    const understanding = payload.understanding || {};
    const heard = document.getElementById("heard");
    if (heard) {
      const line = understanding.summary || (understanding.label ? `听成：${understanding.label}` : "");
      heard.textContent = line;
    }
    const speech = wrapTaskSpeech(
      task.decision,
      mainTask,
      (result && (result.speech || result.action)) || "请再试一次。"
    );
    const speakNow = mode === "precise" || shouldSpeak(state.lastResult, result, state.lastSpeakAt, now);
    if (mode === "live" && now < state.holdLiveSpeech) {
      state.lastResult = result;
      return;
    }
    state.lastResult = result;
    state.mainTask = mainTask;
    if (task.question) state.question = task.question;
    const hunting = shortGoal(mainTask);
    const risk = (result && result.risk_level) || "";
    document.body.className = risk ? `risk-${risk}` : "";
    setStatus(hunting ? `正在帮你找${hunting}` : "正在看", speech);
    els.meta.textContent = [
      result && result.direction ? result.direction : "",
      result && result.distance_band ? result.distance_band : "",
    ]
      .filter(Boolean)
      .join(" · ");
    vibrate(risk);
    if (speakNow) {
      state.lastSpeakAt = now;
      speak(speech);
    }
  }

  function bindHold(button, onStart, onEnd) {
    button.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      button.setPointerCapture(event.pointerId);
      onStart();
    });
    button.addEventListener("pointerup", () => onEnd(false));
    button.addEventListener("pointercancel", () => onEnd(true));
  }

  function bindLongPress(node, onLong) {
    let timer = null;
    node.addEventListener("pointerdown", () => {
      timer = setTimeout(onLong, 800);
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach((name) => {
      node.addEventListener(name, () => {
        if (timer) clearTimeout(timer);
        timer = null;
      });
    });
  }

  function onKeyDown(event) {
    if (event.repeat) return;
    if (event.code === "Space") {
      event.preventDefault();
      beginTalk();
    } else if (event.code === "Enter") {
      captureAndInfer("precise");
    } else if (event.code === "Escape") {
      stopAssist();
    }
  }

  function onKeyUp(event) {
    if (event.code === "Space") endTalk(false);
  }

  async function openCamera() {
    if (state.running && state.stream) return true;
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: true,
      });
    } catch (error) {
      setStatus("无法打开摄像头", "请允许摄像头和麦克风后，再按住说话。");
      await speak("请允许摄像头和麦克风后，再按住说话。");
      return false;
    }
    els.preview.srcObject = state.stream;
    state.running = true;
    els.talkBtn.disabled = false;
    return true;
  }

  async function beginTalk() {
    if (state.recording) return;
    state.holding = true;
    if (!(await openCamera())) {
      state.holding = false;
      return;
    }
    if (!state.holding) {
      if (!state.timer) startLiveTimer(false);
      return;
    }
    state.recording = true;
    state.pressAt = Date.now();
    stopLiveTimer();
    stopSpeak();
    els.talkBtn.classList.add("recording");
    els.talkBtn.textContent = "松开结束";
    setStatus("正在录音，请说话", "正在听你说要找什么。");
    if (startBrowserSpeech()) return;
    try {
      state.chunks = [];
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/webm";
      state.mediaRecorder = new MediaRecorder(state.stream, { mimeType: mime });
      state.mediaRecorder.ondataavailable = (event) => {
        if (event.data && event.data.size) state.chunks.push(event.data);
      };
      state.mediaRecorder.start();
    } catch (error) {
      state.recording = false;
      els.talkBtn.classList.remove("recording");
      els.talkBtn.textContent = "按住说话";
      await speak("我听不见，请允许用麦克风。");
      startLiveTimer(true);
    }
  }

  function startBrowserSpeech() {
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Rec) return false;
    const rec = new Rec();
    rec.lang = "zh-CN";
    rec.interimResults = false;
    rec.onresult = (event) => {
      const text = Array.from(event.results)
        .map((item) => item[0].transcript)
        .join("")
        .trim();
      finishTalk(text);
    };
    rec.onerror = () => {};
    rec.onend = () => {
      if (state.recording) finishTalk("");
    };
    state.browserRec = rec;
    rec.start();
    return true;
  }

  async function endTalk(silent) {
    state.holding = false;
    if (!state.recording) return;
    if (state.browserRec) {
      try {
        state.browserRec.stop();
      } catch (error) {
        finishTalk("");
      }
      return;
    }
    const held = Date.now() - state.pressAt;
    const recorder = state.mediaRecorder;
    state.mediaRecorder = null;
    if (!recorder) {
      finishTalk("");
      return;
    }
    if (silent || held < 400) {
      try {
        recorder.stop();
      } catch (error) {
        /* ignore */
      }
      finishTalk(silent ? null : "");
      return;
    }
    const blob = await new Promise((resolve) => {
      recorder.onstop = () => resolve(new Blob(state.chunks, { type: recorder.mimeType || "audio/webm" }));
      recorder.stop();
    });
    try {
      const body = new FormData();
      body.append("audio", blob, "speech.webm");
      const res = await fetch(apiUrl("/asr"), { method: "POST", headers: headers(), body });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.detail || "没听清");
      finishTalk(String(payload.text || "").trim());
    } catch (error) {
      finishTalk("");
    }
  }

  async function finishTalk(text) {
    state.recording = false;
    state.browserRec = null;
    els.talkBtn.classList.remove("recording");
    els.talkBtn.textContent = "按住说话";
    if (text === null) {
      startLiveTimer(true);
      return;
    }
    if (!text) {
      setStatus("没听清", "没听清，请按住再说一次。");
      await speak("没听清，请按住再说一次。");
      startLiveTimer(true);
      return;
    }
    setStatus(`听到：${text}`, `听到：${text}，正在帮你找`);
    await captureAndInfer("precise", text);
    state.holdLiveSpeech = Date.now() + 4000;
    startLiveTimer(true);
  }

  function openSettings(fromGate) {
    els.apiInput.value = settings().apiBaseUrl;
    els.tokenInput.value = settings().appToken;
    els.settings.dataset.fromGate = fromGate ? "1" : "0";
    els.settings.classList.remove("hidden");
    els.tokenInput.focus();
  }

  function closeSettings(saved) {
    els.settings.classList.add("hidden");
    if (saved) speak("设置好了。");
  }

  function saveSettings() {
    localStorage.setItem("glassesApi", els.apiInput.value.trim().replace(/\/$/, ""));
    localStorage.setItem("glassesToken", els.tokenInput.value.trim());
    closeSettings(true);
  }

  const askForm = document.getElementById("ask-form");
  const askInput = document.getElementById("ask-input");
  if (askForm && askInput) {
    askForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = askInput.value.trim();
      if (!text) return;
      if (!state.running) {
        setStatus("先点开始看", "先点开始看，我才能用摄像头看这一帧。");
        return;
      }
      askInput.value = "";
      captureAndInfer("precise", text);
    });
  }
  readStoredNotes();
  refreshHighlights();
  setInterval(refreshHighlights, 2000);
})();

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
    holdToken: null,
    ending: false,
    stopTimer: null,
    stream: null,
    timer: null,
    sessionId: sessionId(),
    sensors: { camera: false, tof: false },
    tofMm: "",
    question: DEFAULT_QUESTION,
    mainTask: "",
    finishedLabel: "",
    lastResult: null,
    lastSpeakAt: 0,
    holdLiveSpeech: 0,
    lastSpoken: "",
    audio: null,
    mediaRecorder: null,
    chunks: [],
    pressAt: 0,
    tapToTalk: false,
  };

  hydrateSettingsFromUrl();

  if (els.startBtn) els.startBtn.addEventListener("click", startAssist);
  const openSettingsBtn = document.getElementById("open-settings");
  if (openSettingsBtn) openSettingsBtn.addEventListener("click", () => openSettings(false));
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
      if (goal) now.textContent = `正在找：${goal}`;
      else if (state.finishedLabel) now.textContent = `已经找到：${state.finishedLabel}`;
      else now.textContent = "正在找：还没有";
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

  function setRiskClass(risk) {
    document.body.classList.remove("risk-high", "risk-medium", "risk-low");
    if (risk === "high" || risk === "medium" || risk === "low") {
      document.body.classList.add(`risk-${risk}`);
    }
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

  function shouldSpeak(previous, next) {
    if (!next) return false;
    const nextSpeech = compactSpeech(next.speech || next.action || "");
    if (!nextSpeech) return false;
    if (!previous) return true;
    if (next.risk_level === "high" && previous.risk_level !== "high") return true;
    if (nextSpeech === compactSpeech(previous.speech || previous.action || "")) return false;
    const direction = next.direction || "";
    if (!direction || direction === "未确定" || direction === (previous.direction || "")) return false;
    return true;
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
    els.preview.classList.add("is-live");
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
    els.preview.classList.remove("is-live");
    if (els.preciseBtn) els.preciseBtn.disabled = true;
    if (els.stopBtn) els.stopBtn.disabled = true;
    if (els.gate) {
      els.gate.classList.remove("hidden");
      els.gate.removeAttribute("hidden");
      els.gate.removeAttribute("aria-hidden");
    }
    setRiskClass("");
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
    if (!state.running || state.recording) return;
    if (state.inferring && !(mode === "precise" && spoken)) return;
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
    const found = Boolean(task.found) || task.decision === "done";
    const incoming = String(task.main_task || (result && result.main_task) || "").trim();
    if (found) {
      state.finishedLabel = shortGoal(incoming || state.mainTask) || state.finishedLabel || "它";
      state.mainTask = "";
      state.question = DEFAULT_QUESTION;
    } else if (incoming) {
      state.finishedLabel = "";
      state.mainTask = incoming;
    }
    const mainTask = state.mainTask;
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
    const speakNow = mode === "precise" || found || shouldSpeak(state.lastResult, result);
    if (mode === "live" && now < state.holdLiveSpeech && !found) {
      state.lastResult = result;
      return;
    }
    state.lastResult = result;
    state.mainTask = mainTask;
    if (!found && task.question) state.question = task.question;
    const hunting = shortGoal(mainTask);
    const risk = (result && result.risk_level) || "";
    setRiskClass(risk);
    if (found) {
      setStatus("已经找到", speakNow ? speech : "");
      const nowLine = document.getElementById("memory-now");
      if (nowLine) nowLine.textContent = "正在找：已经找到";
    } else {
      setStatus(hunting ? `正在帮你找${hunting}` : "正在看", speakNow ? speech : "");
    }
    els.meta.textContent = [
      result && result.direction ? result.direction : "",
      result && result.distance_band ? result.distance_band : "",
    ]
      .filter(Boolean)
      .join(" · ");
    vibrate(risk);
    if (speakNow && compactSpeech(speech) !== state.lastSpoken) {
      state.lastSpeakAt = now;
      state.lastSpoken = compactSpeech(speech);
      speak(speech);
    }
  }

  function bindHold(button, onStart, onEnd) {
    let pointerId = null;
    let downAt = 0;

    const release = (event) => {
      if (pointerId == null) return;
      if (event && event.pointerId != null && event.pointerId !== pointerId) return;
      const heldMs = Date.now() - downAt;
      pointerId = null;
      if (event && event.type === "pointerup" && heldMs < 500) {
        state.tapToTalk = true;
        showListening();
        return;
      }
      onEnd(Boolean(event && event.type === "pointercancel"));
    };

    button.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      if (state.recording || state.holding) {
        pointerId = null;
        onEnd(false);
        return;
      }
      pointerId = event.pointerId;
      downAt = Date.now();
      try {
        button.setPointerCapture(event.pointerId);
      } catch (error) {
        /* 捕获失败时，仍由 window 上的松开事件结束。 */
      }
      onStart();
    });
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    window.addEventListener("blur", () => release(null));
  }

  function releaseTalkButton() {
    state.tapToTalk = false;
    els.talkBtn.classList.remove("recording");
    els.talkBtn.textContent = "点一下说话";
  }

  function showListening() {
    if (!state.recording) return;
    els.talkBtn.classList.add("recording");
    els.talkBtn.textContent = state.tapToTalk ? "再点一下结束" : "松开结束";
    if (state.tapToTalk) setStatus("正在听你说话", "说完再点一下结束。");
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
      if (state.recording || state.holding) {
        endTalk(false);
        return;
      }
      state.pressAt = Date.now();
      beginTalk();
    } else if (event.code === "Enter") {
      captureAndInfer("precise");
    } else if (event.code === "Escape") {
      stopAssist();
    }
  }

  function onKeyUp(event) {
    if (event.code !== "Space" || state.tapToTalk) return;
    if (Date.now() - state.pressAt < 500) {
      state.tapToTalk = true;
      showListening();
      return;
    }
    endTalk(false);
  }

  function setMicLine(text, connected) {
    const line = document.getElementById("mic-line");
    const retry = document.getElementById("reconnect-mic");
    if (line) line.textContent = text;
    if (retry) retry.hidden = connected;
  }

  function micTrackLive() {
    return Boolean(state.stream && state.stream.getAudioTracks().some((track) => track.readyState === "live"));
  }

  async function openMic(options) {
    const announce = !options || options.announce !== false;
    if (micTrackLive()) {
      setMicLine("麦克风已连接", true);
      return true;
    }
    setMicLine("正在连接麦克风", false);
    try {
      const audioStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      if (!state.stream) state.stream = audioStream;
      else audioStream.getAudioTracks().forEach((track) => state.stream.addTrack(track));
      setMicLine("麦克风已连接", true);
      return true;
    } catch (error) {
      const denied = error && (error.name === "NotAllowedError" || error.name === "PermissionDeniedError");
      const message = denied ? "请允许使用麦克风，再点重连。" : "没有找到可用的麦克风。";
      setMicLine(message, false);
      if (announce) await speak(denied ? "请允许使用麦克风。" : "没有找到可用的麦克风。");
      return false;
    }
  }

  async function openCamera(options) {
    const announce = !options || options.announce !== false;
    const videoLive =
      state.stream && state.stream.getVideoTracks().some((track) => track.readyState === "live");
    if (state.running && videoLive) return true;
    setStatus("正在打开摄像头");
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
    } catch (error) {
      const denied = error && (error.name === "NotAllowedError" || error.name === "PermissionDeniedError");
      const message = denied ? "请允许使用摄像头。" : "没有找到可用的摄像头。";
      setStatus(denied ? "需要摄像头权限" : "无法打开摄像头", message);
      if (announce) await speak(message);
      return false;
    }
    els.preview.srcObject = state.stream;
    els.preview.muted = true;
    els.preview.classList.add("is-live");
    try {
      await els.preview.play();
    } catch (error) {
      /* 自动播放被拦住时，画面仍会在有帧之后显示。 */
    }
    state.running = true;
    els.talkBtn.disabled = false;
    if (!state.recording) setStatus("摄像头已打开", "点一下，说要找什么。");
    if (!state.recording && !state.timer) startLiveTimer(true);
    return true;
  }

  async function beginTalk() {
    if (state.recording || state.holding) return;
    const token = {};
    state.holdToken = token;
    state.holding = true;
    state.ending = false;
    const opened = await openCamera();
    if (state.holdToken !== token || !state.holding) {
      if (opened && state.running && !state.timer) startLiveTimer(false);
      return;
    }
    if (!opened) {
      state.holding = false;
      state.holdToken = null;
      return;
    }
    state.recording = true;
    state.pressAt = Date.now();
    stopLiveTimer();
    stopSpeak();
    els.talkBtn.classList.add("recording");
    showListening();
    if (!state.tapToTalk) setStatus("正在录音，请说话", "正在听你说要找什么。");
    if (!(await openMic({ announce: true }))) {
      state.recording = false;
      releaseTalkButton();
      await speak("我听不见，请允许用麦克风。");
      startLiveTimer(true);
      return;
    }
    if (!state.recording || state.holdToken !== token) return;
    try {
      state.chunks = [];
      const audioTracks = state.stream.getAudioTracks().filter((track) => track.readyState === "live");
      if (!audioTracks.length) throw new Error("no-audio");
      const recordStream = new MediaStream(audioTracks);
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
      state.mediaRecorder = mime
        ? new MediaRecorder(recordStream, { mimeType: mime })
        : new MediaRecorder(recordStream);
      state.mediaRecorder.ondataavailable = (event) => {
        if (event.data && event.data.size) state.chunks.push(event.data);
      };
      state.mediaRecorder.start();
      setMicLine("正在收音", true);
    } catch (error) {
      state.recording = false;
      releaseTalkButton();
      await speak("我听不见，请允许用麦克风。");
      startLiveTimer(true);
    }
  }

  async function blobToWav(blob) {
    const audioCtx = new AudioContext();
    try {
      const decoded = await audioCtx.decodeAudioData(await blob.arrayBuffer());
      const rate = 16000;
      const frames = Math.max(1, Math.ceil(decoded.duration * rate));
      const offline = new OfflineAudioContext(1, frames, rate);
      const source = offline.createBufferSource();
      source.buffer = decoded;
      source.connect(offline.destination);
      source.start(0);
      const rendered = await offline.startRendering();
      return pcmToWav(rendered.getChannelData(0), rate);
    } finally {
      audioCtx.close();
    }
  }

  function pcmToWav(samples, rate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeStr = (offset, text) => {
      for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
    };
    writeStr(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    writeStr(8, "WAVE");
    writeStr(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeStr(36, "data");
    view.setUint32(40, samples.length * 2, true);
    let offset = 44;
    for (let i = 0; i < samples.length; i += 1, offset += 2) {
      const sample = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    return new Blob([buffer], { type: "audio/wav" });
  }

  function startBrowserSpeech() {
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Rec) return false;
    const rec = new Rec();
    rec.lang = "zh-CN";
    rec.continuous = true;
    rec.interimResults = true;
    rec._started = false;
    rec._stopRequested = false;
    rec._text = "";
    rec.onstart = () => {
      rec._started = true;
      if (!rec._stopRequested) return;
      try {
        rec.stop();
      } catch (error) {
        finishTalk(rec._silent ? null : rec._text);
        return;
      }
      watchRecognitionEnd(rec);
    };
    rec.onresult = (event) => {
      rec._text = Array.from(event.results)
        .map((item) => item[0].transcript)
        .join("")
        .trim();
    };
    rec.onerror = (event) => {
      if (!state.recording) return;
      const code = event && event.error;
      if (state.tapToTalk && !rec._stopRequested && code !== "not-allowed" && code !== "service-not-allowed") return;
      finishTalk(rec._silent ? null : rec._text);
    };
    rec.onend = () => {
      if (!state.recording) return;
      if (state.tapToTalk && !rec._stopRequested && Date.now() - state.pressAt < 60000) {
        setTimeout(() => {
          if (!state.recording || rec._stopRequested || state.browserRec !== rec) return;
          try {
            rec.start();
          } catch (error) {
            /* 这一轮没接上，等用户再点一下结束。 */
          }
        }, 250);
        return;
      }
      finishTalk(rec._silent ? null : rec._text);
    };
    state.browserRec = rec;
    rec.start();
    return true;
  }

  async function endTalk(silent) {
    const tapped = state.tapToTalk;
    state.holding = false;
    state.holdToken = null;
    releaseTalkButton();
    if (!state.recording || state.ending) return;
    state.ending = true;
    setStatus("正在识别", tapped ? "正在识别刚才的话。" : "松开了，正在识别刚才的话。");
    if (state.browserRec) {
      const rec = state.browserRec;
      rec._silent = silent;
      rec._stopRequested = true;
      if (rec._started) {
        try {
          rec.stop();
        } catch (error) {
          finishTalk(silent ? null : "");
          return;
        }
      }
      watchRecognitionEnd(rec);
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
    let wav;
    try {
      wav = await blobToWav(blob);
    } catch (error) {
      finishTalk("");
      return;
    }
    try {
      const body = new FormData();
      body.append("audio", wav, "speech.wav");
      const res = await fetch(apiUrl("/asr"), { method: "POST", headers: headers(), body });
      const payload = await res.json().catch(() => ({}));
      if (res.status === 401) {
        setStatus("口令不对", "请在设置里填写使用密码。");
        finishTalk(null);
        return;
      }
      if (!res.ok) throw new Error(payload.detail || "没听清");
      finishTalk(String(payload.text || "").trim());
    } catch (error) {
      finishTalk("");
    }
  }

  function watchRecognitionEnd(rec) {
    if (state.stopTimer) clearTimeout(state.stopTimer);
    state.stopTimer = setTimeout(() => {
      state.stopTimer = null;
      if (!state.recording || state.browserRec !== rec) return;
      try {
        rec.abort();
      } catch (error) {
        /* 识别已经结束 */
      }
      finishTalk(rec._silent ? null : "");
    }, 2500);
  }

  async function finishTalk(text) {
    if (!state.recording && !state.ending) return;
    state.ending = false;
    state.recording = false;
    state.holding = false;
    state.browserRec = null;
    if (state.stopTimer) {
      clearTimeout(state.stopTimer);
      state.stopTimer = null;
    }
    releaseTalkButton();
    if (micTrackLive()) setMicLine("麦克风已连接", true);
    try {
      if (text === null) {
        startLiveTimer(true);
        return;
      }
      if (!text) {
        setStatus("没听清", "没听清，请再说一次。");
        await speak("没听清，请再说一次。");
        startLiveTimer(true);
        return;
      }
      setStatus(`听到：${text}`, `听到：${text}，正在帮你找`);
      await captureAndInfer("precise", text);
      state.holdLiveSpeech = Date.now() + 4000;
      startLiveTimer(true);
    } finally {
      if (!state.recording) openMic({ announce: false });
    }
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
  const openCameraBtn = document.getElementById("open-camera");
  if (openCameraBtn) openCameraBtn.addEventListener("click", () => openCamera({ announce: true }));
  const reconnectMicBtn = document.getElementById("reconnect-mic");
  if (reconnectMicBtn) reconnectMicBtn.addEventListener("click", () => openMic({ announce: true }));
  readStoredNotes();
  ensureLocalToken().finally(() => {
    refreshHighlights();
    setInterval(refreshHighlights, 2000);
    openCamera({ announce: false })
      .then(() => openMic({ announce: false }))
      .then(() => beginAssignedTask());
  });

  function assignedTask() {
    const value = new URLSearchParams(location.search).get("task") || "";
    return value.replace(/\s+/g, " ").trim().slice(0, 80);
  }

  async function beginAssignedTask() {
    const task = assignedTask();
    if (!task) return;
    for (let i = 0; i < 20; i += 1) {
      if (state.running && els.preview.videoWidth) break;
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    if (!state.running || !els.preview.videoWidth || state.recording) return;
    state.sessionId = `task${Date.now().toString(36)}`;
    localStorage.setItem("glassesSessionId", state.sessionId);
    state.mainTask = task;
    renderMemory(null, task);
    setStatus(`正在找${task}`, `正在找${task}`);
    await captureAndInfer("precise", task);
    if (!state.recording && state.sensors.camera) startLiveTimer(true);
  }

  function localDemoHost() {
    const host = location.hostname;
    if (host === "localhost" || host === "127.0.0.1" || host === "::1") return true;
    const parts = host.split(".").map((item) => Number(item));
    if (parts.length !== 4 || parts.some((item) => !Number.isInteger(item) || item < 0 || item > 255)) return false;
    if (parts[0] === 10 || (parts[0] === 192 && parts[1] === 168)) return true;
    return parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31;
  }

  async function ensureLocalToken() {
    if (settings().appToken) return;
    if (!localDemoHost()) return;
    try {
      const res = await fetch("/local-app-token");
      if (!res.ok) return;
      const payload = await res.json();
      const token = String((payload && payload.token) || "");
      if (token) localStorage.setItem("glassesToken", token);
    } catch (error) {
      /* 本机口令没拿到时，设置页仍可手填。 */
    }
  }
})();

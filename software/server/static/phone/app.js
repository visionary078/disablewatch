(function () {
  const DAY_MS = 24 * 60 * 60 * 1000;
  const NOTES_KEY = "phone-journal-v1";
  const USER_KEY = "phone-journal-user";
  const API_KEY = "phone-journal-api";
  const TOKEN_KEY = "phone-journal-token";

  const subtitleEl = document.getElementById("subtitle");
  const talkEl = document.getElementById("talk");
  const settingsEl = document.getElementById("settings");
  const apiEl = document.getElementById("api");
  const tokenEl = document.getElementById("token");
  const voice = { recording: false, recorder: null, chunks: [], stream: null, audio: null, browser: null, browserText: "" };

  function userId() {
    let id = localStorage.getItem(USER_KEY);
    if (!id) {
      id = `phone-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
      localStorage.setItem(USER_KEY, id);
    }
    return id;
  }

  function apiBase() {
    return (localStorage.getItem(API_KEY) || location.origin).replace(/\/$/, "");
  }

  function tokenHeader() {
    const token = localStorage.getItem(TOKEN_KEY) || "";
    return token ? { "X-App-Token": token } : {};
  }

  function jsonHeaders() {
    return Object.assign({ "Content-Type": "application/json" }, tokenHeader());
  }

  function loadNotes() {
    let rows = [];
    try {
      rows = JSON.parse(localStorage.getItem(NOTES_KEY) || "[]");
    } catch (error) {
      rows = [];
    }
    if (!Array.isArray(rows)) rows = [];
    const now = Date.now();
    const fresh = rows.filter((item) => item && item.text && now - Number(item.at || 0) < DAY_MS);
    localStorage.setItem(NOTES_KEY, JSON.stringify(fresh));
    return fresh;
  }

  function saveNotes(rows) {
    localStorage.setItem(NOTES_KEY, JSON.stringify(rows));
  }

  function showSubtitle(text) {
    subtitleEl.textContent = String(text || "").trim() || "点一下说话。";
  }

  function addLocal(role, text) {
    const line = String(text || "").trim().slice(0, 200);
    if (!line) return loadNotes();
    const rows = loadNotes();
    rows.push({ role: role, text: line, at: Date.now() });
    saveNotes(rows);
    return rows;
  }

  function stopSpeak() {
    if (voice.audio) {
      voice.audio.pause();
      voice.audio = null;
    }
    if (window.speechSynthesis) window.speechSynthesis.cancel();
  }

  async function speak(text) {
    const spoken = String(text || "").trim();
    if (!spoken) return;
    stopSpeak();
    try {
      const response = await fetch(`${apiBase()}/tts`, {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ text: spoken.slice(0, 200) }),
      });
      if (!response.ok) throw new Error("tts");
      const url = URL.createObjectURL(await response.blob());
      voice.audio = new Audio(url);
      voice.audio.onended = () => URL.revokeObjectURL(url);
      await voice.audio.play();
    } catch (error) {
      if (!window.speechSynthesis) return;
      const utterance = new SpeechSynthesisUtterance(spoken);
      utterance.lang = "zh-CN";
      utterance.rate = 0.95;
      window.speechSynthesis.speak(utterance);
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

  function startBrowserSpeech() {
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Rec) return;
    const rec = new Rec();
    rec.lang = "zh-CN";
    rec.continuous = true;
    rec.interimResults = true;
    voice.browserText = "";
    rec.onresult = (event) => {
      voice.browserText = Array.from(event.results)
        .map((item) => item[0].transcript)
        .join("")
        .trim();
    };
    rec.onerror = () => {};
    voice.browser = rec;
    try {
      rec.start();
    } catch (error) {
      voice.browser = null;
    }
  }

  function stopBrowserSpeech() {
    const rec = voice.browser;
    voice.browser = null;
    if (rec) {
      try {
        rec.stop();
      } catch (error) {
        /* 浏览器识别已经停了 */
      }
    }
    return new Promise((resolve) => {
      setTimeout(() => resolve(String(voice.browserText || "").trim()), 300);
    });
  }

  async function transcribe(blob) {
    const browserText = await stopBrowserSpeech();
    if (browserText) return browserText;
    let wav = blob;
    try {
      wav = await blobToWav(blob);
    } catch (error) {
      wav = blob;
    }
    const body = new FormData();
    body.append("audio", wav, "speech.wav");
    const response = await fetch(`${apiBase()}/asr`, { method: "POST", headers: tokenHeader(), body });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "没听清");
    return String(payload.text || "").trim();
  }

  async function handleSpoken(text) {
    const line = String(text || "").trim().slice(0, 200);
    if (!line) {
      showSubtitle("没听清，请再说一次。");
      await speak("没听清，请再说一次。");
      return;
    }
    const notes = loadNotes();
    const response = await fetch(`${apiBase()}/journal/act`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ user_id: userId(), text: line, notes: notes }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "没听清");
    if (payload.action === "ask") {
      const speech = String(payload.speech || "这24小时里还没记下这件事。");
      showSubtitle(speech);
      await speak(speech);
      return;
    }
    addLocal("user", line);
    showSubtitle(`已经记下。${line}`);
    await speak("已经记下。");
  }

  async function startTalk() {
    if (voice.recording) return;
    stopSpeak();
    voice.chunks = [];
    voice.browserText = "";
    try {
      voice.stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    } catch (error) {
      showSubtitle("我听不见，请允许用麦克风。");
      await speak("我听不见，请允许用麦克风。");
      return;
    }
    if (!window.MediaRecorder) {
      startBrowserSpeech();
      voice.recording = true;
      talkEl.classList.add("recording");
      talkEl.textContent = "说完再点一下";
      showSubtitle("正在听。");
      return;
    }
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
    voice.recorder = mime ? new MediaRecorder(voice.stream, { mimeType: mime }) : new MediaRecorder(voice.stream);
    voice.recorder.ondataavailable = (event) => {
      if (event.data && event.data.size) voice.chunks.push(event.data);
    };
    voice.recorder.start();
    startBrowserSpeech();
    voice.recording = true;
    talkEl.classList.add("recording");
    talkEl.textContent = "说完再点一下";
    showSubtitle("正在听。");
  }

  async function stopTalk() {
    if (!voice.recording) return;
    voice.recording = false;
    talkEl.classList.remove("recording");
    talkEl.textContent = "说话";
    showSubtitle("正在识别刚才的话。");
    const recorder = voice.recorder;
    voice.recorder = null;
    const blob = await new Promise((resolve) => {
      if (!recorder) {
        resolve(new Blob());
        return;
      }
      recorder.onstop = () => resolve(new Blob(voice.chunks, { type: recorder.mimeType || "audio/webm" }));
      try {
        recorder.stop();
      } catch (error) {
        resolve(new Blob());
      }
    });
    if (voice.stream) {
      voice.stream.getTracks().forEach((track) => track.stop());
      voice.stream = null;
    }
    try {
      const text = await transcribe(blob);
      await handleSpoken(text);
    } catch (error) {
      showSubtitle(error.message || "没听清，请再说一次。");
      await speak("没听清，请再说一次。");
    }
  }

  talkEl.addEventListener("click", () => {
    if (voice.recording) stopTalk();
    else startTalk();
  });

  document.getElementById("open-settings").addEventListener("click", () => {
    settingsEl.classList.toggle("hidden");
    apiEl.value = apiBase();
    tokenEl.value = localStorage.getItem(TOKEN_KEY) || "";
  });

  document.getElementById("save-settings").addEventListener("click", () => {
    localStorage.setItem(API_KEY, apiEl.value.trim() || location.origin);
    localStorage.setItem(TOKEN_KEY, tokenEl.value.trim());
    settingsEl.classList.add("hidden");
  });

  loadNotes();
  showSubtitle("点一下说话。");
})();

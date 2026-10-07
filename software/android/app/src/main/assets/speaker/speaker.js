(function () {
  const SPEAK_GAP_MS = 4000;
  const speechEl = document.getElementById("speech");
  const findingEl = document.getElementById("finding");
  const talkBtn = document.getElementById("talk-btn");
  const state = {
    seq: 0,
    lastResult: null,
    lastSpeakAt: 0,
    holding: false,
    recognition: null,
  };

  talkBtn.addEventListener("pointerdown", beginTalk);
  talkBtn.addEventListener("pointerup", endTalk);
  talkBtn.addEventListener("pointercancel", endTalk);
  poll();
  setInterval(poll, 700);

  function beginTalk(event) {
    event.preventDefault();
    if (state.holding) return;
    state.holding = true;
    talkBtn.classList.add("holding");
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      state.holding = false;
      talkBtn.classList.remove("holding");
      sayAloud("这个浏览器听不了，换一个能听的再按。");
      return;
    }
    const recognition = new Recognition();
    recognition.lang = "zh-CN";
    recognition.interimResults = false;
    recognition.onresult = (event) => {
      const text = event.results && event.results[0] && event.results[0][0] ? event.results[0][0].transcript : "";
      sendSpoken(text);
    };
    recognition.onerror = () => sendSpoken("");
    recognition.onend = () => {
      state.holding = false;
      talkBtn.classList.remove("holding");
    };
    state.recognition = recognition;
    recognition.start();
  }

  function endTalk() {
    if (state.recognition) state.recognition.stop();
  }

  async function sendSpoken(text) {
    const spoken = String(text || "").trim();
    if (!spoken) {
      sayAloud("没听清。再按住说一次。");
      return;
    }
    const res = await fetch("/say", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spoken_text: spoken }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) sayAloud(payload.error || "还没送到。");
  }

  async function poll() {
    let res;
    try {
      res = await fetch("/speech");
    } catch (error) {
      return;
    }
    if (!res.ok) return;
    const payload = await res.json();
    if (!payload || payload.seq === state.seq) return;
    state.seq = payload.seq;
    const goal = shortGoal(payload.main_task);
    if (payload.decision === "done" || payload.found) state.finished = true;
    else if (goal) state.finished = false;
    findingEl.textContent = state.finished && !goal ? "正在找：已经找到" : "正在找：" + (goal || "还没有");
    if (payload.error) {
      sayAloud(payload.error);
      return;
    }
    const line = wrapTaskSpeech(payload.decision, payload.main_task, payload.speech);
    const now = Date.now();
    const speakNow = payload.mode === "precise" || shouldSpeak(state.lastResult, payload, state.lastSpeakAt, now);
    state.lastResult = payload;
    speechEl.textContent = line || speechEl.textContent;
    if (speakNow && line) sayAloud(line);
  }

  function sayAloud(text) {
    const spoken = String(text || "").trim();
    if (!spoken) return;
    speechEl.textContent = spoken;
    state.lastSpeakAt = Date.now();
    if (!window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(spoken);
    utterance.lang = "zh-CN";
    utterance.rate = 0.95;
    window.speechSynthesis.speak(utterance);
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
    const nextSpeech = compactSpeech(next.speech || "");
    if (!nextSpeech) return false;
    if (!previous) return true;
    if (next.risk_level === "high" && previous.risk_level !== "high") return true;
    if (nextSpeech !== compactSpeech(previous.speech || "")) return true;
    const stillUncertain = next.direction === "未确定" || nextSpeech.indexOf("继续观察") >= 0;
    return stillUncertain && now - lastSpeakAt >= SPEAK_GAP_MS;
  }

  function wrapTaskSpeech(decision, mainTask, speech) {
    const spoken = String(speech || "").trim();
    const goal = shortGoal(mainTask);
    if (!goal || /正在帮你找|帮你找/.test(spoken)) return spoken;
    if (decision === "switch") return "好，改成找" + goal + "。" + spoken;
    if (decision === "refine") return "继续帮你找" + goal + "。" + spoken;
    return spoken;
  }
})();

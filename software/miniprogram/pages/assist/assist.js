const api = require("../../utils/api")
const a11y = require("../../utils/a11y")
const tts = require("../../utils/tts")
const asr = require("../../utils/asr")
const depth = require("../../utils/depth")
const config = require("../../utils/config")

function decorateTasks(taskId) {
  return config.TASKS.map((item) => ({
    id: item.id,
    label: item.label,
    ariaLabel: taskId === item.id ? `当前任务 ${item.label}，已选中` : `选择任务 ${item.label}`,
  }))
}

function shortGoal(mainTask) {
  return String(mainTask || "")
    .replace(/请|帮我|一下/g, "")
    .replace(/找|买|拿/g, "")
    .replace(/[：:，,。；;]/g, "")
    .trim()
    .slice(0, 12)
}

function wrapTaskSpeech(decision, mainTask, speech) {
  const spoken = String(speech || "").trim()
  const goal = shortGoal(mainTask)
  if (!goal) {
    return spoken
  }
  if (/正在帮你找|帮你找/.test(spoken)) {
    return spoken
  }
  if (decision === "switch") {
    return `好，改成找${goal}。${spoken}`
  }
  if (decision === "refine") {
    return `继续帮你找${goal}。${spoken}`
  }
  return spoken
}

Page({
  data: {
    statusText: "正在准备摄像头",
    memoryNow: "还没有",
    statusLabel: "当前状态：正在准备摄像头",
    liveOn: false,
    liveLabel: "开始实时辅助",
    liveAria: "开始实时辅助",
    recording: false,
    voiceLabel: "按住说话",
    voiceAria: "按住说话说出需求，松开后开始识别。",
    busy: false,
    cameraReady: false,
    taskId: "avoid_obstacle",
    tasks: decorateTasks("avoid_obstacle"),
    question: config.taskById("avoid_obstacle").question,
    result: null,
    resultSpeech: "还没有识别结果。",
    resultAnnouncement: "还没有识别结果。打开实时辅助或点精确确认。",
    spokenLive: "",
    riskClass: "",
    riskText: "",
    confidenceText: "",
    obstacleText: "",
    distanceBandText: "无法判断",
    mainTaskText: "",
    showDebug: false,
    debugStatus: "",
    debugLatency: "",
    debugProfile: "",
    debugModel: "",
    debugRaw: "",
    safetyText: config.SAFETY_TEXT,
  },

  onLoad() {
    this._inferring = false
    this._timer = null
    this._wantLive = true
    this._lastResult = null
    this._lastSpeakAt = 0
    this._safetyShown = false
    this._recording = false
    this._voicePromise = null
    this._voicePressAt = 0
    this._voiceCancelBeforeStart = false
    this._resumeLive = false
    this._mainTaskText = ""
    this._sessionId = config.getSessionId()
    this._sensors = { camera: false, tof: false }
    this._tofMm = ""
    this.refreshSettings()
  },

  onShow() {
    this.refreshSettings()
    config.saveHighlights()
    if (this._wantLive && this.data.cameraReady && !this.data.liveOn) {
      this.startLive()
    }
  },

  onHide() {
    this._wantLive = this.data.liveOn
    this._cancelRecording(true)
    this.stopLive()
    tts.stop()
  },

  onUnload() {
    this._cancelRecording(true)
    this.stopLive()
    tts.stop()
  },

  refreshSettings() {
    const app = getApp()
    this.settings = config.loadSettings()
    app.globalData.settings = this.settings
  },

  onCameraInit() {
    this.setData({
      cameraReady: true,
      statusText: "摄像头已就绪",
      statusLabel: "当前状态：摄像头已就绪",
    })
    this.maybeShowSafetyThenStart()
  },

  onCameraError() {
    this.setData({
      cameraReady: false,
      statusText: "摄像头不可用",
      statusLabel: "当前状态：摄像头不可用",
    })
    this.askCameraPermission()
  },

  onCameraStop() {
    this._wantLive = this.data.liveOn
    this.stopLive()
  },

  maybeShowSafetyThenStart() {
    if (this._safetyShown) {
      if (this._wantLive) {
        this.startLive()
      }
      return
    }
    this._safetyShown = true
    const start = () => {
      config.markSafetySeen()
      if (this._wantLive) {
        this.startLive()
      }
    }
    if (config.hasSeenSafety()) {
      start()
      return
    }
    wx.showModal({
      title: "使用前请了解",
      content: config.SAFETY_TEXT,
      showCancel: false,
      confirmText: "我知道了",
      success: start,
      fail: start,
    })
  },

  async askCameraPermission() {
    const go = await a11y.confirmOpenSetting()
    if (!go) {
      return
    }
    wx.openSetting({
      success: (res) => {
        if (res.authSetting && res.authSetting["scope.camera"]) {
          this.setData({
            statusText: "已获得摄像头权限，请重新进入页面",
            statusLabel: "当前状态：已获得摄像头权限，请重新进入页面",
          })
        }
      },
    })
  },

  onToggleLive() {
    if (this._recording) {
      this._cancelRecording(true)
    }
    if (this.data.liveOn) {
      this._wantLive = false
      this.stopLive()
      return
    }
    this._wantLive = true
    this.startLive()
  },

  startLive(options) {
    if (this.data.liveOn) {
      return
    }
    if (!this.data.cameraReady) {
      a11y.showError("摄像头未就绪", "请允许摄像头权限后，再开始实时辅助。")
      return
    }
    const skipImmediate = Boolean(options && options.skipImmediate)
    const hunting = shortGoal(this._mainTaskText || this.data.mainTaskText)
    this.setData({
      liveOn: true,
      liveLabel: "停止实时辅助",
      liveAria: "停止实时辅助",
      statusText: hunting ? `正在帮你找${hunting}` : "实时辅助中",
      statusLabel: hunting ? `当前状态：正在帮你找${hunting}` : "当前状态：实时辅助中",
    })
    if (!skipImmediate) {
      this.captureAndInfer("live")
    }
    this._timer = setInterval(() => {
      this.captureAndInfer("live")
    }, 2500)
  },

  stopLive() {
    if (this._timer) {
      clearInterval(this._timer)
      this._timer = null
    }
    if (!this.data.liveOn && this.data.statusText !== "实时辅助中") {
      this.setData({
        liveOn: false,
        liveLabel: "开始实时辅助",
        liveAria: "开始实时辅助",
      })
      return
    }
    this.setData({
      liveOn: false,
      liveLabel: "开始实时辅助",
      liveAria: "开始实时辅助",
      statusText: this._inferring ? "正在识别" : "已停止",
      statusLabel: this._inferring ? "当前状态：正在识别" : "当前状态：已停止",
    })
  },

  onPreciseConfirm() {
    if (this._recording) {
      return
    }
    this.captureAndInfer("precise")
  },

  async onVoicePress() {
    if (this._recording || this.data.busy) {
      return
    }
    this._voiceCancelBeforeStart = false
    this.refreshSettings()
    const allowed = await this._ensureRecordPermission()
    if (!allowed) {
      return
    }
    if (this._voiceCancelBeforeStart) {
      this._voiceCancelBeforeStart = false
      return
    }
    this._resumeLive = this.data.liveOn
    if (this.data.liveOn) {
      this.stopLive()
    }
    tts.stop()
    this._recording = true
    this._voicePressAt = Date.now()
    this.setData({
      recording: true,
      voiceLabel: "松开结束",
      voiceAria: "正在录音，松开后识别你说的话。",
      statusText: "正在录音，请说话",
      statusLabel: "当前状态：正在录音，请说话",
    })
    wx.vibrateShort({ type: "medium" })
    try {
      this._voicePromise = asr.startPluginRecognition()
    } catch (error) {
      this._recording = false
      this._voiceFail(error.message || "无法开始录音")
    }
  },

  async onVoiceRelease() {
    if (!this._recording) {
      this._voiceCancelBeforeStart = true
      return
    }
    const held = Date.now() - (this._voicePressAt || 0)
    this._recording = false
    this.setData({
      recording: false,
      voiceLabel: "按住说话",
      voiceAria: "按住说话说出需求，松开后开始识别。",
      statusText: "正在把语音交给模型",
      statusLabel: "当前状态：正在把语音交给模型",
    })
    asr.stopPluginRecognition()
    if (held < 400) {
      this._voiceFail("按得太短，请按住说话后再松开。")
      return
    }
    try {
      const voice = await (this._voicePromise || Promise.reject(new Error("录音未开始")))
      this._voicePromise = null
      const text = String((voice && voice.text) || "").trim()
      if (!text) {
        this._voiceFail("没听清，请长按再说一次。")
        return
      }
      this.setData({
        question: text,
        statusText: `听到：${text}，正在帮你找`,
        statusLabel: `当前状态：听到 ${text}，正在帮你找`,
        spokenLive: `听到：${text}，正在帮你找`,
      })
      await this.captureAndInfer("precise", text)
      this._restoreAfterVoice()
    } catch (error) {
      this._voicePromise = null
      this._voiceFail(error.message || "没听清，请长按再说一次。")
    }
  },

  _cancelRecording(silent) {
    if (!this._recording && !this._voicePromise) {
      return
    }
    this._recording = false
    this._voicePromise = null
    this._resumeLive = silent ? false : this._resumeLive
    asr.stopPluginRecognition()
    this.setData({
      recording: false,
      voiceLabel: "按住说话",
      voiceAria: "按住说话说出需求，松开后开始识别。",
    })
    if (!silent) {
      this._restoreAfterVoice()
    }
  },

  async _ensureRecordPermission() {
    try {
      const setting = await new Promise((resolve, reject) => {
        wx.getSetting({
          success: resolve,
          fail: reject,
        })
      })
      if (setting.authSetting && setting.authSetting["scope.record"]) {
        return true
      }
      if (setting.authSetting && setting.authSetting["scope.record"] === false) {
        const go = await this._confirmOpenRecordSetting()
        if (!go) {
          return false
        }
        const opened = await new Promise((resolve) => {
          wx.openSetting({
            success: (res) => resolve(Boolean(res.authSetting && res.authSetting["scope.record"])),
            fail: () => resolve(false),
          })
        })
        return opened
      }
      await new Promise((resolve, reject) => {
        wx.authorize({
          scope: "scope.record",
          success: resolve,
          fail: reject,
        })
      })
      return true
    } catch (error) {
      a11y.showError("需要麦克风权限", "请允许使用麦克风，才能用语音说出要找什么。")
      return false
    }
  },

  _confirmOpenRecordSetting() {
    return new Promise((resolve) => {
      wx.showModal({
        title: "需要麦克风权限",
        content: "请在设置中允许使用麦克风，才能语音说出需求。",
        confirmText: "去设置",
        cancelText: "取消",
        success(res) {
          resolve(Boolean(res.confirm))
        },
        fail() {
          resolve(false)
        },
      })
    })
  },

  _voiceFail(message) {
    const speech = message || "没听清，请长按再说一次。"
    this.setData({
      statusText: speech,
      statusLabel: `当前状态：${speech}`,
    })
    tts.speak(this.settings, speech)
    this._restoreAfterVoice()
  },

  _restoreAfterVoice() {
    const resume = this._resumeLive
    this._resumeLive = false
    if (resume && this.data.cameraReady && !this.data.liveOn) {
      this._wantLive = true
      this.startLive({ skipImmediate: true })
      return
    }
    if (!this.data.liveOn && !this._inferring) {
      const hunting = shortGoal(this._mainTaskText || this.data.mainTaskText)
      this.setData({
        statusText: hunting ? `正在帮你找${hunting}` : "已停止",
        statusLabel: hunting ? `当前状态：正在帮你找${hunting}` : "当前状态：已停止",
      })
    }
  },

  onSelectTask(event) {
    const id = event.currentTarget.dataset.id
    const task = config.taskById(id)
    this.setData({
      taskId: task.id,
      question: task.question,
      tasks: decorateTasks(task.id),
    })
  },

  onQuestionInput(event) {
    this.setData({ question: event.detail.value })
  },

  onReplay() {
    const speech = this.data.resultSpeech
    if (!this.data.result) {
      a11y.showError("没有可播报内容", "请先完成一次识别。")
      return
    }
    tts.speak(this.settings, speech)
  },

  onStopSpeech() {
    tts.stop()
  },

  onOpenSettings() {
    this._wantLive = this.data.liveOn
    this.stopLive()
    wx.navigateTo({ url: "/pages/settings/settings" })
  },

  onToggleDebug() {
    this.setData({ showDebug: !this.data.showDebug })
  },

  takePhoto(quality) {
    return new Promise((resolve, reject) => {
      const camera = wx.createCameraContext()
      camera.takePhoto({
        quality,
        success: (res) => resolve(res.tempImagePath),
        fail: (error) => reject(new Error(error.errMsg || "拍照失败")),
      })
    })
  },

  async captureAndInfer(mode, spokenText) {
    if (this._inferring) {
      return
    }
    if (!this.data.cameraReady) {
      if (mode === "precise") {
        a11y.showError("摄像头未就绪", "请允许摄像头权限后再识别。")
      }
      return
    }
    this.refreshSettings()
    if (!this.settings.apiBaseUrl) {
      if (mode === "precise") {
        a11y.showError("未配置服务器", "请先到设置页填写 API 地址。")
      }
      return
    }

    const spoken = spokenText || ""
    const forceLook = mode === "precise" && !spoken
    if (!spoken && !forceLook && this._sensors && this._sensors.camera === false) {
      return
    }
    this._inferring = true
    const isPrecise = mode === "precise"
    this.setData({
      busy: isPrecise,
      statusText: isPrecise ? "正在精确确认" : this.data.liveOn ? "实时辅助中，正在识别" : "正在识别",
      statusLabel: isPrecise ? "当前状态：正在精确确认" : "当前状态：正在识别",
    })

    try {
      const filePath = await this.takePhoto(isPrecise ? "high" : "normal")
      const distanceBand = await depth.estimateDistanceBand(filePath, {
        enabled: this.settings.enableDepth,
      })
      const payload = await api.infer(this.settings, filePath, {
        question: this.data.question,
        mode,
        modelProfile: this.settings.modelProfile,
        distanceBand,
        sessionId: this._sessionId || config.getSessionId(),
        userId: config.getUserId(),
        spokenText: spoken,
        tofMm: this._sensors && this._sensors.tof ? this._tofMm || "" : "",
      })
      this.handleInferPayload(payload, mode)
    } catch (error) {
      if (error && error.code === 429) {
        return
      }
      if (isPrecise) {
        a11y.showError("识别失败", error.message || "请检查服务器地址、口令和网络。")
      }
      this.setData({
        statusText: this.data.liveOn ? "实时辅助中，上次识别失败" : "识别失败",
        statusLabel: this.data.liveOn ? "当前状态：实时辅助中，上次识别失败" : "当前状态：识别失败",
      })
    } finally {
      this._inferring = false
      const liveOn = this.data.liveOn
      const current = this.data.statusText
      const keep = current.indexOf("失败") >= 0
      const hunting = shortGoal(this._mainTaskText || this.data.mainTaskText)
      const liveStatus = hunting ? `正在帮你找${hunting}` : "实时辅助中"
      this.setData({
        busy: false,
        statusText: liveOn ? liveStatus : keep ? current : hunting ? `正在帮你找${hunting}` : "已停止",
        statusLabel: liveOn
          ? `当前状态：${liveStatus}`
          : keep
            ? `当前状态：${current}`
            : hunting
              ? `当前状态：正在帮你找${hunting}`
              : "当前状态：已停止",
      })
    }
  },

  handleInferPayload(payload, mode) {
    if (!payload) {
      return
    }
    if (payload.status === "error") {
      throw new Error(payload.error || "远程模型调用失败")
    }
    const result = payload.result || null
    const now = Date.now()
    const announcement = result
      ? a11y.buildResultAnnouncement(result)
      : "结果无法解析，请再试一次。"
    const speech = result ? result.speech || announcement : "请再试一次。"
    if (payload.sensors && typeof payload.sensors.camera === "boolean") {
      this._sensors = payload.sensors
    }
    config.saveHighlights()
    const task = payload.task || {}
    const mainTask = task.main_task || (result && result.main_task) || this.data.mainTaskText || ""
    const speakNow =
      mode === "precise" ||
      a11y.shouldSpeak(this._lastResult, result, this._lastSpeakAt, now)

    this._lastResult = result
    const patch = {
      result,
      resultSpeech: speech,
      memoryNow: mainTask || "还没有",
      riskClass: result ? `risk-${result.risk_level}` : "",
      riskText: result ? a11y.riskLabel(result.risk_level) : "",
      confidenceText: result ? a11y.confidenceLabel(result.confidence) : "",
      obstacleText: result ? a11y.joinObstacles(result.obstacles) : "",
      distanceBandText: (result && result.distance_band) || "无法判断",
      mainTaskText: mainTask,
      debugStatus: payload.status || "",
      debugLatency: payload.latency_ms != null ? `${payload.latency_ms} 毫秒` : "",
      debugProfile: payload.profile || "",
      debugModel: payload.model || "",
      debugRaw: payload.raw_answer || payload.error || "",
    }
    if (task.intent) {
      patch.taskId = task.intent
      patch.question = task.question || this.data.question
      patch.tasks = decorateTasks(task.intent)
    }
    this._mainTaskText = mainTask
    const hunting = shortGoal(mainTask)
    if (hunting) {
      patch.statusText = `正在帮你找${hunting}`
      patch.statusLabel = `当前状态：正在帮你找${hunting}`
    }
    let spoken = wrapTaskSpeech(task.decision, mainTask, speech)
    if (speakNow) {
      this._lastSpeakAt = now
      patch.resultAnnouncement = spoken
      patch.spokenLive = spoken
      patch.resultSpeech = spoken
    }
    this.setData(patch)

    if (result) {
      a11y.vibrateForRisk(result.risk_level)
    }
    if (speakNow) {
      tts.speak(this.settings, spoken)
    }
  },
})

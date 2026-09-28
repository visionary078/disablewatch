const api = require("../../utils/api")
const a11y = require("../../utils/a11y")
const config = require("../../utils/config")

Page({
  data: {
    apiBaseUrl: "",
    appToken: "",
    modelProfile: "",
    enableAppTts: true,
    enableDepth: true,
    playbackRatePercent: 90,
    playbackRateText: "0.9 倍",
    healthText: "",
    safetyText: config.SAFETY_TEXT,
  },

  onLoad() {
    this.fill(config.loadSettings())
  },

  fill(settings) {
    const percent = Math.round((settings.playbackRate || 0.9) * 100)
    this.setData({
      apiBaseUrl: settings.apiBaseUrl,
      appToken: settings.appToken,
      modelProfile: settings.modelProfile,
      enableAppTts: settings.enableAppTts,
      enableDepth: settings.enableDepth,
      playbackRatePercent: percent,
      playbackRateText: `${(percent / 100).toFixed(1)} 倍`,
      healthText: "",
    })
  },

  onApiBaseUrl(event) {
    this.setData({ apiBaseUrl: event.detail.value })
  },

  onAppToken(event) {
    this.setData({ appToken: event.detail.value })
  },

  onModelProfile(event) {
    this.setData({ modelProfile: event.detail.value })
  },

  onToggleTts() {
    this.setData({ enableAppTts: !this.data.enableAppTts })
  },

  onToggleDepth() {
    this.setData({ enableDepth: !this.data.enableDepth })
  },

  onPlaybackRate(event) {
    const percent = Number(event.detail.value || 90)
    this.setData({
      playbackRatePercent: percent,
      playbackRateText: `${(percent / 100).toFixed(1)} 倍`,
    })
  },

  currentSettings() {
    return {
      apiBaseUrl: String(this.data.apiBaseUrl || "").trim().replace(/\/$/, ""),
      appToken: String(this.data.appToken || "").trim(),
      modelProfile: String(this.data.modelProfile || "").trim(),
      enableAppTts: this.data.enableAppTts,
      enableDepth: this.data.enableDepth,
      playbackRate: this.data.playbackRatePercent / 100,
    }
  },

  onSave() {
    const settings = config.saveSettings(this.currentSettings())
    getApp().globalData.settings = settings
    wx.showModal({
      title: "已保存",
      content: "服务器地址和口令已保存在本机。返回后可继续实时辅助。",
      showCancel: false,
      confirmText: "好的",
    })
  },

  async onCheckHealth() {
    const settings = this.currentSettings()
    this.setData({ healthText: "正在检查服务器…" })
    try {
      const data = await api.health(settings)
      const mock = data.model_gateway && data.model_gateway.mock ? "，当前是 Mock 演示模式" : ""
      this.setData({
        healthText: `服务器可用${mock}。状态：${data.status || "ok"}。`,
      })
    } catch (error) {
      this.setData({ healthText: error.message || "服务器不可用" })
      a11y.showError("无法连接服务器", error.message || "请检查地址、HTTPS 证书和微信合法域名。")
    }
  },
})

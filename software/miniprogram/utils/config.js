const KEYS = {
  apiBaseUrl: "apiBaseUrl",
  appToken: "appToken",
  enableAppTts: "enableAppTts",
  playbackRate: "playbackRate",
  modelProfile: "modelProfile",
  enableDepth: "enableDepth",
  safetySeen: "safetySeen",
}

const TASKS = [
  {
    id: "find_entrance",
    label: "找入口",
    question: "入口在哪个方向？请先提醒风险。",
  },
  {
    id: "find_product",
    label: "找商品",
    question: "请帮我找目标商品，先提醒风险，再给出方向。",
  },
  {
    id: "read_price",
    label: "识别价格",
    question: "请读取看得见的商品名称和价格，看不清的文字不要猜测。",
  },
  {
    id: "find_cashier",
    label: "找收银台",
    question: "收银台或服务台在哪个方向？请先提醒通道障碍。",
  },
  {
    id: "avoid_obstacle",
    label: "障碍提醒",
    question: "请判断当前是否安全，并告诉我目标在哪个方向。",
  },
]

const SAFETY_TEXT =
  "本软件只提供环境信息辅助，不能替代导盲杖、导盲犬、无障碍设施或人工协助。不能用于过马路决策，也不能保证通道绝对安全。距离只是粗估计。不确定时请停下，重新确认或寻求协助。"

function readBool(key, defaultValue) {
  const value = wx.getStorageSync(key)
  if (value === "" || value === undefined || value === null) {
    return defaultValue
  }
  return Boolean(value)
}

function loadSettings() {
  const rate = Number(wx.getStorageSync(KEYS.playbackRate) || 0.9)
  return {
    apiBaseUrl: String(wx.getStorageSync(KEYS.apiBaseUrl) || "https://watchapi.divesee.com").replace(/\/$/, ""),
    appToken: String(wx.getStorageSync(KEYS.appToken) || ""),
    enableAppTts: readBool(KEYS.enableAppTts, true),
    playbackRate: rate > 0 && rate <= 1.5 ? rate : 0.9,
    modelProfile: String(wx.getStorageSync(KEYS.modelProfile) || ""),
    enableDepth: readBool(KEYS.enableDepth, true),
  }
}

function saveSettings(partial) {
  const next = Object.assign(loadSettings(), partial || {})
  wx.setStorageSync(KEYS.apiBaseUrl, next.apiBaseUrl)
  wx.setStorageSync(KEYS.appToken, next.appToken)
  wx.setStorageSync(KEYS.enableAppTts, next.enableAppTts)
  wx.setStorageSync(KEYS.playbackRate, next.playbackRate)
  wx.setStorageSync(KEYS.modelProfile, next.modelProfile)
  wx.setStorageSync(KEYS.enableDepth, next.enableDepth)
  return next
}

function hasSeenSafety() {
  return Boolean(wx.getStorageSync(KEYS.safetySeen))
}

function markSafetySeen() {
  wx.setStorageSync(KEYS.safetySeen, true)
}

function getSessionId() {
  let id = String(wx.getStorageSync("sessionId") || "")
  if (!id) {
    id = `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
    wx.setStorageSync("sessionId", id)
  }
  return id
}

function getUserId() {
  let id = String(wx.getStorageSync("userId") || "")
  if (!id) {
    id = `u${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
    wx.setStorageSync("userId", id)
  }
  return id
}

function loadHighlights() {
  const value = wx.getStorageSync("memoryHighlights")
  return Array.isArray(value) ? value.filter(Boolean).slice(0, 20) : []
}

function saveHighlights(items) {
  const incoming = Array.isArray(items) ? items.map((item) => String(item || "").trim()).filter(Boolean) : []
  if (!incoming.length) {
    return loadHighlights()
  }
  const merged = []
  incoming.concat(loadHighlights()).forEach((item) => {
    if (merged.indexOf(item) < 0 && merged.length < 20) {
      merged.push(item)
    }
  })
  wx.setStorageSync("memoryHighlights", merged)
  return merged
}

function taskById(id) {
  return TASKS.find((item) => item.id === id) || TASKS[4]
}

module.exports = {
  KEYS,
  TASKS,
  SAFETY_TEXT,
  loadSettings,
  saveSettings,
  hasSeenSafety,
  markSafetySeen,
  taskById,
  getSessionId,
  getUserId,
  loadHighlights,
  saveHighlights,
}

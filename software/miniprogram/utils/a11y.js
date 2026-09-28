const RISK_LABELS = {
  high: "高风险",
  medium: "中等风险",
  low: "较低风险",
}

function riskLabel(level) {
  return RISK_LABELS[level] || "风险未知"
}

function confidenceLabel(level) {
  if (level === "high") return "把握较高"
  if (level === "medium") return "把握一般"
  if (level === "low") return "把握较低"
  return "把握未知"
}

function joinObstacles(obstacles) {
  if (!Array.isArray(obstacles) || !obstacles.length) {
    return "未确认明显障碍"
  }
  return obstacles.join("、")
}

const SPEAK_GAP_MS = 4000

function compactSpeech(text) {
  return String(text || "")
    .replace(/\s+/g, "")
    .replace(/[。．，,、！!？?；;：:]/g, "")
}

function buildResultAnnouncement(result) {
  if (!result) {
    return "还没有识别结果。"
  }
  return String(result.speech || result.action || "还没有可用的识别结果。")
}

function shouldSpeak(previous, next, lastSpeakAt, now) {
  if (!next) {
    return false
  }
  const nextSpeech = compactSpeech(next.speech || next.action || "")
  if (!nextSpeech) {
    return false
  }
  if (!previous) {
    return true
  }
  if (next.risk_level === "high" && previous.risk_level !== "high") {
    return true
  }
  const prevSpeech = compactSpeech(previous.speech || previous.action || "")
  if (nextSpeech !== prevSpeech) {
    return true
  }
  const gap = Number(now || 0) - Number(lastSpeakAt || 0)
  const stillUncertain = next.direction === "未确定" || nextSpeech.indexOf("继续观察") >= 0
  return stillUncertain && gap >= SPEAK_GAP_MS
}

function vibrateForRisk(level) {
  if (level === "high") {
    wx.vibrateLong()
    return
  }
  if (level === "medium") {
    wx.vibrateShort({ type: "medium" })
  }
}

function showError(title, content) {
  wx.showModal({
    title: title || "提示",
    content: content || "出现问题，请重试。",
    showCancel: false,
    confirmText: "知道了",
  })
}

function confirmOpenSetting(content) {
  return new Promise((resolve) => {
    wx.showModal({
      title: "需要摄像头权限",
      content: content || "请在设置中允许使用摄像头，才能拍摄当前环境。",
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
}

module.exports = {
  SPEAK_GAP_MS,
  riskLabel,
  confidenceLabel,
  joinObstacles,
  compactSpeech,
  buildResultAnnouncement,
  shouldSpeak,
  vibrateForRisk,
  showError,
  confirmOpenSetting,
}

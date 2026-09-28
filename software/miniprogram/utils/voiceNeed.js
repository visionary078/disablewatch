const config = require("./config")

function applySpokenNeed(rawText) {
  const text = String(rawText || "").replace(/\s+/g, " ").trim()
  if (!text) {
    return null
  }
  if (/入口|大门|正门|门在哪/.test(text)) {
    return {
      taskId: "find_entrance",
      question: text.indexOf("方向") >= 0 ? text : config.taskById("find_entrance").question,
    }
  }
  if (/收银|付款|结账|服务台/.test(text)) {
    return {
      taskId: "find_cashier",
      question: text.indexOf("方向") >= 0 ? text : config.taskById("find_cashier").question,
    }
  }
  if (/价格|多少钱|价签|标签/.test(text)) {
    return {
      taskId: "read_price",
      question: text.indexOf("价格") >= 0 || text.indexOf("多少钱") >= 0
        ? text
        : config.taskById("read_price").question,
    }
  }
  if (/障碍|台阶|安全|能不能走|路面/.test(text)) {
    return {
      taskId: "avoid_obstacle",
      question: text.length > 4 ? text : config.taskById("avoid_obstacle").question,
    }
  }
  return {
    taskId: "find_product",
    question: `请帮我找：${text}。先提醒风险，再给出方向。看不清不要猜测。`,
  }
}

function confirmSpeech(question) {
  const short = String(question || "").replace(/\s+/g, "").slice(0, 24)
  return short ? `已记下，${short}。正在按你的需求识别。` : "已记下你的需求，正在识别。"
}

module.exports = {
  applySpokenNeed,
  confirmSpeech,
}

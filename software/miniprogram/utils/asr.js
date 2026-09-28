let _plugin = null
let _pluginChecked = false
let _manager = null
let _pending = null

function loadPlugin() {
  if (_pluginChecked) {
    return _plugin
  }
  _pluginChecked = true
  try {
    _plugin = requirePlugin("WechatSI")
  } catch (error) {
    _plugin = null
  }
  return _plugin
}

function pluginAvailable() {
  const plugin = loadPlugin()
  return Boolean(plugin && typeof plugin.getRecordRecognitionManager === "function")
}

function getManager() {
  if (_manager) {
    return _manager
  }
  const plugin = loadPlugin()
  if (!plugin || typeof plugin.getRecordRecognitionManager !== "function") {
    throw new Error("同声传译插件不可用。请在公众平台添加 WechatSI 0.3.9（wx069ba97219f66d99）后重新编译。")
  }
  _manager = plugin.getRecordRecognitionManager()
  return _manager
}

function startPluginRecognition() {
  if (_pending) {
    return _pending
  }
  _pending = new Promise((resolve, reject) => {
    let manager
    try {
      manager = getManager()
    } catch (error) {
      _pending = null
      reject(error)
      return
    }
    manager.onRecognize = () => {}
    manager.onStop = (res) => {
      _pending = null
      const text = String((res && (res.result || res.translateResult)) || "").trim()
      if (text) {
        resolve({ text, filePath: (res && res.tempFilePath) || "" })
        return
      }
      reject(new Error("没听清，请长按再说一次。"))
    }
    manager.onError = (err) => {
      _pending = null
      const message =
        (err && (err.msg || err.errMsg || err.message)) || "语音识别失败"
      reject(new Error(message))
    }
    try {
      manager.start({ duration: 60000, lang: "zh_CN" })
    } catch (error) {
      _pending = null
      reject(error)
    }
  })
  return _pending
}

function stopPluginRecognition() {
  if (!_manager) {
    return
  }
  try {
    _manager.stop()
  } catch (error) {
    // ignore
  }
}

function translateVoiceFile(filePath) {
  return new Promise((resolve, reject) => {
    const plugin = loadPlugin()
    if (!plugin || typeof plugin.translateVoice !== "function") {
      reject(new Error("同声传译插件未提供 translateVoice"))
      return
    }
    plugin.translateVoice({
      filePath,
      lfrom: "zh_CN",
      lto: "zh_CN",
      success(res) {
        const text = String((res && res.result) || "").trim()
        if (text) {
          resolve({ text })
          return
        }
        reject(new Error("没听清，请长按再说一次。"))
      },
      fail(err) {
        reject(new Error((err && (err.errMsg || err.msg)) || "语音转写失败"))
      },
    })
  })
}

module.exports = {
  pluginAvailable,
  startPluginRecognition,
  stopPluginRecognition,
  translateVoiceFile,
}

function requestHeaders(settings) {
  const headers = {}
  if (settings.appToken) {
    headers["X-App-Token"] = settings.appToken
  }
  return headers
}

function unwrapError(error, fallback) {
  if (!error) {
    return fallback
  }
  if (typeof error === "string") {
    return error
  }
  return error.errMsg || error.message || fallback
}

function health(settings) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${settings.apiBaseUrl}/health`,
      method: "GET",
      header: requestHeaders(settings),
      timeout: 8000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
          return
        }
        reject(new Error(`健康检查失败，状态码 ${res.statusCode}`))
      },
      fail(error) {
        reject(new Error(unwrapError(error, "无法连接服务器，请检查地址、HTTPS 和合法域名。")))
      },
    })
  })
}

function models(settings) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${settings.apiBaseUrl}/models`,
      method: "GET",
      header: requestHeaders(settings),
      timeout: 8000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
          return
        }
        reject(new Error(`读取模型配置失败，状态码 ${res.statusCode}`))
      },
      fail(error) {
        reject(new Error(unwrapError(error, "无法读取模型配置。")))
      },
    })
  })
}

function infer(settings, filePath, options) {
  const payload = options || {}
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${settings.apiBaseUrl}/infer`,
      filePath,
      name: "image",
      header: requestHeaders(settings),
      formData: {
        question: payload.question || "请判断当前是否安全，并告诉我目标在哪个方向。",
        mode: payload.mode || "precise",
        model_profile: payload.modelProfile || settings.modelProfile || "",
        model: payload.model || "",
        distance_band: payload.distanceBand || "",
        session_id: payload.sessionId || "",
        user_id: payload.userId || "",
        spoken_text: payload.spokenText || "",
        tof_mm: payload.tofMm || "",
      },
      timeout: 120000,
      success(res) {
        let data = res.data
        if (typeof data === "string") {
          try {
            data = JSON.parse(data)
          } catch (error) {
            reject(new Error("服务器返回了无法解析的结果。"))
            return
          }
        }
        if (res.statusCode === 429) {
          const err = new Error(data.detail || "上一帧仍在识别中")
          err.code = 429
          reject(err)
          return
        }
        if (res.statusCode === 401) {
          reject(new Error("业务口令无效，请到设置页检查 APP_TOKEN。"))
          return
        }
        if (res.statusCode >= 400) {
          reject(new Error(data.detail || `识别失败，状态码 ${res.statusCode}`))
          return
        }
        resolve(data)
      },
      fail(error) {
        reject(new Error(unwrapError(error, "上传图片失败，请检查网络和服务器地址。")))
      },
    })
  })
}

function transcribe(settings, filePath) {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${settings.apiBaseUrl}/asr`,
      filePath,
      name: "audio",
      header: requestHeaders(settings),
      formData: {},
      timeout: 30000,
      success(res) {
        let data = res.data
        if (typeof data === "string") {
          try {
            data = JSON.parse(data)
          } catch (error) {
            reject(new Error("服务器返回了无法解析的语音结果。"))
            return
          }
        }
        if (res.statusCode === 401) {
          reject(new Error("业务口令无效，请到设置页检查 APP_TOKEN。"))
          return
        }
        if (res.statusCode === 429) {
          reject(new Error("上一段语音仍在识别中，请稍后重试。"))
          return
        }
        if (res.statusCode === 501) {
          reject(new Error("服务器尚未开通语音识别。"))
          return
        }
        if (res.statusCode >= 400) {
          reject(new Error((data && data.detail) || `语音识别失败，状态码 ${res.statusCode}`))
          return
        }
        resolve(data)
      },
      fail(error) {
        reject(new Error(unwrapError(error, "上传录音失败，请检查网络和服务器地址。")))
      },
    })
  })
}

function clearHighlights(settings, userId) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${settings.apiBaseUrl}/highlights?user_id=${encodeURIComponent(userId || "")}`,
      method: "DELETE",
      header: requestHeaders(settings),
      timeout: 8000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data || {})
          return
        }
        reject(new Error("没删掉"))
      },
      fail(error) {
        reject(new Error(unwrapError(error, "没删掉")))
      },
    })
  })
}

function synthesizeSpeech(settings, text) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${settings.apiBaseUrl}/tts`,
      method: "POST",
      header: Object.assign(
        {
          "Content-Type": "application/json",
        },
        requestHeaders(settings)
      ),
      data: { text },
      responseType: "arraybuffer",
      timeout: 20000,
      success(res) {
        if (res.statusCode === 501 || res.statusCode === 503) {
          reject(new Error("TTS_UNAVAILABLE"))
          return
        }
        if (res.statusCode >= 400) {
          reject(new Error("语音合成失败"))
          return
        }
        resolve(res.data)
      },
      fail(error) {
        reject(new Error(unwrapError(error, "语音合成请求失败")))
      },
    })
  })
}

module.exports = {
  health,
  models,
  infer,
  transcribe,
  synthesizeSpeech,
  clearHighlights,
}

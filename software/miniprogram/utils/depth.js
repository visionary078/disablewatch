const BANDS = ["一臂内", "较近", "较远", "无法判断"]

function valueToBand(avg, maxValue) {
  if (!(avg > 0) || Number.isNaN(avg)) {
    return "无法判断"
  }
  const normalized = maxValue <= 1.5
  if (normalized) {
    if (avg < 0.25) return "一臂内"
    if (avg < 0.55) return "较近"
    return "较远"
  }
  if (avg < 0.6) return "一臂内"
  if (avg < 1.8) return "较近"
  return "较远"
}

function sampleCenter(depthArray, width, height) {
  if (!depthArray || !width || !height) {
    return "无法判断"
  }
  const data = depthArray instanceof Float32Array ? depthArray : new Float32Array(depthArray)
  const cx = Math.floor(width / 2)
  const cy = Math.floor(height / 2)
  const radius = Math.max(1, Math.floor(Math.min(width, height) * 0.08))
  let sum = 0
  let count = 0
  let maxValue = 0
  for (let y = cy - radius; y <= cy + radius; y += 1) {
    for (let x = cx - radius; x <= cx + radius; x += 1) {
      if (x < 0 || y < 0 || x >= width || y >= height) {
        continue
      }
      const value = data[y * width + x]
      if (!(value > 0) || Number.isNaN(value)) {
        continue
      }
      sum += value
      count += 1
      if (value > maxValue) {
        maxValue = value
      }
    }
  }
  if (!count) {
    return "无法判断"
  }
  return valueToBand(sum / count, maxValue)
}

function loadRgba(imagePath, size) {
  return new Promise((resolve, reject) => {
    if (typeof wx.createOffscreenCanvas !== "function") {
      reject(new Error("NO_OFFSCREEN_CANVAS"))
      return
    }
    const canvas = wx.createOffscreenCanvas({ type: "2d", width: size, height: size })
    const ctx = canvas.getContext("2d")
    const image = canvas.createImage()
    image.onload = () => {
      try {
        ctx.drawImage(image, 0, 0, size, size)
        const imageData = ctx.getImageData(0, 0, size, size)
        resolve({
          buffer: imageData.data.buffer,
          width: size,
          height: size,
        })
      } catch (error) {
        reject(error)
      }
    }
    image.onerror = () => reject(new Error("IMAGE_LOAD_FAILED"))
    image.src = imagePath
  })
}

function createSession() {
  if (typeof wx.createVKSession !== "function") {
    return null
  }
  try {
    return wx.createVKSession({
      track: {
        depth: {
          mode: 2,
        },
      },
    })
  } catch (error) {
    return null
  }
}

function estimateDistanceBand(imagePath, options) {
  const enabled = !options || options.enabled !== false
  if (!enabled || !imagePath) {
    return Promise.resolve("无法判断")
  }
  return new Promise((resolve) => {
    let done = false
    const timer = setTimeout(() => finish("无法判断"), 1800)
    const finish = (band) => {
      if (done) {
        return
      }
      done = true
      clearTimeout(timer)
      resolve(BANDS.indexOf(band) >= 0 ? band : "无法判断")
    }
    const session = createSession()
    if (!session) {
      finish("无法判断")
      return
    }
    loadRgba(imagePath, 160)
      .then((frame) => {
        session.on("updateAnchors", (anchors) => {
          try {
            const anchor = (anchors || [])[0]
            if (!anchor) {
              finish("无法判断")
              return
            }
            const size = anchor.size || [frame.width, frame.height]
            const width = size[0] || frame.width
            const height = size[1] || frame.height
            finish(sampleCenter(anchor.depthArray, width, height))
          } catch (error) {
            finish("无法判断")
          } finally {
            try {
              session.stop()
            } catch (stopError) {
              // ignore
            }
          }
        })
        session.start((errno) => {
          if (errno) {
            finish("无法判断")
            return
          }
          try {
            session.detectDepth({
              frameBuffer: frame.buffer,
              width: frame.width,
              height: frame.height,
            })
          } catch (error) {
            finish("无法判断")
          }
        })
      })
      .catch(() => finish("无法判断"))
  })
}

module.exports = {
  estimateDistanceBand,
  sampleCenter,
  valueToBand,
}

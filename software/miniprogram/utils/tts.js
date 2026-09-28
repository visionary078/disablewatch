const api = require("./api")

let audio = null
let playingPath = ""
let speakSeq = 0

function getAudio() {
  if (audio) {
    return audio
  }
  audio = wx.createInnerAudioContext()
  audio.obeyMuteSwitch = false
  audio.onError(() => {
    playingPath = ""
  })
  audio.onEnded(() => {
    playingPath = ""
  })
  return audio
}

function stop() {
  speakSeq += 1
  if (!audio) {
    return
  }
  try {
    audio.stop()
  } catch (error) {
    // ignore
  }
  playingPath = ""
}

function writeTempAudio(buffer, seq) {
  return new Promise((resolve, reject) => {
    const filePath = `${wx.env.USER_DATA_PATH}/nextstep-tts-${seq}.mp3`
    wx.getFileSystemManager().writeFile({
      filePath,
      data: buffer,
      success() {
        resolve(filePath)
      },
      fail(error) {
        reject(error)
      },
    })
  })
}

async function speak(settings, text) {
  const speech = String(text || "").trim()
  if (!speech || !settings.enableAppTts) {
    return false
  }
  const seq = ++speakSeq
  if (audio) {
    try {
      audio.stop()
    } catch (error) {
      // ignore
    }
  }
  const player = getAudio()
  player.playbackRate = settings.playbackRate || 0.9
  try {
    const buffer = await api.synthesizeSpeech(settings, speech)
    if (seq !== speakSeq) {
      return false
    }
    const filePath = await writeTempAudio(buffer, seq)
    if (seq !== speakSeq) {
      return false
    }
    playingPath = filePath
    player.src = filePath
    player.play()
    return true
  } catch (error) {
    return false
  }
}

function replay() {
  if (!audio || !playingPath) {
    return false
  }
  audio.stop()
  audio.src = playingPath
  audio.play()
  return true
}

module.exports = {
  speak,
  stop,
  replay,
}

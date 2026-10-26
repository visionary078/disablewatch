package com.seenext.phonehub

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import kotlin.math.abs
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import kotlin.concurrent.thread

class MainActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private val pcm = ByteArrayOutputStream()
    private var audio: AudioRecord? = null
    private var tts: TextToSpeech? = null
    private var holding = false
    private var recognizing = false
    private var recording = false
    @Volatile private var capturedVoice = false
    private var pressAt = 0L
    private var focus: AudioFocusRequest? = null
    private var lastSeq = -1
    private var lastSpeech = ""
    private var lastRisk = ""
    private var lastSpeakAt = 0L
    private var finished = false
    private var polling = false
    private lateinit var talk: Button
    private lateinit var speechView: TextView
    private lateinit var findingView: TextView
    private lateinit var status: TextView
    private lateinit var gateway: EditText
    private lateinit var toggle: Button

    private val poll = object : Runnable {
        override fun run() {
            if (!polling) {
                polling = true
                thread(name = "hub-speech") {
                    val payload = getJson("/speech")
                    runOnUiThread {
                        polling = false
                        if (payload != null) showSpeech(payload)
                    }
                }
            }
            handler.postDelayed(this, 700)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        gateway = findViewById(R.id.gateway)
        toggle = findViewById(R.id.toggle)
        status = findViewById(R.id.status)
        talk = findViewById(R.id.talk)
        speechView = findViewById(R.id.speech)
        findingView = findViewById(R.id.finding)
        val page = findViewById<TextView>(R.id.page)
        gateway.setText(savedKey(prefs))
        page.text = "照片和距离留在这台平板。说话后直接问小米。"
        toggle.setOnClickListener { startHub(checkPage = true) }
        talk.setOnClickListener {
            if (recognizing) return@setOnClickListener
            if (holding) {
                if (System.currentTimeMillis() - pressAt < 400) return@setOnClickListener
                endTalk()
            } else {
                beginTalk()
            }
        }
        tts = TextToSpeech(this) { code ->
            if (code == TextToSpeech.SUCCESS) {
                tts?.language = Locale.SIMPLIFIED_CHINESE
                tts?.setSpeechRate(0.92f)
            }
        }
        handler.postDelayed(poll, 700)
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        stopRecorder()
        tts?.shutdown()
        super.onDestroy()
    }

    private fun beginTalk() {
        if (holding || recognizing) return
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 2)
            sayAloud("先允许麦克风，再点一下说。")
            return
        }
        startHub(checkPage = false)
        tts?.stop()
        if (!startRecorder()) {
            sayAloud("麦克风没打开。")
            return
        }
        holding = true
        pressAt = System.currentTimeMillis()
        talk.text = "正在听\n说完停一下"
        speechView.text = "正在听。说完停一下，它自己结束。"
        handler.post(tick)
    }

    private val tick = object : Runnable {
        override fun run() {
            if (!holding || recognizing) return
            val seconds = ((System.currentTimeMillis() - pressAt) / 1000).toInt()
            talk.text = "正在听 $seconds 秒\n说完停一下"
            handler.postDelayed(this, 500)
        }
    }

    private fun startRecorder(): Boolean {
        val min = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (min <= 0) return false
        val rec = openMic(min * 4) ?: return false
        listenFocus(true)
        pcm.reset()
        capturedVoice = false
        recording = true
        rec.startRecording()
        if (rec.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            rec.release()
            listenFocus(false)
            recording = false
            return false
        }
        audio = rec
        val started = System.currentTimeMillis()
        thread(name = "mic") {
            val buf = ByteArray(min)
            var lastVoice = 0L
            while (recording) {
                val n = rec.read(buf, 0, buf.size)
                if (n <= 0) continue
                if (peakPcm(buf, n) >= SPEECH_PEAK) {
                    capturedVoice = true
                    lastVoice = System.currentTimeMillis()
                }
                var full = false
                synchronized(pcm) {
                    if (pcm.size() < MAX_PCM) pcm.write(buf, 0, n)
                    full = pcm.size() >= MAX_PCM
                }
                val elapsed = System.currentTimeMillis() - started
                val quiet = capturedVoice && lastVoice > 0 && System.currentTimeMillis() - lastVoice >= SILENCE_MS
                val gaveUp = !capturedVoice && elapsed >= WAIT_SPEECH_MS
                if (full || quiet || gaveUp || elapsed >= MAX_LISTEN_MS) {
                    handler.post { endTalk() }
                    break
                }
            }
        }
        return true
    }

    private fun openMic(buffer: Int): AudioRecord? {
        val sources = intArrayOf(MediaRecorder.AudioSource.VOICE_RECOGNITION, MediaRecorder.AudioSource.MIC)
        for (source in sources) {
            val rec = try {
                AudioRecord.Builder()
                    .setAudioSource(source)
                    .setAudioFormat(
                        AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(SAMPLE_RATE)
                            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                            .build()
                    )
                    .setBufferSizeInBytes(buffer)
                    .build()
            } catch (_: Exception) {
                null
            } ?: continue
            if (rec.state == AudioRecord.STATE_INITIALIZED) return rec
            rec.release()
        }
        return null
    }

    private fun peakPcm(buf: ByteArray, n: Int): Int {
        var peak = 0
        var i = 0
        val end = n - (n and 1)
        while (i < end) {
            val sample = (buf[i].toInt() and 0xff) or (buf[i + 1].toInt() shl 8)
            val level = abs(sample.toShort().toInt())
            if (level > peak) peak = level
            i += 2
        }
        return peak
    }

    private fun listenFocus(on: Boolean) {
        val manager = getSystemService(AUDIO_SERVICE) as AudioManager
        if (on) {
            val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .build()
            focus = request
            manager.requestAudioFocus(request)
        } else {
            focus?.let { manager.abandonAudioFocusRequest(it) }
            focus = null
        }
    }

    private fun stopRecorder() {
        recording = false
        try {
            audio?.stop()
        } catch (_: Exception) {
        }
        audio?.release()
        audio = null
        listenFocus(false)
    }

    private fun endTalk() {
        if (!holding || recognizing) return
        stopRecorder()
        handler.removeCallbacks(tick)
        if (!capturedVoice) {
            finishHold("", "没录到声音。再点一下说。")
            return
        }
        recognizing = true
        talk.text = "正在识别"
        speechView.text = "正在识别刚才的话。"
        val bytes = synchronized(pcm) { pcm.toByteArray() }
        thread(name = "hub-asr") {
            val heard = transcribe(bytes)
            runOnUiThread {
                recognizing = false
                if (!holding) holding = true
                if (heard.text.isEmpty()) finishHold("", heard.failed.ifEmpty { "没听清。再点一下说。" })
                else finishHold(heard.text)
            }
        }
    }

    private fun finishHold(text: String, failed: String = "") {
        if (!holding) return
        holding = false
        handler.removeCallbacks(tick)
        talk.text = "点一下说话"
        if (failed.isNotEmpty()) {
            sayAloud(failed)
            return
        }
        val spoken = text.trim()
        if (spoken.isEmpty()) {
            sayAloud("没听清。再点一下说。")
            return
        }
        speechView.text = spoken
        thread(name = "hub-say") {
            val result = postSay(spoken)
            runOnUiThread {
                val error = result?.optString("error").orEmpty()
                if (result == null) sayAloud("还没送到。先点开始接收。")
                else if (error.isNotEmpty()) sayAloud(error)
            }
        }
    }

    private fun startHub(checkPage: Boolean) {
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        val key = gateway.text.toString().trim().ifEmpty { BuildConfig.XIAOMI_API_KEY }
        prefs.edit().putString(HubService.EXTRA_API_KEY, key).apply()
        val intent = Intent(this, HubService::class.java)
            .putExtra(HubService.EXTRA_API_KEY, key)
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent) else startService(intent)
        toggle.text = "已在接收"
        if (!checkPage) return
        val page = findViewById<TextView>(R.id.page)
        page.text = "照片和距离留在这台平板。说话后直接问小米。"
        status.text = "正在检查本机页面"
        thread(name = "hub-self-test") {
            var message = "本机页面没打开"
            for (attempt in 1..8) {
                val payload = getJson("/status")
                if (payload != null) {
                    message = payload.toString()
                    break
                }
                message = "本机页面没打开"
                Thread.sleep(300)
            }
            runOnUiThread {
                status.text = if (message.startsWith("{")) "本机页面已打开\n$message" else message
            }
        }
    }

    private fun showSpeech(payload: JSONObject) {
        val seq = payload.optInt("seq", 0)
        if (seq == lastSeq) return
        lastSeq = seq
        val goal = shortGoal(payload.optString("main_task"))
        val decision = payload.optString("decision")
        if (decision == "done") finished = true
        else if (goal.isNotEmpty()) finished = false
        findingView.text = if (finished && goal.isEmpty()) "正在找：已经找到" else "正在找：${goal.ifEmpty { "还没有" }}"
        val err = payload.optString("error")
        if (err.isNotEmpty()) {
            sayAloud(err)
            return
        }
        val previousSpeech = lastSpeech
        val previousRisk = lastRisk
        lastSpeech = payload.optString("speech")
        lastRisk = payload.optString("risk_level")
        val line = wrapTaskSpeech(decision, payload.optString("main_task"), lastSpeech)
        val now = System.currentTimeMillis()
        val speakNow = payload.optString("mode") == "precise" || shouldSpeak(previousSpeech, previousRisk, payload, now)
        if (line.isNotEmpty()) speechView.text = line
        if (speakNow && line.isNotEmpty()) sayAloud(line)
    }

    private fun sayAloud(text: String) {
        val spoken = text.trim()
        if (spoken.isEmpty()) return
        speechView.text = spoken
        lastSpeakAt = System.currentTimeMillis()
        tts?.speak(spoken, TextToSpeech.QUEUE_FLUSH, null, "seenext")
    }

    private fun shouldSpeak(previousSpeech: String, previousRisk: String, next: JSONObject, now: Long): Boolean {
        val nextSpeech = compact(next.optString("speech"))
        if (nextSpeech.isEmpty()) return false
        if (previousSpeech.isEmpty()) return true
        if (next.optString("risk_level") == "high" && previousRisk != "high") return true
        if (nextSpeech != compact(previousSpeech)) return true
        val stillUncertain = next.optString("direction") == "未确定" || nextSpeech.contains("继续观察")
        return stillUncertain && now - lastSpeakAt >= SPEAK_GAP_MS
    }

    private fun shortGoal(mainTask: String): String {
        return mainTask.replace(Regex("[请帮我一下找买拿：:，,。；;]"), "").trim().take(12)
    }

    private fun wrapTaskSpeech(decision: String, mainTask: String, speech: String): String {
        val spoken = speech.trim()
        val goal = shortGoal(mainTask)
        if (goal.isEmpty() || spoken.contains("帮你找")) return spoken
        if (decision == "switch") return "好，改成找$goal。$spoken"
        if (decision == "refine") return "继续帮你找$goal。$spoken"
        return spoken
    }

    private fun compact(text: String): String {
        return text.replace(Regex("\\s+"), "").replace(Regex("[。．，,、！!？?；;：:]"), "")
    }

    private fun getJson(path: String): JSONObject? {
        return try {
            val conn = URL("http://127.0.0.1:${HubServer.PORT}$path").openConnection() as HttpURLConnection
            conn.connectTimeout = 800
            conn.readTimeout = 800
            if (conn.responseCode !in 200..299) return null
            val text = conn.inputStream.bufferedReader().use { it.readText() }
            if (text.startsWith("{")) JSONObject(text) else null
        } catch (_: Exception) {
            null
        }
    }

    private fun transcribe(pcmBytes: ByteArray): Heard {
        if (pcmBytes.size < SAMPLE_RATE / 2) return Heard(failed = "没录到声音。再点一下说。")
        val key = gateway.text.toString().trim().ifEmpty { BuildConfig.XIAOMI_API_KEY }
        if (!key.startsWith("sk-")) return Heard(failed = "先填小米密钥。")
        return try {
            val heard = Xiaomi.transcribe(key, wavBytes(pcmBytes))
            if (heard.isEmpty()) Heard(failed = "没听清。再点一下说。") else Heard(heard)
        } catch (error: Exception) {
            Heard(failed = error.message ?: "小米没接上。")
        }
    }

    private fun wavBytes(pcmBytes: ByteArray): ByteArray {
        val out = ByteArrayOutputStream(44 + pcmBytes.size)
        fun str(value: String) = out.write(value.toByteArray(Charsets.US_ASCII))
        fun intLe(value: Int) {
            out.write(value and 0xff)
            out.write(value shr 8 and 0xff)
            out.write(value shr 16 and 0xff)
            out.write(value shr 24 and 0xff)
        }
        fun shortLe(value: Int) {
            out.write(value and 0xff)
            out.write(value shr 8 and 0xff)
        }
        str("RIFF")
        intLe(36 + pcmBytes.size)
        str("WAVE")
        str("fmt ")
        intLe(16)
        shortLe(1)
        shortLe(1)
        intLe(SAMPLE_RATE)
        intLe(SAMPLE_RATE * 2)
        shortLe(2)
        shortLe(16)
        str("data")
        intLe(pcmBytes.size)
        out.write(pcmBytes)
        return out.toByteArray()
    }

    private fun postSay(spoken: String): JSONObject? {
        repeat(6) {
            try {
                val conn = URL("http://127.0.0.1:${HubServer.PORT}/say").openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.connectTimeout = 800
                conn.readTimeout = 2000
                conn.setRequestProperty("Content-Type", "application/json")
                val body = JSONObject().put("spoken_text", spoken).toString().toByteArray()
                conn.outputStream.use { it.write(body) }
                val code = conn.responseCode
                val stream = if (code in 200..299) conn.inputStream else conn.errorStream
                val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                return if (text.startsWith("{")) JSONObject(text) else JSONObject()
            } catch (_: Exception) {
                Thread.sleep(300)
            }
        }
        return null
    }

    private fun savedKey(prefs: android.content.SharedPreferences): String {
        val saved = prefs.getString(HubService.EXTRA_API_KEY, "").orEmpty().trim()
        if (saved.startsWith("sk-")) return saved
        val built = BuildConfig.XIAOMI_API_KEY.trim()
        return if (built.startsWith("sk-")) built else ""
    }

    private class Heard(val text: String = "", val failed: String = "")

    companion object {
        private const val PREFS = "phone-hub"
        private const val SPEAK_GAP_MS = 4000L
        private const val SAMPLE_RATE = 16000
        private const val MAX_PCM = SAMPLE_RATE * 2 * 20
        private const val SPEECH_PEAK = 700
        private const val SILENCE_MS = 1100L
        private const val MAX_LISTEN_MS = 15000L
        private const val WAIT_SPEECH_MS = 8000L
    }
}

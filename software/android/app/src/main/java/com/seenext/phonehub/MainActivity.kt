package com.seenext.phonehub

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.view.MotionEvent
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
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
    private var pressAt = 0L
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
    private lateinit var token: EditText
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
        token = findViewById(R.id.token)
        toggle = findViewById(R.id.toggle)
        status = findViewById(R.id.status)
        talk = findViewById(R.id.talk)
        speechView = findViewById(R.id.speech)
        findingView = findViewById(R.id.finding)
        val page = findViewById<TextView>(R.id.page)
        gateway.setText(prefs.getString(HubService.EXTRA_GATEWAY, "https://watchapi.divesee.com"))
        token.setText(prefs.getString(HubService.EXTRA_TOKEN, ""))
        page.text = "眼镜打开 ${PhoneAddress.glassesPage()}"
        toggle.setOnClickListener { startHub(checkPage = true) }
        talk.isLongClickable = false
        talk.setOnTouchListener { view, event ->
            if (event.actionMasked == MotionEvent.ACTION_DOWN) {
                view.parent?.requestDisallowInterceptTouchEvent(true)
                val tooSoon = holding && System.currentTimeMillis() - pressAt < 700
                if (!tooSoon) {
                    if (holding && !recognizing) endTalk() else beginTalk()
                }
            }
            true
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
            sayAloud("先允许麦克风，再按住说。")
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
        talk.text = "正在听\n再点一下结束"
        speechView.text = "正在听。说完再点一下。"
        handler.post(tick)
    }

    private val tick = object : Runnable {
        override fun run() {
            if (!holding || recognizing) return
            val seconds = ((System.currentTimeMillis() - pressAt) / 1000).toInt()
            talk.text = "正在听 $seconds 秒\n再点一下结束"
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
        val rec = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            min * 2,
        )
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            return false
        }
        pcm.reset()
        recording = true
        rec.startRecording()
        audio = rec
        thread(name = "mic") {
            val buf = ByteArray(min)
            while (recording) {
                val n = rec.read(buf, 0, buf.size)
                if (n <= 0) continue
                var full = false
                synchronized(pcm) {
                    if (pcm.size() < MAX_PCM) pcm.write(buf, 0, n)
                    full = pcm.size() >= MAX_PCM
                }
                if (full) {
                    handler.post { endTalk() }
                    break
                }
            }
        }
        return true
    }

    private fun stopRecorder() {
        recording = false
        try {
            audio?.stop()
        } catch (_: Exception) {
        }
        audio?.release()
        audio = null
    }

    private fun endTalk() {
        if (!holding || recognizing) return
        val held = System.currentTimeMillis() - pressAt
        stopRecorder()
        handler.removeCallbacks(tick)
        if (held < 600) {
            finishHold("", "再说长一点，然后点一下结束。")
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
                if (heard.isNullOrEmpty()) finishHold("", "没听清。再按住说一次。")
                else finishHold(heard)
            }
        }
    }

    private fun finishHold(text: String, failed: String = "") {
        if (!holding) return
        holding = false
        handler.removeCallbacks(tick)
        talk.text = "点一下开始说"
        if (failed.isNotEmpty()) {
            sayAloud(failed)
            return
        }
        val spoken = text.trim()
        if (spoken.isEmpty()) {
            sayAloud("没听清。再按住说一次。")
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
        prefs.edit()
            .putString(HubService.EXTRA_GATEWAY, gateway.text.toString().trim())
            .putString(HubService.EXTRA_TOKEN, token.text.toString().trim())
            .apply()
        val intent = Intent(this, HubService::class.java)
            .putExtra(HubService.EXTRA_GATEWAY, gateway.text.toString().trim())
            .putExtra(HubService.EXTRA_TOKEN, token.text.toString().trim())
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent) else startService(intent)
        toggle.text = "已在接收"
        if (!checkPage) return
        val page = findViewById<TextView>(R.id.page)
        val glasses = PhoneAddress.glassesPage()
        page.text = "眼镜打开 $glasses。说话按钮就在这个页面。"
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

    private fun transcribe(pcmBytes: ByteArray): String? {
        if (pcmBytes.size < SAMPLE_RATE) return null
        return try {
            val boundary = "seenext${System.currentTimeMillis()}"
            val conn = URL(gateway.text.toString().trim().trimEnd('/') + "/asr").openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 15000
            conn.readTimeout = 30000
            val tokenText = token.text.toString().trim()
            if (tokenText.isNotEmpty()) conn.setRequestProperty("X-App-Token", tokenText)
            conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            conn.outputStream.use { out ->
                out.write(
                    "--$boundary\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"speech.wav\"\r\nContent-Type: audio/wav\r\n\r\n"
                        .toByteArray()
                )
                out.write(wavBytes(pcmBytes))
                out.write("\r\n--$boundary--\r\n".toByteArray())
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299 || !text.startsWith("{")) null
            else JSONObject(text).optString("text").trim().ifEmpty { null }
        } catch (_: Exception) {
            null
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

    companion object {
        private const val PREFS = "phone-hub"
        private const val SPEAK_GAP_MS = 4000L
        private const val SAMPLE_RATE = 16000
        private const val MAX_PCM = SAMPLE_RATE * 2 * 60
    }
}

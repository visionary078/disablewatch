package com.seenext.phonehub

import android.content.res.AssetManager
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URL
import java.nio.charset.StandardCharsets
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

class HubServer(
    private val assets: AssetManager,
    private val gateway: String,
    private val token: String,
    private val onStatus: (String) -> Unit,
) {
    private val running = AtomicBoolean(false)
    private val lock = Any()
    private var server: ServerSocket? = null
    private var latest: ByteArray? = null
    private var tofMm = 0
    private var tofAt = 0L
    private var pendingSpoken = ""
    private var busy = false
    private var cameraOn = false
    private var seq = 0
    private var speech = ""
    private var decision = ""
    private var mainTask = ""
    private var risk = ""
    private var direction = ""
    private var mode = ""
    private var error = ""
    private val sessionId = UUID.randomUUID().toString()

    fun start() {
        if (!running.compareAndSet(false, true)) return
        val socket = ServerSocket()
        try {
            socket.reuseAddress = true
            socket.bind(InetSocketAddress("0.0.0.0", PORT))
        } catch (error: Exception) {
            running.set(false)
            try {
                socket.close()
            } catch (_: Exception) {
            }
            throw error
        }
        server = socket
        thread(name = "phone-hub") {
            try {
                onStatus("正在等摄像头。眼镜打开 ${PhoneAddress.glassesPage()}")
                while (running.get()) {
                    val client = try {
                        socket.accept()
                    } catch (_: Exception) {
                        break
                    }
                    thread(name = "hub-conn") {
                        try {
                            handle(client)
                        } catch (_: Exception) {
                            try {
                                client.close()
                            } catch (_: Exception) {
                            }
                        }
                    }
                }
            } catch (error: Exception) {
                onStatus("接收中断：${error.message ?: error.javaClass.simpleName}")
            }
        }
    }

    fun stop() {
        running.set(false)
        try {
            server?.close()
        } catch (_: Exception) {
        }
    }

    private fun handle(socket: Socket) {
        socket.use { client ->
            client.soTimeout = 8000
            val input = BufferedInputStream(client.getInputStream())
            val headerBytes = readUntilHeaderEnd(input) ?: return
            val headerText = String(headerBytes, StandardCharsets.ISO_8859_1)
            val lines = headerText.split("\r\n")
            val request = lines.firstOrNull().orEmpty().split(" ")
            val method = request.getOrElse(0) { "" }
            val path = request.getOrElse(1) { "/" }.substringBefore("?")
            val headers = parseHeaders(lines.drop(1))
            val length = headers["content-length"]?.toIntOrNull() ?: 0
            val body = if (length > 0) readExact(input, length) else ByteArray(0)
            val response = route(method, path, body, headers["content-type"].orEmpty())
            BufferedOutputStream(client.getOutputStream()).use { out ->
                out.write(response)
                out.flush()
            }
        }
    }

    private fun route(method: String, path: String, body: ByteArray, contentType: String): ByteArray {
        return when {
            method == "POST" && path == "/frame" -> frame(body, contentType)
            method == "POST" && path == "/tof" -> tof(body)
            method == "POST" && path == "/say" -> say(body)
            method == "GET" && path == "/status" -> json(200, statusJson())
            method == "GET" && path == "/speech" -> json(200, speechJson())
            method == "GET" && path == "/" -> asset("speaker/index.html", "text/html; charset=utf-8")
            method == "GET" && path == "/speaker.js" -> asset("speaker/speaker.js", "text/javascript; charset=utf-8")
            method == "GET" && path == "/speaker.css" -> asset("speaker/speaker.css", "text/css; charset=utf-8")
            else -> text(404, "找不到")
        }
    }

    private fun frame(body: ByteArray, contentType: String): ByteArray {
        val jpeg = extractJpeg(body, contentType) ?: return text(400, "需要 JPEG")
        if (jpeg.size > MAX_JPEG_BYTES) return text(400, "图片太大")
        val action = synchronized(lock) {
            val spoken = pendingSpoken
            val decision = FrameRules.frameAction(busy, spoken.isNotEmpty(), cameraOn)
            if (decision == FrameRules.DROP) return@synchronized FrameRules.DROP
            latest = jpeg
            if (decision == FrameRules.STORE) return@synchronized FrameRules.STORE
            pendingSpoken = ""
            busy = true
            val useMode = if (spoken.isNotEmpty()) "precise" else "live"
            thread(name = "hub-infer") { infer(jpeg, spoken, useMode) }
            FrameRules.FORWARD
        }
        return when (action) {
            FrameRules.DROP -> text(429, "上一帧还在识别")
            FrameRules.STORE -> empty(204)
            else -> json(202, """{"status":"forward"}""")
        }
    }

    private fun say(body: ByteArray): ByteArray {
        val spoken = try {
            JSONObject(String(body, StandardCharsets.UTF_8)).optString("spoken_text").trim()
        } catch (_: Exception) {
            ""
        }
        if (spoken.isEmpty()) return json(400, """{"error":"没听清"}""")
        val result = synchronized(lock) {
            pendingSpoken = spoken
            val jpeg = latest
            if (jpeg == null) return@synchronized "no-frame"
            if (busy) return@synchronized "wait"
            pendingSpoken = ""
            busy = true
            thread(name = "hub-infer") { infer(jpeg, spoken, "precise") }
            "forward"
        }
        return when (result) {
            "no-frame" -> json(409, """{"error":"还没看到画面"}""")
            "wait" -> json(202, """{"status":"queued"}""")
            else -> json(202, """{"status":"forward"}""")
        }
    }

    private fun tof(body: ByteArray): ByteArray {
        val mm = parseMm(body) ?: return json(400, """{"error":"需要 1 到 8000 的毫米数"}""")
        synchronized(lock) {
            tofMm = mm
            tofAt = System.currentTimeMillis()
        }
        return json(200, """{"status":"ok","mm":$mm}""")
    }

    private fun parseMm(body: ByteArray): Int? {
        val text = String(body, StandardCharsets.UTF_8).trim()
        if (text.isEmpty()) return null
        val raw = if (text.startsWith("{")) {
            try {
                JSONObject(text).opt("mm")?.toString() ?: return null
            } catch (_: Exception) {
                return null
            }
        } else {
            text
        }
        val value = raw.toIntOrNull() ?: raw.toDoubleOrNull()?.toInt() ?: return null
        if (value <= 0 || value > 8000) return null
        return value
    }

    private fun currentTofField(): String {
        val now = System.currentTimeMillis()
        val reading = synchronized(lock) { tofMm to (now - tofAt) }
        return FrameRules.freshTofMm(reading.first, reading.second)
    }

    private fun infer(jpeg: ByteArray, spoken: String, useMode: String) {
        try {
            val payload = postInfer(jpeg, spoken, useMode)
            if (payload.isEmpty()) return
            val root = JSONObject(payload)
            val result = root.optJSONObject("result")
            val task = root.optJSONObject("task")
            val sensors = root.optJSONObject("sensors")
            val nextSpeech = result?.optString("speech").orEmpty().ifEmpty { result?.optString("action").orEmpty() }
            val nextError = if (root.optString("status") == "error") root.optString("error") else ""
            synchronized(lock) {
                seq += 1
                speech = nextSpeech
                decision = task?.optString("decision").orEmpty()
                mainTask = task?.optString("main_task").orEmpty()
                risk = result?.optString("risk_level").orEmpty()
                direction = result?.optString("direction").orEmpty()
                mode = useMode
                error = nextError
                if (sensors != null && sensors.has("camera")) cameraOn = sensors.optBoolean("camera")
            }
            onStatus(if (nextError.isNotEmpty()) nextError else nextSpeech.ifEmpty { "这一帧没有要说的话" })
        } catch (error: Exception) {
            val message = error.message ?: "识别失败"
            synchronized(lock) {
                seq += 1
                mode = useMode
                this.error = message
                speech = ""
            }
            onStatus(message)
        } finally {
            var follow: Triple<ByteArray, String, String>? = null
            synchronized(lock) {
                busy = false
                val jpegNow = latest
                val spokenNow = pendingSpoken
                if (jpegNow != null && spokenNow.isNotEmpty()) {
                    pendingSpoken = ""
                    busy = true
                    follow = Triple(jpegNow, spokenNow, "precise")
                }
            }
            val next = follow
            if (next != null) infer(next.first, next.second, next.third)
        }
    }

    private fun postInfer(jpeg: ByteArray, spoken: String, useMode: String): String {
        val boundary = "seenext${System.currentTimeMillis()}"
        val url = URL(gateway.trimEnd('/') + "/infer")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.connectTimeout = 15000
        conn.readTimeout = 90000
        if (token.isNotEmpty()) conn.setRequestProperty("X-App-Token", token)
        conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
        conn.outputStream.use { out ->
            fun field(name: String, value: String) {
                out.write("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n$value\r\n".toByteArray())
            }
            out.write(
                "--$boundary\r\nContent-Disposition: form-data; name=\"image\"; filename=\"scene.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n"
                    .toByteArray()
            )
            out.write(jpeg)
            out.write("\r\n".toByteArray())
            field("question", "请判断当前是否安全，并告诉我目标在哪个方向。")
            field("mode", useMode)
            field("session_id", sessionId)
            field("user_id", "phone")
            field("spoken_text", spoken)
            field("client", "phone")
            val tof = currentTofField()
            if (tof.isNotEmpty()) field("tof_mm", tof)
            out.write("--$boundary--\r\n".toByteArray())
        }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.bufferedReader()?.readText().orEmpty()
        if (code == 429) return ""
        if (code !in 200..299) throw IllegalStateException("识别失败 $code")
        return text
    }

    private fun speechJson(): String {
        synchronized(lock) {
            return JSONObject()
                .put("seq", seq)
                .put("mode", mode)
                .put("speech", speech)
                .put("decision", decision)
                .put("main_task", mainTask)
                .put("risk_level", risk)
                .put("direction", direction)
                .put("error", error)
                .toString()
        }
    }

    private fun asset(name: String, contentType: String): ByteArray {
        val bytes = assets.open(name).use { it.readBytes() }
        return bytesResponse(200, contentType, bytes)
    }

    private fun statusJson(): String {
        synchronized(lock) {
            val age = if (tofAt == 0L) -1L else System.currentTimeMillis() - tofAt
            val fresh = FrameRules.freshTofMm(tofMm, age)
            return JSONObject()
                .put("has_frame", latest != null)
                .put("frame_bytes", latest?.size ?: 0)
                .put("tof_mm", if (tofMm <= 0) JSONObject.NULL else tofMm)
                .put("tof_fresh", fresh.isNotEmpty())
                .put("tof_age_ms", age)
                .put("busy", busy)
                .put("camera_on", cameraOn)
                .put("speech", speech)
                .toString()
        }
    }

    private fun looksLikeJpeg(body: ByteArray): Boolean {
        return body.size > 3 && body[0] == 0xFF.toByte() && body[1] == 0xD8.toByte()
    }

    private fun extractJpeg(body: ByteArray, contentType: String): ByteArray? {
        if (looksLikeJpeg(body)) return body
        if (!contentType.lowercase().contains("multipart")) return null
        var start = -1
        for (index in 0 until body.size - 1) {
            if (body[index] == 0xFF.toByte() && body[index + 1] == 0xD8.toByte()) {
                start = index
                break
            }
        }
        if (start < 0) return null
        var end = -1
        for (index in body.size - 2 downTo start) {
            if (body[index] == 0xFF.toByte() && body[index + 1] == 0xD9.toByte()) {
                end = index + 2
                break
            }
        }
        if (end <= start) return null
        return body.copyOfRange(start, end)
    }

    private fun parseHeaders(lines: List<String>): Map<String, String> {
        val headers = linkedMapOf<String, String>()
        for (line in lines) {
            val split = line.indexOf(':')
            if (split <= 0) continue
            headers[line.substring(0, split).trim().lowercase()] = line.substring(split + 1).trim()
        }
        return headers
    }

    private fun readUntilHeaderEnd(input: BufferedInputStream): ByteArray? {
        val out = ByteArrayOutputStream()
        var last = 0
        while (true) {
            val next = input.read()
            if (next < 0) return if (out.size() == 0) null else out.toByteArray()
            out.write(next)
            last = (last shl 8) or next
            if (last == 0x0d0a0d0a) return out.toByteArray()
            if (out.size() > 16384) return null
        }
    }

    private fun readExact(input: BufferedInputStream, length: Int): ByteArray {
        val out = ByteArray(length)
        var offset = 0
        while (offset < length) {
            val count = input.read(out, offset, length - offset)
            if (count < 0) break
            offset += count
        }
        return if (offset == length) out else out.copyOf(offset)
    }

    private fun text(code: Int, message: String) = bytesResponse(code, "text/plain; charset=utf-8", message.toByteArray())

    private fun json(code: Int, message: String) = bytesResponse(code, "application/json; charset=utf-8", message.toByteArray())

    private fun empty(code: Int) = bytesResponse(code, "text/plain", ByteArray(0))

    private fun bytesResponse(code: Int, contentType: String, body: ByteArray): ByteArray {
        val reason = when (code) {
            200 -> "OK"
            202 -> "Accepted"
            204 -> "No Content"
            400 -> "Bad Request"
            404 -> "Not Found"
            409 -> "Conflict"
            429 -> "Too Many Requests"
            else -> "OK"
        }
        val head = "HTTP/1.1 $code $reason\r\nContent-Type: $contentType\r\nContent-Length: ${body.size}\r\nConnection: close\r\n\r\n"
        val headBytes = head.toByteArray(StandardCharsets.ISO_8859_1)
        return headBytes + body
    }

    companion object {
        const val PORT = 8788
        const val MAX_JPEG_BYTES = 2_000_000
    }
}

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
        thread(name = "phone-hub") {
            val socket = ServerSocket()
            socket.reuseAddress = true
            socket.bind(InetSocketAddress("0.0.0.0", PORT))
            server = socket
            onStatus("正在等摄像头。眼镜打开 http://192.168.43.1:$PORT/")
            while (running.get()) {
                val client = try {
                    socket.accept()
                } catch (_: Exception) {
                    break
                }
                thread(name = "hub-conn") { handle(client) }
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
            val response = route(method, path, body)
            BufferedOutputStream(client.getOutputStream()).use { out ->
                out.write(response)
                out.flush()
            }
        }
    }

    private fun route(method: String, path: String, body: ByteArray): ByteArray {
        return when {
            method == "POST" && path == "/frame" -> frame(body)
            method == "POST" && path == "/say" -> say(body)
            method == "GET" && path == "/speech" -> json(200, speechJson())
            method == "GET" && path == "/" -> asset("speaker/index.html", "text/html; charset=utf-8")
            method == "GET" && path == "/speaker.js" -> asset("speaker/speaker.js", "text/javascript; charset=utf-8")
            method == "GET" && path == "/speaker.css" -> asset("speaker/speaker.css", "text/css; charset=utf-8")
            else -> text(404, "找不到")
        }
    }

    private fun frame(body: ByteArray): ByteArray {
        if (!looksLikeJpeg(body)) return text(400, "需要 JPEG")
        val action = synchronized(lock) {
            val spoken = pendingSpoken
            val decision = FrameRules.frameAction(busy, spoken.isNotEmpty(), cameraOn)
            if (decision == FrameRules.DROP) return@synchronized FrameRules.DROP
            latest = body
            if (decision == FrameRules.STORE) return@synchronized FrameRules.STORE
            pendingSpoken = ""
            busy = true
            val useMode = if (spoken.isNotEmpty()) "precise" else "live"
            thread(name = "hub-infer") { infer(body, spoken, useMode) }
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

    private fun looksLikeJpeg(body: ByteArray): Boolean {
        return body.size > 3 && body[0] == 0xFF.toByte() && body[1] == 0xD8.toByte()
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
    }
}

package com.seenext.phonehub

import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** 平板直接问小米。密钥由调用方传入，不写在这个文件里。 */
object Xiaomi {
    private const val COMPLETIONS = "https://api.xiaomimimo.com/v1/chat/completions"

    fun transcribe(apiKey: String, wav: ByteArray): String {
        val audio = Base64.encodeToString(wav, Base64.NO_WRAP)
        val payload = JSONObject()
            .put("model", "mimo-v2.5-asr")
            .put(
                "messages",
                JSONArray().put(
                    JSONObject()
                        .put("role", "user")
                        .put(
                            "content",
                            JSONArray().put(
                                JSONObject()
                                    .put("type", "input_audio")
                                    .put(
                                        "input_audio",
                                        JSONObject().put("data", "data:audio/wav;base64,$audio"),
                                    ),
                            ),
                        ),
                ),
            )
            .put("asr_options", JSONObject().put("language", "zh"))
        val text = contentOf(post(apiKey, payload, 45000))
        if (text.isEmpty()) throw IllegalStateException("没听清")
        return text
    }

    fun look(apiKey: String, jpeg: ByteArray, spoken: String, tofMm: String): String {
        val image = Base64.encodeToString(jpeg, Base64.NO_WRAP)
        val prompt = """
            你是视障人士身边的助手。看这张照片，结合用户刚才说的话，用中文回答。
            有危险先说危险。不要说米、厘米、毫米或步数，也不要说可以直接前进。
            用户刚才说：$spoken
            距离感觉：${band(tofMm)}
            只输出一个 JSON，不要 Markdown：
            {"speech":"一两句短话","risk_level":"low","direction":"左侧|左前方|正前方|右前方|右侧|未确定","main_task":"要找的东西，没有就空着"}
        """.trimIndent()
        val payload = JSONObject()
            .put("model", "mimo-v2.6-flash")
            .put("temperature", 0.1)
            .put("max_completion_tokens", 256)
            .put("thinking", JSONObject().put("type", "disabled"))
            .put(
                "messages",
                JSONArray().put(
                    JSONObject()
                        .put("role", "user")
                        .put(
                            "content",
                            JSONArray()
                                .put(
                                    JSONObject()
                                        .put("type", "image_url")
                                        .put(
                                            "image_url",
                                            JSONObject().put("url", "data:image/jpeg;base64,$image"),
                                        ),
                                )
                                .put(JSONObject().put("type", "text").put("text", prompt)),
                        ),
                ),
            )
        return wrap(contentOf(post(apiKey, payload, 90000)))
    }

    private fun band(tofMm: String): String {
        val mm = tofMm.toIntOrNull() ?: return "无法判断"
        return when {
            mm <= 0 -> "无法判断"
            mm <= 800 -> "一臂内"
            mm <= 2000 -> "较近"
            else -> "较远"
        }
    }

    private fun wrap(raw: String): String {
        val cleaned = raw.trim().removePrefix("```json").removePrefix("```").removeSuffix("```").trim()
        val obj = try {
            JSONObject(cleaned)
        } catch (_: Exception) {
            JSONObject().put("speech", cleaned.take(80))
        }
        val speech = obj.optString("speech").trim().ifEmpty { cleaned.take(80) }
        return JSONObject()
            .put("status", "ok")
            .put(
                "result",
                JSONObject()
                    .put("speech", speech)
                    .put("action", speech)
                    .put("risk_level", obj.optString("risk_level", "low"))
                    .put("direction", obj.optString("direction")),
            )
            .put(
                "task",
                JSONObject()
                    .put("decision", "keep")
                    .put("main_task", obj.optString("main_task")),
            )
            .put("sensors", JSONObject().put("camera", false))
            .toString()
    }

    private fun contentOf(root: JSONObject): String {
        val content = root.optJSONArray("choices")
            ?.optJSONObject(0)
            ?.optJSONObject("message")
            ?.opt("content")
        return when (content) {
            is String -> content.trim()
            is JSONArray -> {
                val parts = StringBuilder()
                for (i in 0 until content.length()) {
                    val item = content.opt(i)
                    if (item is String) parts.append(item)
                    else if (item is JSONObject) parts.append(item.optString("text"))
                }
                parts.toString().trim()
            }
            else -> ""
        }
    }

    private fun post(apiKey: String, payload: JSONObject, readMs: Int): JSONObject {
        if (apiKey.isBlank()) throw IllegalStateException("先填小米密钥")
        val conn = URL(COMPLETIONS).openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.connectTimeout = 15000
        conn.readTimeout = readMs
        conn.setRequestProperty("Authorization", "Bearer $apiKey")
        conn.setRequestProperty("api-key", apiKey)
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
        if (code == 401) throw IllegalStateException("密钥不对")
        if (code == 402) throw IllegalStateException("余额不够")
        if (code !in 200..299) throw IllegalStateException("小米没接上 $code")
        return JSONObject(text)
    }
}

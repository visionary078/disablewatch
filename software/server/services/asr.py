"""语音识别（ASR）通道。

小程序默认用 wx.getRecorderManager 录音后上传本接口。
mock 模式返回固定中文，便于无密钥联调。
真实通道走 OpenAI 兼容 /audio/transcriptions（如硅基流动 SenseVoice）。
"""

from __future__ import annotations

import base64
import json
import time
import uuid
import urllib.error
import urllib.request
from typing import Any, Dict

MOCK_ASR_TEXT = "请帮我找目标商品，先提醒风险，再给出方向。"


class ASRHandler:
    def __init__(
        self,
        mock: bool = False,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout: int = 30,
        provider: str = "",
    ) -> None:
        self.mock = mock
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip() or "whisper-1"
        self.timeout = timeout
        self.provider = (provider or "").strip()

    @property
    def enabled(self) -> bool:
        return self.mock or bool(self.base_url)

    async def transcribe(self, audio: bytes, filename: str = "audio.mp3") -> Dict[str, Any]:
        started = time.perf_counter()
        if self.mock:
            return {"text": MOCK_ASR_TEXT, "latency_ms": _elapsed_ms(started)}
        if not self.base_url:
            raise RuntimeError("ASR 服务未配置，请设置 ASR_API_BASE_URL。")
        transcribe = _transcribe_mimo if self.provider == "mimo" else _transcribe_remote
        text = transcribe(
            self.base_url,
            self.api_key,
            self.model,
            audio,
            filename,
            self.timeout,
        )
        return {"text": text, "latency_ms": _elapsed_ms(started)}


def _transcribe_mimo(
    base_url: str,
    api_key: str,
    model: str,
    audio: bytes,
    filename: str,
    timeout: int,
) -> str:
    endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    payload = {
        "model": model or "mimo-v2.5-asr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": _audio_data_url(audio, filename)},
                    }
                ],
            }
        ],
        "asr_options": {"language": "zh"},
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["api-key"] = api_key
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"ASR 服务返回 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 ASR 服务：{exc.reason}") from exc
    try:
        body = json.loads(raw)
        content = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("ASR 服务返回的内容无法读取。") from exc
    text = content.strip() if isinstance(content, str) else str(content or "").strip()
    if not text:
        raise RuntimeError("ASR 服务返回了空文本。")
    return text


def _audio_data_url(audio: bytes, filename: str) -> str:
    lower = (filename or "").lower()
    if lower.endswith(".wav"):
        mime = "audio/wav"
    elif lower.endswith(".mp3"):
        mime = "audio/mpeg"
    else:
        raise RuntimeError("语音识别只接受 wav 或 mp3。")
    encoded = base64.b64encode(audio).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _transcribe_remote(
    base_url: str,
    api_key: str,
    model: str,
    audio: bytes,
    filename: str,
    timeout: int,
) -> str:
    endpoint = (
        base_url
        if base_url.endswith("/audio/transcriptions")
        else f"{base_url}/audio/transcriptions"
    )
    body, content_type = _encode_multipart(
        {"model": model, "language": "zh"},
        {"file": (filename or "audio.mp3", audio, "application/octet-stream")},
    )
    headers = {"Content-Type": content_type}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"ASR 服务返回 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 ASR 服务：{exc.reason}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("ASR 服务返回的不是合法 JSON。") from exc
    text = str((payload or {}).get("text") or "").strip()
    if not text:
        raise RuntimeError("ASR 服务返回了空文本。")
    return text


def _encode_multipart(fields: Dict[str, str], files: Dict[str, tuple]) -> tuple:
    boundary = uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"))
        chunks.append(b"")
        chunks.append(str(value).encode("utf-8"))
    for name, (filename, content, content_type) in files.items():
        chunks.append(f"--{boundary}".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode("utf-8")
        )
        chunks.append(f"Content-Type: {content_type}".encode("utf-8"))
        chunks.append(b"")
        chunks.append(content)
    chunks.append(f"--{boundary}--".encode("utf-8"))
    chunks.append(b"")
    return b"\r\n".join(chunks), f"multipart/form-data; boundary={boundary}"


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)

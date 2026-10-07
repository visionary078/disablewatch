"""豆包语音合成 2.0。短句一次送出，拼好 mp3 再交给播放端。"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import uuid

DOUBAO_TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_SPEAKER = "zh_female_xiaohe_uranus_bigtts"
DONE_CODE = 20000000


def audio_from_stream(raw: str) -> bytes:
    audio = bytearray()
    failure = ""
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = payload.get("code")
        data = payload.get("data")
        if code == 0 and isinstance(data, str) and data:
            audio.extend(base64.b64decode(data))
            continue
        if code == DONE_CODE:
            continue
        if code not in (0, None):
            failure = str(payload.get("message") or code)
    if not audio:
        raise RuntimeError(failure or "豆包没有返回音频")
    return bytes(audio)


def synthesize(text: str, api_key: str = "", speaker: str = "", resource_id: str = "") -> bytes:
    key = (api_key or os.getenv("DOUBAO_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("未配置 DOUBAO_API_KEY")
    voice = (speaker or os.getenv("DOUBAO_TTS_SPEAKER", "") or DEFAULT_SPEAKER).strip()
    resource = (resource_id or os.getenv("DOUBAO_TTS_RESOURCE_ID", "") or DEFAULT_RESOURCE_ID).strip()
    body = json.dumps(
        {
            "user": {"uid": "seenext"},
            "req_params": {
                "text": text,
                "speaker": voice,
                "audio_params": {
                    "format": "mp3",
                    "sample_rate": 24000,
                    "bit_rate": 128000,
                    "speech_rate": -10,
                },
                "additions": json.dumps(
                    {
                        "explicit_language": "zh-cn",
                        "context_texts": ["用平静、亲近的语气说话，像在身边帮忙，不要播音腔。"],
                    },
                    ensure_ascii=False,
                ),
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        DOUBAO_TTS_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": key,
            "X-Api-Resource-Id": resource,
            "X-Api-Request-Id": str(uuid.uuid4()),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"豆包语音合成失败：{exc.code} {detail}") from exc
    return audio_from_stream(raw)

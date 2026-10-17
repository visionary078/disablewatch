"""中文朗读。相同的短句只合成一次，下一句直接用缓存。"""

import asyncio
import io
from collections import OrderedDict
from typing import Awaitable, Callable, Optional

VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "-8%"
CACHE_LIMIT = 48

Render = Callable[[str], Awaitable[bytes]]


class SpeechUnavailable(Exception):
    """本机没有装 edge-tts。"""


class SpeechFailed(Exception):
    """语音服务没有返回音频。"""


def normalize_speech(text: str) -> str:
    return " ".join(str(text or "").split())[:200]


class SpeechCache:
    def __init__(self, limit: int = CACHE_LIMIT):
        self.limit = limit
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self._inflight = {}
        self._gate = asyncio.Lock()

    def get(self, key: str) -> Optional[bytes]:
        audio = self._items.get(key)
        if audio is None:
            return None
        self._items.move_to_end(key)
        return audio

    def put(self, key: str, audio: bytes) -> None:
        if not key or not audio:
            return
        self._items[key] = audio
        self._items.move_to_end(key)
        while len(self._items) > self.limit:
            self._items.popitem(last=False)

SHARED_CACHE = SpeechCache()


async def _edge_render(text: str) -> bytes:
    try:
        import edge_tts
    except ImportError as exc:
        raise SpeechUnavailable("未安装 edge-tts") from exc
    try:
        try:
            communicate = edge_tts.Communicate(text, VOICE, rate=RATE)
        except TypeError:
            communicate = edge_tts.Communicate(text, VOICE)
        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                buffer.write(chunk["data"])
        audio = buffer.getvalue()
    except (SpeechUnavailable, SpeechFailed):
        raise
    except Exception as exc:
        raise SpeechFailed("语音合成失败") from exc
    if not audio:
        raise SpeechFailed("没有音频")
    return audio


async def synthesize_speech(
    text: str,
    cache: Optional[SpeechCache] = None,
    render: Optional[Render] = None,
) -> bytes:
    spoken = normalize_speech(text)
    if not spoken:
        raise SpeechFailed("没有要读的字")
    store = SHARED_CACHE if cache is None else cache
    hit = store.get(spoken)
    if hit is not None:
        return hit
    produce = render or _edge_render
    async with store._gate:
        hit = store.get(spoken)
        if hit is not None:
            return hit
        task = store._inflight.get(spoken)
        if task is None:
            task = asyncio.create_task(produce(spoken))
            store._inflight[spoken] = task
    try:
        audio = await task
    finally:
        async with store._gate:
            current = store._inflight.get(spoken)
            if current is task:
                store._inflight.pop(spoken, None)
    if not audio:
        raise SpeechFailed("没有音频")
    store.put(spoken, audio)
    return audio

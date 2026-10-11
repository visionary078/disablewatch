import asyncio
import unittest

from services.tts import SpeechCache, normalize_speech, synthesize_speech


class SpeechCacheTests(unittest.TestCase):
    def test_blank_space_does_not_change_the_line(self):
        self.assertEqual(normalize_speech("  稍向左。 \n"), "稍向左。")

    def test_oldest_line_is_dropped(self):
        cache = SpeechCache(limit=2)
        cache.put("先停一下。", b"a")
        cache.put("稍向左。", b"b")
        cache.put("稍向右。", b"c")
        self.assertIsNone(cache.get("先停一下。"))
        self.assertEqual(cache.get("稍向右。"), b"c")


class SynthesizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_same_line_is_rendered_once(self):
        cache = SpeechCache()
        calls = []

        async def render(text):
            calls.append(text)
            await asyncio.sleep(0.05)
            return b"audio"

        first, second = await asyncio.gather(
            synthesize_speech("稍向左。", cache=cache, render=render),
            synthesize_speech("  稍向左。", cache=cache, render=render),
        )
        self.assertEqual(first, b"audio")
        self.assertEqual(second, b"audio")
        self.assertEqual(calls, ["稍向左。"])

    async def test_a_later_call_reuses_the_audio(self):
        cache = SpeechCache()
        calls = []

        async def render(text):
            calls.append(text)
            return b"once"

        await synthesize_speech("已经记下。", cache=cache, render=render)
        again = await synthesize_speech("已经记下。", cache=cache, render=render)
        self.assertEqual(again, b"once")
        self.assertEqual(calls, ["已经记下。"])

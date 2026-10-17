import time
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

from minicpmo_runner import MiniCPMOAccessibilityRunner
from services.long_memory import (
    HighlightBook,
    LongMemoryService,
    _snippet_texts,
    build_highlights,
    scrub_speech,
)
from services.utterance import is_day_summary, needs_live_info
from services.task_memory import TaskMemoryStore

if TestClient is not None:
    from server import create_app


class SlowMemory:
    def __init__(self, delay=0.0, snippets=None):
        self.delay = delay
        self.snippets = list(snippets or [])
        self.searches = 0
        self.writes = []

    def search_memory(self, query="", user_id="", **kwargs):
        self.searches += 1
        if self.delay:
            time.sleep(self.delay)
        return [{"memory": item} for item in self.snippets]

    def add_message(self, **kwargs):
        self.writes.append(kwargs)


class LongMemoryUnitTests(unittest.TestCase):
    def test_scrub_drops_negated_target_and_habit_phrase(self):
        speech = scrub_speech(
            "根据你的习惯，正在帮你找可乐，正前方货架中部较近。无糖可乐在左边。",
            "不是这个",
            ["无糖可乐在左边"],
            rejected_target="可乐",
        )
        self.assertNotIn("可乐", speech)
        self.assertNotIn("根据你的习惯", speech)
        self.assertNotIn("在左边", speech)
        self.assertIn("先停一下", speech)

    def test_highlights_skip_filler_and_keep_correction(self):
        self.assertEqual(build_highlights("看到了吗", "refine", "可乐", "可乐"), [])
        self.assertEqual(build_highlights("嗯", "keep", "可乐"), [])
        self.assertEqual(
            build_highlights("不是这个", "refine", "可乐", "可乐"),
            ["纠正：先不要按旧说法找可乐"],
        )
        self.assertEqual(build_highlights("帮我找无糖可乐", "new", "无糖可乐"), ["常找无糖可乐"])
        self.assertEqual(build_highlights("帮我记住出口在右侧", "keep", ""), ["记住：出口在右侧"])

    def test_day_summary_is_not_treated_as_news(self):
        self.assertTrue(is_day_summary("今天发生了什么"))
        self.assertFalse(needs_live_info("今天发生了什么"))
        self.assertFalse(is_day_summary("今天有什么大事发生"))
        self.assertTrue(needs_live_info("今天有什么大事发生"))

    def test_notes_older_than_a_day_are_dropped(self):
        book = HighlightBook()
        book.add("user-old", ["常找可乐"])
        book._rows["user-old"][0]["updated_at"] -= 25 * 60 * 60
        self.assertEqual(book.list("user-old"), [])

    def test_reads_cloud_search_model(self):
        class Detail:
            def __init__(self, memory):
                self.memory = memory

            def model_dump(self):
                return {"memory": self.memory}

        class Response:
            memories = [Detail("常找无糖可乐")]
            preferences = [Detail("看到了吗")]

        self.assertEqual(_snippet_texts(Response()), ["常找无糖可乐"])

    def test_search_timeout_returns_nothing(self):
        client = SlowMemory(delay=0.4, snippets=["无糖可乐在左边"])
        service = LongMemoryService(client=client, enabled=True, timeout_s=0.05)
        started = time.perf_counter()
        found = service.recall("user-1", "上次找的", "keep", "我上次找的是什么")
        elapsed = time.perf_counter() - started
        self.assertEqual(found, [])
        self.assertLess(elapsed, 0.3)
        self.assertEqual(client.writes, [])


@unittest.skipIf(TestClient is None, "未安装 fastapi，跳过接口测试")
class LongMemoryInferTests(unittest.TestCase):
    def _client(self, memory):
        runner = MiniCPMOAccessibilityRunner(mock=True)
        runner.load()
        return TestClient(
            create_app(
                runner,
                app_token="secret",
                task_store=TaskMemoryStore(),
                long_memory=memory,
            )
        )

    def _infer(self, client, **data):
        payload = {"question": "请判断当前是否安全，并告诉我目标在哪个方向。", "mode": "precise"}
        payload.update(data)
        return client.post(
            "/infer",
            files={"image": ("scene.jpg", b"fake-jpeg", "image/jpeg")},
            data=payload,
            headers={"X-App-Token": "secret"},
        )

    def test_without_user_id_speech_stays_the_same(self):
        memory = SlowMemory(snippets=["无糖可乐在左边"])
        response = self._infer(
            self._client(memory),
            spoken_text="帮我找可乐",
            session_id="no-user",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("正在帮你找可乐", body["result"]["speech"])
        self.assertEqual(body["highlights"], [])
        self.assertEqual(memory.searches, 0)
        self.assertNotIn("根据你的习惯", body["result"]["speech"])

    def test_negation_is_not_spoken_and_only_correction_is_highlighted(self):
        memory = SlowMemory(snippets=["无糖可乐在左边"])
        service = LongMemoryService(client=memory, enabled=True, timeout_s=0.3, sync_writes=True)
        client = self._client(service)
        first = self._infer(client, spoken_text="帮我找可乐", session_id="neg", user_id="user-1")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["highlights"], ["常找可乐"])
        second = self._infer(client, spoken_text="不是这个", session_id="neg", user_id="user-1")
        self.assertEqual(second.status_code, 200)
        body = second.json()
        self.assertNotIn("可乐", body["result"]["speech"])
        self.assertNotIn("在左边", body["result"]["speech"])
        self.assertEqual(body["highlights"], ["纠正：先不要按旧说法找可乐"])
        self.assertEqual(memory.writes, [])
        self.assertNotIn("fake-jpeg", str(memory.writes))

    def test_recall_uses_saved_highlights_and_backend_lists_them(self):
        memory = SlowMemory()
        service = LongMemoryService(client=memory, enabled=True, timeout_s=0.3, sync_writes=True)
        client = self._client(service)
        first = self._infer(
            client,
            spoken_text="帮我找无糖可乐",
            session_id="recall",
            user_id="user-r",
        )
        self.assertEqual(first.status_code, 200)
        self.assertIn("常找无糖可乐", first.json()["highlights"])
        listed = client.get(
            "/highlights",
            params={"user_id": "user-r"},
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(listed.status_code, 200)
        self.assertIn("常找无糖可乐", listed.json()["highlights"])
        self.assertEqual(listed.json()["memory"]["finds"], ["常找无糖可乐"])
        self.assertEqual(listed.json()["memory"]["kept"], [])
        second = self._infer(
            client,
            spoken_text="我上次找的是什么",
            session_id="recall",
            user_id="user-r",
        )
        self.assertEqual(second.status_code, 200)
        speech = second.json()["result"]["speech"]
        self.assertIn("你常找的是无糖可乐", speech)
        self.assertNotIn("根据你的习惯", speech)
        self.assertNotIn("上次", second.json()["task"]["main_task"])
        self.assertEqual(second.json()["highlights"], [])
        deleted = client.delete(
            "/highlights",
            params={"user_id": "user-r"},
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["highlights"], [])
        self.assertEqual(deleted.json()["memory"]["finds"], [])

    def test_live_frame_does_not_search_again_inside_cache(self):
        memory = SlowMemory(snippets=["常去门口"])
        service = LongMemoryService(client=memory, enabled=True, timeout_s=0.3)
        client = self._client(service)
        first = self._infer(client, spoken_text="帮我找可乐", session_id="cache", user_id="user-2")
        self.assertEqual(memory.searches, 0)
        self.assertEqual(memory.writes, [])
        self.assertEqual(first.json()["memory"]["finds"], ["常找可乐"])
        self.assertTrue(first.json()["task"]["found"])
        live = self._infer(client, session_id="cache", user_id="user-2", mode="live")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(memory.searches, 0)
        self.assertEqual(live.json()["understanding"]["kind"], "watch")
        self.assertTrue(live.json()["sensors"]["camera"])
        self.assertNotIn("可乐", live.json()["result"]["speech"] or "")

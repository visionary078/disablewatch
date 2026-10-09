import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

from minicpmo_runner import MiniCPMOAccessibilityRunner
from services.task_memory import TaskMemoryStore
from services.sensing import band_from_tof_mm, object_found, sensors_for
from services.utterance import understand_speech

if TestClient is not None:
    from server import create_app


class UtteranceUnderstandingTests(unittest.TestCase):
    def test_rules_still_hear_a_clear_search(self):
        heard = understand_speech("帮我找可乐")
        self.assertEqual(heard["source"], "rule")
        self.assertEqual(heard["intent"], "find_product")
        self.assertEqual(heard["label"], "找东西")

    def test_model_json_overrides_a_misleading_sentence(self):
        heard = understand_speech(
            "我要走了",
            '{"intent":"指路","target":"出口","summary":"你是想离开这里"}',
        )
        self.assertEqual(heard["source"], "model")
        self.assertEqual(heard["intent"], "guide_way")
        self.assertEqual(heard["target"], "出口")
        self.assertEqual(heard["summary"], "你是想离开这里")

    def test_model_can_name_a_thing_without_the_word_find(self):
        heard = understand_speech(
            "我渴了想喝点甜的",
            '{"intent":"找东西","target":"甜的饮料","summary":"你想找甜的饮料"}',
        )
        self.assertEqual(heard["intent"], "find_product")
        self.assertEqual(heard["kind"], "task")
        self.assertEqual(heard["target"], "甜的饮料")

    def test_small_talk_is_not_a_search(self):
        heard = understand_speech("今天天气怎么样")
        self.assertEqual(heard["kind"], "chat")
        self.assertEqual(heard["intent"], "")
        news = understand_speech("今天有什么大事发生")
        self.assertEqual(news["kind"], "chat")
        self.assertEqual(news["intent"], "")

    def test_tof_reading_becomes_a_coarse_band(self):
        self.assertEqual(band_from_tof_mm(500), "一臂内")
        self.assertEqual(band_from_tof_mm(1200), "较近")
        self.assertEqual(band_from_tof_mm(3000), "较远")
        self.assertEqual(band_from_tof_mm(""), "无法判断")
        self.assertEqual(sensors_for("task"), {"camera": True, "tof": True})
        self.assertEqual(sensors_for("chat"), {"camera": False, "tof": False})

    def test_object_found_needs_a_place(self):
        seen = {
            "direction": "右前方",
            "confidence": "medium",
            "anchor": "柜子",
            "speech": "正在帮你找红色的瓶子。在你右前方的柜子。",
        }
        missing = {
            "direction": "未确定",
            "confidence": "low",
            "speech": "还在帮你找红色的瓶子。",
        }
        self.assertTrue(object_found(seen, "find_product"))
        self.assertFalse(object_found(missing, "find_product"))
        self.assertFalse(object_found(seen, "guide_way"))


@unittest.skipIf(TestClient is None, "未安装 fastapi，跳过接口测试")
class UtteranceInferTests(unittest.TestCase):
    def test_infer_uses_model_intent_instead_of_keywords(self):
        runner = MiniCPMOAccessibilityRunner(mock=True)
        runner.load()
        runner.classify_utterance = lambda text: (
            '{"intent":"指路","target":"出口","summary":"你是想离开这里"}'
        )
        client = TestClient(create_app(runner, app_token="secret", task_store=TaskMemoryStore()))
        response = client.post(
            "/infer",
            files={"image": ("scene.jpg", b"fake-jpeg", "image/jpeg")},
            data={"spoken_text": "我要走了", "session_id": "intent", "user_id": "user-intent", "mode": "precise"},
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["understanding"]["source"], "model")
        self.assertEqual(body["understanding"]["label"], "指路")
        self.assertEqual(body["understanding"]["summary"], "你是想离开这里")
        self.assertEqual(body["task"]["intent"], "guide_way")
        self.assertIn("稍向右", body["result"]["speech"])
        self.assertIn("展示架", body["result"]["speech"])
        self.assertNotIn("正在帮你找", body["result"]["speech"])
        self.assertEqual(body["understanding"]["kind"], "task")
        self.assertTrue(body["sensors"]["camera"])
        self.assertTrue(body["sensors"]["tof"])

    def test_small_talk_skips_the_camera_model(self):
        runner = MiniCPMOAccessibilityRunner(mock=True)
        runner.load()
        client = TestClient(create_app(runner, app_token="secret", task_store=TaskMemoryStore()))
        response = client.post(
            "/infer",
            files={"image": ("scene.jpg", b"fake-jpeg", "image/jpeg")},
            data={"spoken_text": "今天天气怎么样", "session_id": "chat", "user_id": "user-chat", "mode": "precise"},
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["understanding"]["kind"], "chat")
        self.assertNotIn("正在帮你找", body["result"]["speech"])
        self.assertNotIn("天气", body["task"]["main_task"])
        self.assertFalse(body["sensors"]["camera"])
        self.assertFalse(body["sensors"]["tof"])
        self.assertNotIn("米", body["result"]["speech"])

    def test_task_uses_tof_millimetres_as_a_band(self):
        runner = MiniCPMOAccessibilityRunner(mock=True)
        runner.load()
        client = TestClient(create_app(runner, app_token="secret", task_store=TaskMemoryStore()))
        response = client.post(
            "/infer",
            files={"image": ("scene.jpg", b"fake-jpeg", "image/jpeg")},
            data={
                "spoken_text": "帮我找可乐",
                "session_id": "tof",
                "user_id": "user-tof",
                "mode": "precise",
                "tof_mm": "500",
            },
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["understanding"]["kind"], "task")
        self.assertEqual(body["result"]["distance_band"], "一臂内")
        self.assertTrue(body["task"]["found"])
        self.assertTrue(body["sensors"]["camera"])
        self.assertFalse(body["sensors"]["tof"])
        self.assertNotIn("米", body["result"]["speech"])
        self.assertNotIn("500", body["result"]["speech"])

import time
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

from minicpmo_runner import MiniCPMOAccessibilityRunner
from services.journal import fresh_notes, looks_like_journal_question, select_for_question
from services.long_memory import LongMemoryService
from services.task_memory import TaskMemoryStore

if TestClient is not None:
    from server import create_app


class JournalUnitTests(unittest.TestCase):
    def test_drops_notes_older_than_a_day_and_keeps_the_users_words(self):
        now = 1_000_000.0
        notes = fresh_notes(
            [
                {"text": "降压药放在床头柜左边", "at": now - 3600},
                {"text": "太旧了", "at": now - 25 * 3600},
            ],
            now=now,
        )
        self.assertEqual([item["text"] for item in notes], ["降压药放在床头柜左边"])

    def test_question_pulls_the_matching_note_not_a_preset_bucket(self):
        now = 1_000_000.0
        picked = select_for_question(
            "药放在哪",
            [
                {"text": "降压药放在床头柜左边", "at": now - 3600},
                {"text": "中午吃了面", "at": now - 1800},
            ],
            now=now,
        )
        self.assertEqual([item["text"] for item in picked], ["降压药放在床头柜左边"])

    def test_day_question_returns_the_recent_notes(self):
        now = 1_000_000.0
        picked = select_for_question(
            "今天发生了什么",
            [
                {"text": "上午去了药店", "at": now - 7200},
                {"text": "钥匙在蓝色外套里", "at": now - 600},
            ],
            now=now,
        )
        self.assertEqual(
            [item["text"] for item in picked],
            ["上午去了药店", "钥匙在蓝色外套里"],
        )

    def test_voice_question_is_not_stored_as_a_plain_note(self):
        self.assertTrue(looks_like_journal_question("药放在哪"))
        self.assertTrue(looks_like_journal_question("今天发生了什么"))
        self.assertFalse(looks_like_journal_question("降压药放在床头柜左边"))
        self.assertFalse(looks_like_journal_question("帮我记住钥匙在口袋里"))


@unittest.skipIf(TestClient is None, "未安装 fastapi，跳过接口测试")
class JournalApiTests(unittest.TestCase):
    def _client(self, memory=None):
        runner = MiniCPMOAccessibilityRunner(mock=True)
        runner.load()
        service = LongMemoryService(client=memory, enabled=memory is not None)
        return TestClient(
            create_app(
                runner,
                app_token="secret",
                task_store=TaskMemoryStore(),
                long_memory=service,
            )
        )

    def test_phone_page_is_served(self):
        client = self._client()
        response = client.get("/phone/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("使用说明", response.text)
        self.assertIn("说话", response.text)
        self.assertIn("只留字幕", response.text)
        self.assertNotIn("记下来", response.text)

    def test_ask_answers_from_the_phones_24h_notes(self):
        client = self._client()
        now = time.time()
        response = client.post(
            "/journal/ask",
            json={
                "user_id": "phone-1",
                "question": "药放在哪",
                "notes": [
                    {"role": "user", "text": "降压药放在床头柜左边", "at": now - 3600},
                    {"role": "user", "text": "中午吃了面", "at": now - 1000},
                    {"role": "user", "text": "太旧了", "at": now - 30 * 3600},
                ],
            },
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("床头柜", body["speech"])
        self.assertNotIn("太旧了", body["speech"])
        self.assertNotIn("中午吃了面", body["speech"])
        self.assertEqual(body["stored_on"], "phone")

    def test_remember_stays_on_the_phone(self):
        client = self._client()
        saved = client.post(
            "/journal/remember",
            json={"user_id": "phone-1", "text": "钥匙在蓝色外套里"},
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), {"stored_on": "phone"})
        self.assertNotIn("memos", saved.json())
        asked = client.post(
            "/journal/ask",
            json={"user_id": "phone-1", "question": "钥匙呢", "notes": []},
            headers={"X-App-Token": "secret"},
        )
        self.assertNotIn("蓝色外套", asked.json()["speech"])
        self.assertIn("还没记下", asked.json()["speech"])

    def test_voice_act_saves_a_statement_and_answers_a_question(self):
        client = self._client()
        now = time.time()
        notes = [{"role": "user", "text": "降压药放在床头柜左边", "at": now - 60}]
        saved = client.post(
            "/journal/act",
            json={"user_id": "phone-1", "text": "降压药放在床头柜左边", "notes": notes},
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["action"], "save")
        self.assertEqual(saved.json()["speech"], "已经记下。")
        asked = client.post(
            "/journal/act",
            json={"user_id": "phone-1", "text": "药放在哪", "notes": notes},
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(asked.status_code, 200)
        self.assertEqual(asked.json()["action"], "ask")
        self.assertIn("床头柜", asked.json()["speech"])

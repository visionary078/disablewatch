import unittest

from minicpmo_runner import MiniCPMOAccessibilityRunner
from services.chat_turns import TURN_TTL_S, ChatTurnStore


class ChatTurnTests(unittest.TestCase):
    def test_follow_up_keeps_the_last_five_exchanges_for_ten_minutes(self):
        store = ChatTurnStore()
        now = 1_000_000.0
        for index in range(6):
            store.append("session-1", f"第{index}句", f"答{index}", now=now + index)
        kept = store.recent("session-1", now=now + 5)
        self.assertEqual([item["text"] for item in kept], ["第1句", "答1", "第2句", "答2", "第3句", "答3", "第4句", "答4", "第5句", "答5"])
        self.assertEqual(store.recent("session-1", now=now + 5 + TURN_TTL_S + 1), [])

    def test_chat_prompt_carries_the_previous_turn(self):
        runner = MiniCPMOAccessibilityRunner(mock=False)
        captured = {}

        class Gateway:
            def complete_chat(self, prompt, web_search=False, timeout=20):
                captured["prompt"] = prompt

                class Reply:
                    text = "明天也是多云。"

                return Reply()

        runner.gateway = Gateway()
        speech = runner.chat_reply(
            "那明天呢",
            [{"role": "user", "text": "今天天气怎么样"}, {"role": "assistant", "text": "北京今天多云。"}],
        )
        self.assertEqual(speech, "明天也是多云。")
        self.assertIn("北京今天多云", captured["prompt"])
        self.assertIn("那明天呢", captured["prompt"])
        self.assertIn("接上一句", captured["prompt"])

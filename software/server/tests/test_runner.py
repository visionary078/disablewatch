import unittest

from minicpmo_runner import apply_distance_band, normalize_result, sanitize_guidance, try_parse_json
from prompts import build_accessibility_prompt


class RunnerSafetyTests(unittest.TestCase):
    def test_parse_json_code_fence(self):
        value = try_parse_json('```json\n{"direction":"左侧"}\n```')
        self.assertEqual(value["direction"], "左侧")

    def test_invalid_direction_becomes_unknown(self):
        value = normalize_result({"direction": "身后", "action": "目标在身后", "speech": "目标在身后"})
        self.assertEqual(value["direction"], "未确定")
        self.assertNotIn("不确定方向", value["speech"])
        self.assertNotIn("重新拍摄", value["speech"])

    def test_precise_distance_and_steps_are_removed(self):
        text = sanitize_guidance("入口约两米，向前走三步后可以直接前进。")
        self.assertNotIn("两米", text)
        self.assertNotIn("三步", text)
        self.assertNotIn("可以直接前进", text)

    def test_high_risk_warning_is_first(self):
        value = normalize_result(
            {
                "direction": "正前方",
                "risk_level": "high",
                "action": "请缓慢确认。",
                "speech": "目标在正前方。",
            }
        )
        self.assertTrue(value["speech"].startswith("注意"))

    def test_mock_schema_is_complete(self):
        value = normalize_result({})
        expected = {
            "intent",
            "scene",
            "target",
            "direction",
            "proximity",
            "text_reading",
            "obstacles",
            "risk_level",
            "confidence",
            "action",
            "speech",
            "task_decision",
            "anchor",
            "level",
            "slot",
            "place_index",
            "main_task",
        }
        self.assertEqual(set(value), expected)

    def test_live_prompt_asks_for_short_speech(self):
        text = build_accessibility_prompt("前方安全吗", mode="live")
        self.assertIn("实时巡视", text)
        self.assertIn("36 个汉字", text)

    def test_prompt_includes_task_memory(self):
        text = build_accessibility_prompt(
            "帮我找可乐",
            extra_prompt="[主任务记忆]\n当前主任务：找可乐",
        )
        self.assertIn("主任务记忆", text)
        self.assertIn("keep 或 refine", text)
        self.assertIn("正在帮你找", text)

    def test_humanize_search_speech_mentions_goal(self):
        from minicpmo_runner import humanize_search_speech

        value = humanize_search_speech(
            {
                "intent": "find_product",
                "target": "可乐",
                "direction": "正前方",
                "proximity": "较近",
                "scene": "货架区域",
                "risk_level": "medium",
                "speech": "目标在正前方。",
            },
            intent="find_product",
            target="可乐",
            main_task="帮我找可乐",
        )
        self.assertIn("正在帮你找可乐", value["speech"])
        self.assertNotIn("目标在", value["speech"])
        self.assertNotIn("第", value["speech"])

    def test_steer_stops_for_a_hazard_and_names_the_side(self):
        from minicpmo_runner import humanize_search_speech

        blocked = humanize_search_speech(
            {
                "direction": "正前方",
                "risk_level": "high",
                "obstacles": ["台阶"],
                "speech": "往前走。",
            },
            intent="avoid_obstacle",
        )
        self.assertIn("先停一下", blocked["speech"])
        self.assertIn("台阶", blocked["speech"])
        clear = humanize_search_speech(
            {"direction": "右前方", "risk_level": "low", "obstacles": [], "speech": "往右。"},
            intent="guide_way",
        )
        self.assertEqual(clear["speech"], "稍向右。")

    def test_shelf_is_counted_from_the_users_left_hand(self):
        from minicpmo_runner import compose_find_speech

        speech = compose_find_speech(
            "可乐",
            direction="正前方",
            anchor="货架",
            level="手高这一层",
            slot="中间",
            place_index=2,
        )
        self.assertIn("正前方的货架", speech)
        self.assertIn("手高这一层", speech)
        self.assertIn("从你左手边数第2个", speech)
        self.assertNotIn("米", speech)

    def test_blank_surface_uses_the_near_edge_not_a_count(self):
        from minicpmo_runner import compose_find_speech

        speech = compose_find_speech(
            "纸",
            direction="正前方",
            anchor="平面",
            level="手高这一层",
            slot="靠左",
            place_index=3,
        )
        self.assertIn("靠近你这一侧", speech)
        self.assertIn("靠左", speech)
        self.assertNotIn("第", speech)

    def test_distance_band_is_coarse_and_avoids_meters(self):
        value = apply_distance_band(
            {
                "speech": "注意前方有展示架。入口在右前方，请先停下确认。",
                "action": "请先停下确认。",
            },
            "较近",
        )
        self.assertEqual(value["distance_band"], "较近")
        self.assertIn("较近", value["speech"])
        self.assertNotIn("米", value["speech"])

    def test_invalid_distance_band_is_unknown(self):
        value = apply_distance_band({"speech": "请停下确认。"}, "3米")
        self.assertEqual(value["distance_band"], "无法判断")
        self.assertNotIn("3米", value["speech"])


if __name__ == "__main__":
    unittest.main()

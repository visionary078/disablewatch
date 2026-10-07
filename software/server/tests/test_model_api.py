import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minicpmo_runner import MiniCPMOAccessibilityRunner
from model_api import ModelGateway, ModelProfile, OpenAICompatibleVisionClient, load_model_gateway


class _FakeHTTPResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.value, ensure_ascii=False).encode("utf-8")


class ModelAPITests(unittest.TestCase):
    def test_endpoint_is_built_from_version_root(self):
        profile = ModelProfile(name="p", base_url="https://example.test/v1", model="vision")
        self.assertEqual(profile.endpoint, "https://example.test/v1/chat/completions")

    def test_full_chat_completions_endpoint_is_kept(self):
        profile = ModelProfile(
            name="p",
            base_url="https://example.test/v1/chat/completions",
            model="vision",
        )
        self.assertEqual(profile.endpoint, "https://example.test/v1/chat/completions")

    def test_request_contains_image_model_and_bearer_key(self):
        profile = ModelProfile(
            name="primary",
            base_url="https://example.test/v1",
            model="vision-a",
            api_key="secret",
        )
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeHTTPResponse(
                {
                    "id": "response-1",
                    "model": "vision-b",
                    "choices": [{"message": {"content": "{\"direction\":\"正前方\"}"}}],
                    "usage": {"total_tokens": 12},
                }
            )

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as image:
            image.write(b"fake-jpeg")
            image_path = image.name
        try:
            with patch("model_api.urllib.request.urlopen", side_effect=fake_urlopen):
                result = OpenAICompatibleVisionClient().complete(
                    profile,
                    image_path,
                    "describe",
                    model_override="vision-b",
                )
        finally:
            os.unlink(image_path)

        self.assertEqual(captured["url"], "https://example.test/v1/chat/completions")
        self.assertEqual(captured["payload"]["model"], "vision-b")
        self.assertEqual(captured["payload"]["max_completion_tokens"], 512)
        image_url = captured["payload"]["messages"][0]["content"][0]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/jpeg;base64,"))
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(result.model, "vision-b")
        self.assertEqual(result.usage["total_tokens"], 12)

    def test_profile_specific_api_key_env_is_used(self):
        value = {
            "default_profile": "backup",
            "profiles": {
                "backup": {
                    "base_url": "https://example.test/v1",
                    "model": "vision",
                    "api_key_env": "BACKUP_TEST_KEY",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "profiles.json"
            config.write_text(json.dumps(value), encoding="utf-8")
            with patch.dict(os.environ, {"BACKUP_TEST_KEY": "backup-secret"}, clear=False):
                gateway = load_model_gateway(str(config))
        self.assertEqual(gateway.profiles["backup"].api_key, "backup-secret")
        self.assertEqual(gateway.profiles["backup"].api_key_env, "BACKUP_TEST_KEY")

    def test_mock_runner_requires_no_local_model(self):
        runner = MiniCPMOAccessibilityRunner(mock=True)
        runner.load()
        status = runner.status()
        self.assertFalse(status["local_model_required"])
        self.assertEqual(status["device"], "remote-api")

    def test_chat_weather_asks_for_web_search_and_no_image(self):
        profile = ModelProfile(
            name="primary",
            base_url="https://example.test/v1",
            model="mimo-v2.5",
            api_key="secret",
        )
        gateway = ModelGateway({"primary": profile}, "primary")
        captured = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _FakeHTTPResponse(
                {
                    "model": "mimo-v2.5",
                    "choices": [{"message": {"content": "北京今天多云。"}}],
                }
            )

        runner = MiniCPMOAccessibilityRunner(mock=False)
        runner.gateway = gateway
        with patch("model_api.urllib.request.urlopen", side_effect=fake_urlopen):
            speech = runner.chat_reply("今天天气怎么样")

        self.assertIn("多云", speech)
        self.assertNotIn("image_url", json.dumps(captured["payload"]))
        tool = captured["payload"]["tools"][0]
        self.assertEqual(tool["type"], "web_search")
        self.assertTrue(tool["force_search"])
        self.assertNotIn("正在帮你找", speech)

    def test_plain_hello_does_not_force_web_search(self):
        profile = ModelProfile(name="primary", base_url="https://example.test/v1", model="mimo-v2.5", api_key="secret")
        gateway = ModelGateway({"primary": profile}, "primary")
        captured = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _FakeHTTPResponse({"choices": [{"message": {"content": "我在。"}}]})

        runner = MiniCPMOAccessibilityRunner(mock=False)
        runner.gateway = gateway
        with patch("model_api.urllib.request.urlopen", side_effect=fake_urlopen):
            runner.chat_reply("你好")
        self.assertNotIn("tools", captured["payload"])

    def test_chat_profile_is_used_when_it_has_its_own_key(self):
        primary = ModelProfile(name="primary", base_url="https://vision.test/v1", model="vision", api_key="vision-key")
        chat = ModelProfile(name="chat", base_url="https://chat.test/v1", model="chat-model", api_key="chat-key")
        gateway = ModelGateway({"primary": primary, "chat": chat}, "primary")
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return _FakeHTTPResponse({"choices": [{"message": {"content": "你好。"}}]})

        with patch("model_api.urllib.request.urlopen", side_effect=fake_urlopen):
            gateway.complete_chat("你好", web_search=False)
        self.assertEqual(captured["url"], "https://chat.test/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
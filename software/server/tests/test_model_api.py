import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minicpmo_runner import MiniCPMOAccessibilityRunner
from model_api import ModelProfile, OpenAICompatibleVisionClient, load_model_gateway


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


if __name__ == "__main__":
    unittest.main()
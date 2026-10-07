import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from minicpmo_runner import MiniCPMOAccessibilityRunner
from server import ASR_LOCKS, _build_asr_handler, create_app
from services.asr import ASRHandler, MOCK_ASR_TEXT


class ASRHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_returns_text(self):
        handler = ASRHandler(mock=True)
        result = await handler.transcribe(b"fake-audio", "recording.mp3")
        self.assertEqual(result["text"], MOCK_ASR_TEXT)
        self.assertIn("latency_ms", result)

    async def test_unconfigured_raises(self):
        handler = ASRHandler(mock=False, base_url="")
        self.assertFalse(handler.enabled)
        with self.assertRaises(RuntimeError):
            await handler.transcribe(b"fake-audio")

    async def test_mimo_posts_wav_to_chat_completions(self):
        handler = ASRHandler(
            base_url="https://api.xiaomimimo.com/v1",
            api_key="test-key",
            model="mimo-v2.5-asr",
            provider="mimo",
        )
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": "找一下水杯"}}]}
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            captured["auth"] = request.get_header("Authorization")
            return Response()

        with patch("services.asr.urllib.request.urlopen", fake_urlopen):
            result = await handler.transcribe(b"RIFFwav", "speech.wav")
        self.assertEqual(result["text"], "找一下水杯")
        self.assertEqual(captured["url"], "https://api.xiaomimimo.com/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "mimo-v2.5-asr")
        self.assertEqual(captured["body"]["asr_options"]["language"], "zh")
        self.assertTrue(captured["body"]["messages"][0]["content"][0]["input_audio"]["data"].startswith("data:audio/wav;base64,"))
        self.assertEqual(captured["auth"], "Bearer test-key")


class ASRBuildTests(unittest.TestCase):
    def test_mimo_key_enables_asr_when_dedicated_asr_is_empty(self):
        with patch.dict(
            "os.environ",
            {
                "ASR_API_BASE_URL": "",
                "MODEL_API_BASE_URL": "https://api.xiaomimimo.com/v1",
                "MODEL_API_KEY": "test-key",
            },
            clear=False,
        ):
            handler = _build_asr_handler(SimpleNamespace(mock=False))
        self.assertTrue(handler.enabled)
        self.assertEqual(handler.provider, "mimo")
        self.assertEqual(handler.model, "mimo-v2.5-asr")


class ASRServerTests(unittest.TestCase):
    def setUp(self):
        runner = MiniCPMOAccessibilityRunner(mock=True)
        runner.load()
        self.client = TestClient(create_app(runner, app_token="secret"))

    def test_asr_mock_contract(self):
        response = self.client.post(
            "/asr",
            files={"audio": ("recording.mp3", b"fake-audio", "audio/mpeg")},
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["text"], MOCK_ASR_TEXT)
        self.assertIn("latency_ms", body)

    def test_asr_rejects_missing_token(self):
        response = self.client.post(
            "/asr",
            files={"audio": ("recording.mp3", b"fake-audio", "audio/mpeg")},
        )
        self.assertEqual(response.status_code, 401)

    def test_health_reports_asr(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["asr_enabled"])


class ASRConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_asr_returns_429_when_busy(self):
        try:
            import httpx
        except ImportError:
            self.skipTest("未安装 httpx")

        runner = MiniCPMOAccessibilityRunner(mock=True)
        runner.load()
        app = create_app(runner, app_token="asr429")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            lock = ASR_LOCKS["asr429"]
            await lock.acquire()
            try:
                response = await client.post(
                    "/asr",
                    files={"audio": ("recording.mp3", b"fake-audio", "audio/mpeg")},
                    headers={"X-App-Token": "asr429"},
                )
            finally:
                lock.release()
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()

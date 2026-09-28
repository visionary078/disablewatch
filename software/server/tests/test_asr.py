import unittest

from fastapi.testclient import TestClient

from minicpmo_runner import MiniCPMOAccessibilityRunner
from server import ASR_LOCKS, create_app
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

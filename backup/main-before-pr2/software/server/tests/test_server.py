import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - environment without FastAPI
    TestClient = None

from minicpmo_runner import MiniCPMOAccessibilityRunner

if TestClient is not None:
    from server import create_app


@unittest.skipIf(TestClient is None, "未安装 fastapi，跳过接口测试")
class ServerMiniProgramTests(unittest.TestCase):
    def setUp(self):
        runner = MiniCPMOAccessibilityRunner(mock=True)
        runner.load()
        self.client = TestClient(create_app(runner, app_token="secret"))

    def test_health_does_not_require_token(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["glasses_ui"])

    def test_infer_rejects_missing_token(self):
        response = self.client.post(
            "/infer",
            files={"image": ("scene.jpg", b"fake-jpeg", "image/jpeg")},
            data={"question": "入口在哪个方向？"},
        )
        self.assertEqual(response.status_code, 401)

    def test_infer_mock_accepts_live_mode_and_distance_band(self):
        response = self.client.post(
            "/infer",
            files={"image": ("scene.jpg", b"fake-jpeg", "image/jpeg")},
            data={
                "question": "前方安全吗？",
                "mode": "live",
                "distance_band": "较近",
            },
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["result"]["distance_band"], "较近")
        self.assertNotIn("米", body["result"]["speech"])

    def test_infer_spoken_text_searches_product(self):
        response = self.client.post(
            "/infer",
            files={"image": ("scene.jpg", b"fake-jpeg", "image/jpeg")},
            data={
                "question": "请判断当前是否安全，并告诉我目标在哪个方向。",
                "spoken_text": "帮我找可乐",
                "session_id": "voice-cola",
                "mode": "precise",
            },
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["task"]["intent"], "find_product")
        self.assertIn("可乐", body["task"]["main_task"])
        self.assertIn("正在帮你找可乐", body["result"]["speech"])
        self.assertEqual(body["result"]["target"], "可乐")

        live = self.client.post(
            "/infer",
            files={"image": ("scene.jpg", b"fake-jpeg", "image/jpeg")},
            data={
                "question": "前方安全吗？",
                "session_id": "voice-cola",
                "mode": "live",
            },
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(live.status_code, 200)
        live_body = live.json()
        self.assertIn("可乐", live_body["task"]["question"])
        self.assertIn("正在帮你找可乐", live_body["result"]["speech"])

    def test_infer_rejects_oversize_image(self):
        runner = MiniCPMOAccessibilityRunner(mock=True)
        runner.load()
        client = TestClient(create_app(runner, app_token="secret", max_upload_bytes=8))
        response = client.post(
            "/infer",
            files={"image": ("scene.jpg", b"1234567890", "image/jpeg")},
            data={"question": "入口在哪个方向？"},
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(response.status_code, 413)

    def test_glasses_page_is_served(self):
        page = self.client.get("/glasses/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers.get("content-type", ""))
        self.assertIn("眼镜", page.text)
        redirect = self.client.get("/glasses", follow_redirects=False)
        self.assertIn(redirect.status_code, (301, 302, 307, 308))

    def test_glasses_look_page_is_served(self):
        page = self.client.get("/glasses/look.html")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers.get("content-type", ""))
        self.assertIn("看见眼前", page.text)
        self.assertIn("体验一下", page.text)
        self.assertIn("超市", page.text)
        self.assertIn("sunglasses.glb", page.text)
        self.assertNotIn("Khronos", page.text)

    def test_infer_accepts_glasses_client_and_session(self):
        response = self.client.post(
            "/infer",
            files={"image": ("scene.jpg", b"fake-jpeg", "image/jpeg")},
            data={
                "question": "前方安全吗？",
                "mode": "live",
                "session_id": "glasses-demo",
                "client": "glasses",
            },
            headers={"X-App-Token": "secret"},
        )
        self.assertEqual(response.status_code, 200)
        task = response.json()["task"]
        self.assertEqual(task["session_id"], "glasses-demo")
        self.assertEqual(task["client"], "glasses")


if __name__ == "__main__":
    unittest.main()

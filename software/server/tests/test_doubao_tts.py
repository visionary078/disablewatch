import base64
import json
import unittest

from services.doubao_tts import audio_from_stream


class DoubaoTtsTests(unittest.TestCase):
    def test_joins_audio_chunks_and_skips_the_ending_mark(self):
        raw = "\n".join(
            [
                json.dumps({"code": 0, "data": base64.b64encode(b"abc").decode("ascii")}),
                json.dumps({"code": 0, "data": None, "sentence": {"text": "你"}}),
                json.dumps({"code": 0, "data": base64.b64encode(b"def").decode("ascii")}),
                json.dumps({"code": 20000000, "message": "ok", "data": None}),
            ]
        )
        self.assertEqual(audio_from_stream(raw), b"abcdef")

    def test_error_without_audio_raises(self):
        raw = json.dumps({"code": 55000000, "message": "resource ID is mismatched"})
        with self.assertRaises(RuntimeError) as caught:
            audio_from_stream(raw)
        self.assertIn("mismatched", str(caught.exception))

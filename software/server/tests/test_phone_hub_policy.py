import importlib.util
import unittest
from pathlib import Path


def _policy():
    path = Path(__file__).resolve().parents[2] / "android" / "hub_policy.py"
    spec = importlib.util.spec_from_file_location("hub_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PhoneHubPolicyTest(unittest.TestCase):
    def test_busy_frame_is_dropped(self):
        policy = _policy()
        self.assertEqual(policy.frame_action(True, True, True), policy.DROP)

    def test_idle_frame_is_stored_until_a_task_asks_to_look(self):
        policy = _policy()
        self.assertEqual(policy.frame_action(False, False, False), policy.STORE)

    def test_spoken_line_forwards_the_latest_frame(self):
        policy = _policy()
        self.assertEqual(policy.frame_action(False, True, False), policy.FORWARD)

    def test_camera_sensor_keeps_forwarding(self):
        policy = _policy()
        self.assertEqual(policy.frame_action(False, False, True), policy.FORWARD)


if __name__ == "__main__":
    unittest.main()

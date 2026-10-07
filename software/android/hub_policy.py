"""Whether the phone keeps a WiFi camera frame. The Android service uses the same rules."""

DROP = "drop"
STORE = "store"
FORWARD = "forward"


def frame_action(busy, has_spoken, camera_on):
    if busy:
        return DROP
    if has_spoken or camera_on:
        return FORWARD
    return STORE

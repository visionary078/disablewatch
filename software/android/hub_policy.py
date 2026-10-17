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


TOF_MAX_MM = 8000
TOF_MAX_AGE_S = 3.0


def fresh_tof_mm(mm, age_s):
    """ESP32 上报的毫米数。过期、空值或超出模块量程就不要带进这一帧。"""
    try:
        value = int(mm)
    except (TypeError, ValueError):
        return ""
    try:
        age = float(age_s)
    except (TypeError, ValueError):
        return ""
    if value <= 0 or value > TOF_MAX_MM:
        return ""
    if age < 0 or age > TOF_MAX_AGE_S:
        return ""
    return str(value)

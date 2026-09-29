"""任务才需要摄像头和 ToF。这里只把读数收成远近档，不把毫米说出去。"""

TASK_KIND = "task"
CHAT_KIND = "chat"


def sensors_for(kind: str) -> dict:
    needed = str(kind or "") == TASK_KIND
    return {"camera": needed, "tof": needed}


def band_from_tof_mm(value) -> str:
    try:
        millimeters = float(value)
    except (TypeError, ValueError):
        return "无法判断"
    if millimeters != millimeters or millimeters <= 0:
        return "无法判断"
    if millimeters <= 800:
        return "一臂内"
    if millimeters <= 2000:
        return "较近"
    return "较远"

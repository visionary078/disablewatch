"""任务才需要摄像头和 ToF。这里只把读数收成远近档，不把毫米说出去。"""

TASK_KIND = "task"
CHAT_KIND = "chat"


def sensors_for(kind: str) -> dict:
    needed = str(kind or "") == TASK_KIND
    return {"camera": needed, "tof": needed}


LOCATE_INTENTS = {"find_product", "find_entrance", "find_cashier"}
KNOWN_DIRECTIONS = {"左侧", "左前方", "正前方", "右前方", "右侧"}
PLACE_ANCHORS = {"货架", "柜子", "桌子", "台面", "平面"}


def object_found(parsed: dict, intent: str) -> bool:
    """找东西的任务已经指出具体位置时，这条任务可以结束。"""
    if str(intent or "") not in LOCATE_INTENTS or not isinstance(parsed, dict):
        return False
    direction = str(parsed.get("direction") or "")
    if direction not in KNOWN_DIRECTIONS:
        return False
    confidence = str(parsed.get("confidence") or "low").lower()
    if confidence not in {"medium", "high"}:
        return False
    speech = str(parsed.get("speech") or "")
    if any(token in speech for token in ("还在帮你找", "没看到", "没有看到", "继续观察")):
        return False
    anchor = str(parsed.get("anchor") or "")
    if anchor in PLACE_ANCHORS:
        return True
    return "在你" in speech


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

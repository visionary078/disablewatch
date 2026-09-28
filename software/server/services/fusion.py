"""模块 C：并行双模型融合。

对主模型与验证模型的归一化结果做字段级融合，输出一致/相近/分歧/降级四类结论。
纯函数实现，不依赖网络或模型运行时，便于单测覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

# 与 prompts.py 中 ALLOWED_DIRECTIONS / ALLOWED_RISK_LEVELS 保持一致。
DIRECTION_ORDER = ("左侧", "左前方", "正前方", "右前方", "右侧")
KNOWN_DIRECTIONS = DIRECTION_ORDER + ("后方", "左后方", "右后方")
UNKNOWN_DIRECTION = "未确定"
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
CONFIDENCE_UP = {"low": "medium", "medium": "high", "high": "high"}

AGREEMENT_CONSISTENT = "consistent"
AGREEMENT_SIMILAR = "similar"
AGREEMENT_CONFLICT = "conflict"
AGREEMENT_DEGRADED = "degraded"

CONFLICT_SPEECH_PREFIX = "两次识别不一致，请停下确认。"
UNCERTAIN_SPEECH_MARKERS = (
    "未能确认目标",
    "无法确认目标",
    "不确定方向",
    "方向未确定",
    "目标方向未确定",
    "两次识别不一致",
    "请停下并重新拍摄",
    "请停下重新确认",
    "无法给出明确判断",
    "请尝试换一个角度拍摄",
    "未能确认目标方向",
)


@dataclass
class FusionConfig:
    """model_profiles.json 中 fusion 块的运行配置。"""

    mode: str = "conditional"  # none | parallel | conditional
    precise_mode: str = "parallel"
    live_mode: str = "conditional"
    verify_profile: str = "verify"
    timeout_scale: float = 1.5
    live_verify_wait: float = 2.0

    @classmethod
    def from_mapping(cls, value: Optional[Mapping[str, Any]]) -> "FusionConfig":
        mapping = value if isinstance(value, Mapping) else {}

        def pick(key: str, default: Any) -> Any:
            item = mapping.get(key)
            return default if item is None or item == "" else item

        def one_of(key: str, default: str, allowed: tuple) -> str:
            item = str(pick(key, default)).strip().lower()
            return item if item in allowed else default

        try:
            timeout_scale = float(pick("timeout_scale", 1.5))
        except (TypeError, ValueError):
            timeout_scale = 1.5
        if timeout_scale <= 0:
            timeout_scale = 1.5
        try:
            live_verify_wait = float(pick("live_verify_wait", 2.0))
        except (TypeError, ValueError):
            live_verify_wait = 2.0
        if live_verify_wait <= 0:
            live_verify_wait = 2.0

        return cls(
            mode=one_of("mode", "conditional", ("none", "parallel", "conditional")),
            precise_mode=one_of("precise_mode", "parallel", ("none", "parallel", "conditional")),
            live_mode=one_of("live_mode", "conditional", ("none", "parallel", "conditional")),
            verify_profile=str(pick("verify_profile", "verify")).strip() or "verify",
            timeout_scale=timeout_scale,
            live_verify_wait=live_verify_wait,
        )

    def effective_mode(self, infer_mode: str) -> str:
        """返回精确到本次推理的执行模式：none | parallel | conditional。"""
        if self.mode == "none":
            return "none"
        if self.mode == "parallel":
            return "parallel"
        return self.precise_mode if infer_mode == "precise" else self.live_mode


def direction_index(direction: Optional[str]) -> int:
    """方向归一化为相邻顺序索引；未确定/未知返回 -1。"""
    if direction in DIRECTION_ORDER:
        return DIRECTION_ORDER.index(direction)
    return -1


def is_known_direction(direction: Optional[str]) -> bool:
    return direction in KNOWN_DIRECTIONS


def directions_conflict(a: Optional[str], b: Optional[str]) -> bool:
    """两个已确定方向是否明显矛盾（跨度为 2 及以上）。"""
    ia, ib = direction_index(a), direction_index(b)
    if ia < 0 or ib < 0:
        return False
    return abs(ia - ib) >= 2


def direction_from_text(text: str) -> str:
    value = str(text or "")
    for item in ("左前方", "右前方", "左后方", "右后方", "正前方", "左侧", "右侧", "后方"):
        if item in value:
            return item
    return UNKNOWN_DIRECTION


def is_uncertain_speech(text: str) -> bool:
    value = str(text or "")
    return any(marker in value for marker in UNCERTAIN_SPEECH_MARKERS)


def scrub_uncertain_speech(text: str) -> str:
    cleaned = str(text or "")
    for marker in UNCERTAIN_SPEECH_MARKERS:
        cleaned = cleaned.replace(marker, "")
    cleaned = cleaned.replace("请停下确认。", "").replace("请停下确认", "")
    return cleaned.strip(" ，,。.;；")


def _is_placeholder(value: str) -> bool:
    text = str(value or "").strip()
    return (not text) or text.startswith("未确定")


def stabilize_live_result(
    parsed: Optional[Dict[str, Any]],
    previous_direction: str = "",
    previous_speech: str = "",
) -> Optional[Dict[str, Any]]:
    """实时帧：补方向、去掉反复的“不确定方向”套话，沿用上一帧稳定结论。"""
    if not parsed:
        return parsed
    result = dict(parsed)
    direction = str(result.get("direction") or UNKNOWN_DIRECTION)
    speech = str(result.get("speech") or result.get("action") or "")
    risk = str(result.get("risk_level") or "")
    if not is_known_direction(direction):
        hinted = direction_from_text(speech)
        if is_known_direction(hinted):
            direction = hinted
        elif is_known_direction(previous_direction):
            direction = previous_direction
        result["direction"] = direction

    previous_ok = bool(previous_speech) and not is_uncertain_speech(previous_speech)
    cleaned = scrub_uncertain_speech(speech)
    current_uncertain = is_uncertain_speech(speech) or not cleaned

    # 实时巡视：套话或方向不清时，宁可复用上一句，也不每 2.5 秒念“不确定”。
    if risk != "high" and previous_ok and current_uncertain:
        result["speech"] = previous_speech
        result["action"] = previous_speech
        return result

    if current_uncertain:
        scene = str(result.get("scene") or "").strip()
        target = str(result.get("target") or "").strip()
        label = target if not _is_placeholder(target) else ""
        if is_known_direction(direction):
            if label and previous_direction == direction:
                speech = f"{label}仍在{direction}。"
            elif label:
                speech = f"{label}在{direction}。"
            else:
                speech = f"目标在{direction}。"
        elif cleaned:
            speech = f"{cleaned}。"
        elif scene and not _is_placeholder(scene):
            speech = f"看到{scene}。"
        elif previous_ok:
            speech = previous_speech
        else:
            speech = "继续观察周围。"
        result["speech"] = speech
        result["action"] = speech

    spoken = str(result.get("speech") or "")
    if is_uncertain_speech(spoken):
        if previous_ok:
            result["speech"] = previous_speech
            result["action"] = previous_speech
        elif is_known_direction(direction):
            result["speech"] = f"目标在{direction}。"
            result["action"] = result["speech"]
        else:
            result["speech"] = "继续观察周围。"
            result["action"] = result["speech"]
    return result


def _higher_risk(a: Optional[str], b: Optional[str]) -> str:
    return a if RISK_ORDER.get(a, 0) >= RISK_ORDER.get(b, 0) else b


def _union_obstacles(a: Any, b: Any) -> list:
    result = []
    for value in (a, b):
        items = value if isinstance(value, list) else ([value] if value else [])
        for item in items:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
    return result


def should_verify(primary: Optional[Dict[str, Any]]) -> bool:
    """live 条件切换触发条件：低置信 / 方向未确定 / 高风险 / 无法解析。"""
    if not primary:
        return True
    if primary.get("confidence") == "low":
        return True
    if primary.get("direction") == UNKNOWN_DIRECTION:
        return True
    if primary.get("risk_level") == "high":
        return True
    return False


def fuse_results(
    primary: Optional[Dict[str, Any]],
    verify: Optional[Dict[str, Any]],
) -> tuple:
    """字段级融合两个归一化结果。

    返回 (fused_dict|None, agreement, confidence_adjusted)。
    - verify 为 None（单侧超时/失败）→ degraded，置信度降为 low。
    - 方向一致且风险一致 → consistent，置信度上调。
    - 方向一致风险不同 / 方向相邻 → similar，模糊归一取主模型方向。
    - 方向明显矛盾 → conflict，保留主模型方向并降低置信度，不改口成“未确定”。
    """
    if primary is None and verify is None:
        return None, AGREEMENT_DEGRADED, "low"
    if verify is None:
        fused = dict(primary)
        fused["confidence"] = "low"
        return fused, AGREEMENT_DEGRADED, "low"
    if primary is None:
        fused = dict(verify)
        fused["confidence"] = "low"
        return fused, AGREEMENT_DEGRADED, "low"

    direction_1 = primary.get("direction")
    direction_2 = verify.get("direction")
    risk_1 = primary.get("risk_level")
    risk_2 = verify.get("risk_level")
    index_1, index_2 = direction_index(direction_1), direction_index(direction_2)
    risk = _higher_risk(risk_1, risk_2)

    if index_1 == index_2 >= 0 and risk_1 == risk_2:
        agreement = AGREEMENT_CONSISTENT
        fused_direction = direction_1
        confidence = CONFIDENCE_UP.get(primary.get("confidence") or "low", "low")
        confidence_adjusted = "up"
    elif index_1 == index_2 >= 0:
        agreement = AGREEMENT_SIMILAR
        fused_direction = direction_1
        confidence = primary.get("confidence") or "low"
        confidence_adjusted = "neutral"
    elif index_1 >= 0 and index_2 >= 0 and abs(index_1 - index_2) == 1:
        agreement = AGREEMENT_SIMILAR
        fused_direction = direction_1  # 模糊归一：保留主模型已归一的方向
        confidence = primary.get("confidence") or "low"
        confidence_adjusted = "neutral"
    elif direction_1 == UNKNOWN_DIRECTION and direction_2 == UNKNOWN_DIRECTION:
        agreement = AGREEMENT_SIMILAR
        fused_direction = UNKNOWN_DIRECTION
        confidence = "low"
        confidence_adjusted = "low"
    else:
        agreement = AGREEMENT_CONFLICT
        if is_known_direction(direction_1):
            fused_direction = direction_1
        elif is_known_direction(direction_2):
            fused_direction = direction_2
        else:
            fused_direction = UNKNOWN_DIRECTION
        confidence = "low"
        confidence_adjusted = "low"

    fused = dict(primary)
    fused["direction"] = fused_direction
    fused["risk_level"] = risk
    fused["obstacles"] = _union_obstacles(primary.get("obstacles"), verify.get("obstacles"))
    fused["confidence"] = confidence

    speech = str(fused.get("speech") or "")
    action = str(fused.get("action") or "")
    if is_uncertain_speech(speech) or not speech.strip():
        rewritten = stabilize_live_result(fused) or fused
        speech = str(rewritten.get("speech") or speech)
        action = speech
    if risk == "high" and not speech.startswith("注意"):
        speech = f"注意，当前画面可能存在较高风险。{speech}"
    fused["speech"] = speech
    fused["action"] = action or speech
    return fused, agreement, confidence_adjusted


def fusion_meta(
    actual_mode: str,
    models: list,
    agreement: Optional[str],
    confidence_adjusted: Optional[str],
) -> Dict[str, Any]:
    """构造 /infer 响应中的 fusion 字段。"""
    return {
        "mode": actual_mode,
        "models": [m for m in models if m],
        "agreement": agreement,
        "confidence_adjusted": confidence_adjusted,
    }

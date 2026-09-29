"""用户用意。有模型回复时以模型为准，模型没说出结果时才退回关键词。"""

import json
import re

from services.task_memory import detect_intent, intent_label, looks_like_product_request

MODEL_INTENTS = (
    ("ask_past", ("问以前",)),
    ("stop", ("先停下",)),
    ("guide_way", ("指路",)),
    ("find_product", ("找东西",)),
    ("read_price", ("看价格",)),
    ("avoid_obstacle", ("看障碍",)),
    ("find_entrance", ("找入口",)),
    ("find_cashier", ("找收银台",)),
)
TASK_INTENTS = {
    "find_product",
    "guide_way",
    "find_entrance",
    "find_cashier",
    "read_price",
    "avoid_obstacle",
}
SPECIAL_LABELS = {
    "ask_past": "问以前",
    "stop": "先停下",
    "": "闲聊",
}
CHAT_RE = re.compile(r"天气|你好|谢谢|再见|聊天|吃了吗|在干嘛|辛苦了")


def understand_speech(text: str, model_text: str = "") -> dict:
    spoken = str(text or "").replace("\n", " ").strip()
    if not spoken:
        return _pack("", "rule", "", "")
    mapped, target, summary, model_kind = _parse_model(model_text)
    if model_kind == "chat":
        return _pack(mapped if mapped in {"ask_past", "stop"} else "", "model", target, summary)
    if mapped:
        return _pack(mapped, "model", target, summary)
    ruled = detect_intent(spoken)
    if ruled in TASK_INTENTS:
        return _pack(ruled, "rule", "", "")
    if CHAT_RE.search(spoken):
        return _pack("", "rule", "", "")
    if not ruled and looks_like_product_request(spoken):
        ruled = "find_product"
    return _pack(ruled, "rule", "", "")


def _pack(intent: str, source: str, target: str, summary: str) -> dict:
    kind = "task" if intent in TASK_INTENTS else "chat"
    return {
        "intent": intent,
        "kind": kind,
        "label": SPECIAL_LABELS.get(intent) or intent_label(intent),
        "source": source,
        "target": target,
        "summary": summary,
    }


def _parse_model(model_text: str):
    raw = str(model_text or "").strip()
    if not raw:
        return "", "", "", ""
    data = {}
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            loaded = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            data = loaded
    intent_word = str(data.get("intent") or raw)
    target = str(data.get("target") or "").strip()[:24]
    summary = str(data.get("summary") or "").strip()[:40]
    kind_word = str(data.get("kind") or "")
    model_kind = "chat" if kind_word in {"chat", "闲聊"} else ""
    if not model_kind and "其他" in intent_word:
        model_kind = "chat"
    for intent, hints in MODEL_INTENTS:
        if any(hint in intent_word for hint in hints):
            return intent, target, summary, model_kind
    return "", target, summary, model_kind

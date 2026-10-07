import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Optional

from model_api import (
    ModelConfigurationError,
    ModelGateway,
    load_model_gateway,
)
from prompts import (
    ALLOWED_CONFIDENCE_LEVELS,
    ALLOWED_DIRECTIONS,
    ALLOWED_RISK_LEVELS,
    build_accessibility_prompt,
)
from services.fusion import direction_from_text, is_uncertain_speech
from services.task_memory import detect_intent, speech_goal, _product_target
from services.utterance import needs_live_info

ALLOWED_DISTANCE_BANDS = {"一臂内", "较近", "较远", "无法判断"}
DISTANCE_BAND_HINTS = {
    "一臂内": "看起来已接近。",
    "较近": "看起来较近。",
    "较远": "看起来较远。",
}

ALLOWED_INTENTS = {
    "find_entrance",
    "find_product",
    "read_price",
    "find_cashier",
    "avoid_obstacle",
    "general_help",
}
ALLOWED_PROXIMITY = {"较近", "较远", "已接近", "无法判断"}
PRECISE_DISTANCE_PATTERN = re.compile(
    r"(?:约|大约|距离|还有|向前|向左|向右)?\s*[零一二两三四五六七八九十百\d.]+\s*(?:米|厘米|cm|步|度)",
    re.IGNORECASE,
)


@dataclass
class InferenceResult:
    answer: str
    latency_ms: int
    parsed: Optional[Dict[str, Any]] = None
    raw_answer: str = ""
    status: str = "ok"
    error: str = ""
    load_time_ms: int = 0
    profile: str = ""
    model: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)


class MiniCPMOAccessibilityRunner:
    """Accessibility business runner backed exclusively by remote model APIs.

    The historical class name is kept so existing imports continue to work.
    It no longer imports or loads MiniCPM, torch, transformers, CUDA, or NPU.
    """

    def __init__(
        self,
        model_path: str = "",
        device: str = "remote-api",
        dtype: str = "",
        init_audio: bool = False,
        init_tts: bool = False,
        mock: bool = False,
        backend: str = "api",
        api_url: str = "",
        api_key: str = "",
        api_key_env: str = "MODEL_API_KEY",
        model_name: str = "",
        config_path: str = "",
        profile: str = "",
        timeout: Optional[int] = None,
    ) -> None:
        # model_path/dtype/init_* are accepted only for source compatibility.
        del model_path, dtype, init_audio, init_tts
        if backend not in {"api", "openai_compatible", "mock"}:
            raise ModelConfigurationError(
                "当前独立版只支持远程 API。请使用 --backend api，"
                "或把本地模型部署为 OpenAI 兼容接口后再连接。"
            )
        self.device = "remote-api"
        self.mock = mock or backend == "mock"
        self.backend = "mock" if self.mock else "api"
        self.config_path = config_path
        self.requested_profile = profile
        self.api_url_override = api_url
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.model_name_override = model_name
        self.timeout = timeout
        self.gateway: Optional[ModelGateway] = None
        self.load_time_ms = 0
        self.last_error = ""
        self.last_profile = ""
        self.last_model = ""

    def load(self) -> None:
        if self.mock or self.gateway is not None:
            return
        started = time.perf_counter()
        try:
            self.gateway = load_model_gateway(
                config_path=self.config_path,
                default_profile=self.requested_profile,
                api_url=self.api_url_override,
                api_key=self.api_key,
                api_key_env=self.api_key_env,
                model=self.model_name_override,
                timeout=self.timeout,
            )
            self.load_time_ms = int((time.perf_counter() - started) * 1000)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.gateway = None
            raise RuntimeError(f"模型 API 配置加载失败：{self.last_error}") from exc

    @property
    def default_profile(self) -> str:
        if self.mock:
            return "mock"
        if self.gateway is None:
            self.load()
        return self.gateway.default_profile if self.gateway else ""

    def available_profiles(self) -> Dict[str, Dict[str, Any]]:
        if self.mock:
            return {"mock": {"name": "mock", "label": "Mock 演示", "model": "mock"}}
        if self.gateway is None:
            self.load()
        return self.gateway.public_profiles() if self.gateway else {}

    def classify_utterance(self, text: str) -> str:
        spoken = str(text or "").strip()
        if not spoken or self.mock:
            return ""
        if self.gateway is None:
            try:
                self.load()
            except Exception:
                return ""
        if self.gateway is None:
            return ""
        prompt = (
            "根据用户这句话判断用意。只输出一行 JSON，不要解释。\n"
            '{"kind":"task|chat","intent":"找东西|指路|看价格|看障碍|问以前|先停下|其他",'
            '"target":"短目标或空","summary":"一句口语"}\n'
            "task 只用于找东西、指路、看价格、看障碍。问好、闲聊、天气、新闻、今天发生的事是 chat。\n"
            "指路是问往哪边走、出入口或收银台在哪一侧，不是过马路。\n"
            f"用户说：{spoken[:80]}"
        )
        try:
            reply = self.gateway.complete_text(prompt, timeout=6)
        except Exception:
            return ""
        return str(reply.text or "").strip()

    def chat_reply(self, text: str) -> str:
        spoken = str(text or "").strip()
        if not spoken or self.mock:
            return "我在听。你要找东西，或者问往哪边走，直接说就行。"
        if self.gateway is None:
            try:
                self.load()
            except Exception:
                return "我在听。你要找东西，或者问往哪边走，直接说就行。"
        live = needs_live_info(spoken)
        prompt = (
            "你是视障使用者身边的聊天助手。你不看画面，不找东西，不指路。\n"
            f"今天是 {date.today().isoformat()}。\n"
            "天气、新闻、今天发生的事，只根据联网结果回答。\n"
            "最多两句短话，适合朗读。用户没说城市时只说一个地方，不要把两个城市连在一起。\n"
            "不要念网址，不要说正在找，不要说米、厘米或步数，不要建议过马路。\n"
            "查不到就说没查到，不要编。\n"
            f"用户说：{spoken[:80]}"
        )
        try:
            reply = self.gateway.complete_chat(prompt, web_search=live, timeout=20)
        except Exception:
            if live:
                return "我没查到最新的消息。你要找东西或问路，直接说就行。"
            return "我在听。你要找东西，或者问往哪边走，直接说就行。"
        cleaned = _speakable_chat(str(reply.text or ""))
        if cleaned:
            return cleaned
        if live:
            return "我没查到最新的消息。你要找东西或问路，直接说就行。"
        return "我在听。你要找东西，或者问往哪边走，直接说就行。"

    def infer_image(
        self,
        image_path: str,
        question: str,
        json_output: bool = True,
        model_profile: str = "",
        model_override: str = "",
        mode: str = "precise",
        distance_band: str = "",
        extra_prompt: str = "",
    ) -> InferenceResult:
        if self.mock:
            parsed = apply_distance_band(
                humanize_search_speech(
                    normalize_result(_mock_vision_result(question, extra_prompt)),
                    intent=detect_intent(question),
                    target=_product_target(question) if detect_intent(question) == "find_product" else "",
                    main_task=question,
                ),
                distance_band,
            )
            answer = json.dumps(parsed, ensure_ascii=False)
            return InferenceResult(
                answer=answer,
                raw_answer=answer,
                parsed=parsed,
                latency_ms=0,
                profile="mock",
                model="mock",
            )

        if self.gateway is None:
            self.load()

        started = time.perf_counter()
        try:
            prompt = build_accessibility_prompt(
                question,
                json_output=json_output,
                mode=mode,
                extra_prompt=extra_prompt,
            )
            remote = self.gateway.complete(
                image_path=image_path,
                prompt=prompt,
                profile_name=model_profile,
                model_override=model_override,
            )
            self.last_profile = remote.profile
            self.last_model = remote.model
            raw_answer = remote.text
            parsed_raw = try_parse_json(raw_answer)
            parsed = normalize_result(parsed_raw) if parsed_raw else None
            if parsed:
                parsed = apply_distance_band(parsed, distance_band)
            answer = json.dumps(parsed, ensure_ascii=False) if parsed else raw_answer
            return InferenceResult(
                answer=answer,
                raw_answer=raw_answer,
                parsed=parsed,
                latency_ms=int((time.perf_counter() - started) * 1000),
                status="ok" if parsed else "unparsed",
                load_time_ms=self.load_time_ms,
                profile=remote.profile,
                model=remote.model,
                usage=remote.usage,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.last_error = error
            return InferenceResult(
                answer="远程模型调用失败，请检查 API 地址、密钥、模型名称或网络连接。",
                latency_ms=int((time.perf_counter() - started) * 1000),
                status="error",
                error=error,
                load_time_ms=self.load_time_ms,
                profile=model_profile or self.requested_profile,
                model=model_override,
            )

    def status(self) -> Dict[str, Any]:
        profiles: Dict[str, Dict[str, Any]] = {}
        default_profile = "mock"
        loaded = self.mock
        if not self.mock:
            try:
                profiles = self.available_profiles()
                default_profile = self.default_profile
                loaded = self.gateway is not None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
        return {
            "backend": self.backend,
            "device": self.device,
            "loaded": loaded,
            "mock": self.mock,
            "default_profile": default_profile,
            "profiles": profiles,
            "last_profile": self.last_profile,
            "last_model": self.last_model,
            "load_time_ms": self.load_time_ms,
            "last_error": self.last_error,
            "local_model_required": False,
        }


def try_parse_json(text: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def normalize_result(value: Dict[str, Any]) -> Dict[str, Any]:
    intent = str(value.get("intent") or "general_help")
    if intent not in ALLOWED_INTENTS:
        intent = "general_help"

    direction = str(value.get("direction") or "未确定")
    if direction not in ALLOWED_DIRECTIONS:
        direction = "未确定"
    if direction == "未确定":
        hinted = direction_from_text(str(value.get("speech") or value.get("action") or ""))
        if hinted != "未确定":
            direction = hinted

    risk_level = str(value.get("risk_level") or "medium").lower()
    if risk_level not in ALLOWED_RISK_LEVELS:
        risk_level = "medium"

    confidence = str(value.get("confidence") or "low").lower()
    if confidence not in ALLOWED_CONFIDENCE_LEVELS:
        confidence = "low"

    proximity = str(value.get("proximity") or "无法判断")
    if proximity not in ALLOWED_PROXIMITY:
        proximity = "无法判断"

    obstacles = value.get("obstacles") or []
    if not isinstance(obstacles, list):
        obstacles = [obstacles]
    obstacles = [str(item).strip() for item in obstacles if str(item).strip()]

    action = sanitize_guidance(str(value.get("action") or value.get("action_hint") or ""))
    speech = sanitize_guidance(str(value.get("speech") or value.get("spoken_response") or action))
    if is_uncertain_speech(speech) or not speech or direction == "未确定" and (
        "未能确认" in speech or "不确定方向" in speech or "重新拍摄" in speech
    ):
        scene = str(value.get("scene") or "").strip()
        if direction != "未确定":
            if scene and not scene.startswith("未确定"):
                speech = f"{direction}是{scene}。"
            else:
                speech = f"看到的位置在{direction}。"
        elif scene and not scene.startswith("未确定"):
            speech = f"看到{scene}。"
        else:
            speech = "继续观察周围。"
        action = speech

    if risk_level == "high":
        warning = "注意，当前画面可能存在较高风险。"
        if not speech.startswith("注意"):
            speech = f"{warning}{speech}"
        if not action.startswith("注意"):
            action = f"{warning}{action}"

    anchor = _choice(value.get("anchor"), ALLOWED_ANCHORS)
    level = _choice(value.get("level"), ALLOWED_LEVELS)
    slot = _choice(value.get("slot"), ALLOWED_SLOTS)
    if anchor in {"桌子", "台面", "平面"} and level in {"手高这一层", "再高一层", "再矮一层", "最上面", "最下面"}:
        level = "靠近你这一侧"
    place_index = _place_index(value.get("place_index"))
    if anchor not in {"货架", "柜子"}:
        place_index = 0

    return {
        "intent": intent,
        "scene": str(value.get("scene") or "未确定场景").strip(),
        "target": str(value.get("target") or "未确定目标").strip(),
        "direction": direction,
        "proximity": proximity,
        "text_reading": str(value.get("text_reading") or "").strip(),
        "obstacles": obstacles,
        "risk_level": risk_level,
        "confidence": confidence,
        "action": action or speech or "继续观察周围。",
        "speech": speech or "继续观察周围。",
        "task_decision": str(value.get("task_decision") or "").strip(),
        "main_task": str(value.get("main_task") or "").strip(),
        "anchor": anchor,
        "level": level,
        "slot": slot,
        "place_index": place_index,
    }


ALLOWED_ANCHORS = {"货架", "柜子", "桌子", "台面", "平面"}
ALLOWED_LEVELS = {"手高这一层", "再高一层", "再矮一层", "最上面", "最下面", "靠近你这一侧", "靠里"}
ALLOWED_SLOTS = {"最左", "靠左", "中间", "靠右", "最右"}


def _choice(value: Any, allowed: set) -> str:
    text = str(value or "").strip()
    return text if text in allowed else ""


def _place_index(value: Any) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    if 1 <= number <= 6:
        return number
    return 0


def compose_find_speech(
    goal: str,
    *,
    direction: str = "",
    anchor: str = "",
    level: str = "",
    slot: str = "",
    place_index: int = 0,
    risk_level: str = "",
    seen: bool = True,
) -> str:
    """用身体和能摸到的参照说位置。空白平面不数第几个。"""
    prefix = "注意。" if risk_level == "high" else ""
    face = ""
    if direction and direction != "未确定":
        face = f"在你{direction}"
    anchor_name = anchor if anchor in ALLOWED_ANCHORS else ""
    if anchor_name in {"桌子", "台面", "平面"}:
        if level in {"手高这一层", "再高一层", "再矮一层", "最上面", "最下面"}:
            level = "靠近你这一侧"
        place_index = 0
    level_name = level if level in ALLOWED_LEVELS else ""
    on_shelf = anchor_name in {"货架", "柜子"} and 1 <= int(place_index or 0) <= 6
    if on_shelf:
        across = f"从你左手边数第{int(place_index)}个"
    else:
        across = slot if slot in ALLOWED_SLOTS else ""
    holder = f"{face}的{anchor_name}" if face and anchor_name else (face or anchor_name)
    parts = [part for part in (holder, level_name, across) if part]
    place = "，".join(parts)
    name = goal or "它"
    if not seen:
        if place:
            return f"{prefix}还在帮你找{name}。先找到{place}。"
        return f"{prefix}还在帮你找{name}。"
    if place:
        return f"{prefix}正在帮你找{name}。{place}。"
    return f"{prefix}正在帮你找{name}。"


SEARCH_INTENTS = {"find_product", "find_entrance", "find_cashier"}
HELPING_RE = re.compile(r"正在帮你找|帮你找")


def _mock_vision_result(question: str, extra_prompt: str = "") -> Dict[str, Any]:
    intent = detect_intent(question) or detect_intent(extra_prompt) or "general_help"
    goal = speech_goal(
        _product_target(question) if intent == "find_product" else "",
        question,
        intent,
    )
    if "当前要找的东西" in (extra_prompt or ""):
        match = re.search(r"当前要找的东西：([^\n]+)", extra_prompt)
        if match and match.group(1).strip() not in {"无", ""}:
            goal = match.group(1).strip()[:12]
            intent = intent if intent in SEARCH_INTENTS else "find_product"
    if intent == "guide_way":
        return {
            "intent": "guide_way",
            "scene": "通道",
            "target": "往哪边走",
            "direction": "右前方",
            "proximity": "较近",
            "text_reading": "",
            "obstacles": ["展示架"],
            "risk_level": "medium",
            "confidence": "medium",
            "action": "往右前方走，先注意面前的展示架。",
            "speech": "往右前方走，先注意面前的展示架。",
        }
    if intent == "find_product" and goal:
        return {
            "intent": "find_product",
            "scene": "货架区域",
            "target": goal,
            "direction": "正前方",
            "proximity": "较近",
            "text_reading": "",
            "obstacles": ["货架"],
            "risk_level": "medium",
            "confidence": "medium",
            "anchor": "货架",
            "level": "手高这一层",
            "slot": "中间",
            "place_index": 0,
            "action": f"正在帮你找{goal}。在你正前方的货架，手高这一层，中间。",
            "speech": f"正在帮你找{goal}。在你正前方的货架，手高这一层，中间。",
        }
    return {
        "intent": "find_entrance",
        "scene": "便利店入口区域",
        "target": "入口",
        "direction": "右前方",
        "proximity": "无法判断",
        "text_reading": "",
        "obstacles": ["展示架"],
        "risk_level": "medium",
        "confidence": "medium",
        "action": "入口位于右前方。前方有展示架，请停下确认通道后再缓慢靠近。",
        "speech": "注意前方有展示架。入口在右前方，请先停下确认。",
    }


def humanize_search_speech(
    parsed: Dict[str, Any],
    *,
    intent: str = "",
    target: str = "",
    main_task: str = "",
) -> Dict[str, Any]:
    result = dict(parsed or {})
    used_intent = str(intent or result.get("intent") or "")
    goal = speech_goal(
        target or str(result.get("target") or ""),
        main_task or str(result.get("main_task") or ""),
        used_intent,
    )
    speech = str(result.get("speech") or "").strip()
    if used_intent == "guide_way":
        if not re.search(r"往|走", speech):
            speech = f"正在帮你看往哪边走。{speech}".strip()
        result["speech"] = speech
        result["action"] = speech
        result["intent"] = used_intent
        return result
    should_help = bool(goal) and (
        used_intent in SEARCH_INTENTS or "找" in str(main_task or result.get("main_task") or "")
    )
    if should_help and not HELPING_RE.search(speech):
        helping = f"正在帮你找{goal}"
        body = re.sub(r"^目标在", "", speech).strip()
        if body.startswith("注意"):
            warning = "注意，当前画面可能存在较高风险。"
            rest = body[len(warning):].strip() if body.startswith(warning) else re.sub(r"^注意[，,]?", "", body).strip()
            rest = re.sub(r"^目标在", "", rest).strip(" ，,。")
            speech = f"{warning}{helping}，{rest}" if rest else f"{warning}{helping}。"
        elif body != speech:
            speech = f"{helping}，在{body}" if body and not body.startswith("在") else f"{helping}，{body or '继续看眼前画面。'}"
        else:
            speech = f"{helping}，{body}" if body else f"{helping}。"
    if goal:
        speech = speech.replace("目标在", f"{goal}在")
    if should_help:
        direction = str(result.get("direction") or "未确定")
        seen = direction != "未确定" or bool(result.get("anchor"))
        speech = compose_find_speech(
            goal,
            direction=direction,
            anchor=str(result.get("anchor") or ""),
            level=str(result.get("level") or ""),
            slot=str(result.get("slot") or ""),
            place_index=int(result.get("place_index") or 0),
            risk_level=str(result.get("risk_level") or ""),
            seen=seen,
        )
    result["speech"] = speech or result.get("speech") or "继续观察周围。"
    if should_help:
        result["action"] = result["speech"]
    if should_help and result.get("target") in {"", "未确定目标"}:
        result["target"] = goal
    if main_task and not result.get("main_task"):
        result["main_task"] = main_task
    return result


def apply_distance_band(parsed: Dict[str, Any], distance_band: str) -> Dict[str, Any]:
    band = str(distance_band or "").strip()
    if band not in ALLOWED_DISTANCE_BANDS:
        band = "无法判断"
    result = dict(parsed)
    result["distance_band"] = band
    hint = DISTANCE_BAND_HINTS.get(band, "")
    speech = str(result.get("speech") or "")
    if hint and not any(token in speech for token in ("较近", "较远", "已接近", "一臂")):
        result["speech"] = sanitize_guidance(f"{speech}{hint}")
    return result


def _speakable_chat(text: str) -> str:
    cleaned = re.sub(r"https?://\S+", "", str(text or ""))
    cleaned = re.sub(r"正在帮你找[^。]*。?", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned).strip()
    sentences = [part for part in re.split(r"(?<=[。！？])", cleaned) if part.strip()]
    cleaned = "".join(sentences[:2]) if sentences else cleaned
    return cleaned[:72]


def sanitize_guidance(text: str) -> str:
    cleaned = PRECISE_DISTANCE_PATTERN.sub("无法准确判断的距离", text.strip())
    replacements = {
        "可以直接前进": "请保持谨慎并使用辅助工具确认后再移动",
        "可以继续前进": "未发现明显障碍，但请使用辅助工具确认后再移动",
        "伸出右手": "接近后请重新拍摄确认目标位置",
        "伸出左手": "接近后请重新拍摄确认目标位置",
    }
    for unsafe, safe in replacements.items():
        cleaned = cleaned.replace(unsafe, safe)
    return cleaned
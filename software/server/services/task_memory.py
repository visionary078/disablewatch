"""主任务记忆：按会话保存当前目标，并用新语音决定 keep / refine / switch。"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

INTENT_PATTERNS = (
    ("find_cashier", re.compile(r"收银|付款|结账|服务台|买单")),
    ("find_entrance", re.compile(r"入口|大门|正门|出门|门口")),
    (
        "guide_way",
        re.compile(r"指路|怎么走|往哪|带我去|从哪走|怎么去|走哪|去哪|找路|出口|往前走|怎么过去"),
    ),
    ("read_price", re.compile(r"价格|多少钱|价签|标签|售价")),
    ("avoid_obstacle", re.compile(r"障碍|台阶|安全|能不能走|路面|有没有坑")),
    ("find_product", re.compile(r"找|买|拿|要一份|帮我找|商品|有没有|我要")),
)
INTENT_LABELS = {
    "find_product": "找东西",
    "find_entrance": "指路",
    "find_cashier": "指路",
    "guide_way": "指路",
    "read_price": "看价格",
    "avoid_obstacle": "看障碍",
    "general_help": "看眼前",
}
FILLER_RE = re.compile(r"^(嗯+|啊+|呃+|哦+|你好|在吗|喂)$")
GOAL_STRIP_RE = re.compile(
    r"请|帮我|一下|先提醒风险|再给出方向|看不清不要猜测|找|买|拿|要一份|商品|我要"
)

REFINE_RE = re.compile(
    r"左边|右边|左前|右前|正前|再近|近一点|远一点|不是这个|换一个|无糖|"
    r"那个|继续|还是|刚才|上面|下面|第.排|中间|这边|那边"
)
FOLLOWUP_RE = re.compile(r"在哪|方向|看到了吗|到了吗|这个是不是|还在吗|有没有")
SWITCH_HINT_RE = re.compile(r"改成|换成|不要.*了|先去|现在去|不找了|另外|别的")

TTL_SECONDS = 30 * 60
MAX_UTTERANCES = 8


@dataclass
class TaskMemory:
    session_id: str
    intent: str = "general_help"
    target: str = ""
    main_task: str = ""
    question: str = ""
    utterances: List[str] = field(default_factory=list)
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "intent": self.intent,
            "target": self.target,
            "main_task": self.main_task,
            "question": self.question,
            "utterances": list(self.utterances),
            "updated_at": self.updated_at,
        }


@dataclass
class TaskRevision:
    decision: str  # new | keep | refine | switch
    intent: str
    target: str
    main_task: str
    question: str
    spoken_text: str
    reason: str = ""


class TaskMemoryStore:
    def __init__(self, db_path: str = "") -> None:
        self._lock = threading.RLock()
        self._memory: Dict[str, TaskMemory] = {}
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        if db_path:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS task_memory ("
                "session_id TEXT PRIMARY KEY,"
                "payload_json TEXT,"
                "updated_at REAL)"
            )
            self._conn.commit()

    def get(self, session_id: str) -> Optional[TaskMemory]:
        sid = (session_id or "").strip()
        if not sid:
            return None
        now = time.time()
        with self._lock:
            self._cleanup_locked(now)
            item = self._memory.get(sid)
            if item is None and self._conn is not None:
                item = self._load_locked(sid)
                if item:
                    self._memory[sid] = item
            return item

    def save(self, memory: TaskMemory) -> None:
        now = time.time()
        memory.updated_at = now
        memory.utterances = list(memory.utterances or [])[-MAX_UTTERANCES:]
        with self._lock:
            self._memory[memory.session_id] = memory
            if self._conn is not None:
                self._conn.execute(
                    "INSERT OR REPLACE INTO task_memory "
                    "(session_id, payload_json, updated_at) VALUES (?, ?, ?)",
                    (memory.session_id, json.dumps(memory.to_dict(), ensure_ascii=False), now),
                )
                self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _cleanup_locked(self, now: float) -> None:
        expired = [
            sid for sid, item in self._memory.items() if now - (item.updated_at or 0) > TTL_SECONDS
        ]
        for sid in expired:
            self._memory.pop(sid, None)
            if self._conn is not None:
                self._conn.execute("DELETE FROM task_memory WHERE session_id = ?", (sid,))
        if expired and self._conn is not None:
            self._conn.commit()

    def _load_locked(self, session_id: str) -> Optional[TaskMemory]:
        row = self._conn.execute(
            "SELECT payload_json FROM task_memory WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return TaskMemory(
            session_id=session_id,
            intent=str(data.get("intent") or "general_help"),
            target=str(data.get("target") or ""),
            main_task=str(data.get("main_task") or ""),
            question=str(data.get("question") or ""),
            utterances=list(data.get("utterances") or []),
            updated_at=float(data.get("updated_at") or 0),
        )


def detect_intent(text: str) -> str:
    value = str(text or "")
    for intent, pattern in INTENT_PATTERNS:
        if pattern.search(value):
            return intent
    return ""


def intent_label(intent: str) -> str:
    return INTENT_LABELS.get(str(intent or ""), "看眼前")


def _product_target(text: str) -> str:
    cleaned = GOAL_STRIP_RE.sub("", text)
    return cleaned.strip(" ：:，,。")[:24]


def speech_goal(target: str = "", main_task: str = "", intent: str = "") -> str:
    raw = str(target or "").strip()
    if raw and "未确定" not in raw:
        return raw[:12]
    if intent == "find_entrance":
        return "入口"
    if intent == "find_cashier":
        return "收银台"
    if intent == "guide_way":
        return "往哪边走"
    cleaned = GOAL_STRIP_RE.sub("", str(main_task or ""))
    cleaned = re.sub(r"[：:，,。；;]+", "", cleaned).strip()
    return cleaned[:12]


def looks_like_product_request(text: str) -> bool:
    value = str(text or "").strip()
    if not value or len(value) > 20 or FILLER_RE.match(value):
        return False
    if detect_intent(value):
        return False
    if re.search(r"安全|障碍|方向|怎么走|能不能走|重新|停止", value):
        return False
    return True


def build_question(intent: str, spoken_text: str, previous_question: str = "", target: str = "") -> str:
    text = spoken_text.strip()
    if intent == "find_entrance":
        return (
            "请在当前照片里寻找入口或大门。"
            "speech 要口语化，例如「正在帮你找入口，右前方有玻璃门」。先提醒风险。"
        )
    if intent == "find_cashier":
        return (
            "请在当前照片里寻找收银台或服务台。"
            "speech 要口语化，例如「正在帮你找收银台，左前方柜台较近」。先提醒通道障碍。"
        )
    if intent == "read_price":
        return text if ("价格" in text or "多少钱" in text) else "请读取看得见的商品名称和价格，看不清的文字不要猜测。"
    if intent == "avoid_obstacle":
        return text if len(text) > 4 else "请判断当前是否安全，并告诉我目标在哪个方向。"
    if intent == "guide_way":
        return (
            "用户要的是指路：根据这一帧说往哪一边走，不要把它说成在找一件商品。"
            "speech 用口语，例如「往右前方走，先注意面前的架子」。"
            "不要说米或步数，不要建议过马路。"
        )
    if intent == "find_product":
        target = str(target or "").strip() or _product_target(text) or text
        return (
            f"请在当前这张照片里寻找「{target}」。这是具体寻找任务，不是泛泛看路。"
            f"speech 必须像对人说话，例如「正在帮你找{target}，正前方货架中部较近」。"
            "先提醒危险，再给方位；没看到就说还在帮你找、眼前看到什么，不要编造已经拿到。"
        )
    if previous_question:
        return f"{previous_question} 用户补充：{text}"
    return text or "请判断当前是否安全，并告诉我目标在哪个方向。"


def revise_main_task(
    previous: Optional[TaskMemory],
    spoken_text: str,
    intent_override: str = "",
    target_override: str = "",
) -> TaskRevision:
    text = str(spoken_text or "").replace("\n", " ").strip()
    detected = str(intent_override or "").strip() or detect_intent(text)
    if not text:
        if previous and previous.main_task:
            return TaskRevision(
                decision="keep",
                intent=previous.intent,
                target=previous.target,
                main_task=previous.main_task,
                question=previous.question,
                spoken_text="",
                reason="没有新的语音，沿用上次主任务",
            )
        return TaskRevision(
            decision="new",
            intent="general_help",
            target="",
            main_task="",
            question="请判断当前是否安全，并告诉我目标在哪个方向。",
            spoken_text="",
            reason="没有主任务也没有新语音",
        )

    if not detected and looks_like_product_request(text):
        detected = "find_product"
    named = str(target_override or "").strip()[:24]
    if detected == "find_product":
        target = named or _product_target(text)
    elif detected == "guide_way":
        target = named or "往哪边走"
    else:
        target = named or text[:24]
    if not previous or not previous.main_task:
        intent = detected or "general_help"
        question = build_question(intent, text, target=target)
        return TaskRevision(
            decision="new",
            intent=intent,
            target=target,
            main_task=text,
            question=question,
            spoken_text=text,
            reason="尚无主任务，用本次语音建立",
        )

    prev_intent = previous.intent or "general_help"
    same_intent = bool(detected) and detected == prev_intent
    different_intent = bool(detected) and detected != prev_intent
    refine_like = bool(REFINE_RE.search(text) or FOLLOWUP_RE.search(text))
    switch_like = bool(SWITCH_HINT_RE.search(text)) or different_intent

    if switch_like and not (same_intent and refine_like):
        intent = detected or prev_intent
        question = build_question(intent, text, target=target)
        return TaskRevision(
            decision="switch",
            intent=intent,
            target=target,
            main_task=text,
            question=question,
            spoken_text=text,
            reason="新语音改变了目的，切换主任务",
        )

    if refine_like or same_intent:
        intent = detected or prev_intent
        merged_target = target or previous.target
        main_task = previous.main_task
        if target and target not in main_task:
            main_task = f"{previous.main_task}；补充：{text}"
        question = (
            f"{previous.question} 用户补充：{text}"
            if previous.question
            else build_question(intent, text, target=target)
        )
        return TaskRevision(
            decision="refine",
            intent=intent,
            target=merged_target,
            main_task=main_task[:80],
            question=question[:240],
            spoken_text=text,
            reason="新语音是对原主任务的补充或追问",
        )

    return TaskRevision(
        decision="keep",
        intent=prev_intent,
        target=previous.target,
        main_task=previous.main_task,
        question=f"{previous.question} 用户说：{text}"[:240],
        spoken_text=text,
        reason="新语音未改变原主任务，继续回答该目的",
    )


def apply_revision(previous: Optional[TaskMemory], revision: TaskRevision, session_id: str) -> TaskMemory:
    utterances = list((previous.utterances if previous else []) or [])
    if revision.spoken_text:
        utterances.append(revision.spoken_text)
    return TaskMemory(
        session_id=session_id,
        intent=revision.intent,
        target=revision.target,
        main_task=revision.main_task,
        question=revision.question,
        utterances=utterances[-MAX_UTTERANCES:],
    )


def format_task_context(memory: Optional[TaskMemory], revision: TaskRevision) -> str:
    history = ""
    if memory and memory.utterances:
        recent = " / ".join(memory.utterances[-4:])
        history = f"最近语音：{recent}\n"
    previous_task = memory.main_task if memory and memory.main_task else "无"
    goal = speech_goal(revision.target, revision.main_task, revision.intent)
    return (
        "[主任务记忆]\n"
        f"上次主任务：{previous_task}\n"
        f"{history}"
        f"本次语音：{revision.spoken_text or '无'}\n"
        f"判定：{revision.decision}（{revision.reason}）\n"
        f"当前主任务：{revision.main_task or '无'}\n"
        f"{'当前要去的方向' if revision.intent == 'guide_way' else '当前要找的东西'}：{goal or '无'}\n"
        f"听成：{intent_label(revision.intent)}\n"
        "请立刻在当前画面中执行该寻找，不要只确认已经记下。"
        "speech 用「正在帮你找」的口吻，像身边的人在帮忙，不要像填表。"
        "若判定为 keep 或 refine，不要丢掉原目标；若为 switch 或 new，改答新目的。"
        "不确定时降低 confidence，不要编造。"
    )

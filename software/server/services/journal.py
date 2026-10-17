"""手机上的 24 小时记要。

正文留在手机里，满 24 小时由手机删掉。服务端只按手机传来的原话检索，不调用记忆张量。
"""

import re
import time
from typing import Any, List, Optional

DAY_SECONDS = 24 * 60 * 60
DIGEST_RE = re.compile(r"记要|总结|今天发生|有什么事|这24小时|记下了什么|发生了什么")
SAVE_RE = re.compile(r"^(?:请)?(?:帮我)?(?:记住|记下|记一下|记下来)")
QUESTION_RE = re.compile(r"[?？]|吗|呢|哪|什么|谁|多少|怎么|为何|为什么|有没有")


def looks_like_journal_question(text: str) -> bool:
    """问记要里的事就去检索。说「记住」开头的，按原话保存。"""
    spoken = str(text or "").strip()
    if not spoken or SAVE_RE.match(spoken):
        return False
    return bool(QUESTION_RE.search(spoken) or DIGEST_RE.search(spoken))


def fresh_notes(notes: Any, now: Optional[float] = None) -> List[dict]:
    """丢掉超过 24 小时的条目。时间用秒；手机如果传来毫秒，这里换算。"""
    clock = time.time() if now is None else float(now)
    kept: List[dict] = []
    for note in notes or []:
        if not isinstance(note, dict):
            continue
        text = str(note.get("text") or "").strip()[:200]
        if not text:
            continue
        try:
            stamp = float(note.get("at") or 0)
        except (TypeError, ValueError):
            stamp = 0
        if stamp > 10_000_000_000:
            stamp = stamp / 1000.0
        if stamp and clock - stamp > DAY_SECONDS:
            continue
        role = "assistant" if str(note.get("role") or "") == "assistant" else "user"
        kept.append({"role": role, "text": text, "at": stamp or clock})
    kept.sort(key=lambda item: item["at"])
    return kept


def _overlap(question: str, text: str) -> int:
    query = re.sub(r"\s+", "", question)
    body = re.sub(r"\s+", "", text)
    if len(query) < 2 or not body:
        return 0
    score = 0
    for size in (2, 3):
        if len(query) < size:
            continue
        for index in range(len(query) - size + 1):
            if query[index : index + size] in body:
                score += 1
    return score


def select_for_question(question: str, notes: Any, limit: int = 8, now: Optional[float] = None) -> List[dict]:
    """问整天就取最近几条；问具体的事就按原话重合度召回。对不上就不要拿别的凑。"""
    fresh = fresh_notes(notes, now=now)
    if not fresh:
        return []
    spoken = str(question or "").strip()
    if not spoken or DIGEST_RE.search(spoken):
        return fresh[-limit:]
    ranked = sorted(
        ((_overlap(spoken, item["text"]), item) for item in fresh),
        key=lambda pair: (pair[0], pair[1]["at"]),
        reverse=True,
    )
    return [item for score, item in ranked if score > 0][:limit]


def texts_for_question(question: str, notes: Any) -> List[str]:
    return [item["text"] for item in select_for_question(question, notes)]

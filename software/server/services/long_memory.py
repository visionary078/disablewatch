"""跨会话长期记忆。无 MEMOS_API_KEY 时不检索、不写云端。"""

import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, List, Optional

SEARCH_TIMEOUT_S = 0.3
CACHE_TTL_S = 10 * 60
DEFAULT_BASE_URL = "https://memos.memtensor.cn/api/openmem/v1"

FILLER_RE = re.compile(r"^(?:嗯+|啊+|哦+|呃+|喂|在吗|看到了吗)$")
FOLLOWUP_RE = re.compile(r"^(?:看到了吗|到了吗|还在吗|这个是不是|有没有)$")
NEGATION_RE = re.compile(r"不是这个|不要了|不要这个|不找了|别找|别要")
HABIT_RE = re.compile(r"根据你的习惯|根据您的习惯|根据您上周|根据上次的记忆|根据你上次")
PREFERENCE_RE = re.compile(r"我喜欢|我习惯|以后叫|别名")
REMEMBER_RE = re.compile(r"记住|帮我记|别忘了|记一下|记下来")
RECALL_RE = re.compile(r"上次|以前|之前找|刚才找|还记得|记得我|我常找|找过什么|找的是什么|我找过|过去找")
NOTE_AGENT = "see-next-note"
TASK_AGENT = "see-next-task"
NOTE_TTL_SECONDS = 24 * 60 * 60


def is_filler(text: str) -> bool:
    return bool(FILLER_RE.match(str(text or "").strip()))


def is_followup(text: str) -> bool:
    return bool(FOLLOWUP_RE.match(str(text or "").strip()))


def is_negation(text: str) -> bool:
    return bool(NEGATION_RE.search(str(text or "")))


def is_recall(text: str) -> bool:
    spoken = str(text or "").strip()
    return bool(spoken) and not is_negation(spoken) and bool(RECALL_RE.search(spoken))


def _speakable_highlight(item: str) -> str:
    text = str(item or "").strip()
    if text.startswith("常找"):
        return f"你常找的是{text[2:]}"
    if text.startswith("纠正："):
        return text[3:]
    return text


def answer_recall(lines: Optional[List[str]]) -> str:
    spoken = [_speakable_highlight(item) for item in (lines or []) if str(item or "").strip()]
    spoken = spoken[:3]
    if not spoken:
        return "我还没记下你之前说的重点。"
    if len(spoken) == 1:
        body = spoken[0]
        return body if body.endswith(("。", "！", "？")) else f"{body}。"
    return "你之前让我记下这些：" + "，".join(spoken) + "。"


def blend_recall(recall: str, scene: str) -> str:
    past = str(recall or "").strip()
    current = str(scene or "").strip()
    if not past:
        return current
    if not current:
        return past
    if not past.endswith(("。", "！", "？")):
        past += "。"
    return f"{past}{current}"


def build_highlights(
    spoken: str,
    decision: str,
    target: str,
    rejected_target: str = "",
) -> List[str]:
    text = str(spoken or "").strip()
    if not text or is_filler(text) or is_followup(text):
        return []
    if is_negation(text):
        goal = str(rejected_target or "").strip()
        if not goal or goal == text:
            goal = "刚才那个"
        return [f"纠正：先不要按旧说法找{goal}"[:40]]
    if REMEMBER_RE.search(text):
        body = re.sub(r"^(?:请)?(?:帮我)?(?:记住|记一下|记下来|别忘了)", "", text)
        body = body.strip(" ，,。") or text
        return [f"记住：{body}"[:40]]
    if decision in {"new", "switch"}:
        name = str(target or "").strip()
        if name and "未确定" not in name and not is_negation(name) and not is_filler(name):
            return [f"常找{name}"[:40]]
    if PREFERENCE_RE.search(text):
        return [text[:40]]
    return []


def scrub_speech(
    speech: str,
    spoken: str,
    snippets: Optional[List[str]] = None,
    rejected_target: str = "",
) -> str:
    original = str(speech or "").strip()
    if not original:
        return original
    pieces = [str(item or "").strip() for item in (snippets or []) if str(item or "").strip()]
    if not is_negation(spoken) and not pieces and not HABIT_RE.search(original):
        return original

    text = HABIT_RE.sub("", original)
    for piece in pieces:
        if len(piece) >= 2:
            text = text.replace(piece, "")
    if is_negation(spoken):
        target = str(rejected_target or "").strip()
        if target:
            text = text.replace(f"正在帮你找{target}", "先停一下，这个先不找")
            text = text.replace(target, "")
        else:
            text = re.sub(r"正在帮你找[^，。]*", "先停一下，这个先不找", text)
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"^[，,。\s]+|[，,\s]+$", "", text).strip()
    if is_negation(spoken) and not text:
        text = "先停一下，这个先不找。"
    elif text and not text.endswith(("。", "！", "？")):
        text += "。"
    return text


def format_memory_prompt(snippets: List[str]) -> str:
    lines = [str(item).strip() for item in snippets if str(item).strip()]
    if not lines:
        return ""
    body = "\n".join(f"- {line[:80]}" for line in lines[:5])
    return (
        "[用户长期记忆]\n"
        f"{body}\n"
        "禁止：复述本段；禁止用记忆代替当前画面。风险以这一帧为准。"
    )


def _item_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    data = item if isinstance(item, dict) else None
    if data is None and hasattr(item, "model_dump"):
        data = item.model_dump()
    if not isinstance(data, dict):
        return ""
    for key in ("memory", "memory_value", "content", "text", "memory_key"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _snippet_texts(raw: Any) -> List[str]:
    if raw is None:
        return []
    if hasattr(raw, "memories") or hasattr(raw, "preferences"):
        items = list(getattr(raw, "memories", None) or [])
        items.extend(list(getattr(raw, "preferences", None) or []))
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        data = raw.get("data") if isinstance(raw.get("data"), (dict, list)) else raw
        if isinstance(data, dict):
            items = (
                data.get("memory_detail_list")
                or data.get("memories")
                or data.get("results")
                or []
            )
            if data.get("preference_detail_list"):
                items = list(items) + list(data.get("preference_detail_list") or [])
        else:
            items = data
    else:
        return []
    texts: List[str] = []
    for item in items or []:
        cleaned = _item_text(item)
        if cleaned and not is_filler(cleaned) and not is_followup(cleaned):
            texts.append(cleaned[:80])
    return texts[:5]


def note_record(text: str) -> dict:
    raw = str(text or "").strip()[:40]
    if raw.startswith("纠正"):
        kind, weight = "correction", 3
    elif raw.startswith("常找"):
        kind, weight = "find", 2
    else:
        kind, weight = "keep", 3
    return {
        "text": raw,
        "kind": kind,
        "weight": weight,
        "uses": 1,
        "updated_at": time.time(),
    }


def _as_record(item: Any) -> Optional[dict]:
    if isinstance(item, str):
        text = item.strip()
        return note_record(text) if text else None
    if not isinstance(item, dict):
        return None
    text = str(item.get("text") or "").strip()[:40]
    if not text:
        return None
    base = note_record(text)
    try:
        uses = int(item.get("uses") or 1)
    except (TypeError, ValueError):
        uses = 1
    try:
        updated = float(item.get("updated_at") or base["updated_at"])
    except (TypeError, ValueError):
        updated = base["updated_at"]
    base["uses"] = max(1, uses)
    base["updated_at"] = updated
    return base


class HighlightBook:
    """本机记下的重点。不存照片。无云端密钥时也保留，供问过去的事和后台查看。"""

    def __init__(self, db_path: str = ""):
        self._lock = threading.Lock()
        self._rows: dict = {}
        self._conn: Optional[sqlite3.Connection] = None
        if db_path:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS highlights ("
                "user_id TEXT PRIMARY KEY, payload TEXT)"
            )
            self._conn.commit()

    def add(self, user_id: str, lines: List[str]) -> List[str]:
        uid = str(user_id or "").strip()
        incoming = [str(item).strip()[:40] for item in lines or [] if str(item).strip()]
        if not uid or not incoming:
            return self.list(uid)
        with self._lock:
            stored = {item["text"]: item for item in self._keep_fresh(uid)}
            now = time.time()
            for text in incoming:
                fresh = note_record(text)
                previous = stored.get(fresh["text"])
                if previous:
                    previous["uses"] = int(previous.get("uses") or 1) + 1
                    previous["updated_at"] = now
                    previous["weight"] = max(int(previous.get("weight") or 1), fresh["weight"])
                else:
                    fresh["updated_at"] = now
                    stored[fresh["text"]] = fresh
            ranked = sorted(
                stored.values(),
                key=lambda item: (item["weight"] + min(int(item["uses"]), 5) * 0.15, item["updated_at"]),
                reverse=True,
            )[:20]
            self._rows[uid] = ranked
            self._write(uid, ranked)
            return [item["text"] for item in ranked]

    def list(self, user_id: str) -> List[str]:
        uid = str(user_id or "").strip()
        if not uid:
            return []
        with self._lock:
            rows = self._keep_fresh(uid)
            rows.sort(key=lambda item: item["updated_at"], reverse=True)
            return [item["text"] for item in rows]

    def groups(self, user_id: str) -> dict:
        uid = str(user_id or "").strip()
        empty = {"kept": [], "finds": [], "corrections": []}
        if not uid:
            return empty
        with self._lock:
            rows = self._keep_fresh(uid)

        def texts(kind: str) -> List[str]:
            picked = [item for item in rows if item["kind"] == kind]
            picked.sort(key=lambda item: (item["uses"], item["updated_at"]), reverse=True)
            return [item["text"] for item in picked]

        return {"kept": texts("keep"), "finds": texts("find"), "corrections": texts("correction")}

    def clear(self, user_id: str) -> None:
        uid = str(user_id or "").strip()
        if not uid:
            return
        with self._lock:
            self._rows[uid] = []
            if self._conn is not None:
                self._conn.execute("DELETE FROM highlights WHERE user_id = ?", (uid,))
                self._conn.commit()

    def _keep_fresh(self, user_id: str) -> List[dict]:
        now = time.time()
        fresh = [
            item
            for item in self._read(user_id)
            if now - float(item.get("updated_at") or 0) <= NOTE_TTL_SECONDS
        ]
        if len(fresh) != len(self._rows.get(user_id) or []):
            self._rows[user_id] = fresh
            self._write(user_id, fresh)
        return [dict(item) for item in fresh]

    def _read(self, user_id: str) -> List[dict]:
        if user_id in self._rows:
            return [dict(item) for item in self._rows[user_id]]
        if self._conn is None:
            return []
        row = self._conn.execute(
            "SELECT payload FROM highlights WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return []
        try:
            loaded = json.loads(row[0])
        except json.JSONDecodeError:
            loaded = []
        if not isinstance(loaded, list):
            loaded = []
        records = [record for record in (_as_record(item) for item in loaded) if record]
        self._rows[user_id] = records
        return [dict(item) for item in records]

    def _write(self, user_id: str, rows: List[dict]) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO highlights (user_id, payload) VALUES (?, ?)",
            (user_id, json.dumps(rows, ensure_ascii=False)),
        )
        self._conn.commit()


class LongMemoryService:
    def __init__(
        self,
        client: Any = None,
        enabled: bool = False,
        timeout_s: float = SEARCH_TIMEOUT_S,
        cache_ttl_s: float = CACHE_TTL_S,
        sync_writes: bool = False,
        book: Optional[HighlightBook] = None,
    ):
        self._client = client
        self.enabled = bool(enabled and client is not None)
        self.timeout_s = timeout_s
        self.cache_ttl_s = cache_ttl_s
        self.sync_writes = sync_writes
        self._cache = {}
        self._book = book or HighlightBook()

    def remember(self, user_id: str, highlights: List[str]) -> List[str]:
        return self._book.add(user_id, highlights)

    def list_highlights(self, user_id: str) -> List[str]:
        return self._book.list(user_id)

    def grouped(self, user_id: str) -> dict:
        return self._book.groups(user_id)

    def forget(self, user_id: str) -> None:
        self._book.clear(user_id)
        self._cache.pop(str(user_id or "").strip(), None)

    def recall(self, user_id: str, query: str, decision: str, spoken: str) -> List[str]:
        del decision
        if not self.enabled or not str(user_id or "").strip() or not is_recall(spoken):
            return []
        cached = self._cached(user_id)
        if cached is not None:
            return list(cached)
        fresh = self._search(user_id, query or spoken)
        if fresh is None:
            return []
        self._cache[user_id] = (time.monotonic(), fresh)
        return list(fresh)

    def schedule_write(
        self,
        user_id: str,
        conversation_id: str,
        highlights: List[str],
        agent_id: str = "",
    ) -> None:
        lines = [str(item).strip() for item in highlights or [] if str(item).strip()]
        if not self.enabled or not str(user_id or "").strip() or not lines:
            return
        content = "；".join(lines)
        agent = str(agent_id or NOTE_AGENT)

        def run() -> None:
            payload = {
                "user_id": user_id,
                "conversation_id": conversation_id or user_id,
                "messages": [{"role": "user", "content": content}],
                "agent_id": agent,
            }
            try:
                self._client.add_message(**payload)
            except TypeError:
                payload.pop("agent_id", None)
                try:
                    self._client.add_message(**payload)
                except Exception:
                    return
            except Exception:
                return

        if self.sync_writes:
            run()
            return
        threading.Thread(target=run, daemon=True).start()

    def _cached(self, user_id: str):
        found = self._cache.get(user_id)
        if not found:
            return None
        saved_at, snippets = found
        if time.monotonic() - saved_at > self.cache_ttl_s:
            self._cache.pop(user_id, None)
            return None
        return snippets

    def _search(self, user_id: str, query: str) -> Optional[List[str]]:
        pool = ThreadPoolExecutor(max_workers=1)
        query_text = query or "以前记下的事"

        def search() -> Any:
            try:
                return self._client.search_memory(
                    query=query_text,
                    user_id=user_id,
                    agent_id=NOTE_AGENT,
                )
            except TypeError:
                return self._client.search_memory(query=query_text, user_id=user_id)

        future = pool.submit(search)
        try:
            raw = future.result(timeout=self.timeout_s)
        except FuturesTimeout:
            return None
        except Exception:
            return None
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return _snippet_texts(raw)


def build_long_memory() -> LongMemoryService:
    book_path = os.getenv("HIGHLIGHT_DB_PATH", "").strip()
    if book_path.lower() == "memory":
        book = HighlightBook()
    else:
        if not book_path:
            book_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "highlights.sqlite",
            )
        book = HighlightBook(book_path)
    key = os.getenv("MEMOS_API_KEY", "").strip()
    if not key or key.startswith("replace-with"):
        return LongMemoryService(enabled=False, book=book)
    try:
        from memos.api.client import MemOSClient
    except ImportError:
        return LongMemoryService(enabled=False)
    base_url = os.getenv("MEMOS_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    try:
        client = MemOSClient(api_key=key, base_url=base_url)
    except TypeError:
        client = MemOSClient(api_key=key)
    return LongMemoryService(client=client, enabled=True, book=book)

"""眼镜闲聊的最近几轮。只接上一句，不记照片，也不写进 24 小时记要。"""

import time
from typing import List, Optional

MAX_TURNS = 5
TURN_TTL_S = 10 * 60


class ChatTurnStore:
    def __init__(self):
        self._rows = {}

    def recent(self, session_id: str, now: Optional[float] = None) -> List[dict]:
        row = self._fresh(session_id, now)
        if not row:
            return []
        return [dict(item) for item in row["turns"]]

    def append(self, session_id: str, user: str, assistant: str, now: Optional[float] = None) -> None:
        sid = str(session_id or "").strip()[:64]
        spoken = str(user or "").strip()[:80]
        reply = str(assistant or "").strip()[:72]
        if not sid or not spoken or not reply:
            return
        clock = time.time() if now is None else float(now)
        row = self._fresh(sid, clock) or {"updated": clock, "turns": []}
        row["turns"].append({"role": "user", "text": spoken})
        row["turns"].append({"role": "assistant", "text": reply})
        row["turns"] = row["turns"][-MAX_TURNS * 2 :]
        row["updated"] = clock
        self._rows[sid] = row

    def _fresh(self, session_id: str, now: Optional[float]):
        sid = str(session_id or "").strip()[:64]
        row = self._rows.get(sid)
        if not row:
            return None
        clock = time.time() if now is None else float(now)
        if clock - float(row.get("updated") or 0) > TURN_TTL_S:
            self._rows.pop(sid, None)
            return None
        return row

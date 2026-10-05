"""多轮会话上下文：滑动窗口历史 + 槽位（指代消解）。

设计取舍（面试点）：
- 历史不无限累积：只保留最近 HISTORY_TURNS 条消息 —— LLM 模式下直接决定 token 成本，
  mock 模式下也让“多轮”行为可预测；
- 槽位（last_course_id 等）从工具调用参数回填，用于“它/这门课/那它呢”这类指代；
  槽位落 SQLite sessions 表，服务重启不丢；
- 更大规模的做法：完整消息历史入 Redis/DB + 按 token 预算裁剪 + 摘要压缩，
  这里刻意保持最小实现以聚焦主链路。
"""
from __future__ import annotations

from .. import store

HISTORY_TURNS = 6  # 滑动窗口大小


class ContextStore:
    def __init__(self) -> None:
        self._history: dict[str, list[dict]] = {}
        self._slots: dict[str, dict] = {}

    def get_history(self, session_id: str) -> list[dict]:
        return list(self._history.get(session_id, []))

    def append(self, session_id: str, role: str, content: str) -> None:
        hist = self._history.setdefault(session_id, [])
        hist.append({"role": role, "content": content})
        del hist[:-HISTORY_TURNS]  # 滑动窗口：只留最近 HISTORY_TURNS 条

    def get_slots(self, session_id: str) -> dict:
        if session_id not in self._slots:  # 进程重启后从 SQLite 恢复
            self._slots[session_id] = store.get_store().get_slots(session_id)
        return self._slots[session_id]

    def update_slots(self, session_id: str, **kwargs) -> None:
        slots = self.get_slots(session_id)
        slots.update({k: v for k, v in kwargs.items() if v})
        self._slots[session_id] = slots
        store.get_store().save_slots(session_id, slots)

    def clear_slot(self, session_id: str, key: str) -> None:
        """删除单个槽位（update_slots 过滤假值，显式清空待确认状态要走这里）。"""
        slots = self.get_slots(session_id)
        slots.pop(key, None)
        self._slots[session_id] = slots
        store.get_store().save_slots(session_id, slots)

    def clear(self, session_id: str) -> None:
        self._history.pop(session_id, None)
        self._slots.pop(session_id, None)


CONTEXT = ContextStore()  # 进程级单例；多 worker 部署时应换集中式存储

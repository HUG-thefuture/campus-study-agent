"""SQLite 存储层：轨迹（traces）/ 学习任务（tasks）/ 会话槽位（sessions）。

设计取舍（面试点）：
- SQLite 零部署、单文件，和“回放器（简历项目二）”天然衔接：轨迹表按 会话+步骤 落库，
  支持 only_fail 过滤失败记录 —— 对应简历原话“记录调用输入、输出与异常”；
- 写入统一加锁（check_same_thread=False 允许跨线程，FastAPI 线程池下安全）；
- 生产化路径：换 SQLAlchemy + Postgres，接口不变，工具层与 Agent 层无感知。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  step INTEGER NOT NULL,
  role TEXT NOT NULL,           -- user / tool / assistant
  content TEXT,                 -- 文本内容（用户输入 / 助手回答 / 观察摘要）
  tool_name TEXT,
  tool_args TEXT,               -- JSON 字符串
  tool_ok INTEGER,              -- 1 成功 0 失败；非工具步骤为 NULL
  err TEXT,
  created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  due TEXT DEFAULT '',
  course_id TEXT DEFAULT '',
  status TEXT DEFAULT 'open',
  created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  slots_json TEXT DEFAULT '{}', -- 槽位：last_course_id 等，用于多轮指代消解
  updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);
"""


class TraceStore:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    # ---- 轨迹 ----
    def log_trace(self, session_id: str, step: int, role: str, content: str = "",
                  tool_name: str | None = None, tool_args: dict | None = None,
                  tool_ok: int | None = None, err: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO traces(session_id, step, role, content, tool_name, tool_args,"
                " tool_ok, err) VALUES(?,?,?,?,?,?,?,?)",
                (
                    session_id, step, role, content, tool_name,
                    json.dumps(tool_args, ensure_ascii=False) if tool_args is not None else None,
                    tool_ok, err,
                ),
            )
            self._conn.commit()

    def get_traces(self, session_id: str, only_fail: bool = False) -> list[dict]:
        sql = ("SELECT id, session_id, step, role, content, tool_name, tool_args,"
               " tool_ok, err, created_at FROM traces WHERE session_id=?")
        if only_fail:
            sql += " AND tool_ok=0"
        sql += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(sql, (session_id,)).fetchall()
        keys = ["id", "session_id", "step", "role", "content", "tool_name",
                "tool_args", "tool_ok", "err", "created_at"]
        return [dict(zip(keys, r)) for r in rows]

    # ---- 学习任务 ----
    def save_task(self, title: str, due: str = "", course_id: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks(title, due, course_id) VALUES(?,?,?)",
                (title, due, course_id),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_tasks(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, due, course_id, status, created_at FROM tasks ORDER BY id"
            ).fetchall()
        keys = ["id", "title", "due", "course_id", "status", "created_at"]
        return [dict(zip(keys, r)) for r in rows]

    # ---- 会话槽位（重启后可从 SQLite 恢复） ----
    def save_slots(self, session_id: str, slots: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(session_id, slots_json, updated_at)"
                " VALUES(?,?,datetime('now','localtime'))"
                " ON CONFLICT(session_id) DO UPDATE SET"
                " slots_json=excluded.slots_json, updated_at=excluded.updated_at",
                (session_id, json.dumps(slots, ensure_ascii=False)),
            )
            self._conn.commit()

    def get_slots(self, session_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT slots_json FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            # 槽位 JSON 损坏时按空会话处理：单条脏数据不应让该会话所有请求 500
            return {}


_store: TraceStore | None = None
_store_lock = threading.Lock()


def get_store() -> TraceStore:
    """惰性单例：首次调用时按配置路径建库。

    双检加锁：FastAPI 在线程池中并发处理请求，无锁时两个请求可能同时
    通过 _store is None 检查并各建一个实例，其一的连接被丢弃泄漏。
    """
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = TraceStore(config.DB_PATH)
    return _store


def reset_store(db_path: str | None = None) -> TraceStore:
    """测试专用：重建存储。默认重新读 AGENT_DB_PATH 环境变量（monkeypatch 友好）。"""
    global _store
    if _store is not None:
        _store.close()
    path = db_path or os.environ.get("AGENT_DB_PATH") or config.DB_PATH
    _store = TraceStore(path)
    return _store

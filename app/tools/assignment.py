"""作业/截止日期查询 与 学习任务创建。

任务创建写 SQLite（store.tasks 表），体现“读接口走 JSON 模拟、写接口走真实持久化”。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from ..config import DATA_DIR, SEMESTER_START
from .. import store
from .base import tool

with open(DATA_DIR / "assignments.json", encoding="utf-8") as f:
    ASSIGNMENTS = json.load(f)

SEM_START = date.fromisoformat(SEMESTER_START)


def current_week(today: date | None = None) -> int:
    """教学周 = (今天 - 开学周一) // 7 + 1，演示数据可复现。"""
    today = today or date.today()
    return (today - SEM_START).days // 7 + 1


@tool("list_assignments", "查询作业与截止日期，可按课程编号或教学周过滤",
      {"course_id": {"type": "str", "required": False, "default": "",
                     "desc": "课程编号，如 C001"},
       "week": {"type": "int", "required": False,
                "desc": "教学周次，如 2；不传则不过滤周次"},
       "this_week_only": {"type": "bool", "required": False, "default": False,
                          "desc": "只看本周（今天所在的教学周）"}})
def list_assignments(course_id: str = "", week: int | None = None,
                     this_week_only: bool = False) -> dict:
    today = date.today()
    rows = []
    if this_week_only:
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
    for a in ASSIGNMENTS:
        if course_id and a["course_id"] != course_id.strip().upper():
            continue
        if week is not None and a["week"] != week:
            continue
        due = date.fromisoformat(a["due"])
        if this_week_only and not (monday <= due <= sunday):
            continue
        days_left = (due - today).days
        status = "已过期" if days_left < 0 else ("今天截止" if days_left == 0 else f"还剩{days_left}天")
        rows.append({**a, "days_left": days_left, "status": status})
    rows.sort(key=lambda x: x["due"])
    return {"week": current_week(today), "count": len(rows), "assignments": rows}


@tool("create_task", "创建一条学习任务（标题必填），保存到 SQLite",
      {"title": {"type": "str", "required": True, "desc": "任务标题，不能为空"},
       "due": {"type": "str", "required": False, "default": "",
               "desc": "截止日期，格式 YYYY-MM-DD"},
       "course_id": {"type": "str", "required": False, "default": "",
                     "desc": "关联课程编号，如 C001"}},
      confirm=True)  # 确认门：写库操作，回显参数经用户确认后才落库
def create_task(title: str, due: str = "", course_id: str = "") -> dict:
    title = title.strip()
    if not title:
        # 业务级参数校验：空标题直接失败 → Agent 进入“澄清”分支（简历：参数校验+异常记录）
        raise ValueError("任务标题不能为空")
    if due:
        date.fromisoformat(due)  # 格式非法会抛 ValueError，被 execute_tool 记录为工具失败
    task_id = store.get_store().save_task(title=title, due=due, course_id=course_id.upper())
    return {"task_id": task_id, "title": title, "due": due or "未设置",
            "course_id": course_id.upper() or "无"}

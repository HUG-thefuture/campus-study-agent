"""课程类工具：课程查询、选课冲突检查。数据来自内置 JSON（模拟教务系统读接口）。"""
from __future__ import annotations

import json

from ..config import DATA_DIR
from .base import tool

with open(DATA_DIR / "courses.json", encoding="utf-8") as f:
    _COURSES_RAW = json.load(f)["courses"]
COURSES = {c["course_id"]: c for c in _COURSES_RAW}

with open(DATA_DIR / "student.json", encoding="utf-8") as f:
    STUDENT = json.load(f)

DAY_WORDS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _resolve_course(key: str) -> dict | None:
    """编号精确 → 名称/别名双向包含（唯一命中才认，歧义时不猜）。"""
    key = key.strip()
    c = COURSES.get(key.upper())
    if c:
        return c
    hits = [
        x for x in COURSES.values()
        if key and (key in x["name"] or x["name"] in key
                    or any(a and a in key for a in x.get("aliases", [])))
    ]
    return hits[0] if len(hits) == 1 else None


@tool("query_course", "按课程编号或名称查询课程信息（学分/教师/时间/教室）",
      {"course_id": {"type": "str", "required": True,
                     "desc": "课程编号如 C001，也支持课程名/别名（如 操作系统）"}})
def query_course(course_id: str) -> dict:
    c = _resolve_course(course_id)
    if c is None:
        # 业务级“未找到”返回 found=False（ok 仍为 True），让 Agent 诚实回答而不是报错
        return {"found": False,
                "message": f"未找到课程 {course_id}，请确认课程编号（如 C001）或课程名。"}
    return {"found": True, **c}


@tool("check_conflict", "检查目标课程与已选课程在指定时间是否冲突；不传时段则检查全部时段",
      {"course_id": {"type": "str", "required": True, "desc": "要选的课程编号，如 C004"},
       "time_slot": {"type": "str", "required": False, "default": "",
                     "desc": "如 周一 或 周一 3-4节；缺省检查全部时段"}})
def check_conflict(course_id: str, time_slot: str = "") -> dict:
    target = _resolve_course(course_id)
    if target is None:
        return {"found": False, "message": f"未找到课程 {course_id}，无法检查冲突。"}
    day = next((d for d in DAY_WORDS if d in time_slot), "")
    conflicts = []
    for cid in STUDENT["enrolled"]:                 # 已选课程来自模拟学籍数据
        c = COURSES.get(cid)
        if not c or c["course_id"] == target["course_id"]:
            continue
        for slot in target["schedule"]:             # 时段字符串在数据里保持统一格式
            if day and day not in slot:
                continue
            if slot in c["schedule"]:
                conflicts.append({"course_id": c["course_id"], "name": c["name"], "slot": slot})
    return {"found": True, "course_id": target["course_id"], "name": target["name"],
            "has_conflict": bool(conflicts), "conflicts": conflicts}

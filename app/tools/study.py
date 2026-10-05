"""GPA 计算 与 学习计划生成。"""
from __future__ import annotations

import json
from datetime import date, timedelta

from ..config import DATA_DIR
from .base import tool
from .assignment import ASSIGNMENTS


def _gp(score: float) -> float:
    """标准 4.0 制：绩点 = 分数/10 - 5（90→4.0，60→1.0），<60 记 0，上限 4.0。"""
    if score < 60:
        return 0.0
    return min(round(score / 10 - 5, 1), 4.0)


@tool("calculate_gpa", "按“学分加权平均绩点（4.0 制）”计算 GPA；不传成绩则使用内置成绩单",
      {"scores": {"type": "list", "required": False,
                  "desc": "形如 [{\"course_id\":\"C001\",\"credit\":4,\"score\":92}]；缺省用内置成绩单"}})
def calculate_gpa(scores: list | None = None) -> dict:
    if not scores:
        with open(DATA_DIR / "student.json", encoding="utf-8") as f:
            scores = json.load(f)["grades"]
    total_credit = sum(s["credit"] for s in scores)
    if total_credit <= 0:
        raise ValueError("学分之和必须大于 0")
    points = [
        {"course_id": s.get("course_id", ""), "score": s["score"],
         "credit": s["credit"], "gp": _gp(s["score"])}
        for s in scores
    ]
    gpa = round(sum(p["gp"] * p["credit"] for p in points) / total_credit, 2)
    return {"gpa": gpa, "total_credit": total_credit, "courses": points,
            "formula": "绩点=min(分数/10-5, 4.0)，<60 记 0；GPA=Σ(绩点×学分)/Σ学分"}


@tool("generate_study_plan", "根据未来作业截止日期，贪心生成未来 N 天的作业/复习计划",
      {"days": {"type": "int", "required": False, "default": 7,
                "desc": "计划天数，默认 7，上限 30"}})
def generate_study_plan(days: int = 7) -> dict:
    if days <= 0 or days > 30:
        raise ValueError("days 需在 1~30 之间")
    today = date.today()
    upcoming = sorted(
        (a for a in ASSIGNMENTS if date.fromisoformat(a["due"]) >= today),
        key=lambda x: x["due"],
    )
    plan = []
    for offset in range(days):
        d = today + timedelta(days=offset)
        due_today = [a for a in upcoming if a["due"] == d.isoformat()]
        items = [f"完成作业：{a['title']}（{a['course_id']}，今日截止）" for a in due_today]
        if not items:  # 简单贪心：无当日截止时，推进最近一个未完成作业
            nxt = next((a for a in upcoming if date.fromisoformat(a["due"]) > d), None)
            if nxt:
                items.append(f"推进作业：{nxt['title']}（{nxt['course_id']}，{nxt['due']} 截止）")
            else:
                items.append("无截止任务，可自主复习薄弱知识点")
        plan.append({"date": d.isoformat(), "weekday": "周" + "一二三四五六日"[d.weekday()],
                     "items": items})
    return {"days": days, "start": today.isoformat(), "plan": plan}

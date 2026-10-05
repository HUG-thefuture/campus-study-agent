"""工具层单测：业务正确性 + 参数校验 + 异常记录 + 数据一致性。"""
from datetime import date, timedelta

import pytest

from app import store
from app.tools import execute_tool
from app.tools.assignment import ASSIGNMENTS, create_task, current_week, list_assignments
from app.tools.course import COURSES, check_conflict, query_course
from app.tools.knowledge import KNOWLEDGE, knowledge_search
from app.tools.study import calculate_gpa, generate_study_plan

pytestmark = pytest.mark.usefixtures("tmp_store")  # 需要隔离数据库


# ---- 课程查询 ----
def test_query_course_by_id():
    r = query_course("C001")
    assert r["found"] and r["name"] == "数据结构与算法" and r["credits"] == 4


def test_query_course_by_name_alias():
    r = execute_tool("query_course", {"course_id": "操作系统"})
    assert r["ok"] and r["data"]["found"] and r["data"]["course_id"] == "C002"


def test_query_course_unknown():
    r = query_course("C999")
    assert not r["found"] and "未找到" in r["message"]


# ---- 冲突检查 ----
def test_conflict_detected():
    r = check_conflict("C004")  # C004 周一 3-4节 与已选 C001 重叠
    assert r["found"] and r["has_conflict"]
    assert any(c["course_id"] == "C001" and c["slot"] == "周一 3-4节"
               for c in r["conflicts"])


def test_conflict_with_time_slot():
    r = execute_tool("check_conflict", {"course_id": "C004", "time_slot": "周四"})
    assert r["ok"] and r["data"]["has_conflict"]  # 周四 3-4节 与 C006 重叠


def test_no_conflict():
    r = check_conflict("C002")
    assert r["found"] and not r["has_conflict"]


# ---- 作业查询 ----
def test_assignments_filter_by_course():
    expected = len([a for a in ASSIGNMENTS if a["course_id"] == "C001"])
    r = list_assignments(course_id="C001")
    assert r["count"] == expected and all(a["course_id"] == "C001" for a in r["assignments"])


def test_assignments_filter_by_week():
    r = list_assignments(week=2)
    assert r["count"] > 0 and all(a["week"] == 2 for a in r["assignments"])


def test_assignments_this_week_consistent():
    """this_week_only 的结果必须与独立计算的本周区间一致（对任意运行日期都成立）。"""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    expected = [a for a in ASSIGNMENTS
                if monday <= date.fromisoformat(a["due"]) <= monday + timedelta(days=6)]
    r = list_assignments(this_week_only=True)
    assert r["count"] == len(expected)


def test_current_week_calc():
    assert current_week(date.fromisoformat("2026-08-31")) == 1
    assert current_week(date.fromisoformat("2026-09-07")) == 2


# ---- GPA ----
def test_gpa_manual_scores():
    r = calculate_gpa(scores=[{"course_id": "X", "credit": 2, "score": 90},
                              {"course_id": "Y", "credit": 1, "score": 60}])
    assert r["gpa"] == 3.0  # (4.0*2 + 1.0*1) / 3


def test_gpa_builtin_transcript():
    r = calculate_gpa()
    # 2026-10 种子扩容：内置成绩单 4 门 → 8 门
    assert 0 < r["gpa"] <= 4.0 and len(r["courses"]) == 8


# ---- 任务创建（写 SQLite）----
def test_create_task_and_persist():
    r = create_task(title="背单词", due="2026-09-20", course_id="c007")
    tasks = store.get_store().list_tasks()
    assert any(t["id"] == r["task_id"] and t["title"] == "背单词" for t in tasks)


def test_create_task_empty_title_rejected():
    # confirmed=True：确认门已过，专测业务级校验（纯空白标题被拒）
    r = execute_tool("create_task", {"title": "   "}, confirmed=True)
    assert not r["ok"] and "任务标题" in r["error"]


def test_create_task_bad_due_format():
    # confirmed=True：确认门已过，专测业务级校验（due 格式非法被记录而不是静默吞掉）
    r = execute_tool("create_task", {"title": "x", "due": "2026/09/01"}, confirmed=True)
    assert not r["ok"] and "ValueError" in r["error"]  # 异常被记录而不是静默吞掉


def test_create_task_confirm_gate_blocks_write():
    """确认门：未确认时 create_task 不执行、不落库，返回 needs_confirm 并带回清洗参数。"""
    r = execute_tool("create_task", {"title": "确认门测试"})
    assert not r["ok"] and r.get("needs_confirm") is True
    assert r["args"] == {"title": "确认门测试", "due": "", "course_id": ""}
    assert not store.get_store().list_tasks()


# ---- 校验基础设施 ----
def test_missing_required_param():
    r = execute_tool("knowledge_search", {})
    assert not r["ok"] and "缺少必填参数" in r["error"]


def test_unknown_tool_recorded():
    r = execute_tool("no_such_tool", {})
    assert not r["ok"] and "未知工具" in r["error"]


def test_string_number_coercion():
    r = execute_tool("list_assignments", {"week": "2"})  # LLM 常把数字输出成字符串
    assert r["ok"] and all(a["week"] == 2 for a in r["data"]["assignments"])


# ---- 学习计划 / 知识点 ----
def test_study_plan_days():
    r = generate_study_plan(days=3)
    assert len(r["plan"]) == 3


def test_study_plan_invalid_days():
    r = execute_tool("generate_study_plan", {"days": 40})
    assert not r["ok"] and "days" in r["error"]


def test_knowledge_hit_and_miss():
    assert knowledge_search("进程和线程有什么区别")["found"]
    assert not knowledge_search("区块链是什么")["found"]  # 知识库外 → 诚实拒答的依据
    assert len(KNOWLEDGE) >= 8


def test_add_knowledge_confirm_gate_and_runtime_search(tmp_path, monkeypatch):
    """知识库运行时可扩充：确认门拦截 → 确认后持久化并立即可检索。"""
    monkeypatch.setattr("app.tools.knowledge.DATA_DIR", tmp_path)
    from app.tools.knowledge import KNOWLEDGE as _K
    before = len(_K)
    payload = {"topic": "一致性哈希",
               "content": "环状空间+虚拟节点，节点增减只迁移相邻区间。"}
    r = execute_tool("add_knowledge", payload)
    assert not r["ok"] and r.get("needs_confirm") is True  # 确认门：未确认不落库
    assert not (tmp_path / "knowledge.json").exists()
    r2 = execute_tool("add_knowledge", {**payload, "course_id": "c002"}, confirmed=True)
    assert r2["ok"] and r2["data"]["total"] == before + 1
    assert (tmp_path / "knowledge.json").exists()          # 已持久化到磁盘
    assert knowledge_search("一致性哈希是什么")["found"]   # 运行时立即命中
    assert _K[-1]["topic"] == "一致性哈希"
    _K.pop()  # 还原内存，避免污染其他用例


def test_add_knowledge_duplicate_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("app.tools.knowledge.DATA_DIR", tmp_path)
    r = execute_tool("add_knowledge", {"topic": "哈希表冲突解决", "content": "x"},
                     confirmed=True)
    assert not r["ok"] and "已存在" in r["error"]

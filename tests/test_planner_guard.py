# -*- coding: utf-8 -*-
"""Planner 规则守卫回归（2026-09 审计修复）：

1. 副作用工具（create_task）只认句首明确创建句式 —— "我想建一个学习小组，
   一起完成任务"不得触发真实写库；
2. "冲突"关键词不得遮蔽知识问答 —— "哈希冲突是什么"应到 knowledge_search。
"""
from __future__ import annotations

from app.agent.planners import MockPlanner


def _route(query: str) -> dict:
    return MockPlanner().plan({"query": query, "slots": {}, "observations": [], "history": []})


def test_side_effect_tool_requires_explicit_intent():
    res = _route("我想建一个学习小组，一起完成任务")
    assert res["action"] != "tool" or res.get("tool") != "create_task", \
        "非建任务请求不得触发 create_task 写库"


def test_create_task_legit_phrasings():
    """合法创建句式（评测集 T11/T15 口径）必须仍能命中。"""
    r1 = _route("帮我创建一个复习高数的任务")
    assert r1.get("tool") == "create_task" and r1["args"]["title"] == "复习高数", r1
    r2 = _route("我要建个背单词的任务")
    assert r2.get("tool") == "create_task" and r2["args"]["title"] == "背单词", r2


def test_conflict_keyword_does_not_shadow_knowledge():
    res = _route("哈希冲突是什么")
    assert res.get("tool") == "knowledge_search", f"应路由到知识检索，实际 {res}"


def test_real_course_conflict_still_routed():
    res = _route("C101 和我的课冲突吗")
    assert res.get("tool") == "check_conflict"
    assert res["args"]["course_id"] == "C101"


def test_bare_create_task_still_goes_to_validation():
    """空标题"创建任务"仍应调用工具 → 由工具校验失败走澄清路径（既有约定）。"""
    res = _route("创建任务")
    assert res.get("tool") == "create_task" and res["args"]["title"] == ""


def test_slots_injected_into_llm_prompt():
    """LLMPlanner 必须把槽位写进消息（审计 P2 90：槽位曾是 LLM 模式死代码）。"""
    captured = {}

    class SpyLLM:
        def chat(self, messages):
            captured["messages"] = messages
            return '{"action":"answer","answer":"ok"}'

    from app.agent.planners import LLMPlanner
    LLMPlanner(SpyLLM()).plan({
        "query": "它的老师是谁", "history": [],
        "slots": {"last_course_id": "C002"}, "observations": []})
    text = "".join(m["content"] for m in captured["messages"])
    assert "C002" in text, "槽位 last_course_id 应注入提示词"


def test_add_knowledge_phrase_routes_to_write_tool():
    """“添加知识点”句式必须路由到写工具 add_knowledge（走确认门），
    而不是被知识检索规则（含“知识点”关键词）抢走。"""
    r = _route("添加知识点：一致性哈希。环状空间加虚拟节点，节点增减只迁移相邻区间。")
    assert r.get("tool") == "add_knowledge", f"应路由到 add_knowledge，实际 {r}"
    assert r["args"]["topic"] == "一致性哈希"
    assert r["args"]["content"] == "环状空间加虚拟节点，节点增减只迁移相邻区间"


def test_add_knowledge_incomplete_args_still_routes():
    """句式命中但参数不全 → 仍路由到 add_knowledge，由参数校验走澄清路径
    （与“创建任务”空标题的既有约定一致），不得漏到知识检索。"""
    r = _route("帮我添加一条知识点")
    assert r.get("tool") == "add_knowledge", f"应路由到 add_knowledge，实际 {r}"
    assert r["args"] == {"topic": "", "content": ""}

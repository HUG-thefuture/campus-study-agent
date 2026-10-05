"""Agent 循环测试：路由正确性、多轮指代、失败澄清、停止条件。"""
import pytest

from app.agent import ReActRunner, get_runner


@pytest.fixture()
def runner(tmp_store):
    return get_runner()  # mock 模式：规则路由，无网络


def test_react_loop_routes_assignment_query(runner):
    """完整 ReAct：规则路由 → 工具 → 观察 → 汇总回答。"""
    res = runner.run("我这周有什么作业", "t1")
    tool_steps = [s for s in res.steps if s["action"] == "tool"]
    assert tool_steps and tool_steps[0]["tool"] == "list_assignments"
    assert tool_steps[0]["args"].get("this_week_only") is True
    assert res.answer and "作业" in res.answer
    assert res.iterations == 2  # 1 轮工具 + 1 轮回答


def test_multi_turn_coreference(runner):
    """多轮上下文：第二轮用“它”指代上一轮课程 → 槽位消解为 C002。"""
    runner.run("C002的作业有哪些", "t2")
    res2 = runner.run("那它的老师是谁", "t2")
    first = next(s for s in res2.steps if s["action"] == "tool")
    assert first["tool"] == "query_course" and first["args"]["course_id"] == "C002"
    assert "李强" in res2.answer


def test_failed_tool_leads_to_clarification(runner):
    """工具失败（空标题校验）→ 不编造，向用户澄清。"""
    res = runner.run("创建任务", "t3")
    tool_steps = [s for s in res.steps if s["action"] == "tool"]
    assert tool_steps[0]["tool"] == "create_task" and tool_steps[0]["ok"] is False
    assert "没有成功" in res.answer and "title" in res.answer


def test_fallback_honest_answer(runner):
    """无关问题不乱调工具，诚实回答能力边界。"""
    res = runner.run("今天天气怎么样", "t4")
    assert all(s["action"] != "tool" for s in res.steps)
    assert res.answer.strip()


def test_max_iterations_guard(tmp_store):
    """停止条件：决策器永远要求调工具时，循环到上限后诚实收尾而不是死循环。"""
    # tmp_store：隔离到临时库。不挂夹具时会向真实 app/data/agent.db 写入 7 行轨迹，
    # 且单独运行本用例（pytest tests/test_agent.py::test_max_iterations_guard）时必现。

    class FakePlanner:
        def plan(self, state):
            return {"action": "tool", "tool": "query_course",
                    "args": {"course_id": "C001"}, "thought": "x"}

    res = ReActRunner(FakePlanner()).run("随便", "t5")
    assert len([s for s in res.steps if s["action"] == "tool"]) == 5  # MAX_ITERATIONS
    assert "上限" in res.answer


def test_composite_intent_runs_two_tools(runner):
    """组合意图：作业 + 课程介绍 → 多步 ReAct（list_assignments → query_course）。

    2026-10 反“死板”增量：复合问题不再一轮就收束，Agent 循环真正多轮工作，
    最终回答汇总各步观察结果。
    """
    res = runner.run("查一下C002的作业有哪些，再介绍下这门课", "t6")
    tools = [s for s in res.steps if s["action"] == "tool"]
    assert [s["tool"] for s in tools] == ["list_assignments", "query_course"]
    assert res.iterations == 3  # 两轮工具 + 一轮汇总回答
    assert "作业" in res.answer and "操作系统" in res.answer

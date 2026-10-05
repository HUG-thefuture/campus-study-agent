"""30 条评测（对应教程第 4 周评测集）：覆盖 正常 / 边界 / 异常 三类。

检查点：工具选择是否正确、参数是否正确、工具是否成功、回答是否诚实（拒答/澄清）。
全部离线跑（mock 规则路由），可复现、无网络、无密钥。
"""
import pytest

from app.agent import get_runner

# (查询, 期望工具 or None=不应调工具, 期望工具是否成功, 回答需包含的关键词 or None)
CASES = [
    # ---- 正常（工具选择 + 参数正确）----
    ("查询C001的课程信息", "query_course", True, "数据结构与算法"),
    ("操作系统这门课谁上", "query_course", True, "李强"),
    ("查一下C005上课时间", "query_course", True, None),
    ("Hello, who teaches C002?", "query_course", True, "操作系统"),
    ("我这周有什么作业", "list_assignments", True, None),
    ("C002的作业有哪些", "list_assignments", True, None),
    ("网络作业什么时候截止", "list_assignments", True, None),
    ("我的GPA是多少", "calculate_gpa", True, "GPA"),
    ("给我一个三天学习计划", "generate_study_plan", True, None),
    ("创建任务：背50个单词", "create_task", False, "确认"),  # 写操作确认门：回显待确认，不落库
    ("C004和我的课冲突吗", "check_conflict", True, "有冲突"),
    ("三次握手是什么", "knowledge_search", True, "三次握手"),
    # ---- 边界（参数校验 / 不存在 / 空结果）----
    ("创建任务", "create_task", False, "没有成功"),          # 空标题 → 校验失败
    ("创建任务：", "create_task", False, "没有成功"),         # 空标题 → 校验失败
    ("查询课程", "query_course", False, "没有成功"),          # 缺 course_id → 校验失败
    ("生成40天计划", "generate_study_plan", False, "没有成功"),  # days 越界 → 校验失败
    ("查询C999的课程信息", "query_course", True, "未找到"),    # 不存在 → 诚实告知
    ("C999下周三会冲突吗", "check_conflict", True, "未找到"),  # 不存在 → 诚实告知
    ("C001第99周的作业", "list_assignments", True, "没有"),    # 空结果 → 诚实告知
    ("高等数学的作业第99周", "list_assignments", True, "没有"),
    # ---- 异常（知识库外 / 无关请求 → 拒答，不编造）----
    ("区块链是什么", "knowledge_search", True, "没有收录"),
    ("量子力学怎么理解", "knowledge_search", True, "不确定"),
    ("今天天气怎么样", None, None, None),
    ("你能不能帮我抢课", None, None, None),
    ("给我讲个笑话", None, None, None),
    ("写一首诗", None, None, None),
    ("1+1等于几", None, None, None),
    ("推荐一部电影", None, None, None),
    ("帮我写毕业论文", None, None, None),
    ("明天几点起床比较好", None, None, None),
]
assert len(CASES) == 30


def _params():
    return [pytest.param(q, t, ok, needle, id=f"{i:02d}-{q[:12]}")
            for i, (q, t, ok, needle) in enumerate(CASES)]


@pytest.mark.parametrize("query,expect_tool,expect_ok,needle", _params())
def test_eval(tmp_store, query, expect_tool, expect_ok, needle):
    runner = get_runner()
    res = runner.run(query, session_id=f"eval-{abs(hash(query)) % 100000:05d}")

    if expect_tool is None:
        # 异常类：不应调用任何工具，且必须给出非空、诚实的回答
        assert all(s["action"] != "tool" for s in res.steps), f"不应调工具：{res.steps}"
        assert res.answer.strip()
        return

    tool_steps = [s for s in res.steps if s["action"] == "tool"]
    assert tool_steps, f"应调用 {expect_tool}，实际未调用任何工具"
    assert tool_steps[0]["tool"] == expect_tool, f"工具选择错误：{tool_steps[0]['tool']}"
    assert tool_steps[0]["ok"] is expect_ok, f"工具结果异常：{tool_steps[0]}"
    if needle:
        assert needle in res.answer, f"回答缺少关键信息：{res.answer!r}"


def test_eval_write_confirm_flow(tmp_store):
    """写操作两步确认闭环：创建 → 回显待确认（不落库）→ 确认后按首轮参数落库。

    这是“请求→工具→确认→结果”口径的实现级验收，也是确认门的回归锚点。
    """
    from app import store

    runner = get_runner()

    sid = "eval-confirm-flow"
    r1 = runner.run("创建任务：背单词练习", session_id=sid)
    assert "确认" in r1.answer, r1.answer
    assert not store.get_store().list_tasks(), "未确认前不得写库"
    r2 = runner.run("确认", session_id=sid, confirmed=True)
    assert "已创建" in r2.answer, r2.answer
    tasks = store.get_store().list_tasks()
    assert len(tasks) == 1 and tasks[0]["title"] == "背单词练习"

    # 取消路径：新建 → 取消 → 待确认被丢弃，之后再确认也不会创建
    sid2 = "eval-cancel-flow"
    runner.run("创建任务：晨跑打卡", session_id=sid2)
    r_cancel = runner.run("取消", session_id=sid2)
    assert "取消" in r_cancel.answer, r_cancel.answer
    r3 = runner.run("确认", session_id=sid2, confirmed=True)
    assert "已创建" not in r3.answer, r3.answer
    assert len(store.get_store().list_tasks()) == 1, "取消后不得残留写入"

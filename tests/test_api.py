"""API 集成测试：请求校验、主链路、轨迹回放、任务持久化。"""


def test_root_and_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").json()["name"] == "校园学习助理 Agent"


def test_tools_endpoint(client):
    r = client.get("/agent/tools").json()
    names = {t["name"] for t in r["tools"]}
    assert r["count"] >= 7
    assert {"query_course", "check_conflict", "list_assignments",
            "create_task", "calculate_gpa", "generate_study_plan",
            "knowledge_search"} <= names


def test_chat_returns_tool_trace(client):
    """核心验收：chat 返回的 JSON 必须带完整工具调用轨迹。"""
    r = client.post("/agent/chat", json={"query": "我这周有什么作业"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] and body["answer"]
    tool_steps = [s for s in body["steps"] if s["action"] == "tool"]
    assert tool_steps and tool_steps[0]["tool"] == "list_assignments"
    assert tool_steps[0]["ok"] is True and tool_steps[0]["observation"] is not None
    assert body["iterations"] >= 2


def test_chat_empty_query_422(client):
    assert client.post("/agent/chat", json={"query": ""}).status_code == 422
    assert client.post("/agent/chat", json={"query": "   "}).status_code == 422


def test_traces_replay_and_only_fail(client):
    """轨迹落 SQLite + only_fail 过滤（简历项目二回放器的数据面）。"""
    client.post("/agent/chat", json={"query": "查询C001的课程信息", "session_id": "api-demo-1"})
    rows = client.get("/traces/api-demo-1").json()["traces"]
    roles = [r["role"] for r in rows]
    assert roles[0] == "user" and "tool" in roles and roles[-1] == "assistant"
    tool_rows = [r for r in rows if r["tool_name"]]
    assert tool_rows and all(r["tool_args"] for r in tool_rows)  # 输入被记录

    fails = client.get("/traces/api-demo-1", params={"only_fail": "true"}).json()
    assert fails["count"] == 0  # 该会话无失败记录
    assert all(r["tool_ok"] == 0 for r in fails["traces"])


def test_task_persisted_via_agent(client):
    # 写操作走确认门：首轮回显待确认（不落库），确认后按首轮参数原样落库
    r1 = client.post("/agent/chat", json={"query": "创建任务：测试任务甲",
                                          "session_id": "persist-1"})
    body1 = r1.json()
    assert "确认" in body1["answer"]
    # 演示台依赖步骤级 needs_confirm 标志渲染确认/取消交互
    assert any(s.get("needs_confirm") for s in body1["steps"])
    assert not client.get("/tasks").json()["tasks"]
    r2 = client.post("/agent/chat", json={"query": "确认", "session_id": "persist-1",
                                          "confirm": True})
    assert "已创建" in r2.json()["answer"]
    tasks = client.get("/tasks").json()["tasks"]
    assert any(t["title"] == "测试任务甲" for t in tasks)


def test_demo_page_served(client):
    """产品入口：/demo 返回单文件演示台（HTML），无外部资源依赖。"""
    r = client.get("/demo")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "校园学习助理" in r.text and "/agent/chat" in r.text

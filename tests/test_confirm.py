"""写操作确认门：API 层闭环测试。

不变量（面试可讲的三条）：
1. 没有回显就没有执行 —— confirm 标志只在存在待确认操作时生效，不能直通；
2. 确认的内容 = 执行的内容 —— 重放使用服务端留存的首轮清洗参数，不接受模型重新生成；
3. 待确认状态跨轮持久（槽位落 SQLite），取消后即被丢弃。
"""
import sys
import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


def test_create_task_requires_confirm_before_write(client):
    r1 = client.post("/agent/chat", json={"query": "创建任务：背50个单词",
                                          "session_id": "gate-c1"})
    assert r1.status_code == 200
    body = r1.json()
    assert "确认" in body["answer"], body["answer"]
    assert client.get("/tasks").json()["count"] == 0, "未确认前不得写库"


def test_confirm_replays_first_round_args(client):
    client.post("/agent/chat", json={"query": "创建任务：背50个单词",
                                     "session_id": "gate-c2"})
    r2 = client.post("/agent/chat", json={"query": "确认", "session_id": "gate-c2",
                                          "confirm": True})
    assert "已创建" in r2.json()["answer"], r2.json()["answer"]
    tasks = client.get("/tasks").json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["title"] == "背50个单词"


def test_cancel_discards_pending(client):
    client.post("/agent/chat", json={"query": "创建任务：晨跑打卡",
                                     "session_id": "gate-c3"})
    r_cancel = client.post("/agent/chat", json={"query": "取消",
                                                "session_id": "gate-c3"})
    assert "取消" in r_cancel.json()["answer"]
    r3 = client.post("/agent/chat", json={"query": "确认", "session_id": "gate-c3",
                                          "confirm": True})
    assert "已创建" not in r3.json()["answer"]
    assert client.get("/tasks").json()["count"] == 0, "取消后不得残留写入"


def test_confirm_flag_without_pending_does_not_bypass_gate(client):
    # 未回显就带 confirm=true 直接创建 —— 仍被拦截（不变量 1）
    r = client.post("/agent/chat", json={"query": "创建任务：直通尝试",
                                         "session_id": "gate-c4", "confirm": True})
    assert "已创建" not in r.json()["answer"]
    assert client.get("/tasks").json()["count"] == 0

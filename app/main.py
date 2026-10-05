"""FastAPI 入口：/agent/chat 主链路 + 工具清单 + 轨迹回放（项目二雏形）+ 任务列表。

面试点：OpenAPI 文档由 FastAPI 自动生成（/docs），交付要求里的“OpenAPI 接口”零成本满足；
接口层只做参数校验与编排，业务全部在 agent/tools/store —— 分层清晰，方便替换传输层。
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .agent import get_runner
from .store import get_store
from .tools import list_tools

app = FastAPI(
    title="校园学习助理 Agent",
    version="1.0.0",
    description="轻量自实现 ReAct 工具调用循环 + 可插拔 LLM（mock / OpenAI 兼容）"
                " + SQLite 轨迹存储",
)

RUNNER = get_runner()  # 进程级单例；mock/LLM 由 .env 的 LLM_PROVIDER 决定


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000,
                       description="用户问题，如：我这周有什么作业")
    session_id: str | None = Field(
        None, pattern=r"^[A-Za-z0-9_\-]{1,64}$",
        description="会话 ID（字母/数字/_/-，≤64 字符），不传则自动生成")
    confirm: bool = Field(
        False, description="写操作确认标志：确认待确认操作时置 true，"
                           "服务端按首轮回显参数原样重放落库（不能绕过回显直通）")


@app.get("/")
def root():
    return {"name": "校园学习助理 Agent", "docs": "/docs", "chat": "POST /agent/chat"}


@app.get("/health")
def health():
    return {"status": "ok"}


_DEMO_PAGE = Path(__file__).parent / "static" / "index.html"


@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    """单文件演示台：浏览器打开即用（产品入口——评审看产品，不看 curl）。"""
    return _DEMO_PAGE.read_text(encoding="utf-8")


@app.get("/agent/tools")
def agent_tools():
    """工具清单（name/描述/参数 schema），与提示词里的工具目录同源。"""
    return {"count": len(list_tools()), "tools": list_tools()}


@app.post("/agent/chat")
def agent_chat(req: ChatRequest):
    """主链路：一次 ReAct 循环，返回最终答案 + 完整工具调用轨迹（steps）。"""
    if not req.query.strip():
        raise HTTPException(status_code=422, detail="query 不能为空白字符")
    session_id = req.session_id or f"s-{uuid.uuid4().hex[:8]}"
    result = RUNNER.run(req.query.strip(), session_id, confirmed=req.confirm)
    return asdict(result)


@app.get("/traces/{session_id}")
def traces(session_id: str, only_fail: bool = Query(False, description="只看失败记录")):
    """轨迹回放：按会话查看每步工具选择/参数/结果/失败原因（简历项目二的数据源）。"""
    rows = get_store().get_traces(session_id, only_fail=only_fail)
    return {"session_id": session_id, "only_fail": only_fail,
            "count": len(rows), "traces": rows}


@app.get("/tasks")
def tasks():
    return {"count": len(get_store().list_tasks()), "tasks": get_store().list_tasks()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)

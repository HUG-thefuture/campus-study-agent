"""ReAct 循环核心：Plan → Tool → Observe → Answer（带最大轮数停止条件）。

为什么自实现而不用 LangChain / LangGraph（面试点，最重要的取舍）：
- 本场景是“单 Agent + 7 个工具”的线性循环，用不上 LangGraph 的状态图/检查点/多 Agent
  编排能力；引入它会让依赖树多几十个包，调试变成读黑盒源码；
- 这里把循环收敛成约 60 行可读代码：决策抽象成 Planner 接口（规则实现 / LLM 实现，
  按 .env 一键切换），执行抽象成工具注册表，轨迹显式落 SQLite —— 可控、可测、可回放；
- 若面试官明确要 LangGraph：实现本文件的 AgentRunner 接口，用 StateGraph 把
  decide/tools/answer 包成三个节点即可无缝替换（README 有迁移说明）。
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .. import store
from ..config import MAX_ITERATIONS
from ..tools import execute_tool
from .context import CONTEXT
from .planners import LLMPlanner, MockPlanner

log = logging.getLogger(__name__)


@dataclass
class AgentResult:
    session_id: str
    query: str
    answer: str
    iterations: int
    steps: list[dict] = field(default_factory=list)


class AgentRunner(ABC):
    """Agent 执行器接口：换 LangGraph / 其他编排框架时实现它即可，上层 API 不变。"""

    @abstractmethod
    def run(self, query: str, session_id: str, confirmed: bool = False) -> AgentResult: ...


class ReActRunner(AgentRunner):
    def __init__(self, planner):
        self.planner = planner

    def run(self, query: str, session_id: str, confirmed: bool = False) -> AgentResult:
        steps: list[dict] = []
        observations: list[dict] = []
        history = CONTEXT.get_history(session_id)
        slots = CONTEXT.get_slots(session_id)
        store.get_store().log_trace(session_id, step=0, role="user", content=query)

        answer = ""
        it = 1
        for it in range(1, MAX_ITERATIONS + 1):
            try:
                action = self.planner.plan({"query": query, "history": history,
                                            "slots": slots, "observations": observations})
            except Exception as exc:  # noqa: BLE001
                # 2026-09 修复：LLM 调用失败（网络/401/超时）曾直接穿透循环变 500，
                # 且轨迹只剩 user 记录。现降级为结构化兜底回答并落轨迹，会话历史不残缺。
                # 记完整堆栈：MockPlanner 的编程错误（TypeError 等）也会落到这里，
                # 只记 str(exc) 会把真 bug 伪装成"模型调用失败"，排障困难
                log.exception("planner 决策失败 session=%s", session_id)
                answer = "服务暂时不可用（决策模型调用失败），请稍后重试或改用 mock 模式。"
                steps.append({"iteration": it, "thought": "planner 调用失败",
                              "action": "answer", "error": str(exc)})
                store.get_store().log_trace(session_id, step=it, role="tool",
                                            content="", tool_name="planner",
                                            tool_args={}, tool_ok=0, err=str(exc))
                break
            if action["action"] == "answer":
                answer = action.get("answer", "")
                steps.append({"iteration": it, "thought": action.get("thought", ""),
                              "action": "answer"})
                break
            if action["action"] == "discard_pending":
                # 取消待确认写操作：Planner 负责决策，执行器负责清槽位（职责分离）
                CONTEXT.clear_slot(session_id, "pending")
                answer = action.get("answer", "好的，已取消该操作。")
                steps.append({"iteration": it, "thought": action.get("thought", ""),
                              "action": "answer"})
                break

            name, args = action["tool"], action.get("args") or {}
            pending = (slots or {}).get("pending")
            if confirmed and isinstance(pending, dict) and pending.get("tool") == name:
                # 2026-10 确认门：用户已确认 → 用首轮回显留存的参数原样重放，
                # 不接受模型本轮重新生成的参数，保证“确认的内容 = 执行的内容”
                args = pending.get("args") or {}
                result = execute_tool(name, args, confirmed=True)
                CONTEXT.clear_slot(session_id, "pending")
            else:
                result = execute_tool(name, args)      # 统一入口：校验+确认门+异常+计时
                if result.get("needs_confirm"):
                    # 写操作待确认：参数已回显给用户，留存首轮参数供确认后重放
                    CONTEXT.update_slots(session_id,
                                         pending={"tool": name, "args": result["args"]})
            observations.append(result)                # Observe：喂回下一轮决策
            steps.append({"iteration": it, "thought": action.get("thought", ""),
                          "action": "tool", "tool": name, "args": args,
                          "ok": result["ok"],
                          "observation": result["data"] if result["ok"] else None,
                          "error": result["error"],
                          # 演示台/前端据此渲染“确认/取消”交互，不必猜错误文案
                          "needs_confirm": bool(result.get("needs_confirm"))})
            store.get_store().log_trace(
                session_id, step=it, role="tool",
                content=(json.dumps(result["data"], ensure_ascii=False)[:500]
                         if result["ok"] else ""),
                tool_name=name, tool_args=args,
                tool_ok=1 if result["ok"] else 0, err=result["error"])
            self._update_slots(session_id, name, args)
        else:
            # 停止条件兜底：达到上限后诚实告知，绝不带着半截结果编造答案
            answer = ("这个问题需要多步处理，已达单次对话的工具调用上限"
                      f"（{MAX_ITERATIONS} 轮），请把问题拆小一点再试。")

        CONTEXT.append(session_id, "user", query)
        CONTEXT.append(session_id, "assistant", answer)
        # 2026-09 修复：assistant 步号曾用 len(steps)+1，提前收敛时与 steps 末项
        # iteration 错位导致回放出现空洞；现直接沿用末步 iteration。
        final_step = steps[-1]["iteration"] if steps else it
        store.get_store().log_trace(session_id, step=final_step,
                                    role="assistant", content=answer)
        return AgentResult(session_id=session_id, query=query, answer=answer,
                           iterations=it, steps=steps)

    @staticmethod
    def _update_slots(session_id: str, tool_name: str, args: dict) -> None:
        """工具参数回填槽位：为后续“它/这门课”的指代消解提供依据。"""
        cid = (args or {}).get("course_id")
        if cid and tool_name in ("query_course", "check_conflict",
                                 "list_assignments", "create_task"):
            CONTEXT.update_slots(session_id, last_course_id=str(cid).upper())


def get_runner() -> AgentRunner:
    """按 LLM_PROVIDER 组装：mock → 规则路由；其余 → LLM JSON 协议。"""
    from ..config import LLM_PROVIDER
    from ..llm import get_llm

    if LLM_PROVIDER == "mock":
        return ReActRunner(MockPlanner())
    return ReActRunner(LLMPlanner(get_llm()))

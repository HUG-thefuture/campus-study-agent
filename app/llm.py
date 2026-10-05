"""可插拔 LLM 层：对 Agent 暴露统一的最小接口 chat(messages) -> str。

- MockLLM：规则式回复，无网络依赖 —— mock 模式下 Agent 全流程可跑通，pytest 无密钥全绿；
- OpenAICompatLLM：OpenAI 兼容 /chat/completions（OpenAI / DashScope / Kimi 等均适用），
  用 httpx 直连而不是 openai SDK，避免多拉一层重依赖（依赖只有 5 个包，见 requirements.txt）。

面试点（LLM 可插拔怎么设计）：Agent 循环只依赖 chat() 这一个函数签名，
后端选择由配置决定，测试时可随时打桩 —— 这是依赖倒置在 LLM 应用里的最小落地。
"""
from __future__ import annotations

from typing import Protocol

import httpx

from .config import LLM_PROVIDER, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL


class LLMBackend(Protocol):
    def chat(self, messages: list[dict]) -> str: ...


class MockLLM:
    """规则式 mock：不联网，按最后一条消息给确定性回复；宁可承认能力边界也不编造。"""

    def chat(self, messages: list[dict]) -> str:
        last = (messages[-1]["content"] if messages else "").strip()
        if not last:
            return "你好，我是校园学习助理，可以帮你查课程、查作业、算 GPA、制定学习计划。"
        if any(k in last.lower() for k in ("你好", "您好", "hi", "hello", "在吗")):
            return "你好！我是校园学习助理，可以帮你：查课程、查作业与截止日期、算 GPA、制定学习计划、查知识点。"
        return (
            "（mock 模式）我基于工具结果回答问题，暂不支持自由聊天。"
            "你可以试试：查课程 / 这周有什么作业 / 我的 GPA / 制定学习计划 / 查知识点。"
        )


class OpenAICompatLLM:
    """OpenAI 兼容后端：读取 .env 的 API_KEY / BASE_URL / MODEL（用户自备，占位符默认）。"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, timeout: float = 30.0):
        self.api_key = api_key or OPENAI_API_KEY
        self.base_url = (base_url or OPENAI_BASE_URL).rstrip("/")
        self.model = model or OPENAI_MODEL
        self.timeout = timeout

    def chat(self, messages: list[dict]) -> str:
        # 密钥缺失/仍是占位符时直接快速失败，绝不带着假 Key 去打网络请求
        if not self.api_key or self.api_key.startswith(("sk-your", "sk-xxx")):
            raise RuntimeError(
                "未配置 OPENAI_API_KEY：请在 .env 中填写真实 Key，或将 LLM_PROVIDER 保持为 mock"
            )
        payload = {"model": self.model, "messages": messages, "temperature": 0.2}
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def get_llm(provider: str | None = None) -> LLMBackend:
    """工厂：按配置返回后端。Agent 循环对其一无所知，方便替换与测试打桩。"""
    p = (provider or LLM_PROVIDER).lower()
    return MockLLM() if p == "mock" else OpenAICompatLLM()

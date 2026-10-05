"""LLM 层测试：mock 后端确定性输出；OpenAI 兼容后端无密钥时快速失败；JSON 解析。"""
import pytest

from app.llm import MockLLM, OpenAICompatLLM, get_llm
from app.agent.planners import LLMPlanner


def test_mock_llm_returns_str():
    out = MockLLM().chat([{"role": "user", "content": "随便聊聊"}])
    assert isinstance(out, str) and out.strip()


def test_mock_llm_greeting():
    out = MockLLM().chat([{"role": "user", "content": "你好"}])
    assert "校园学习助理" in out


def test_get_llm_factory():
    assert isinstance(get_llm("mock"), MockLLM)
    assert isinstance(get_llm("openai_compatible"), OpenAICompatLLM)


def test_openai_backend_requires_key():
    """无密钥/占位符必须快速失败，绝不带假 Key 打网络请求。"""
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAICompatLLM(api_key="sk-your-key-here").chat([{"role": "user", "content": "hi"}])
    with pytest.raises(RuntimeError):
        OpenAICompatLLM(api_key="").chat([{"role": "user", "content": "hi"}])


def test_llm_planner_parse_valid():
    p = LLMPlanner(MockLLM())
    action = p._parse('```json\n{"action":"tool","tool":"query_course",'
                      '"args":{"course_id":"C001"},"thought":"查询"}\n```')
    assert action and action["action"] == "tool" and action["tool"] == "query_course"


def test_llm_planner_parse_rejects_bad():
    p = LLMPlanner(MockLLM())
    assert p._parse("我觉得应该查一下课程") is None            # 非 JSON
    assert p._parse('{"action":"tool","tool":"hack_db","args":{}}') is None  # 白名单外工具
    assert p._parse('{"action":"dance"}') is None             # 非法 action

"""提示词版本管理（交付点：提示词版本 v1/v2 对比）。

v1：只有角色设定，模型自由发挥 —— 实测工具选择不稳定、容易闲聊跑偏、输出不可解析；
v2：加入“工具目录 + JSON Action 协议 + 少样本示例 + 拒答策略” ——
    结构化输出可 json.loads 并校验，工具选择与参数抽取显著更稳。
当前使用 v2，v1 保留作对照（面试可现场对比讲解）。
"""
from __future__ import annotations

from .config import MAX_ITERATIONS

SYSTEM_PROMPT_V1 = "你是校园学习助理，用中文简洁回答学生的课程、作业、GPA、学习计划问题。"


def _tool_catalog() -> str:
    from .tools import list_tools  # 局部导入避免循环依赖

    lines = []
    for t in list_tools():
        params = ", ".join(
            f"{p['name']}:{p.get('type', 'str')}{'*' if p.get('required') else ''}"
            for p in t["params"]
        )
        lines.append(f"- {t['name']}：{t['description']}；参数：{params or '无'}")
    return "\n".join(lines)


# 用 __TOOLS__ / __MAX__ 占位符而非 str.format，避免 JSON 花括号转义问题
_SYSTEM_PROMPT_V2_TEMPLATE = """你是校园学习助理 Agent。你可以调用以下工具：
__TOOLS__

输出协议（必须严格遵守）：
1. 每一轮只输出一个 JSON 对象，禁止输出 JSON 之外的任何文字：
   {"action":"tool","tool":"工具名","args":{...},"thought":"为什么调用它"}
   或
   {"action":"answer","answer":"给学生的最终中文回答","thought":"为什么不再调用工具"}
2. 拿到工具观察结果后：信息足够就 answer；不够就继续调用其他工具（最多 __MAX__ 轮）。
3. 工具/知识库查不到的内容，必须诚实回答“未找到/不确定”，禁止编造。

示例：
用户：查一下 C001 的课
输出：{"action":"tool","tool":"query_course","args":{"course_id":"C001"},"thought":"用户询问课程信息"}"""

SYSTEM_PROMPT_V2 = _SYSTEM_PROMPT_V2_TEMPLATE.replace("__TOOLS__", _tool_catalog()).replace(
    "__MAX__", str(MAX_ITERATIONS)
)

PROMPT_VERSIONS = {"v1": SYSTEM_PROMPT_V1, "v2": SYSTEM_PROMPT_V2}
CURRENT_SYSTEM_PROMPT = SYSTEM_PROMPT_V2  # 当前生效版本
PROMPT_VERSION = "v2"

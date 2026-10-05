"""知识点/课程资料检索（课程资料问答）：关键词打分检索内置知识库。

轻量做法（面试点）：中文没有分词器，这里用“主题双向包含 + 关键词命中 + 逐字弱匹配”
三层打分，零依赖可解释；生产可换 TF-IDF 或向量检索，接口不变。

2026-10 新增 add_knowledge：知识库运行时可扩充（写操作，走确认门）——
演示里当场“教”Agent 一条新知识并立即可查，避免“知识库永远只有内置几条”的死板观感。
"""
from __future__ import annotations

import json

from ..config import DATA_DIR
from .base import tool

with open(DATA_DIR / "knowledge.json", encoding="utf-8") as f:
    KNOWLEDGE = json.load(f)


def _bigrams(text: str) -> set[str]:
    """中文没有分词器：取相邻 2 字片段集合，用于“主题片段相交”的强信号判断。"""
    return {text[i:i + 2] for i in range(len(text) - 1) if len(text[i:i + 2]) == 2}


@tool("knowledge_search", "在课程知识点库中检索概念/资料（课程资料问答）",
      {"query": {"type": "str", "required": True, "desc": "要检索的知识点关键词或问题"},
       "course_id": {"type": "str", "required": False, "default": "",
                     "desc": "限定课程编号，如 C001"}})
def knowledge_search(query: str, course_id: str = "") -> dict:
    q = query.strip()
    if not q:
        raise ValueError("检索词不能为空")
    scored = []
    for k in KNOWLEDGE:
        if course_id and k["course_id"] != course_id.strip().upper():
            continue
        # 强信号门槛：主题双向包含 / 关键词命中查询 / 2 字片段相交。
        # 没有强信号一律视为未命中 —— 逐字弱匹配只做排序，不能单独命中，
        # 否则“区块链”会被“链地址法”误召回，Agent 就可能答非所问。
        # 2026-10 新增 2-gram 通道：覆盖运行时新增、未配 keywords 的条目
        #（如演示里当场教的“B+树与数据库索引”，查“B+树是什么”也能命中）；
        # 只认 ≥2 字片段相交，单字不算——保住“区块链”类拒答边界。
        topic_grams = _bigrams(k["topic"])
        kw_grams: set[str] = set()
        for kw in k["keywords"]:
            kw_grams |= _bigrams(kw)
        q_grams = _bigrams(q)
        strong = (q in k["topic"] or k["topic"] in q
                  or any(kw in q for kw in k["keywords"])
                  or bool(q_grams & (topic_grams | kw_grams)))
        if not strong:
            continue
        score = 0.0
        if q in k["topic"] or k["topic"] in q:
            score += 3.0
        for kw in k["keywords"]:
            if kw in q:
                score += 2.0
        for ch in set(q):  # 逐字弱匹配：仅用于同主题条目之间的排序
            if ch in k["topic"] or any(ch in kw for kw in k["keywords"]):
                score += 0.5
        if score > 0:
            scored.append((score, k))
    scored.sort(key=lambda x: -x[0])
    hits = [{"course_id": k["course_id"], "topic": k["topic"], "content": k["content"]}
            for _, k in scored[:2]]
    return {"found": bool(hits), "hits": hits}


@tool("add_knowledge", "新增一条课程知识点（写库：追加进知识库并持久化，需用户确认）",
      {"topic": {"type": "str", "required": True, "desc": "知识点主题，如：B+树与数据库索引"},
       "content": {"type": "str", "required": True, "desc": "知识内容，建议 1-3 句话，500 字以内"},
       "course_id": {"type": "str", "required": False, "default": "",
                     "desc": "关联课程编号，如 C004；留空为通用知识"},
       "keywords": {"type": "list", "required": False, "default": [],
                    "desc": "触发关键词列表，如 [\"索引\",\"B+树\"]"}},
      confirm=True)  # 确认门：写操作，参数回显经用户确认后才落库
def add_knowledge(topic: str, content: str, course_id: str = "",
                  keywords: list | None = None) -> dict:
    topic = str(topic).strip()
    content = str(content).strip()
    if not topic or not content:
        raise ValueError("topic 与 content 不能为空")
    if len(content) > 500:
        raise ValueError("content 需在 500 字以内")
    if any(k["topic"] == topic for k in KNOWLEDGE):
        raise ValueError(f"知识点已存在：{topic}")
    kws = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    course_id = course_id.strip().upper() if course_id else ""
    KNOWLEDGE.append({"course_id": course_id, "topic": topic,
                      "keywords": kws, "content": content})
    # 持久化：内存与 JSON 同步生效，重启不丢（演示里当场“教”Agent 一条新知识）
    with open(DATA_DIR / "knowledge.json", "w", encoding="utf-8") as f:
        json.dump(KNOWLEDGE, f, ensure_ascii=False, indent=2)
    return {"topic": topic, "course_id": course_id or "无",
            "keywords": kws, "total": len(KNOWLEDGE)}

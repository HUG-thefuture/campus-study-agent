"""决策层（Planner）：把“下一步做什么”抽象成统一接口，是 mock/LLM 双模式的关键。

- MockPlanner：关键词/正则规则路由 —— 无 LLM、无网络也能完整走通 ReAct 循环，
  30 条评测离线全绿靠的就是它（可复现、零成本、可解释）；
- LLMPlanner：把工具目录 + JSON Action 协议写进 system prompt（prompts.py v2），
  要求模型每轮只输出一个 JSON；解析失败自动重试一次，再失败则诚实拒答（不编造）。

面试点（工具路由怎么做）：
规则路由适合高频固定问法（演示/回归测试友好）；LLM 路由泛化到没见过的表达。
生产的稳妥做法是 LLM 优先 + 规则兜底 + 工具白名单校验，而不是二选一。
"""
from __future__ import annotations

import json
import re

from ..config import MAX_ITERATIONS
from ..llm import LLMBackend, MockLLM
from ..prompts import CURRENT_SYSTEM_PROMPT
from ..tools import REGISTRY
from ..tools.assignment import current_week
from ..tools.course import COURSES

COURSE_CODE_RE = re.compile(r"C\d{3}", re.IGNORECASE)
DAY_WORDS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}


def _extract_course_id(query: str, slots: dict) -> str:
    """课程抽取优先级：显式编号 > 课程名/别名 > 上下文指代（它/这门课）。"""
    m = COURSE_CODE_RE.search(query)
    if m:
        return m.group(0).upper()
    for cid, c in COURSES.items():
        if c["name"] in query or any(a and a in query for a in c.get("aliases", [])):
            return cid
    if re.search(r"它|这门课|那门课|该课", query) and slots.get("last_course_id"):
        return str(slots["last_course_id"])  # 多轮指代消解
    return ""


def _extract_day(query: str) -> str:
    return next((d for d in DAY_WORDS if d in query), "")


class MockPlanner:
    """规则式决策器：第一次观察前按规则路由工具；有观察后汇总/澄清。"""

    def __init__(self, llm: MockLLM | None = None):
        self.llm = llm or MockLLM()

    def plan(self, state: dict) -> dict:
        observations = state.get("observations") or []
        if observations:
            last = observations[-1]
            if last.get("needs_confirm"):
                # 确认门命中：本轮不落库，先向用户回显参数请求确认。
                # 2026-10 泛化：不写死 create_task 文案——按工具给出可读回显，
                # 新写工具（如 add_knowledge）接入确认门零改动。
                tool_name = last.get("tool", "")
                pretty = {"create_task": "创建任务", "add_knowledge": "新增知识点"}.get(
                    tool_name, f"执行写操作 {tool_name}")
                args = last.get("args") or {}
                if tool_name == "create_task":
                    detail = f"即将{pretty}“{args.get('title', '')}”"
                else:
                    kv = "；".join(f"{k}={str(v)[:40]}" for k, v in args.items() if v)
                    detail = f"即将{pretty}：{kv}" if kv else f"即将{pretty}"
                return {"action": "answer",
                        "answer": (f"{detail}。这会写入数据，"
                                   f"回复“确认”后我才会执行；不想执行请直接说取消。"),
                        "thought": "写操作待确认：先请求用户确认，绝不擅自写库"}
            if last["ok"]:
                # 组合意图：查询中还有未被满足的诉求 → 继续调工具而不是一轮就收束。
                # 2026-10 新增：让“查作业 + 看课程信息”这类复合问题真正走多步 ReAct
                #（多轮循环是 Agent 与一问一答 chat 的核心区别）。
                q = state["query"]
                slots = state.get("slots") or {}
                done = {o.get("tool") for o in observations}
                cid = _extract_course_id(q, slots) or slots.get("last_course_id")
                if ("query_course" not in done and cid
                        and re.search(r"老师|谁上|什么课|上课|课表|学分|介绍", q)):
                    return {"action": "tool", "tool": "query_course",
                            "args": {"course_id": cid},
                            "thought": "组合意图：还包含课程信息诉求，继续查课程"}
                if len(observations) > 1:
                    parts = [self._format(o) for o in observations if o.get("ok")]
                    return {"action": "answer", "answer": "\n".join(parts),
                            "thought": "多步工具执行完成，汇总各步观察结果组织回答"}
                return {"action": "answer", "answer": self._format(last),
                        "thought": "工具执行成功，基于观察结果组织回答"}
            return {"action": "answer",
                    "answer": f"这个操作没有成功：{last['error']}。你可以补充信息后再试一次。",
                    "thought": "工具失败，向用户澄清原因而不是编造结果"}
        return self._route(state["query"], state.get("slots") or {})

    # ---- 规则路由：按“意图特异性”从高到低匹配，先命中先服务 ----
    def _route(self, query: str, slots: dict) -> dict:
        low = query.lower()

        # 待确认写操作优先处理：确认语 → 重放首轮参数；取消语 → 丢弃。
        # 确认词用 exact 匹配（不用 startswith），防止“确认一下C101的冲突”被误劫持。
        pending = slots.get("pending") if isinstance(slots, dict) else None
        if isinstance(pending, dict) and pending.get("tool"):
            q = query.strip().lower()
            if q in ("确认", "确认创建", "确认执行", "确定", "同意", "可以",
                     "好的", "好", "嗯", "ok", "yes"):
                return {"action": "tool", "tool": pending["tool"], "args": {},
                        "thought": "命中待确认写操作的确认语，按首轮参数重放"}
            if q.startswith(("取消", "算了", "不要了", "先不", "不创建")):
                title = (pending.get("args") or {}).get("title", "")
                return {"action": "discard_pending",
                        "answer": f"好的，已取消创建“{title}”。需要时再告诉我。",
                        "thought": "命中取消语，丢弃待确认写操作"}

        if "gpa" in low or "绩点" in query:
            return {"action": "tool", "tool": "calculate_gpa", "args": {},
                    "thought": "命中 GPA/绩点规则"}

        # 任务创建：有写库副作用，只认"句首创建动宾"的明确句式。
        # 2026-09 修复：旧宽松正则曾把"我想建一个学习小组，一起完成任务"误判成
        # 建任务并真实写库。两段式处理：
        #   a) 紧凑句式（创建任务 / 创建任务：xxx）直接命中；
        #   b) "建一个XX的任务"句式命中后，对抽出的标题做净化——含逗号/连接词/
        #      动宾残句的不算创建（那才是"学习小组，一起完成任务"的误报来源）。
        m = re.match(
            r"^\s*(?:帮我|请|给我|我要|我想)?(?:创建|新建|添加|建)"
            r"(?:一个|一条|个)?(?:学习)?任务(?:[：:，,]?\s*(.*))?$",
            query)
        if m:
            return {"action": "tool", "tool": "create_task",
                    "args": {"title": (m.group(1) or "").strip()},
                    "thought": "命中任务创建句式（句首创建意图），抽取标题"}
        m2 = re.match(
            r"^\s*(?:帮我|请|给我|我要|我想)?(?:创建|新建|添加|建)"
            r"(?:一个|一条|个)?(.{1,20}?)(?:的)?任务$",
            query)
        if m2:
            title = m2.group(1).strip()
            if not re.search(r"[，,。；;！!？?]|一起|共同|然后|顺便", title):
                return {"action": "tool", "tool": "create_task",
                        "args": {"title": title},
                        "thought": "命中“建XX任务”句式且标题干净，抽取标题"}

        # 冲突检查：只有当能抽出课程或时段时才是"选课冲突"意图；
        # 否则（如"哈希冲突是什么"）落到知识问答，避免关键词遮蔽（2026-09 修复）。
        conflict_cid = _extract_course_id(query, slots)
        conflict_day = _extract_day(query)
        if "冲突" in query and (conflict_cid or conflict_day):
            return {"action": "tool", "tool": "check_conflict",
                    "args": {"course_id": conflict_cid, "time_slot": conflict_day},
                    "thought": "命中选课冲突检查规则，抽取课程与时段"}

        # 用小写形式匹配英文关键词（due/deadline），否则 "DUE date" 会漏路由
        if any(k in low for k in ("作业", "截止", "要交", "due", "deadline")):
            args: dict = {}
            cid = _extract_course_id(query, slots)
            if cid:
                args["course_id"] = cid
            if re.search(r"这周|本周|这星期|这礼拜", query):
                args["this_week_only"] = True
            m = re.search(r"第\s*(\d+)\s*周", query)
            if m:
                args["week"] = int(m.group(1))
            elif "下周" in query:
                args["week"] = current_week() + 1
            return {"action": "tool", "tool": "list_assignments", "args": args,
                    "thought": "命中作业/截止日期规则，抽取课程与周次过滤"}

        if any(k in query for k in ("计划", "规划", "怎么学", "怎么安排")):
            days = 7
            m = re.search(r"([一二两三四五六七\d]+)\s*天", query)
            if m:
                g = m.group(1)
                days = int(g) if g.isdigit() else CN_NUM.get(g, 7)
            return {"action": "tool", "tool": "generate_study_plan", "args": {"days": days},
                    "thought": "命中学习计划规则，抽取计划天数"}

        # 新增知识点：写库操作（confirm=True 走确认门）；句式约定“主题。内容”。
        # 必须排在知识检索规则之前——本句式含“知识点”字样，否则会被检索规则抢走。
        m = re.match(
            r"^\s*(?:帮我|请|给我|我要|我想)?(?:添加|新增|录入|记住)一?条?\s*"
            r"(?:知识点|课程知识|笔记)(?:[：:]\s*(.+))?$", query)
        if m:
            rest = (m.group(1) or "").strip()
            topic, _, content = rest.partition("。")
            # 参数不全也照常路由：由工具参数校验走“缺少必填参数”澄清路径
            # （与“创建任务”空标题的既有约定一致），绝不漏到检索规则
            return {"action": "tool", "tool": "add_knowledge",
                    "args": {"topic": topic.strip(),
                             "content": content.strip().rstrip("。")},
                    "thought": "命中新增知识点句式（写操作，将走确认门）"}

        if any(k in query for k in ("是什么", "什么意思", "概念", "知识点",
                                    "讲讲", "区别", "怎么理解", "原理", "资料",
                                    "遍历", "哈希", "负载因子", "银行家", "握手",
                                    "范式", "怎么解决", "怎么实现", "适用场景", "有什么用",
                                    "哪个好", "怎么办", "怎么写", "复习")):
            return {"action": "tool", "tool": "knowledge_search", "args": {"query": query},
                    "thought": "命中知识点/资料问答规则"}

        cid = _extract_course_id(query, slots)
        if cid or any(k in query for k in ("课程", "课表", "上课", "老师", "学分",
                                           "谁上", "什么课", "哪门课")):
            return {"action": "tool", "tool": "query_course", "args": {"course_id": cid},
                    "thought": "命中课程查询规则，抽取课程编号/名称"}

        # 兜底：不调用工具、不编造，诚实说明能力边界
        return {"action": "answer",
                "answer": self.llm.chat([{"role": "user", "content": query}]),
                "thought": "未命中任何工具规则，直接诚实回答"}

    # ---- mock 汇总器：把结构化观察结果格式化成自然语言（不经过 LLM，确定性输出） ----
    def _format(self, result: dict) -> str:
        name, data = result["tool"], result["data"]
        if data is None:
            return f"工具 {name} 没有返回数据：{result['error']}"
        if name == "list_assignments":
            items = data["assignments"]
            if not items:
                return "这段时间没有查询到作业。"
            lines = [f"- [{a['course_id']}] {a['title']}，截止 {a['due']}（{a['status']}）"
                     for a in items]
            return f"共 {data['count']} 条作业（当前为第{data['week']}教学周）：\n" + "\n".join(lines)
        if name == "query_course":
            if not data.get("found"):
                return data.get("message", "未找到该课程。")
            return (f"{data['course_id']} {data['name']}：{data['credits']} 学分，"
                    f"教师 {data['teacher']}，时间 {'；'.join(data['schedule'])}，"
                    f"教室 {data['classroom']}。")
        if name == "check_conflict":
            if not data.get("found"):
                return data.get("message", "未找到该课程，无法检查冲突。")
            if data["has_conflict"]:
                detail = "、".join(f"{c['name']}（{c['slot']}）" for c in data["conflicts"])
                return (f"有冲突：{data['course_id']} {data['name']} 与已选课程在 "
                        f"{detail} 时间重叠，不建议同时选。")
            return f"无冲突：{data['course_id']} {data['name']} 与已选课程时间不重叠。"
        if name == "calculate_gpa":
            return f"按 4.0 制学分加权计算，你的 GPA = {data['gpa']}（{data['formula']}）。"
        if name == "generate_study_plan":
            lines = [f"{d['date']}（{d['weekday']}）：" + "；".join(d["items"])
                     for d in data["plan"]]
            return f"已生成未来 {data['days']} 天的学习计划：\n" + "\n".join(lines)
        if name == "create_task":
            return (f"已创建任务 #{data['task_id']}：{data['title']}"
                    f"（截止：{data['due']}，关联课程：{data['course_id']}）")
        if name == "add_knowledge":
            return (f"已新增知识点【{data['topic']}】（关联课程：{data['course_id']}，"
                    f"知识库现有 {data['total']} 条），现在可以直接问我了。")
        if name == "knowledge_search":
            if not data["found"]:
                return ("知识库里没有收录这个知识点，建议查阅教材或询问老师。"
                        "（我不确定的内容不会编造）")
            h = data["hits"][0]
            extra = f"\n相关主题：{data['hits'][1]['topic']}" if len(data["hits"]) > 1 else ""
            return f"【{h['topic']}】{h['content']}{extra}"
        return json.dumps(data, ensure_ascii=False)


class LLMPlanner:
    """LLM 决策器：JSON Action 协议 + 解析失败重试一次 + 兜底拒答。"""

    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def plan(self, state: dict) -> dict:
        messages: list[dict] = [{"role": "system", "content": CURRENT_SYSTEM_PROMPT}]
        # 2026-09 修复：槽位曾只在 mock 路由里生效，LLM 模式收不到 —— 指代消解
        # 在真实 LLM 下失效且与"槽位持久化"卖点矛盾。现把槽位显式注入提示词。
        slots = state.get("slots") or {}
        if slots:
            messages.append({
                "role": "system",
                "content": ("当前会话已知上下文（槽位，跨轮持久化）："
                            + json.dumps(slots, ensure_ascii=False)
                            + "。用户说“它/这门课/该课”时优先引用这些值。")})
        for h in state.get("history") or []:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": state["query"]})
        for o in state.get("observations") or []:
            messages.append({"role": "user",
                             "content": "工具观察结果：" + json.dumps(o, ensure_ascii=False)})
        for _ in range(2):  # 最多重试一次：第一次输出不合法时明确指出协议
            raw = self.llm.chat(messages)
            action = self._parse(raw)
            if action is not None:
                return action
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user",
                             "content": "上一条不是合法的 JSON Action，请严格按协议只输出一个 JSON。"})
        return {"action": "answer",
                "answer": "抱歉，我暂时没能理解这个请求，请换个说法再试一次。",
                "thought": "LLM 输出两次不可解析，兜底拒答"}

    def _parse(self, raw: str) -> dict | None:
        """健壮 JSON 解析：剥代码围栏、截取首尾花括号；工具名必须在注册表内（白名单）。"""
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict) or obj.get("action") not in ("tool", "answer"):
            return None
        if obj["action"] == "tool":
            if obj.get("tool") not in REGISTRY:  # 白名单：防幻觉调用不存在的工具
                return None
            obj.setdefault("args", {})
        obj.setdefault("thought", "")
        obj.setdefault("answer", "")
        return obj

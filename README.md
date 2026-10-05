# 校园学习助理 Agent（Campus Study Agent）

![CI](https://github.com/HUG-thefuture/campus-study-agent/actions/workflows/ci.yml/badge.svg)

对应简历项目一：**2026.07–2026.08 个人项目：校园学习助理 Agent 独立开发**。
单 Agent 应用：FastAPI 提供 OpenAPI 接口，Agent 核心是**轻量自实现的 ReAct 式工具调用循环**（Plan → Tool → Observe → Answer），LLM 可插拔（mock / OpenAI 兼容），每次工具调用的输入、输出与异常都落 SQLite，可回放。

- 默认 `LLM_PROVIDER=mock`：**无密钥、无网络**即可完整运行 Agent 循环与全部测试；
- 切 `openai_compatible` 后接入任意 OpenAI 兼容 API（OpenAI / DashScope / Kimi），代码零改动；
- 依赖只有 5 个包（fastapi / uvicorn / pydantic / httpx / pytest），**不引入 LangChain / LangGraph 等重型框架**（替换方式见下文）。

## 目录结构

```
project/
├── app/
│   ├── main.py           # FastAPI 入口：/agent/chat、/agent/tools、/traces、/tasks
│   ├── agent/
│   │   ├── core.py       # ReAct 循环 + AgentRunner 接口（换框架的接缝）
│   │   ├── planners.py   # 决策器：MockPlanner(规则路由) / LLMPlanner(JSON协议)
│   │   └── context.py    # 多轮会话：滑动窗口历史 + 槽位（指代消解）
│   ├── tools/
│   │   ├── base.py       # 工具注册表 + 参数校验 + 统一执行/异常记录
│   │   ├── course.py     # 课程查询、选课冲突检查
│   │   ├── assignment.py # 作业/截止日期查询、学习任务创建
│   │   ├── study.py      # GPA 计算、学习计划生成
│   │   └── knowledge.py  # 知识点检索（课程资料问答）
│   ├── llm.py            # 可插拔 LLM：chat(messages)->str，mock / openai_compatible
│   ├── prompts.py        # 提示词版本 v1/v2（对比讲解用）
│   ├── store.py          # SQLite：traces / tasks / sessions
│   ├── config.py         # .env 加载与配置（不依赖 python-dotenv）
│   └── data/             # 内置模拟校园数据（courses/assignments/knowledge/student）
├── tests/                # 85 个用例：工具单测 / Agent 循环 / planner 守卫 / LLM / API 集成 / 30 条评测 / 写操作确认门 / 组合意图与运行时知识库
├── requirements.txt      # 锁定 5 个直接依赖的版本
├── .env.example          # 配置模板（占位符，无任何真实密钥）
├── Dockerfile            # 容器启动说明（见文末验证记录）
└── README.md
```

## 架构图

```
用户 ──POST /agent/chat {"query":...}──▶ FastAPI（app/main.py）
                                          │
                                          ▼
                    ┌─────────── ReActRunner（app/agent/core.py）───────────┐
                    │  循环（≤ MAX_ITERATIONS=5，带停止条件）：              │
                    │    Plan    ─ Planner 决策下一步                       │
                    │                ├ mock: 规则路由（关键词/正则→工具+参数）│
                    │                └ llm : system 提示词要求输出 JSON Action│
                    │    Tool    ─ tools/ 注册表执行（参数校验→调用→计时）    │
                    │    Observe ─ 结构化结果喂回下一轮决策                  │
                    │    Answer  ─ 汇总回答 / 失败澄清 / 知识库外拒答        │
                    └────────┬──────────────────────┬───────────────────────┘
                             │                      │
              ┌──────────────▼─────────┐   ┌────────▼────────────────────┐
              │ llm.py（可插拔后端）    │   │ tools/* 业务工具（8 个）      │
              │  mock │ openai_compatible│  │ 数据：app/data/*.json（模拟）│
              └────────────────────────┘   └────────┬────────────────────┘
                                                    │ 每步落库
                                        ┌───────────▼──────────────┐
                                        │ store.py（SQLite）        │
                                        │ traces/tasks/sessions     │
                                        └──────────────────────────┘
```

## 复现步骤（Windows / Git Bash，Python 3.11）

```bash
cd project
"C:/Users/<你>/AppData/Local/Programs/Python/Python311/python.exe" -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pytest -q          # 85 passed（无密钥、无网络；2026-10 确认门 + 反死板增量）
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8010
```

演示（Git Bash 下中文请求体建议用 UTF-8 文件发送，避免控制台 GBK 编码问题）：

```bash
printf '{"query":"我这周有什么作业"}' > body.json   # 或用 python 写 UTF-8 文件
curl -s -X POST http://127.0.0.1:8010/agent/chat \
     -H "Content-Type: application/json; charset=utf-8" --data-binary @body.json
```

浏览器打开 `http://127.0.0.1:8010/docs` 可看自动生成的 OpenAPI 文档。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/agent/chat` | 主链路：`{"query": "...", "session_id": "可选", "confirm": false}` → 答案 + 工具调用轨迹 `steps`；`confirm=true` 仅用于确认已回显的写操作（服务端按首轮参数原样重放落库） |
| GET | `/agent/tools` | 工具清单（名称/描述/参数 schema），与提示词工具目录同源 |
| GET | `/traces/{session_id}?only_fail=true` | 轨迹回放，可只筛失败记录（简历项目二的数据面） |
| GET | `/tasks` | 已创建的学习任务列表 |
| GET | `/health`、`/` | 健康检查 / 项目信息 |

## 学习注解 1：ReAct 循环是怎么跑的

以「我这周有什么作业」为例（`app/agent/core.py`）：

```
第1轮 Plan : MockPlanner._route("我这周有什么作业")
             → 命中"作业"规则，抽取 this_week_only=True
             → {"action":"tool","tool":"list_assignments","args":{"this_week_only":true}}
第1轮 Tool : execute_tool("list_assignments", {...})
             → validate_args 校验 → 真正执行 → 计时 → 结构化结果
第1轮 Observe: 结果追加进 observations，同时落 SQLite traces
第2轮 Plan : planner 看到 observations 非空且成功
             → {"action":"answer","answer":"共 4 条作业（当前为第2教学周）：…"}
循环结束：写入会话历史 + assistant 轨迹，返回 AgentResult{answer, steps, iterations}
```

三个关键机制：
1. **决策与执行解耦**：`Planner` 只负责"下一步做什么"，`execute_tool` 只负责"安全地执行"。同一套循环代码，mock 模式用规则、真实模式用 LLM，切换不改一行循环逻辑；
2. **停止条件**：`MAX_ITERATIONS=5`，决策器一直要求调工具时兜底收尾，绝不死循环（`tests/test_agent.py::test_max_iterations_guard`）；
3. **失败路径**：工具失败不抛异常，而是作为观察结果喂回去 → 决策器走"澄清重试 / 诚实拒答"，绝不编造（`tests/test_agent.py::test_failed_tool_leads_to_clarification`）。

## 学习注解 2：如何加一个新工具（三步）

1. 在 `app/tools/` 某模块里写函数并挂装饰器：
   ```python
   from .base import tool

   @tool("list_exams", "查询考试安排，可按课程过滤",
         {"course_id": {"type": "str", "required": False, "default": "", "desc": "课程编号"}})
   def list_exams(course_id: str = "") -> dict:
       return {"count": 2, "exams": [...]}
   ```
2. 在 `app/tools/__init__.py` 里 `import` 该模块（触发注册）；
3. 让决策器认识它：mock 模式在 `planners.py::_route` 加一条关键词规则；LLM 模式**什么都不用改**——工具目录由 `list_tools()` 自动注入提示词。

参数校验、异常记录、轨迹落库、`/agent/tools` 文档全部自动生效。

## 学习注解 3：如何替换成 LangGraph

核心循环被 `AgentRunner` 接口隔离（`app/agent/core.py`）。迁移时：

```python
class LangGraphRunner(AgentRunner):
    def __init__(self):
        g = StateGraph(State)                       # State{query, observations, answer}
        g.add_node("decide", decide)                # 内部复用 LLMPlanner.plan / MockPlanner
        g.add_node("tools", call_tools)             # 内部复用 execute_tool
        g.add_node("answer", make_answer)
        g.set_entry_point("decide")
        g.add_conditional_edges("decide", route, {"tool": "tools", "end": "answer"})
        g.add_edge("tools", "decide")
        self.app = g.compile()

    def run(self, query, session_id):               # 实现同一接口，API 层零改动
        ...
```

工具层（`tools/base.py`）、存储层（`store.py`）、提示词（`prompts.py`）都可原样复用——这正是把"框架能力"收敛在接口后面的意义。

## 设计取舍（面试讲解用）

1. **为什么不用 LangChain/LangGraph**：本场景是"单 Agent + 8 工具"的线性循环，用不上状态图/检查点/多 Agent 编排；引入会把依赖树放大几十个包、调试变读黑盒源码。自实现约 60 行循环 + 接口隔离，可控、可测、可回放，且保留了 LangGraph 替换路径。
2. **工具路由：规则 vs LLM**：mock 规则路由零成本、可解释、30 条评测离线可复现；LLM 路由泛化强但要处理 JSON 解析失败。生产稳妥做法是 LLM 优先 + 规则兜底 + 工具白名单校验（`LLMPlanner._parse` 只接受注册表内的工具名）。
3. **上下文管理**：历史用滑动窗口（最近 6 条）控 token 成本；"上一轮课程"这类状态不塞进对话文本，而是抽成槽位（`last_course_id`）落 SQLite，用结构化指代消解代替"把全部历史丢给模型"。
4. **轨迹必须显式落库**：每次工具调用的输入/输出/异常写 SQLite 而不是只打日志——它是评测、回放器（项目二）和"定位模型选错工具还是工具出错"的数据基础。
5. **写操作确认门**：模型只能"提议"写库——`ToolSpec.confirm_required` 声明 + `execute_tool` 在参数校验通过后拦截并回显清洗参数，用户确认（`"confirm": true`）后按服务端留存的首轮参数原样重放落库，不接受模型本轮重新生成的参数；confirm 标志不能绕过回显直通，取消语即丢弃待确认状态。这是"模型不能擅自写库"的实现级答案（2026-10 新增，见文末验证记录）。

## 面试深挖点（5 条）

1. **Agent 和普通 Chat 的区别**：Agent 有"决策→行动→观察"循环并用工具改变世界状态；Chat 只生成文本。本项目里同一个 LLM 后端，套上 Planner+工具循环才是 Agent。
2. **结构化输出为什么是前提**：工具参数必须可靠可解析。本项目用"JSON Action 协议 + 强制 system 约束 + 解析失败重试一次 + 白名单校验"四层保证，非法输出最终走诚实拒答。
3. **工具失败怎么办**：参数校验失败/业务异常 → `execute_tool` 捕获并结构化记录 → Agent 观察到失败向用户澄清 → 仍无法完成则拒答。演示：`创建任务`（空标题）→ "这个操作没有成功：缺少必填参数：title…"。
4. **为什么记录轨迹 / 怎么定位问题**：traces 表按会话存每步 tool_name/args/ok/err，`GET /traces/{sid}?only_fail=true` 一键筛失败；能区分"路由选错工具"（args 对但语义错）和"工具本身出错"（ok=False）。
5. **多轮会话怎么做**：滑动窗口控成本 + 槽位化状态（指代消解"那它的老师是谁"）+ 槽位持久化重启不丢；讨论点：什么时候该上摘要压缩、什么时候该上向量记忆。

## 评测（30 条，`tests/test_eval.py`）

| 类别 | 数量 | 示例 | 检查点 |
|---|---|---|---|
| 正常 | 12 | "查询C001的课程信息"、"我这周有什么作业" | 工具选择正确、参数正确、成功 |
| 边界 | 8 | "创建任务"（空标题）、"查询C999"、"生成40天计划" | 参数校验失败被记录 / 未找到时诚实告知 |
| 异常 | 10 | "区块链是什么"、"今天天气怎么样" | 知识库外拒答 / 不乱调工具不编造 |

运行：`.venv/Scripts/python.exe -m pytest tests/test_eval.py -v`

## 验证记录（2026-09-07，实际执行）

环境：Windows 10.0.26200 / Git Bash / Python 3.11（venv）。

```text
$ "C:/Users/<你>/AppData/Local/Programs/Python/Python311/python.exe" -m venv .venv
$ .venv/Scripts/python.exe -m pip install -r requirements.txt
  （安装结果：fastapi==0.115.6 uvicorn==0.34.0 pydantic==2.10.4 httpx==0.28.1 pytest==8.3.4，
    传递依赖 starlette==0.41.3 pydantic_core==2.27.2 anyio==4.15.1 h11==0.16.0）
$ .venv/Scripts/python.exe -m pytest -q
  74 passed, 1 warning in 1.14s        # 无密钥、无网络（LLM_PROVIDER=mock；2026-09 复验，
                                       # 含新增 planner 守卫 6 例，历史记录为 68 passed）
$ .venv/Scripts/python.exe -m uvicorn app.main:app --port 8010
  INFO:     Application startup complete.
$ curl -s -X POST http://127.0.0.1:8010/agent/chat -H "Content-Type: application/json; charset=utf-8" --data-binary @body_demo.json
```

返回（节选，完整包含工具调用轨迹 steps）：

```json
{
  "session_id": "s-a59e4fdc",
  "answer": "共 4 条作业（当前为第2教学周）：\n- [C001] 实验三：二叉树遍历实现，截止 2026-09-08（还剩1天）\n- [C006] 习题册第3章：极限与连续，截止 2026-09-09（还剩2天）\n- [C004] ER图设计作业，截止 2026-09-10（还剩3天）\n- [C003] Socket编程小作业，截止 2026-09-11（还剩4天）",
  "iterations": 2,
  "steps": [
    {"iteration": 1, "action": "tool", "tool": "list_assignments",
     "args": {"this_week_only": true}, "ok": true,
     "observation": {"week": 2, "count": 4, "assignments": ["A101 实验三：二叉树遍历实现 …"]}},
    {"iteration": 2, "action": "answer"}
  ]
}
```

```text
$ curl http://127.0.0.1:8010/traces/s-a59e4fdc
  count: 3 | roles: ['user', 'tool', 'assistant']      # 轨迹已落 SQLite
$ curl "http://127.0.0.1:8010/traces/s-a59e4fdc?only_fail=true"
  count: 0                                             # only_fail 过滤可用
$ kill $SERVER_PID && curl http://127.0.0.1:8010/health  → server stopped OK
```

备注：
- 本机 8000 端口被其他服务占用（bind 报 Errno 10048），演示改用 8010；
- Git Bash 终端发送中文 JSON 需走 UTF-8 文件（`--data-binary @body.json`），否则服务端按 GBK 收到会报 body 解析错误；
- Dockerfile 已提供（`docker build -t study-agent . && docker run -p 8000:8000 study-agent`），但本机未安装 Docker，未实际执行镜像构建验证。

## 验证记录（2026-10-04 第二批增量：反"死板"——数据保鲜 + 多步组合 + 运行时知识库）

针对"演示功能死板"的三处根因各落一招（pytest 85 passed）：

1. **种子数据保鲜**：作业 12→32 条铺满 14 教学周（2026-08-31 ~ 12-02），知识点 8→20 条，
   内置成绩单 4→8 门。此前截止日期硬编码在 9 月，日历一过"这周作业"就空转、
   学习计划没有未来任务；新增 `scripts/refresh_seed.py`
   （`--check` 校验周次一致性、`--shift N` 过季时整体平移）。
2. **多步组合意图**：复合问题（如"查一下C002的作业有哪些，再介绍下这门课"）不再一轮就收束——
   规则路由识别未满足的诉求继续调工具（list_assignments → query_course，3 轮迭代），
   最终回答汇总各步观察结果。多轮循环是 Agent 与一问一答 chat 的核心区别。
3. **运行时知识库 add_knowledge**（第 8 个工具，写操作走确认门）：
   演示里当场"教"Agent 一条新知识（回显 topic/content → 确认 → 持久化到 knowledge.json），
   立即可查；检索强信号新增"2 字片段相交"通道，未配 keywords 的新条目也能命中，
   单字不算——"区块链是什么"的拒答边界保持不变。

```text
$ python -m pytest -q
  85 passed, 1 warning in 1.3s
$ python scripts/refresh_seed.py --check
  seed-check: CONSISTENT (0 mismatched)
$ curl -d '{"query":"我这周有什么作业"}'          → 第 5 教学周 4 条（本周实验/文档齐活）
$ curl -d '{"query":"查一下C002的作业有哪些，再介绍下这门课"}'
  → steps: list_assignments → query_course，回答汇总作业与课程信息
$ curl -d '{"query":"添加知识点：B+树与数据库索引。……"}' → 确认门回显；确认后立即可查
```

## 验证记录（2026-10-04 增量：写操作确认门）

新增"写操作确认门"：`create_task` 声明为写库工具（`@tool(..., confirm=True)`），模型只产出意图；`execute_tool` 校验通过后拦截并回显清洗参数，用户确认后按首轮留存的参数原样重放落库；回复"取消"即丢弃待确认状态（槽位持久化，跨轮有效）。

```text
$ python -m pytest -q
  85 passed, 1 warning in 1.3s         # 无密钥、无网络（LLM_PROVIDER=mock）
                                       # 含确认门新增 6 例：tests/test_confirm.py 4 例
                                       # + test_eval 确认/取消闭环 + test_tools 确认门单测
$ curl -s -X POST .../agent/chat -d '{"query":"创建任务：背50个单词"}'
  → "即将创建任务“背50个单词”。…回复“确认”后我才会执行"     # 此时不落库
$ curl -s -X POST .../agent/chat -d '{"query":"确认","confirm":true}'
  → "已创建任务 #…"                     # 按首轮回显参数原样落库
```

## 配置说明

复制 `.env.example` 为 `.env`：默认 `LLM_PROVIDER=mock`；要接真实大模型时改为
`LLM_PROVIDER=openai_compatible` 并填入自备的 `OPENAI_API_KEY`（支持 OpenAI / DashScope /
Kimi 等兼容接口，改 `OPENAI_BASE_URL` 和 `OPENAI_MODEL` 即可）。**仓库内不含任何真实密钥。**

## 项目二：Agent 工具调用回放器（agent_replayer/）

- 零依赖标准库实现（http.server + sqlite3），直接读取 Agent 的轨迹库 `app/data/agent.db`
- 2026-09 修复：原硬编码 `../study_agent/traces.db`（不存在）导致两项目不互通，现默认读真实库并支持 `AGENT_DB_PATH` 环境变量覆盖；compose 端口映射同步修正为 8030:8000
- 启动：`python agent_replayer/app.py` → http://127.0.0.1:8031
- 功能：按会话展示每步的工具选择、参数（JSON）、结果与失败原因；`?only_fail=1` 仅筛失败记录；会话快导航
- 本机验证：与 Agent 同时启动，完成一次"缺参数→追问→成功"对话后，回放器可看到 tool_ok=0 的失败步骤及错误详情

## 提示词版本（prompts/）
- v1 单轮问答式 → v2 工具调用式（当前）；评测对比与切换方式见 prompts/版本说明.md

## Docker
- Dockerfile + docker-compose.yml 已交付（本机无 Docker，未实测镜像构建；`docker compose up -d --build`）

---

## 产品视角（面试可讲）

- **目标用户**：大学生——课程、作业、截止日、知识点散落在教务系统/群通知/讲义里。
- **解决的问题**：通用聊天机器人"只能聊不能办事"；本项目让 Agent 真正调工具完成查询与登记。
- **核心场景**：①查本周作业与截止 ②选课冲突检查 ③创建学习任务（写操作确认门）④课程知识问答 ⑤现场"教"Agent 新知识点并立即可查。
- **产品入口**：`uvicorn app.main:app --port 8010` 后浏览器打开 **/demo**——聊天式演示台，工具轨迹可展开、写操作出现确认/取消按钮。
- **成功指标（实测）**：86 例 pytest 全绿；30 条评测离线可复现；写操作 0 次未确认落库（测试锚定）。
- **未来计划**：知识检索工具接入 RAG 后端（见 course-kb-qa）；真实 LLM 模式的回归评测；移动端适配。

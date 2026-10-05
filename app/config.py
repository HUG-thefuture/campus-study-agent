"""全局配置：读取 .env 与环境变量（真实环境变量优先于 .env 文件）。

设计取舍（面试点）：不引入 python-dotenv，用约 20 行标准库实现 .env 解析，
把第三方依赖严格锁定在 fastapi/uvicorn/pydantic/httpx/pytest，
部署面小、审计容易 —— 这是“轻量自实现”路线的第一步。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent          # app/
PROJECT_DIR = BASE_DIR.parent                        # project/
DATA_DIR = BASE_DIR / "data"


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，# 开头为注释；os.environ.setdefault 保证真实环境变量优先。"""
    path = path or (PROJECT_DIR / ".env")
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ---- LLM 配置：默认 mock，保证无密钥、无网络也能完整运行（pytest 全绿的前提） ----
LLM_PROVIDER = _get("LLM_PROVIDER", "mock").lower()
OPENAI_API_KEY = _get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = _get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = _get("OPENAI_MODEL", "gpt-4o-mini")

# ---- 存储：默认 SQLite 文件放在 app/data/agent.db；测试用环境变量指向临时目录 ----
DB_PATH = _get("AGENT_DB_PATH", str(DATA_DIR / "agent.db"))

# ---- ReAct 循环上限：任何 Agent 循环必须有停止条件，防止工具互相调用失控 ----
MAX_ITERATIONS = int(_get("AGENT_MAX_ITERATIONS", "5"))

# ---- 学期起点（周一）：用于“第几周 / 这周”等相对时间计算，让演示数据可复现 ----
SEMESTER_START = _get("SEMESTER_START", "2026-08-31")

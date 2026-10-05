"""工具层基础设施：声明式注册表 + 参数校验 + 结构化执行结果。

设计取舍（面试点）：
1. 工具 = 普通 Python 函数 + 声明式参数表，不绑定任何框架。注册表 name -> ToolSpec
   同时服务于三处：mock 模式的规则路由、LLM 模式的提示词工具目录、/agent/tools 接口文档。
2. 简历原话“校验参数并记录调用输入、输出与异常”统一落在 execute_tool()：
   类型/必填校验 → 调用 → 计时 → 异常捕获，任何失败都返回结构化错误而不是向 Agent 抛异常，
   Agent 观察到失败后可走“澄清重试 / 诚实拒答”分支，而不是静默吞掉或编造结果。
3. 写操作确认门（confirm_required）：模型只能“提议”写库，execute_tool 在校验通过后、
   真正执行前拦截，先向用户回显清洗后的参数；用户确认后带首轮原参数原样重放，
   保证“确认的内容 = 执行的内容”（confirm 标志只解锁已回显的待确认操作，不能直通）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

# 参数类型只保留 JSON 可表达的一小撮，校验规则简单到可以口述 —— 这是刻意取舍
@dataclass
class ToolSpec:
    name: str
    description: str
    params: dict[str, dict]        # {参数名: {"type": str|int|float|bool|list, "required": bool, "desc": str}}
    func: Callable[..., Any]
    confirm_required: bool = False  # 写库类工具：需用户确认后才真正执行（确认门）


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, description: str, params: dict[str, dict] | None = None,
         confirm: bool = False):
    """注册装饰器：新增工具只需写函数 + 挂 @tool，框架其余部分自动感知。

    confirm=True 声明为写库类工具：执行前必须经过用户确认（确认门）。
    """
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        REGISTRY[name] = ToolSpec(name=name, description=description,
                                  params=params or {}, func=fn,
                                  confirm_required=confirm)
        return fn
    return deco


def _coerce(value: Any, typ: str) -> Any:
    """宽松类型收敛：LLM 经常把数字/列表输出成字符串，这里统一转换而不是直接报错。"""
    if typ == "int":
        if isinstance(value, bool):
            raise ValueError("需要整数")
        return int(value)
    if typ == "float":
        if isinstance(value, bool):
            raise ValueError("需要浮点数")
        return float(value)
    if typ == "str":
        if not isinstance(value, str):
            raise ValueError("需要字符串")
        return value
    if typ == "bool":
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "是")
        return bool(value)
    if typ == "list":
        if isinstance(value, str):
            return json.loads(value)     # 字符串化的 JSON 数组
        return list(value)
    return value


def validate_args(spec: ToolSpec, args: dict) -> tuple[dict | None, str | None]:
    """按声明校验：必填缺失 / 类型错误都返回可读的错误信息（给 Agent 当观察结果）。"""
    args = args or {}
    cleaned: dict[str, Any] = {}
    for pname, pdef in spec.params.items():
        if pname in args and args[pname] not in (None, ""):
            try:
                cleaned[pname] = _coerce(args[pname], pdef.get("type", "str"))
            except (TypeError, ValueError) as e:
                return None, f"参数 {pname} 类型错误：{e}（期望 {pdef.get('type', 'str')}）"
        elif pdef.get("required"):
            return None, f"缺少必填参数：{pname}（{pdef.get('desc', '')}）"
        elif "default" in pdef:
            cleaned[pname] = pdef["default"]
    return cleaned, None


def execute_tool(name: str, args: dict | None, confirmed: bool = False) -> dict:
    """统一执行入口：校验→确认门→调用→计时→异常兜底。返回结构化结果，是工具轨迹的最小单元。

    注意业务级校验失败（如空标题）用 raise ValueError 表达，
    会被这里捕获成 ok=False —— 这样“参数错→记录→澄清”是一条统一路径。
    """
    started = time.perf_counter()
    spec = REGISTRY.get(name)
    if spec is None:
        return {"tool": name, "args": args or {}, "ok": False, "data": None,
                "error": f"未知工具：{name}", "elapsed_ms": 0.0}
    cleaned, err = validate_args(spec, args or {})
    if err is not None:
        return {"tool": name, "args": args or {}, "ok": False, "data": None,
                "error": err, "elapsed_ms": 0.0}
    if spec.confirm_required and not confirmed:
        # 确认门：不执行、不落库；把清洗后的参数原样带回，确认后按首轮参数重放
        return {"tool": name, "args": cleaned, "ok": False, "data": None,
                "needs_confirm": True,
                "error": "该操作会写入数据，需要用户确认后执行",
                "elapsed_ms": 0.0}
    try:
        data = spec.func(**cleaned)
        ok, error = True, None
    except Exception as e:  # 工具内部异常也记录而不是静默吞掉
        data, ok, error = None, False, f"{type(e).__name__}: {e}"
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    return {"tool": name, "args": cleaned, "ok": ok, "data": data,
            "error": error, "elapsed_ms": elapsed}


def list_tools() -> list[dict]:
    return [
        {
            "name": s.name,
            "description": s.description,
            "params": [{"name": k, **v} for k, v in s.params.items()],
        }
        for s in REGISTRY.values()
    ]

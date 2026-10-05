"""工具包：导入各模块完成 @tool 注册。新增工具后在这里 import 一次即可。"""
from . import course, assignment, study, knowledge  # noqa: F401  触发注册
from .base import REGISTRY, execute_tool, list_tools, validate_args  # noqa: F401

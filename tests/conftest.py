"""pytest 公共夹具：隔离的 SQLite 临时库 + FastAPI TestClient。"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)  # 保证从任意工作目录运行 pytest 都能 import app


@pytest.fixture()
def tmp_store(tmp_path):
    """每个用例独立的 SQLite 文件，互不污染。"""
    from app import store

    return store.reset_store(str(tmp_path / "test.db"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """API 集成测试客户端：先把 AGENT_DB_PATH 指向临时目录再重建存储。"""
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "api.db"))
    from app import store

    store.reset_store()
    from app.main import app

    with TestClient(app) as c:
        yield c

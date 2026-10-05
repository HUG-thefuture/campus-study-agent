# -*- coding: utf-8 -*-
"""Agent 工具调用回放器（简历项目二，简历口径：读取 Agent 运行日志，按会话展示
工具选择、参数、结果和失败原因；Python + SQLite 保存轨迹，Web 页面筛选失败记录）。

零第三方依赖（标准库 http.server + sqlite3），直接读取 Agent 应用（app/）的轨迹库。
启动：python app.py  （默认 http://127.0.0.1:8031）
"""
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

PORT = 8031
# 2026-09 修复：曾硬编码 ../study_agent/traces.db（该目录不存在），而 Agent 实际
# 轨迹库在 app/data/agent.db —— 两项目根本不互通。现默认读真实库，支持
# AGENT_DB_PATH 环境变量覆盖。
_DEFAULT_DB = Path(__file__).resolve().parent.parent / "app" / "data" / "agent.db"
DB = Path(os.environ.get("AGENT_DB_PATH", _DEFAULT_DB))

PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>Agent 轨迹回放器</title>
<style>body{{font-family:"Microsoft YaHei",sans-serif;margin:20px;background:#f6f8f7}}
h1{{font-size:20px}} table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px}}
th,td{{border:1px solid #d8e4de;padding:6px 8px;text-align:left}} th{{background:#0f6b4f;color:#fff}}
.fail{{background:#fdecec}} .ok{{background:#fff}} .args{{font-family:Consolas,monospace;font-size:12px}}
a{{margin-right:12px}}</style></head><body>
<h1>Agent 工具调用回放器 <small style="font-size:12px;color:#6b7280">{db}</small></h1>
<p><a href="/">全部</a><a href="/?only_fail=1">仅失败</a>
{filter_nav}</p>
<table><tr><th>#</th><th>会话</th><th>步</th><th>角色</th><th>工具</th><th>参数</th><th>结果/错误</th><th>时间</th></tr>
{rows}</table></body></html>"""


def query(only_fail: bool, session_id: str):
    if not DB.exists():
        return None, []
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM traces WHERE 1=1"
    params = []
    if only_fail:
        sql += " AND tool_ok = 0"
    if session_id:
        sql += " AND session_id = ?"
        params.append(session_id)
    sql += " ORDER BY id DESC LIMIT 200"
    rows = [dict(r) for r in conn.execute(sql, params)]
    sessions = [r[0] for r in conn.execute(
        "SELECT DISTINCT session_id FROM traces ORDER BY session_id LIMIT 50")]
    conn.close()
    return sessions, rows


def esc(s):
    # 2026-09 修复：补齐引号转义。session_id 会被拼进 href 属性，
    # 只转义 & < > 时，历史轨迹里的双引号可突破属性边界造成 XSS
    # （API 侧的白名单校验是后加的，库里旧行不受限，且 AGENT_DB_PATH 可指向任意库）
    return ((s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def render(only_fail: bool, session_id: str) -> str:
    try:
        sessions, rows = query(only_fail, session_id)
    except sqlite3.Error as e:
        # 库损坏/被锁/缺表时给出可读页面，而不是让 handler 崩掉
        return PAGE.format(db=str(DB),
                           filter_nav=f"轨迹库不可读：{esc(str(e))}", rows="")
    if sessions is None:
        return PAGE.format(db=str(DB), filter_nav="轨迹库不存在：请先运行 app 主应用并完成一次对话。",
                           rows="")
    # session_id 进 URL query，必须 URL 编码，防止 & ? 破坏参数结构
    nav = "".join(f'<a href="/?session_id={quote(str(s), safe="")}">会话 {esc(str(s)[:12])}…</a>'
                  for s in sessions[:10])
    trs = []
    for r in rows:
        cls = "fail" if r["tool_ok"] == 0 else "ok"
        mark = "✔" if r["tool_ok"] == 1 else ("✘" if r["tool_ok"] == 0 else "—")
        trs.append(
            f'<tr class="{cls}"><td>{r["id"]}</td><td>{esc(str(r["session_id"])[:16])}</td>'
            f'<td>{r["step"]}</td><td>{esc(r["role"])}</td><td>{esc(r["tool_name"] or "—")} {mark}</td>'
            f'<td class="args">{esc(r["tool_args"] or "")}</td>'
            f'<td>{esc((r["content"] or "")[:80])}{esc(r["err"] or "")}</td>'
            f'<td>{esc(r["created_at"] or "")}</td></tr>')
    body = "".join(trs) or '<tr><td colspan="8">无记录</td></tr>'
    return PAGE.format(db=str(DB), filter_nav=nav, rows=body)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        only_fail = parse_qs(u.query).get("only_fail", ["0"])[0] == "1"
        sid = parse_qs(u.query).get("session_id", [""])[0]
        body = render(only_fail, sid).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Agent 轨迹回放器: http://127.0.0.1:{PORT}  (轨迹库: {DB})")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

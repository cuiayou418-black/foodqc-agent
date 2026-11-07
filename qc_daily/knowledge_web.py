"""知识库（记忆板块） + Agent 工作台 · Web 接口层

以 Blueprint 挂进现有 qc_daily.webapp.create_app()，对现有业务路由零改动。
页面：/knowledge（Agent 设置 › 记忆）；/agent 已废弃，302 到独立客户端 /app。
接口：知识库查看 / 上传 / 确认 / 预览，Agent 提问。

⚠️ Agent 提问会捕获 qa.run_qa() 的 stdout；Flask 多线程下用锁串行化，
避免两个请求的输出串台。
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import threading
from typing import Any

from flask import Blueprint, jsonify, redirect, request
from werkzeug.exceptions import RequestEntityTooLarge

from . import knowledge as kb
from . import knowledge_templates as kbt

bp = Blueprint("knowledge", __name__)
_QA_LOCK = threading.Lock()


# ------------------------------------------------------------------ 页面

@bp.get("/knowledge")
def knowledge_view():
    return kbt.knowledge_page()


@bp.get("/agent")
def agent_view():
    """旧的内嵌式工作台已废弃：统一跳到独立客户端 /app，避免两个同名页面。"""
    return redirect("/app", code=302)


# ------------------------------------------------------------------ 知识库接口

@bp.get("/api/knowledge/overview")
def api_overview():
    return jsonify(kb.overview())


@bp.errorhandler(413)
def _too_large(_e):
    return jsonify({"ok": False, "error": "文件超过 20MB 限制"}), 413


@bp.post("/api/knowledge/upload")
def api_upload():
    try:
        f = request.files.get("file")
        source = (request.form.get("source") or "public").strip()
    except RequestEntityTooLarge:
        return jsonify({"ok": False, "error": "文件超过 20MB 限制"}), 413
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "请先选择要上传的文件"}), 400
    try:
        raw = f.read()
    except RequestEntityTooLarge:
        return jsonify({"ok": False, "error": "文件超过 20MB 限制"}), 413
    rec, err, warning = kb.add_upload(raw, f.filename, source)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    rec = _public_record(rec)
    return jsonify({"ok": True, "record": rec, "warning": warning,
                    "overview": kb.overview()})


@bp.post("/api/knowledge/confirm/<rid>")
def api_confirm(rid: str):
    rec, err = kb.confirm(rid)
    if rec is None:
        return jsonify({"ok": False, "error": err or "找不到这条记录"}), 404
    return jsonify({"ok": True, "record": _public_record(rec),
                    "notice": err or "", "overview": kb.overview()})


@bp.get("/api/knowledge/preview/<rid>")
def api_preview(rid: str):
    text, err = kb.preview(rid)
    if err:
        return jsonify({"error": err}), 404
    return jsonify({"ok": True, "text": text})


def _public_record(rec: dict) -> dict:
    """不把本机绝对路径回传前端。"""
    out = {k: v for k, v in rec.items() if k not in {"stored", "preview"}}
    if rec.get("private"):
        out["warning"] = "⚠️ 公司内部材料：仅本机暂存，禁止外传、禁止 push 到 GitHub"
    return out


# ------------------------------------------------------------------ Agent 工作台接口

def _parse_trace(raw: str) -> tuple[list[dict[str, Any]], list[str]]:
    """把 qa.run_qa() 的真实输出解析成 Codex 风格步骤列表。"""
    steps: list[dict[str, Any]] = []
    conclusion: list[str] = []
    in_conclusion = False
    cur: dict[str, Any] | None = None
    for line in raw.splitlines():
        t = line.strip()
        if not t or set(t) <= {"=", "-"}:
            continue
        if t.startswith("[代码结论"):
            in_conclusion = True
            steps.append({"kind": "sep", "title": t})
            continue
        if in_conclusion:
            conclusion.append(t)
            continue
        m = re.match(r"^\[工具\]\s*(.+)$", t)
        if m:
            cur = {"kind": "tool", "title": m.group(1).strip(), "result": ""}
            steps.append(cur)
            continue
        m = re.match(r"^\[第\d+步\]\s*(.+)$", t)
        if m:
            body = m.group(1).strip()
            if " = " in body:
                title, res = body.split(" = ", 1)
                cur = {"kind": "tool", "title": title.strip(), "result": res.strip()}
            else:
                cur = {"kind": "tool", "title": body, "result": ""}
            steps.append(cur)
            continue
        if cur and t.startswith("->"):
            extra = t[2:].strip()
            cur["result"] = (cur["result"] + "\n" + extra).strip()
            continue
        steps.append({"kind": "info", "title": t})
        cur = None
    return steps, conclusion


@bp.post("/api/agent/ask")
def api_agent_ask():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "请先输入一个问题"}), 400
    if len(question) > 300:
        return jsonify({"ok": False, "error": "问题请控制在 300 字以内"}), 400

    buf = io.StringIO()
    try:
        with _QA_LOCK:
            from . import qa  # 延迟导入：P3 未完成时也不拖垮整个 Web 应用
            with contextlib.redirect_stdout(buf):
                qa.run_qa(question)
    except ImportError:
        return jsonify({"ok": False, "error": "提问层 qa.py 尚未就绪，请先跑完今晚 P3"}), 503
    except Exception as e:
        # 只回类型名 + 简短信息，不回栈、不回路径、不回凭据
        return jsonify({"ok": False,
                        "error": f"运行失败（{type(e).__name__}）：{str(e)[:160]}"}), 500

    raw = buf.getvalue()
    steps, conclusion = _parse_trace(raw)
    llm_on = os.getenv("QC_USE_LLM", "").strip().lower() not in {"", "0", "false", "off"}
    return jsonify({"ok": True, "question": question, "steps": steps,
                    "conclusion": conclusion, "llm": llm_on,
                    "raw_lines": len(raw.splitlines())})

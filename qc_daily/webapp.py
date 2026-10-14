"""品控日报智能体 · Web 应用（Flask）

把日报智能体包成一个网站：

- 仪表盘：最新日报 KPI + 合格率趋势图 + 最新预警；
- 日报列表：所有已生成日报，支持在线查看（iframe）、下载 PDF/Excel、一键校验完整性；
- 生成日报：网页上选日期一键生成（后台线程执行，不卡页面）；
- 预警记录 / 审计日志：全量历史查询；
- 可选内嵌调度器：Web 进程本身每天定时生成昨日日报；
- 可选登录保护：配置 token（或环境变量 QC_WEB_TOKEN）后需登录访问，
  局域网演示可不设；公网部署务必设置。

启动：
    python -m qc_daily web [--host 0.0.0.0] [--port 8000] [--scheduler]
"""
from __future__ import annotations

import hmac
import json
import os
import re
import socket
import sqlite3
import threading
import uuid
import webbrowser
from datetime import date, datetime, timedelta

from flask import (Flask, abort, jsonify, redirect, render_template_string,
                   request, send_file, session, url_for)

from .agent import QCDailyAgent
from .audit import verify_report
from .config import QCDailyConfig, env
from .console import say
from .importer import import_path
from . import webtemplates as tpl

#: 上传限制
ALLOWED_UPLOAD_EXT = {".xlsx", ".xlsm", ".csv", ".json"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024   # 20MB

# ------------------------------------------------------------------ 数据读取


def _read_jsonl(path: str) -> list:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def _history(cfg: QCDailyConfig) -> list:
    """历史指标（按日期升序）。"""
    rows = _read_jsonl(os.path.join(cfg.output_root, "audit", "history.jsonl"))
    return sorted(rows, key=lambda e: e.get("report_date", ""))


def _audit(cfg: QCDailyConfig) -> list:
    """审计日志（按时间倒序）。"""
    rows = _read_jsonl(os.path.join(cfg.output_root, "audit", "audit.jsonl"))
    return list(reversed(rows))


def _report_dir(cfg: QCDailyConfig, day: str) -> str:
    return os.path.join(cfg.output_root, "reports", day)


def _report_files(cfg: QCDailyConfig, day: str) -> dict:
    """该日期已生成的导出文件 {fmt: 路径}。"""
    d = _report_dir(cfg, day)
    out = {}
    for fmt, ext in (("html", "html"), ("pdf", "pdf"), ("excel", "xlsx")):
        p = os.path.join(d, f"品控日报_{day}.{ext}")
        if os.path.exists(p):
            out[fmt] = p
    return out


def _report_rows(cfg: QCDailyConfig) -> list:
    """日报列表数据：审计状态 + 历史指标 + 磁盘文件，三方合并。"""
    hist = {e["report_date"]: e for e in _history(cfg)}
    audit_by_date: dict[str, dict] = {}
    for e in _audit(cfg):  # 倒序，同日期保留最新一条
        audit_by_date.setdefault(e.get("report_date", ""), e)

    rows = []
    for day in sorted(set(hist) | set(audit_by_date), reverse=True):
        if not day:
            continue
        h, a = hist.get(day, {}), audit_by_date.get(day, {})
        files = _report_files(cfg, day)
        # 失败且无产出的运行不进日报列表（审计日志页已完整记录全部运行）
        if a.get("status") == "failed" and not files:
            continue
        rows.append({
            "date": day,
            "status": a.get("status", "unknown"),
            "pass_rate": h.get("pass_rate"),
            "fail_tests": h.get("fail_tests", 0),
            "total_tests": h.get("total_tests", 0),
            "abnormal": h.get("abnormal_batch_count", 0),
            "alerts": h.get("alerts_count", len(a.get("alerts", []))),
            "files": files,
        })
    return rows


def _db_path(cfg: QCDailyConfig) -> str:
    """SQL 数据源数据库绝对路径（与 CLI import 保持一致）。"""
    dsn = cfg.source["sql"].get("dsn", "qc_source.db")
    if os.path.isabs(dsn):
        return dsn
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, dsn)


def db_overview(db_path: str) -> dict:
    """已导入数据概要：三张表的记录数、覆盖天数、日期范围。"""
    if not os.path.exists(db_path):
        return {"exists": False, "tables": []}
    tables = []
    try:
        conn = sqlite3.connect(db_path)
        for t, datecol in (("production", "started_at"), ("quality", "tested_at"),
                           ("warehouse", "occurred_at")):
            try:
                total = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                mn, mx = conn.execute(
                    f"SELECT MIN({datecol}), MAX({datecol}) FROM {t}").fetchone()
                days = conn.execute(
                    f"SELECT COUNT(DISTINCT substr({datecol},1,10)) FROM {t}").fetchone()[0]
                tables.append({"table": t, "total": total, "days": days,
                               "min": (mn or "")[:10], "max": (mx or "")[:10]})
            except sqlite3.Error:
                tables.append({"table": t, "total": 0, "days": 0, "min": "", "max": ""})
        conn.close()
    except sqlite3.Error:
        return {"exists": False, "tables": []}
    return {"exists": True, "tables": tables}


# ------------------------------------------------------------------ 后台任务

class JobRunner:
    """一键生成的后台任务执行器（线程池语义：每次生成一个线程）。"""

    def __init__(self, config: QCDailyConfig):
        self.config = config
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self, date_str: str = "", source_override: str | None = None) -> str:
        job_id = uuid.uuid4().hex[:10]
        with self.lock:
            self.jobs[job_id] = {"status": "running", "date": date_str or "昨日",
                                 "source": source_override or self.config.source.get("type"),
                                 "started_at": date.today().isoformat()}
        threading.Thread(target=self._work, args=(job_id, date_str, source_override),
                         daemon=True).start()
        return job_id

    def _work(self, job_id: str, date_str: str, source_override: str | None) -> None:
        try:
            config = self.config
            if source_override:  # 本次运行临时切换数据源（不改配置文件）
                config = QCDailyConfig(raw=json.loads(json.dumps(self.config.raw)))
                config.raw["source"]["type"] = source_override
            target = date.fromisoformat(date_str) if date_str else None
            agent = QCDailyAgent(config)   # 每次任务独立实例，避免状态串扰
            result = agent.run(report_date=target)
            with self.lock:
                self.jobs[job_id].update({
                    "status": "done" if result.status != "failed" else "failed",
                    "summary": result.summary(),
                    "date": result.report_date,
                    "outputs": {k: os.path.basename(v) for k, v in result.outputs.items()},
                    "run_status": result.status,
                })
        except Exception as e:  # 后台线程异常不能拖垮 Web 进程
            with self.lock:
                self.jobs[job_id].update({"status": "failed", "error": str(e)})

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return dict(self.jobs.get(job_id) or {})


# ------------------------------------------------------------------ 导入任务

class ImportJobRunner:
    """网页上传文件的后台导入执行器。"""

    def __init__(self, config: QCDailyConfig):
        self.config = config
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.db = _db_path(config)
        self.upload_dir = os.path.join(config.output_root, "uploads")
        os.makedirs(self.upload_dir, exist_ok=True)

    def start(self, file_path: str, filename: str) -> str:
        job_id = uuid.uuid4().hex[:10]
        with self.lock:
            self.jobs[job_id] = {"status": "running", "filename": filename}
        threading.Thread(target=self._work, args=(job_id, file_path, filename),
                         daemon=True).start()
        return job_id

    def _work(self, job_id: str, file_path: str, filename: str) -> None:
        try:
            summary = import_path(file_path, self.db, self.config)
            summary = dict(summary)
            summary["dates"] = list(summary.get("dates", []))
            with self.lock:
                self.jobs[job_id].update({
                    "status": "done" if not summary.get("errors") else "failed",
                    "summary": summary,
                    "overview": db_overview(self.db),
                    "error": "；".join(summary.get("errors", [])) if summary.get("errors") else "",
                })
        except Exception as e:
            with self.lock:
                self.jobs[job_id].update({"status": "failed", "error": str(e)})

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return dict(self.jobs.get(job_id) or {})


def _safe_upload_name(filename: str) -> str:
    """保留中文文件名的安全化（secure_filename 会吃掉中文，这里自己处理）。"""
    name = os.path.basename(filename or "").strip()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    return name[:120] or "upload"


# ------------------------------------------------------------------ 应用工厂

def create_app(config: QCDailyConfig, with_scheduler: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.secret_key = os.urandom(24)

    runner = JobRunner(config)
    import_runner = ImportJobRunner(config)
    token = config.web.get("token") or env(config.web.get("token_env", "QC_WEB_TOKEN"))

    scheduler_state = {"running": False}
    if with_scheduler:
        from .scheduler import DailyScheduler

        def _run_scheduler():
            scheduler_state["running"] = True
            DailyScheduler(QCDailyAgent(config)).run_forever()

        threading.Thread(target=_run_scheduler, daemon=True).start()
        say("[Web] 内嵌调度器已启动（每日定时生成昨日日报）")

    # ---------- 登录保护 ----------
    @app.before_request
    def _guard():
        if not token:
            return None
        if request.endpoint in ("login", "static"):
            return None
        if not session.get("auth"):
            return redirect(url_for("login"))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = ""
        if request.method == "POST":
            got = request.form.get("token", "")
            if hmac.compare_digest(got, token):
                session["auth"] = True
                return redirect(url_for("dashboard"))
            error = "口令不正确，请重试"
        return tpl.login_page(error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- 页面 ----------
    @app.route("/")
    def dashboard():
        hist = _history(config)
        kpi = None
        if hist:
            kpi = {**hist[-1], "date": hist[-1].get("report_date", "")}
        trend = [{**e, "date": e.get("report_date", "")} for e in hist[-14:]]
        alerts = []
        if _audit(config):
            alerts = _audit(config)[0].get("alerts", [])
        return tpl.dashboard(kpi, trend, alerts, scheduler_state["running"])

    @app.route("/reports")
    def reports():
        return tpl.reports_list(_report_rows(config))

    @app.route("/reports/<day>")
    def report_view(day: str):
        files = _report_files(config, day)
        if "html" not in files:
            abort(404, f"未找到 {day} 的日报（尚未生成或已清理）")
        return tpl.report_viewer(day)

    @app.route("/reports/<day>/file")
    def report_file(day: str):
        files = _report_files(config, day)
        if "html" not in files:
            abort(404)
        return send_file(files["html"])

    @app.route("/reports/<day>/download/<fmt>")
    def report_download(day: str, fmt: str):
        files = _report_files(config, day)
        path = files.get(fmt)
        if not path:
            abort(404, f"{day} 无 {fmt} 格式文件")
        name = f"品控日报_{day}.{os.path.splitext(path)[1].lstrip('.')}"
        return send_file(path, as_attachment=True, download_name=name)

    @app.route("/generate")
    def generate():
        db_ready = db_overview(_db_path(config))["exists"] and any(
            t["total"] > 0 for t in db_overview(_db_path(config))["tables"])
        return tpl.generate_page((date.today() - timedelta(days=1)).isoformat(),
                                 scheduler_state["running"], db_ready)

    @app.route("/alerts")
    def alerts():
        rows = []
        for e in _audit(config):
            for a in e.get("alerts", []):
                rows.append({**a, "date": e.get("report_date", "")})
        order = {"critical": 0, "warning": 1, "info": 2}
        rows.sort(key=lambda r: (r["date"], order.get(r["level"], 3)), reverse=True)
        return tpl.alerts_page(rows)

    @app.route("/audit")
    def audit():
        return tpl.audit_page(_audit(config)[:100])

    # ---------- 数据导入 ----------
    @app.route("/import")
    def import_page_view():
        return tpl.import_page()

    @app.route("/api/import/upload", methods=["POST"])
    def api_import_upload():
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"error": "请先选择要上传的文件"}), 400
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXT:
            return jsonify({"error": f"不支持的文件格式 {ext or '(无扩展名)'}，"
                                     f"仅支持 .xlsx / .csv / .json"}), 400
        safe_name = _safe_upload_name(f.filename)
        stored = os.path.join(import_runner.upload_dir,
                              f"{datetime.now():%Y%m%d_%H%M%S}_{safe_name}")
        try:
            f.save(stored)
        except Exception as e:
            return jsonify({"error": f"文件保存失败: {e}"}), 500
        size = os.path.getsize(stored)
        if size > MAX_UPLOAD_BYTES:
            os.remove(stored)
            return jsonify({"error": f"文件大小 {size / 1048576:.1f}MB 超过 20MB 限制"}), 413
        if size == 0:
            os.remove(stored)
            return jsonify({"error": "文件为空，请检查后重试"}), 400
        job_id = import_runner.start(stored, safe_name)
        say(f"[Web] 收到数据导入 job={job_id} file={safe_name} ({size / 1024:.1f}KB)")
        return jsonify({"job_id": job_id, "filename": safe_name, "size": size})

    @app.route("/api/import/job/<job_id>")
    def api_import_job(job_id: str):
        job = import_runner.get(job_id)
        if not job:
            return jsonify({"status": "unknown", "error": "任务不存在或服务已重启"}), 404
        return jsonify(job)

    @app.route("/api/import/overview")
    def api_import_overview():
        return jsonify(db_overview(_db_path(config)))

    # ---------- API ----------
    @app.route("/api/run", methods=["POST"])
    def api_run():
        payload = request.get_json(silent=True) or {}
        day = (payload.get("date") or "").strip()
        if day:
            try:
                date.fromisoformat(day)
            except ValueError:
                return jsonify({"error": f"日期格式不正确: {day}（应为 YYYY-MM-DD）"}), 400
        source = (payload.get("source") or "").strip()
        if source and source not in ("simulated", "api", "sql"):
            return jsonify({"error": f"未知数据源: {source}"}), 400
        job_id = runner.start(day, source_override=source or None)
        say(f"[Web] 收到生成请求 job={job_id} date={day or '昨日'} source={source or '默认'}")
        return jsonify({"job_id": job_id})

    @app.route("/api/job/<job_id>")
    def api_job(job_id: str):
        job = runner.get(job_id)
        if not job:
            return jsonify({"status": "unknown", "error": "任务不存在或已重启丢失"}), 404
        return jsonify(job)

    @app.route("/api/verify/<day>")
    def api_verify(day: str):
        files = _report_files(config, day)
        if "html" not in files:
            return jsonify({"ok": False, "reason": "报告不存在"})
        ok, fp = verify_report(files["html"])
        return jsonify({"ok": ok, "fingerprint": fp})

    # ---------- 错误页 ----------
    @app.errorhandler(404)
    def _404(e):
        return tpl.page("页面不存在", "", f"""
<div class='empty'><p>😕 {e.description or '页面不存在'}</p>
<a class='btn' href='/'>返回仪表盘</a></div>"""), 404

    return app


# ------------------------------------------------------------------ 启动入口

def _lan_ip() -> str:
    """探测本机局域网 IP（用于打印访问地址）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def run_server(config: QCDailyConfig | None = None, host: str | None = None,
               port: int | None = None, with_scheduler: bool | None = None,
               open_browser_flag: bool = True) -> None:
    cfg = config or QCDailyConfig.load()
    host = host or cfg.web.get("host", "0.0.0.0")
    port = int(port or cfg.web.get("port", 8000))
    if with_scheduler is None:
        with_scheduler = bool(cfg.web.get("start_scheduler", False))

    app = create_app(cfg, with_scheduler=with_scheduler)

    ip = _lan_ip()
    say("=" * 56)
    say("品控日报智能体 · Web 服务已启动")
    say(f"  本机访问   : http://127.0.0.1:{port}/")
    say(f"  局域网访问 : http://{ip}:{port}/   ← 答辩/演示时其他人用这个地址")
    say(f"  后台调度   : {'已开启（每天自动生成昨日日报）' if with_scheduler else '未开启'}")
    say(f"  登录保护   : {'已开启' if (cfg.web.get('token') or env(cfg.web.get('token_env', 'QC_WEB_TOKEN'))) else '未开启（局域网演示模式）'}")
    say("  停止服务   : Ctrl+C")
    say("=" * 56)

    if open_browser_flag:
        try:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        except Exception:
            pass

    app.run(host=host, port=port, threaded=True)

"""品控日报智能体 · 异常预警与推送

推送渠道（均可在配置中开关，默认 dry_run 演练模式，不真实外发）：
- ConsoleChannel  控制台输出（始终可用，用于自测与演示）；
- EmailChannel    SMTP 邮件（支持 SSL/STARTTLS，密码走环境变量）；
- WebhookChannel  企业微信 / 钉钉 / Slack / 通用 Webhook（HTTP POST）。

dry_run 模式下，邮件与 Webhook 内容落盘到 qc_daily_data/outbox/，
既能验证链路又不误发 —— 与 FoodQC-Agent「低置信度强制转人工」同源的谨慎策略。
"""
from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import QCDailyConfig, env
from .models import Alert, DailyMetrics, iso_now

_LEVEL_CN = {"critical": "严重", "warning": "警告", "info": "提示"}


# ------------------------------------------------------------------ 渠道基类

class BaseChannel(ABC):
    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @abstractmethod
    def send(self, alerts: list[Alert], metrics: DailyMetrics, meta: dict) -> list:
        """发送预警，返回投递记录列表。"""


def _alert_lines(alerts: list[Alert]) -> list[str]:
    lines = [f"{_LEVEL_CN.get(a.level, a.level)}｜{a.title}：{a.detail}" for a in alerts]
    if any(a.refs for a in alerts):
        refs = [r for a in alerts for r in a.refs]
        lines.append(f"关联记录：{', '.join(refs[:20])}")
    return lines


# ------------------------------------------------------------------ 控制台

class ConsoleChannel(BaseChannel):
    name = "console"

    def send(self, alerts, metrics, meta):
        print(f"  [推送·控制台] 命中 {len(alerts)} 条预警：")
        for a in alerts:
            print(f"    [{_LEVEL_CN.get(a.level, a.level)}] {a.title} —— {a.detail}")
            if a.refs:
                print(f"           关联记录: {', '.join(a.refs[:8])}")
        return [{"channel": self.name, "status": "delivered", "count": len(alerts),
                 "at": iso_now()}]


# ------------------------------------------------------------------ 邮件

class EmailChannel(BaseChannel):
    name = "email"

    def _build_message(self, alerts, metrics, meta) -> MIMEMultipart:
        cfg = self.cfg
        subject = (f"{cfg.get('subject_prefix', '【品控预警】')}"
                   f"{metrics.report_date} 品控日报 · 命中 {len(alerts)} 条预警")
        plain = "\n".join([
            f"{meta.get('title', '食用油中控品控日报')} {metrics.report_date}",
            f"总体合格率：{metrics.pass_rate:.2%}｜异常批次：{len(metrics.abnormal_batches)} 个",
            "-" * 40,
            *_alert_lines(alerts),
            "-" * 40,
            f"报告指纹：{meta.get('fingerprint', '')}",
            f"审计日志：{meta.get('audit_path', '')}",
            "本邮件由品控日报智能体自动发出，请及时处置并人工复核。",
        ])
        html_rows = "".join(
            f"<tr><td>{_LEVEL_CN.get(a.level, a.level)}</td><td>{a.title}</td>"
            f"<td>{a.detail}</td><td>{', '.join(a.refs[:8])}</td></tr>" for a in alerts)
        html = f"""<html><body style="font-family:sans-serif;font-size:13px">
<h3 style="color:#b81c22">{meta.get('title', '食用油中控品控日报')} {metrics.report_date}</h3>
<p>总体合格率 <b>{metrics.pass_rate:.2%}</b> ｜ 异常批次 <b>{len(metrics.abnormal_batches)}</b> 个 ｜
预警 <b>{len(alerts)}</b> 条</p>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:12px">
<tr style="background:#f2f3f5"><th>级别</th><th>规则</th><th>详情</th><th>关联记录</th></tr>{html_rows}</table>
<p style="color:#8f959e;font-size:11px">报告指纹：{meta.get('fingerprint', '')}<br>
审计日志：{meta.get('audit_path', '')}<br>本邮件由品控日报智能体自动发出，请及时处置并人工复核。</p>
</body></html>"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = cfg.get("sender", "")
        msg["To"] = ", ".join(cfg.get("receivers", []))
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        return msg

    def send(self, alerts, metrics, meta):
        cfg = self.cfg
        receivers = list(cfg.get("receivers", []))
        if not receivers:
            return [{"channel": self.name, "status": "skipped", "reason": "未配置收件人", "at": iso_now()}]
        msg = self._build_message(alerts, metrics, meta)

        if cfg.get("dry_run", True):
            out_dir = os.path.join(meta["output_root"], "outbox")
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"email_{metrics.report_date}_{datetime.now():%H%M%S}.eml.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"Subject: {msg['Subject']}\nTo: {msg['To']}\n\n{msg.get_payload(0).get_payload(decode=True).decode('utf-8')}")
            print(f"  [推送·邮件] dry_run 演练，内容已落盘: {path}")
            return [{"channel": self.name, "status": "dry_run", "path": path,
                     "receivers": receivers, "at": iso_now()}]

        password = env(cfg.get("password_env", "QC_SMTP_PASSWORD"))
        host, port = cfg.get("smtp_host", ""), int(cfg.get("smtp_port", 465))
        if cfg.get("use_ssl", True):
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.starttls()
        try:
            if cfg.get("username"):
                server.login(cfg["username"], password)
            server.sendmail(cfg.get("sender", ""), receivers, msg.as_string())
        finally:
            server.quit()
        print(f"  [推送·邮件] 已发送至 {len(receivers)} 位收件人")
        return [{"channel": self.name, "status": "delivered", "receivers": receivers, "at": iso_now()}]


# ------------------------------------------------------------------ Webhook

class WebhookChannel(BaseChannel):
    name = "webhook"

    _PLATFORMS = ("wecom", "dingtalk", "slack", "generic")

    def _payload(self, alerts, metrics, meta) -> dict:
        cfg = self.cfg
        platform = cfg.get("platform", "wecom")
        title = f"{meta.get('title', '食用油中控品控日报')} {metrics.report_date}"
        summary = (f"总体合格率 {metrics.pass_rate:.2%} ｜ 异常批次 {len(metrics.abnormal_batches)} 个 ｜ "
                   f"预警 {len(alerts)} 条")
        lines = "\n".join(f"> {_LEVEL_CN.get(a.level, a.level)} **{a.title}**：{a.detail}" for a in alerts)
        markdown = f"### {title}\n{summary}\n{lines}\n> 报告指纹 {meta.get('fingerprint', '')[:16]}"
        if platform == "wecom":
            return {"msgtype": "markdown", "markdown": {"content": markdown}}
        if platform == "dingtalk":
            return {"msgtype": "markdown", "markdown": {"title": title, "text": markdown}}
        if platform == "slack":
            return {"text": markdown}
        return {"title": title, "summary": summary, "alerts": [a.to_dict() for a in alerts],
                "fingerprint": meta.get("fingerprint", "")}

    def send(self, alerts, metrics, meta):
        cfg = self.cfg
        url = cfg.get("url") or env(cfg.get("url_env", "QC_WEBHOOK_URL"))
        if not url:
            return [{"channel": self.name, "status": "skipped", "reason": "未配置 Webhook URL", "at": iso_now()}]
        payload = self._payload(alerts, metrics, meta)

        if cfg.get("dry_run", True):
            out_dir = os.path.join(meta["output_root"], "outbox")
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"webhook_{metrics.report_date}_{datetime.now():%H%M%S}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"url": url, "payload": payload}, f, ensure_ascii=False, indent=2)
            print(f"  [推送·Webhook] dry_run 演练，内容已落盘: {path}")
            return [{"channel": self.name, "status": "dry_run", "path": path, "at": iso_now()}]

        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=int(cfg.get("timeout", 10))) as resp:
            body = resp.read().decode("utf-8", "ignore")
        print(f"  [推送·Webhook] 已推送到 {cfg.get('platform')}（响应 {len(body)} 字节）")
        return [{"channel": self.name, "status": "delivered", "response_bytes": len(body), "at": iso_now()}]


# ------------------------------------------------------------------ 通知器

class Notifier:
    """按配置组装渠道并统一派发。"""

    def __init__(self, config: QCDailyConfig):
        push = config.push
        self.channels: list[BaseChannel] = []
        if push.get("console", {}).get("enabled", True):
            self.channels.append(ConsoleChannel(push.get("console", {})))
        if push.get("email", {}).get("enabled", False):
            self.channels.append(EmailChannel(push.get("email", {})))
        if push.get("webhook", {}).get("enabled", False):
            self.channels.append(WebhookChannel(push.get("webhook", {})))

    def push(self, alerts: list[Alert], metrics: DailyMetrics, meta: dict) -> list:
        if not alerts:
            print("  [推送] 当日无预警，跳过推送")
            return []
        deliveries = []
        for ch in self.channels:
            try:
                deliveries.extend(ch.send(alerts, metrics, meta))
            except Exception as e:  # 单渠道失败不影响其他渠道与报告生成
                deliveries.append({"channel": ch.name, "status": "failed", "error": str(e), "at": iso_now()})
                print(f"  [推送·{ch.name}] 失败: {e}")
        return deliveries

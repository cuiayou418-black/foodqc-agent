"""品控日报智能体 · 命令行入口

用法：
    python -m qc_daily run [--date YYYY-MM-DD] [--source simulated|api|sql]
                            [--no-push] [--formats html,pdf,excel] [--config 路径]
    python -m qc_daily daemon [--catch-up]          # 常驻调度（每天定时生成昨日日报）
    python -m qc_daily backfill --days 14           # 一次性补齐最近 N 天日报（构建趋势历史）
    python -m qc_daily selftest                     # 端到端自测（模拟数据 + 断言）
    python -m qc_daily show [--date YYYY-MM-DD]     # 查看某日指标摘要
    python -m qc_daily config-check [--config 路径] # 配置自检
    python -m qc_daily verify --date YYYY-MM-DD     # 校验某日 HTML 报告完整性（防篡改）
    python -m qc_daily web [--port 8000] [--scheduler]  # 启动 Web 服务（局域网演示）
    python -m qc_daily import --file 我的数据.xlsx --dry-run  # 导入自己的 Excel/CSV 数据
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from .agent import QCDailyAgent
from .console import say
from .audit import verify_report
from .config import DEFAULT_CONFIG_PATH, QCDailyConfig


def _load_config(args) -> QCDailyConfig:
    cfg = QCDailyConfig.load(getattr(args, "config", None))
    if getattr(args, "source", None):
        cfg.raw["source"]["type"] = args.source
    if getattr(args, "formats", None):
        cfg.raw["export"]["formats"] = [f.strip() for f in args.formats.split(",") if f.strip()]
    return cfg


def cmd_run(args) -> int:
    agent = QCDailyAgent(_load_config(args))
    d = date.fromisoformat(args.date) if args.date else None
    result = agent.run(report_date=d, push=False if args.no_push else None)
    say("\n" + "=" * 56)
    say(result.summary())
    return 0 if result.status in ("success", "partial") else 1


def cmd_daemon(args) -> int:
    from .scheduler import DailyScheduler

    cfg = _load_config(args)
    if args.catch_up:
        cfg.raw["schedule"]["catch_up"] = True
    agent = QCDailyAgent(cfg)
    DailyScheduler(agent).run_forever()
    return 0


def cmd_backfill(args) -> int:
    agent = QCDailyAgent(_load_config(args))
    today = date.today()
    results = []
    for i in range(args.days, 0, -1):
        d = today - timedelta(days=i)
        results.append(agent.run(report_date=d, push=False if args.no_push else None))
    ok = sum(1 for r in results if r.status in ("success", "partial"))
    say(f"\n补跑完成：{ok}/{len(results)} 天成功（其余失败详情见审计日志）")
    return 0 if ok == len(results) else 1


def cmd_selftest(args) -> int:
    """端到端自测：模拟数据 → 采集 → 校验 → 分析 → 报告 → 导出 → 预警 → 审计。"""
    say("=" * 56)
    say("品控日报智能体 · 端到端自测（模拟数据源）")
    say("=" * 56)
    cfg = QCDailyConfig.load(getattr(args, "config", None))
    cfg.raw["source"]["type"] = "simulated"
    agent = QCDailyAgent(cfg)

    # 先补 6 天历史（10-07 ~ 10-12），让趋势分析有数；最后单独跑昨天（10-13）
    today = date.today()
    for i in range(7, 1, -1):
        agent.run(report_date=today - timedelta(days=i), push=False)
    result = agent.run(report_date=today - timedelta(days=1), push=False)

    say("\n" + "-" * 56)
    say("自测断言：")
    checks = []

    def check(name: str, cond: bool):
        checks.append((name, cond))
        say(f"  {'✅' if cond else '❌'} {name}")

    m = result.metrics
    check("运行成功", result.status in ("success", "partial"))
    check("采集到生产/质检/仓储数据",
          m is not None and m.batch_count > 0 and m.total_tests > 0 and m.warehouse_events > 0)
    check("合格率计算正确", m is not None and 0 <= m.pass_rate <= 1
          and abs(m.pass_rate - (m.total_tests - m.fail_tests) / m.total_tests) < 1e-9)
    check("产出 HTML/PDF/Excel", all(k in result.outputs for k in ("html", "pdf", "excel")))
    check("审计日志已写入", bool(result.audit_path))
    check("趋势分析有数据", m is not None and len(m.trend_points) >= 2)
    ok, fp = verify_report(result.outputs["html"])
    check("报告指纹校验通过（防篡改）", ok)
    say("-" * 56)
    passed = sum(1 for _, c in checks if c)
    say(f"结果：{passed}/{len(checks)} 项通过")
    say(result.summary())
    return 0 if passed == len(checks) else 1


def cmd_show(args) -> int:
    agent = QCDailyAgent(QCDailyConfig.load(getattr(args, "config", None)))
    entries = agent.audit.read_all()
    if args.date:
        entries = [e for e in entries if e.get("report_date") == args.date]
    else:
        entries = entries[-10:]
    if not entries:
        say("无审计记录")
        return 0
    for e in entries:
        rc = e.get("record_counts", {})
        say(f"{e.get('report_date')} | {e.get('status'):8s} | 生产 {rc.get('production', 0):3d} 批 | "
              f"质检 {rc.get('quality', 0):4d} 项次 | 预警 {len(e.get('alerts', []))} 条 | "
              f"运行 {e.get('run_id')}")
    return 0


def cmd_config_check(args) -> int:
    cfg = QCDailyConfig.load(getattr(args, "config", None) or DEFAULT_CONFIG_PATH)
    issues = cfg.check()
    say(f"配置文件: {cfg.config_path or '（未找到，使用内置默认配置）'}")
    say(f"数据源: {cfg.source.get('type')} ｜ 导出格式: {cfg.export.get('formats')} ｜ "
          f"调度时间: {cfg.schedule.get('time')}")
    say(f"产品数: {len(cfg.products)} ｜ 预警规则: {len(cfg.alert_rules)} 条")
    if issues:
        say(f"⛔ 发现 {len(issues)} 个问题：")
        for i in issues:
            say(f"  - {i}")
        return 1
    say("✅ 配置自检通过")
    return 0


def cmd_verify(args) -> int:
    cfg = QCDailyConfig.load(getattr(args, "config", None))
    path = cfg.output_root + f"/reports/{args.date}/品控日报_{args.date}.html"
    import os
    if not os.path.exists(path):
        say(f"报告不存在: {path}")
        return 1
    ok, fp = verify_report(path)
    if ok:
        say(f"✅ 报告完整未被篡改: {path}\n   指纹 {fp}")
        return 0
    say(f"❌ 报告校验失败（可能被篡改）: {path}")
    return 1


def cmd_web(args) -> int:
    """启动 Web 服务（局域网可访问）。"""
    from .webapp import run_server

    cfg = _load_config(args)
    run_server(cfg, host=args.host, port=args.port,
               with_scheduler=True if args.scheduler else None,
               open_browser_flag=not args.no_browser)
    return 0


def cmd_import(args) -> int:
    """把用户的 Excel/CSV 数据导入 SQLite。"""
    import os

    from .importer import import_path, print_summary

    cfg = _load_config(args)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db = args.db or os.path.join(root, cfg.source["sql"].get("dsn", "qc_source.db"))
    if not os.path.isabs(db):
        db = os.path.join(root, db)

    if not os.path.exists(args.path):
        say(f"⛔ 文件或目录不存在: {args.path}")
        return 1
    summary = import_path(args.path, db, cfg, dry_run=args.dry_run)
    print_summary(summary, db, args.dry_run)
    return 0 if not summary["errors"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="qc_daily", description="品控日报自动化智能体")
    parser.add_argument("--config", help="配置文件路径（默认 qc_daily_config.json）")
    # 子命令同样接受 --config（SUPPRESS：未提供时不覆盖顶层取值）
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS,
                        help="配置文件路径（默认 qc_daily_config.json）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", parents=[common], help="立即生成一期日报")
    p.add_argument("--date", help="报告日期 YYYY-MM-DD（默认昨天）")
    p.add_argument("--source", choices=["simulated", "api", "sql"])
    p.add_argument("--no-push", action="store_true", help="禁用预警推送")
    p.add_argument("--formats", help="导出格式，逗号分隔，如 html,pdf,excel")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("daemon", parents=[common], help="常驻调度模式")
    p.add_argument("--catch-up", action="store_true", help="启动时补齐遗漏日期")
    p.set_defaults(func=cmd_daemon)

    p = sub.add_parser("backfill", parents=[common], help="补齐最近 N 天日报")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--no-push", action="store_true")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("selftest", parents=[common], help="端到端自测")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("show", parents=[common], help="查看运行历史")
    p.add_argument("--date")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("config-check", parents=[common], help="配置自检")
    p.set_defaults(func=cmd_config_check)

    p = sub.add_parser("verify", parents=[common], help="校验报告完整性")
    p.add_argument("--date", required=True)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("web", parents=[common], help="启动 Web 服务（局域网演示）")
    p.add_argument("--host", help="监听地址（默认 0.0.0.0，局域网可访问）")
    p.add_argument("--port", type=int, help="端口（默认 8000）")
    p.add_argument("--scheduler", action="store_true", help="同时启动每日定时调度")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("import", parents=[common], help="导入自己的 Excel/CSV 数据")
    p.add_argument("--file", dest="path", required=True, help="Excel/CSV 文件或目录路径")
    p.add_argument("--db", help="目标数据库（默认 qc_source.db）")
    p.add_argument("--dry-run", action="store_true", help="只预览表头映射，不写库")
    p.set_defaults(func=cmd_import)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

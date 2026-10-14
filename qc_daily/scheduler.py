"""品控日报智能体 · 定时调度

- DailyScheduler：每天在配置时间点自动生成"昨天"的日报（数据已收口，数字才不会变）；
- 支持 catch_up 补跑：进程重启后自动补齐遗漏日期，保证日报连续性；
- 用标准库实现（无第三方依赖），Ctrl+C 可优雅退出。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from .agent import QCDailyAgent


class DailyScheduler:
    def __init__(self, agent: QCDailyAgent):
        self.agent = agent
        cfg = agent.config.schedule
        self.run_time = cfg.get("time", "07:30")            # "HH:MM"
        self.tz_offset = int(cfg.get("timezone_offset", 8))
        self.catch_up = bool(cfg.get("catch_up", False))
        self.report_for = agent.config.report.get("report_for", "previous_day")
        self._stop = False

    # ---------- 时间计算 ----------
    def _parse_time(self) -> tuple[int, int]:
        hh, mm = self.run_time.split(":")
        return int(hh), int(mm)

    def now_local(self) -> datetime:
        return datetime.utcnow() + timedelta(hours=self.tz_offset)

    def next_run(self, now: datetime | None = None) -> datetime:
        """下一次触发的本地时间。"""
        now = now or self.now_local()
        hh, mm = self._parse_time()
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _countdown(self, target: datetime) -> str:
        """距离 target 的倒计时（本地时区直接相减）。"""
        now = self.now_local()
        delta = target - now
        secs = max(0, int(delta.total_seconds()))
        return f"还有 {secs // 86400} 天 {secs % 86400 // 3600} 小时 {secs % 3600 // 60} 分"

    def report_date_for(self, run_day: date) -> date:
        """本次运行应生成哪一天的日报。"""
        return run_day - timedelta(days=1) if self.report_for == "previous_day" else run_day

    # ---------- 补跑 ----------
    def catch_up_missed(self, max_days: int = 14) -> list:
        """补齐遗漏日期（从审计日志推断已生成的最晚日期）。"""
        done = self.agent.audit.last_run_dates()
        today = self.now_local().date()
        yesterday = today - timedelta(days=1)
        results = []
        for i in range(max_days, 0, -1):
            d = yesterday - timedelta(days=i - 1)
            if d.isoformat() in done:
                continue
            print(f"[补跑] {d.isoformat()}（该日期无成功记录）")
            try:
                results.append(self.agent.run(report_date=d))
            except Exception as e:
                print(f"[补跑] {d} 失败: {e}")
        if not results:
            print("[补跑] 无遗漏日期")
        return results

    # ---------- 主循环 ----------
    def run_forever(self) -> None:
        nxt = self.next_run()
        print(f"[调度] 品控日报调度已启动：每天 {self.run_time}（UTC{self.tz_offset:+d}）生成"
              f"{'昨日' if self.report_for == 'previous_day' else '当日'}日报")
        print(f"[调度] 下次运行：{nxt:%Y-%m-%d %H:%M:%S}（{self._countdown(nxt)}）")
        if self.catch_up:
            self.catch_up_missed()
        try:
            while not self._stop:
                now = self.now_local()
                if now >= nxt:
                    run_day = now.date()
                    if self.report_date_for(run_day) == run_day:  # same_day 模式等当天结束
                        time.sleep(60)
                        continue
                    target = self.report_date_for(run_day)
                    print(f"\n[调度] {now:%Y-%m-%d %H:%M:%S} 触发，生成 {target} 日报")
                    try:
                        self.agent.run(report_date=target)
                    except Exception as e:
                        print(f"[调度] 运行失败: {e}")
                    nxt = self.next_run(self.now_local())
                    print(f"[调度] 下次运行：{nxt:%Y-%m-%d %H:%M:%S}（{self._countdown(nxt)}）")
                else:
                    time.sleep(min(30, max(1, (nxt - now).total_seconds())))
        except KeyboardInterrupt:
            print("\n[调度] 收到退出信号，调度器已停止")

    def stop(self) -> None:
        self._stop = True

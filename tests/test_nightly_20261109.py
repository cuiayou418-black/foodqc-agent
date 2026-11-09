# -*- coding: utf-8 -*-
"""夜测 2026-11-09 三条严重 bug 的回归测试。

S1 同一页里「N 项次不合格」与「当日无异常批次」并存（签字人读到"无异常"最危险）
S2 xlsx 公式格没有缓存值时被当空格子跳过 → 整行静默消失、少列一项不合格
S3 日期一条都读不出来时，却报「日期不匹配」，把人指去改根本没坏的日期列

跑法：.venv/Scripts/python.exe -m unittest tests.test_nightly_20261109 -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qc_daily.config import QCDailyConfig  # noqa: E402
from qc_daily.importer import (  # noqa: E402
    _is_formula_marker, _read_xlsx, convert,
)
from qc_daily.models import DailyMetrics  # noqa: E402
from qc_daily.tasks.runner import diagnose_missing_dates  # noqa: E402
from qc_daily.templates import render_html  # noqa: E402


def _meta(**kw) -> dict:
    m = {"run_id": "T-TEST", "fingerprint": "fp", "source_type": "file",
         "audit_path": "", "provenance": {}, "errors": [], "qc_details": []}
    m.update(kw)
    return m


def _metrics(**kw) -> DailyMetrics:
    base = dict(report_date="2026-11-09", plant="测试厂",
                generated_at="2026-11-09 08:00:00")
    base.update(kw)
    return DailyMetrics(**base)


class TestNoProductionDoesNotMeanCleanDay(unittest.TestCase):
    """S1：没有生产记录 → 批次维度算不出来，不能写成「当日无异常批次」。"""

    def test_no_prod_with_failures_is_not_a_clean_day(self):
        html = render_html(_metrics(batch_count=0, total_tests=14, fail_tests=2), _meta())
        self.assertNotIn("当日无异常批次", html)
        self.assertIn("无法按批次归集", html)
        self.assertIn("这不等于当日无异常", html)

    def test_no_prod_and_no_failure_still_not_computable(self):
        html = render_html(_metrics(batch_count=0, total_tests=3, fail_tests=0), _meta())
        self.assertNotIn("当日无异常批次", html)
        self.assertIn("无法按批次归集", html)

    def test_with_prod_and_clean_may_say_no_abnormal(self):
        html = render_html(_metrics(batch_count=2, total_tests=6, fail_tests=0), _meta())
        self.assertIn("当日无异常批次", html)


class TestFormulaCellIsNotSwallowed(unittest.TestCase):
    """S2：公式格没有缓存值 → 必须留证据并告知名，不能整行静默消失。"""

    def _make_xlsx(self, path: str) -> None:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "手工表"
        ws.append(["批次号", "检测项目", "实测值", "限值", "单位", "方向",
                   "标准号", "限值出处", "检验日期"])
        # 第一行实测值是公式（openpyxl 写入时不会带缓存值）
        ws.append(["DEMO-FV01", "杂质含量", "=1.3", 1.0, "%", "≤",
                   "GB 1350—2025", "表1 第3等", "2026-11-09"])
        ws.append(["DEMO-FV02", "杂质含量", 0.5, 1.0, "%", "≤",
                   "GB 1350—2025", "表1 第3等", "2026-11-09"])
        wb.save(path)

    def test_formula_cell_is_marked_not_read_as_blank(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "公式.xlsx")
            self._make_xlsx(p)
            records = _read_xlsx(p)[0][1]
            self.assertTrue(any(_is_formula_marker(r.get("实测值")) for r in records),
                            "公式格必须留证据，不能被读成空格子")

    def test_formula_row_skipped_with_reason_others_still_run(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "公式.xlsx")
            self._make_xlsx(p)
            records = _read_xlsx(p)[0][1]
            rows, notes = convert("手工表", records, "quality", QCDailyConfig.load())
            self.assertEqual(len(rows), 1, "正常那行必须照跑")
            self.assertEqual(rows[0]["batch_id"], "DEMO-FV02")
            self.assertTrue(any("公式" in n and "没进判定" in n for n in notes), notes)
            self.assertEqual(rows[0]["tested_at"][:10], "2026-11-09",
                             "日期列不能被公式占位串污染")


class TestMissingDatesIsNotAMismatch(unittest.TestCase):
    """S3：日期读不出来 ≠ 日期不匹配，不能叫人去改日期列。"""

    def test_empty_table_says_nothing_readable(self):
        msg, nxt, code = diagnose_missing_dates([], no_rows=True)
        self.assertNotIn("不匹配", msg)
        self.assertIn("一行可判定的记录都没读出来", msg)
        self.assertEqual(code, "no_rows")
        self.assertIn("实测值要写数字", nxt)

    def test_rows_but_no_date_blames_the_date_column_format(self):
        msg, nxt, code = diagnose_missing_dates([], no_rows=False)
        self.assertNotIn("不匹配", msg)
        self.assertIn("日期", msg)
        self.assertEqual(code, "no_dates")
        self.assertIn("重算并保存", nxt)

    def test_helper_only_covers_the_empty_case(self):
        """分诊函数只在「一条日期都没有」时使用；有日期时由调用方走"不匹配"分支。"""
        msg, nxt, code = diagnose_missing_dates([], no_rows=False)
        self.assertNotIn("与 report_date", msg)   # 不越权替调用方下"不匹配"结论
        self.assertTrue(nxt)


if __name__ == "__main__":
    unittest.main()

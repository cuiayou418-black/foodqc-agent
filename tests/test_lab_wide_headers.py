# -*- coding: utf-8 -*-
"""化验室送检表格式回归（2026-11-08 晚）。

真表特征：表头压在第 3 行、列名带硬换行、全角括号、右侧可带「系统判定区」补列。
覆盖：① _norm_header 吃换行；② guess_header_row 挑中第 3 行；
③ 宽表能读表内自带的项目限值列（如「出糙率限值」），且不受 config 版本门禁影响。
"""
import unittest

from qc_daily.importer import _norm_header, guess_header_row, convert
from qc_daily.config import QCDailyConfig


class TestNormHeader(unittest.TestCase):
    def test_newline_in_header(self):
        self.assertEqual(_norm_header("水分\n (%)"), "水分")
        self.assertEqual(_norm_header("出糙率\n（%）"), "出糙率")
        self.assertEqual(_norm_header("整精米率\n（%）"), "整精米率")
        self.assertEqual(_norm_header("产地\n（省市县镇）"), "产地")

    def test_fullwidth_and_space_still_work(self):
        self.assertEqual(_norm_header("脂肪酸值（mg/100g）"), "脂肪酸值")
        self.assertEqual(_norm_header(" 水 分　(%) "), "水分")


class TestGuessHeaderRow(unittest.TestCase):
    def test_title_and_note_above_header(self):
        rows = [
            ["送检水稻检测"] + [None] * 31,
            ["黄色信息由采购提供，接样时需确认清楚"] + [None] * 31,
            ["日期", "品种", "样品来源", "气味", "水分\n (%)", "出糙率\n（%）"] + [None] * 26,
            ["2026-11-09", "某品种A", "某地某仓库", "正常", 13.1, 76.2] + [None] * 26,
        ]
        # 第 3 行（0-based 2）：能认出的已知表头最多
        self.assertEqual(guess_header_row(rows), 2)


class TestWideWithInlineLimits(unittest.TestCase):
    """右侧补列：批次号 + 每项限值 + 方向/标准/出处 → 判定链能拿到限值。"""

    def _records(self):
        return [
            {
                "批次号": "DEMO-1109-01", "产品名称": "中晚籼稻谷", "检验日期": "2026-11-09",
                "出糙率\n（%）": 76.2, "整精米率\n（%）": 60.0,
                "指标方向": "≥", "出糙率限值": 75.0, "整精米率限值": 44.0,
                "标准号": "GB 1350—2025", "限值出处": "GB 1350—2025 表1 第3等 原文第5页",
            },
            {
                "批次号": "DEMO-1109-02", "产品名称": "中晚籼稻谷", "检验日期": "2026-11-09",
                "出糙率\n（%）": 73.4, "整精米率\n（%）": 41.6,
                "指标方向": "≥", "出糙率限值": 75.0, "整精米率限值": 44.0,
                "标准号": "GB 1350—2025", "限值出处": "GB 1350—2025 表1 第3等 原文第5页",
            },
        ]

    def test_inline_limits_used(self):
        cfg = QCDailyConfig()
        rows, notes = convert("送检水稻检测", self._records(), "quality", cfg)
        self.assertEqual(len(rows), 4)
        by_key = {(r["batch_id"], r["item"]): r for r in rows}
        a = by_key[("DEMO-1109-01", "出糙率")]
        self.assertEqual(a["limit_value"], 75.0)
        self.assertEqual(a["direction"], "≥")
        self.assertEqual(a["standard"], "GB 1350—2025")
        self.assertIn("表1", a["limit_source"])
        self.assertTrue(any("宽表" in n for n in notes))

    def test_without_batch_column_still_blocked(self):
        cfg = QCDailyConfig()
        recs = [{k: v for k, v in r.items() if k != "批次号"} for r in self._records()]
        rows, notes = convert("送检水稻检测", recs, "quality", cfg)
        self.assertEqual(rows, [])
        self.assertTrue(any("⛔" in n and "批次" in n for n in notes))


if __name__ == "__main__":
    unittest.main()

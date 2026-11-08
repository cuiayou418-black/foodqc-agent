# -*- coding: utf-8 -*-
"""导入层 · 表头识别回归（2026-11-08 真表实测后补）

起因：拿米粮部真实的「送检水稻检测」表跑，31 列一列都认不出来，还报成"日期不匹配"，
把人指去改日期。两个根因：
  1. 表头在第 3 行（上面压着标题行和业务说明行），代码硬取第 1 行；
  2. 列名用全角括号 `出糙率（%）`，归一化只剥半角 `()`，于是 31 列全不认。

跑法：.venv/Scripts/python.exe -m unittest tests.test_importer_headers
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qc_daily.importer import (  # noqa: E402
    KNOWN_ITEMS, _norm_header, guess_header_row)


class TestNormHeader(unittest.TestCase):
    def test_fullwidth_parens_are_stripped(self):
        """全角括号是国内化验室表的默认写法，必须能剥。"""
        self.assertEqual(_norm_header("出糙率（%）"), "出糙率")
        self.assertEqual(_norm_header("水分（%）"), "水分")
        self.assertEqual(_norm_header("脂肪酸值（mg/100g）"), "脂肪酸值")

    def test_halfwidth_still_works(self):
        self.assertEqual(_norm_header("出糙率(%)"), "出糙率")
        self.assertEqual(_norm_header("计划产量(t)"), "计划产量")

    def test_fullwidth_header_hits_known_item(self):
        known = {_norm_header(i) for i in KNOWN_ITEMS}
        self.assertIn(_norm_header("出糙率（%）"), known)
        self.assertIn(_norm_header("整精米率（%）"), known)

    def test_space_and_case(self):
        self.assertEqual(_norm_header("  Batch No "), "batchno")


class TestGuessHeaderRow(unittest.TestCase):
    def test_skips_title_and_note_rows(self):
        """标题行 + 业务说明行压在上面时，要认出第 3 行才是表头。"""
        rows = [
            ["送检水稻检测"],
            ["黄色信息在采购时，需针对重点确认检测"],
            ["日期", "品种", "水分(%)", "出糙率（%）", "整精米率（%）"],
            ["2026-11-09", "泰优", "13.2", "76.5", "45.0"],
        ]
        self.assertEqual(guess_header_row(rows), 2)

    def test_normal_table_keeps_first_row(self):
        rows = [
            ["批次号", "产品名称", "检测项目", "实测值", "限值"],
            ["DEMO-01", "中晚籼稻谷", "水分含量", "13.2", "13.5"],
        ]
        self.assertEqual(guess_header_row(rows), 0)

    def test_no_recognizable_header_falls_back_to_first_nonempty(self):
        """全都认不出来时退回第一行非空行，不能把数据行当表头。"""
        rows = [
            ["", "", ""],
            ["甲", "乙", "丙"],
            ["1", "2", "3"],
        ]
        self.assertEqual(guess_header_row(rows), 1)

    def test_all_blank_returns_zero(self):
        self.assertEqual(guess_header_row([[], [None, " "]]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

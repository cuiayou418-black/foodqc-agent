# -*- coding: utf-8 -*-
"""检测项目简称映射回归（2026-11-09）。

真实送检表的列头写「水分」「杂质」「黄粒」「谷外糙米」，词典里是「水分含量」
「杂质含量」「黄粒米含量」「谷外糙米含量」。不映射，32 列里只认出出糙率、
整精米率两项——这就是用户连续三次问「还是只有两个指标吗」的原因。

口径：只做**一对一**映射。语义不明确的（整米）、会合并成重复记录的
（同类互混 / 长短互混 → 互混率）一律不映射，宁可认不出也别悄悄合并。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qc_daily.config import QCDailyConfig  # noqa: E402
from qc_daily.importer import convert, normalize_item  # noqa: E402


class TestNormalizeItem(unittest.TestCase):
    def test_short_names_map_to_dictionary(self):
        for short, full in (("水分", "水分含量"), ("杂质", "杂质含量"),
                            ("黄粒", "黄粒米含量"), ("黄粒米", "黄粒米含量"),
                            ("谷外糙米", "谷外糙米含量")):
            self.assertEqual(normalize_item(short), full, short)

    def test_full_names_pass_through(self):
        for name in ("出糙率", "整精米率", "水分含量", "互混率"):
            self.assertEqual(normalize_item(name), name)

    def test_ambiguous_names_are_not_merged(self):
        """同类互混 / 长短互混 / 整米 不映射：合并会在同批同日造重复记录。"""
        for name in ("同类互混", "长短互混", "整米", "垩白", "C含量"):
            self.assertEqual(normalize_item(name), name, name)

    def test_headers_with_units_still_map(self):
        self.assertEqual(normalize_item("水分（%）"), "水分含量")
        self.assertEqual(normalize_item("杂质\n(%)"), "杂质含量")


class TestWideTableItemRecognition(unittest.TestCase):
    def _records(self, headers: dict) -> list[dict]:
        rec = {"批次号": "DEMO-A01", "产品名称": "中晚籼稻谷", "日期": "2026-11-09"}
        rec.update(headers)
        return [rec]

    def test_short_columns_are_recognized(self):
        rows, notes = convert("送检水稻检测", self._records({
            "出糙率\n（%）": 76.0, "整精米率\n（%）": 45.0,
            "水分": 13.2, "杂质": 0.6, "黄粒": 0.2, "谷外糙米": 1.0,
        }), "quality", QCDailyConfig.load())
        items = {r["item"] for r in rows}
        self.assertEqual(items, {"出糙率", "整精米率", "水分含量", "杂质含量",
                                 "黄粒米含量", "谷外糙米含量"})
        self.assertEqual(len(rows), 6)

    def test_duplicate_columns_are_collapsed_with_note(self):
        """同时有「水分」和「水分含量」时只认第一列，别造重复记录。"""
        rows, notes = convert("送检水稻检测", self._records({
            "水分": 13.2, "水分含量": 13.4,
        }), "quality", QCDailyConfig.load())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item"], "水分含量")
        self.assertTrue(any("都指向" in n for n in notes), notes)


if __name__ == "__main__":
    unittest.main()

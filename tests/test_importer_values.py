# -*- coding: utf-8 -*-
"""导入层 · 实测值语义回归（2026-11-08 夜测 S1/S6 止血后补）

起因（两条都能让人拿到错结论）：
  1. 实测值列写 `>5.0` 被 _to_float 抹掉比较符读成 5.0，方向 ≤ 的项目被判「合格」；
  2. `＜0.5` / `十三点二` 读成 None 照写进库，整单崩在 compare() 的英文 TypeError。

口径（先分诊，再比对）：
  空格子 = 未测该项目（静默跳过）；
  写了但读不懂 = 真值未知（按原文保留、该行不判、其余照跑）；
  限值列写 `≤13.5` / `≥77.0` 是合法写法，单独用 _to_limit_float 剥方向符号。

跑法：.venv/Scripts/python.exe -m unittest tests.test_importer_values
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qc_daily.config import QCDailyConfig  # noqa: E402
from qc_daily.importer import _to_float, _to_limit_float, convert  # noqa: E402
from qc_daily.models import QCRecord  # noqa: E402


class TestToFloat(unittest.TestCase):
    def test_comparator_is_never_silently_eaten(self):
        """比较符 = 真值未知，绝不能只留数字（S1 的唯一放行漏洞）。"""
        for raw in (">5.0", "<0.5", "≤3.0", "≥77.0", "＜0.5", "＞5.0",
                    "约5", "=5.0", "~5.0"):
            self.assertIsNone(_to_float(raw), f"{raw!r} 不该被读成数字")

    def test_unreadable_text_is_none(self):
        for raw in ("十三点二", "未检出", "ND", "—", "见附页", "/"):
            self.assertIsNone(_to_float(raw), f"{raw!r} 读不出来就别猜")

    def test_unit_suffix_and_fullwidth_digits(self):
        self.assertEqual(_to_float("13.2%"), 13.2)
        self.assertEqual(_to_float("0.50 mg/g"), 0.5)
        self.assertEqual(_to_float("１２．３"), 12.3)
        self.assertEqual(_to_float(" 1,450 "), 1450.0)
        self.assertEqual(_to_float(5), 5.0)

    def test_blank_is_none(self):
        self.assertIsNone(_to_float(None))
        self.assertIsNone(_to_float(""))
        self.assertIsNone(_to_float("   "))


class TestToLimitFloat(unittest.TestCase):
    def test_direction_symbols_allowed_on_limits_only(self):
        self.assertEqual(_to_limit_float("≤13.5"), 13.5)
        self.assertEqual(_to_limit_float("≥77.0"), 77.0)
        self.assertEqual(_to_limit_float("＜0.2"), 0.2)
        self.assertEqual(_to_limit_float("13.5"), 13.5)


class TestConvertSkipsUnknownValues(unittest.TestCase):
    @staticmethod
    def _long_records():
        return [
            {"批次号": "DEMO-Q01", "检测项目": "水分含量", "实测值": ">5.0",
             "限值": "≤13.5", "检验日期": "2026-11-08"},
            {"批次号": "DEMO-Q01", "检测项目": "杂质含量", "实测值": "0.8",
             "限值": "≤1.0", "检验日期": "2026-11-08"},
        ]

    def test_unreadable_row_is_skipped_not_written_as_null(self):
        """一格读不懂不再整表陪葬：该行不判，其余照跑，原值进 notes。"""
        rows, notes = convert("手工表", self._long_records(), "quality", QCDailyConfig.load())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item"], "杂质含量")
        self.assertEqual(rows[0]["value"], 0.8)
        self.assertEqual(rows[0]["limit_value"], 1.0)
        self.assertTrue(any("按原文保留" in n and ">5.0" in n for n in notes), notes)

    def test_fullwidth_and_chinese_number_do_not_crash_the_table(self):
        rec = {"批次号": "DEMO-Q01", "检测项目": "水分含量", "实测值": "＜0.5",
               "限值": "≤13.5", "检验日期": "2026-11-08"}
        rows, notes = convert("手工表", [rec, self._long_records()[1]], "quality",
                              QCDailyConfig.load())
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("＜0.5" in n for n in notes), notes)

    def test_wide_table_keeps_other_items(self):
        records = [{"批次号": "DEMO-Q01", "产品名称": "籼稻谷", "检验日期": "2026-11-08",
                    "水分含量": ">5.0", "杂质含量": "0.8", "出糙率": "十三点二"}]
        rows, notes = convert("宽表", records, "quality", QCDailyConfig.load())
        self.assertEqual([r["item"] for r in rows], ["杂质含量"])
        self.assertTrue(any(">5.0" in n for n in notes), notes)
        self.assertTrue(any("十三点二" in n for n in notes), notes)


class TestQCRecordNoGuess(unittest.TestCase):
    def test_missing_value_becomes_not_judged(self):
        """最后一道防线：就算真有 NULL 流到模型层，也不许崩、不许判合格。"""
        r = QCRecord(batch_id="DEMO-Q01", product="籼稻谷", item="水分含量",
                     value=None, unit="%", direction="≤", limit_value=13.5,
                     standard="", limit_source="", verdict="", tested_at="",
                     inspector="", lab="")
        self.assertEqual(r.verdict, "不判")
        self.assertEqual(r.ratio, 0.0)


if __name__ == "__main__":
    unittest.main()

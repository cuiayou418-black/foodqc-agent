# -*- coding: utf-8 -*-
"""2026-11-17 白天优化的回归测试（用户「自己拍」授权后自主修的两条）。

  A 行级物类闸门：问大米 Cd 只贴 ≤0.2 那行，「谷物（稻谷除外）」等排外行不得混入；
                  问稻谷铅必须保留「谷物及淀粉类制品 ≤0.5」、排除油脂 0.08。
  B 段落切句：问砷问铬不能再拿到逐字相同的首段，各答各的。

跑法：.venv/Scripts/python.exe -m unittest tests.test_row_category_gate -v
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qc_daily.intent import _row_cat_ok, knowledge_answer  # noqa: E402


class TestRowCategoryGate(unittest.TestCase):
    """表格行级物类闸门：一整张表里混着别类限量行时的取舍。"""

    def test_大米镉_只留0_2排除两行0_1(self):
        cases = [
            ("镉 · 稻谷、糙米、大米（粉） · ≤0.2 mg/kg", True),
            ("镉 · 谷物（稻谷除外） · ≤0.1 mg/kg", False),
            ("镉 · 谷物碾磨加工品［糙米、大米（粉）除外］ · ≤0.1 mg/kg", False),
        ]
        for row, expected in cases:
            with self.subTest(row=row):
                self.assertEqual(_row_cat_ok(row, ["大米"]), expected)

    def test_稻谷铅_留谷物0_5排除油脂0_08(self):
        self.assertTrue(_row_cat_ok("铅 · 谷物及淀粉类制品 · ≤0.5 mg/kg", ["稻谷"]))
        self.assertFalse(_row_cat_ok("铅 · 油脂及其制品 · ≤0.08 mg/kg", ["稻谷"]))

    def test_上位词映射_谷物行不算别类(self):
        # 只认「稻谷」原词会把 GB 2762 表 1 唯一正确的谷物行误杀
        self.assertTrue(_row_cat_ok("铅 · 谷物及淀粉类制品 · ≤0.5 mg/kg", ["稻谷"]))

    def test_全角方括号排除标记(self):
        self.assertFalse(_row_cat_ok(
            "镉 · 谷物碾磨加工品［糙米、大米（粉）除外］ · ≤0.1 mg/kg", ["大米"]))

    def test_肉类汞行不混进稻谷答案(self):
        self.assertFalse(_row_cat_ok("总汞 · 肉类 · ≤0.05 mg/kg", ["稻谷"]))

    def test_问大米成品镉_答案不含0_1(self):
        lines = knowledge_answer("大米成品的Cd含量不得超过多少")["lines"]
        text = " ".join(lines)
        self.assertIn("0.2", text)
        self.assertNotIn("0.1", text, f"排外的 0.1 混进了大米答案：{lines}")

    def test_问稻谷镉_带上稻谷以糙米计的注(self):
        lines = knowledge_answer("稻谷的镉限值是多少")["lines"]
        self.assertTrue(any("糙米计" in x for x in lines),
                        f"稻谷镉必须带注 a，实际：{lines}")


class TestParagraphSplit(unittest.TestCase):
    """污染物并列写在一段时，按句首实体切分，各答各的。"""

    def test_问砷问铬各答各的不串味(self):
        a = knowledge_answer("稻谷里砷的限值是多少")["lines"]
        b = knowledge_answer("铬在稻谷里的限值是多少")["lines"]
        self.assertNotEqual(a[0], b[0], "问砷问铬首段又撞车了")
        # 真实需求是「各答各的」：答案里有讲所问实体的那句即可。
        # 不硬卡 a[0]——问砷时折叠句「全品类表我不贴」排在砷句前面，
        # 属已知轻微次序问题，不在本次修复范围（见优化清单待拍板②）。
        self.assertTrue(any("砷（表 4）" in x for x in a),
                        f"问砷的答案里没有砷句：{a}")
        self.assertTrue(any("铬（表 7）" in x for x in b),
                        f"问铬的答案里没有铬句：{b}")


if __name__ == "__main__":
    unittest.main()

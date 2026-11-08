# -*- coding: utf-8 -*-
"""日报展示筛选口径回归（2026-11-08 小崔定）。

日报只逐条列出「超标 / 真值未知不判 / 录入疑点」；
接近限值但未超标、以及正常项都只汇总、不逐条列。
判定本身不在这里产生，只测展示筛选。
"""
import unittest

from qc_daily.templates import _attention_rows, ATTENTION_RULES, NEAR_LIMIT_RATIO


def rec(batch, item, value, limit, verdict, ratio, direction="≥"):
    return {"batch_id": batch, "item": item, "value": value, "limit_value": limit,
            "verdict": verdict, "ratio": ratio, "direction": direction,
            "standard": "GB 1350—2025", "limit_source": "表1 第3等 原文第5页"}


class TestAttentionFilter(unittest.TestCase):
    def test_only_abnormal_rows_are_listed(self):
        rows = [
            rec("B1", "出糙率", 68.0, 75.0, "不合格", 1.10),   # 超标 → 列
            rec("B1", "整精米率", 39.5, 44.0, "不合格", 1.11),  # 超标 → 列
            rec("B2", "出糙率", 75.3, 75.0, "合格", 0.996),    # 接近但未超标 → 不列
            rec("B2", "整精米率", 62.0, 44.0, "合格", 0.71),   # 正常 → 不列
            rec("B3", "水分", None, 13.5, "不判", 0.0),         # 真值未知 → 列
        ]
        att, normal, counts, near = _attention_rows(rows, [])
        self.assertEqual(len(att), 3)
        self.assertEqual(normal, 2)
        self.assertEqual(near, 1)                      # 只汇总提示
        self.assertEqual(counts["超标 / 低于下限"], 2)
        self.assertEqual(counts["真值未知 / 不判"], 1)
        self.assertEqual([r["verdict"] for _, r in att], ["不合格", "不合格", "不判"])

    def test_near_limit_is_not_a_listing_rule(self):
        """接近限值不得成为逐条展示条件，否则日报会退化成全量罗列。"""
        names = [n for n, _ in ATTENTION_RULES]
        self.assertNotIn("接近限值", names)
        self.assertEqual(len(names), 3)
        self.assertGreaterEqual(NEAR_LIMIT_RATIO, 0.8)

    def test_suspect_row_is_listed_even_if_verdict_ok(self):
        rows = [rec("B1", "出糙率", 78.2, 75.0, "合格", 0.96)]
        suspects = [{"batch_id": "B1", "item": "出糙率", "reason": "疑似小数点"}]
        att, normal, counts, near = _attention_rows(rows, suspects)
        self.assertEqual(len(att), 1)
        self.assertEqual(counts["录入疑点"], 1)
        self.assertIn("录入疑点", att[0][0])

    def test_all_normal_gives_empty_detail(self):
        # 下限型 ratio = limit/value，要远离限值需 value 明显大于 limit
        rows = [rec("B1", "出糙率", 95.0, 75.0, "合格", 75.0 / 95.0),
                rec("B1", "整精米率", 62.0, 44.0, "合格", 44.0 / 62.0)]
        att, normal, counts, near = _attention_rows(rows, [])
        self.assertEqual(att, [])
        self.assertEqual(normal, 2)
        self.assertEqual(near, 0)


if __name__ == "__main__":
    unittest.main()

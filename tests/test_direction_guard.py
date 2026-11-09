# -*- coding: utf-8 -*-
"""判定方向防判反（2026-11-09 事故回归）。

事故经过：演示表的「指标方向」列被统一填成「≥」，而稻谷 7 项里只有出糙率、
整精米率是下限（≥），其余 5 项是上限（≤）。系统当时优先信表里那一列，结果：

    黄粒米含量 0.0%（限值 ≤1.0）→ 判「不合格」
    谷外糙米   0.4%（限值 ≤4.0）→ 判「不合格」

上限型几乎都是安全/储存指标（水分、杂质、黄粒米、谷外糙米、镉、黄曲霉毒素），
判反＝把超标粮放行，比数值算错严重一个数量级。所以这里立三条规矩：
    1. 标准配置有方向 → 以配置为准；
    2. 表里写反 → 按标准判，但必须留痕（且每个项目只提示一次，不刷屏）；
    3. 表里没写 → 照旧回落到配置。
"""
from __future__ import annotations

import unittest

from qc_daily.config import QCDailyConfig
from qc_daily.importer import convert, resolve_direction, _norm_direction
from qc_daily.models import QCRecord


def _verdict(row: dict) -> str:
    """按导入行的方向判一次，模拟 models.QCRecord 的硬判定。"""
    rec = QCRecord(
        batch_id=row["batch_id"], product=row["product"], item=row["item"],
        value=row["value"], unit=row["unit"], direction=row["direction"],
        limit_value=row["limit_value"], standard=row.get("standard", ""),
        limit_source=row.get("limit_source", ""), verdict="",
        tested_at="2026-11-09 00:00:00", inspector="", lab="",
    )
    return rec.verdict


def _wide_rows(direction_cell: str, water=13.1, yellow=0.0, husked=0.4, rice=76.0):
    """造一张最小宽表：水分/黄粒/谷外糙米是上限型，出糙率是下限型。"""
    recs = [{
        "日期": "2026-11-09",
        "批次号": "T-01",
        "产品名称": "中晚籼稻谷",
        "指标方向": direction_cell,
        "水分\n (%)": water,
        "黄粒\n(%)": yellow,
        "谷外糙米\n（%）": husked,
        "出糙率\n（%）": rice,
        "水分含量限值": 13.5,
        "黄粒米含量限值": 1.0,
        "谷外糙米含量限值": 4.0,
        "出糙率限值": 75.0,
        "单位": "%",
        "标准号": "GB 1350—2025",
    }]
    rows, notes = convert("s", recs, "quality", QCDailyConfig.load())
    return rows, notes


class TestDirectionGuard(unittest.TestCase):
    def test_upper_limit_items_not_judged_as_fail(self):
        """上限型：黄粒米 0.0、谷外糙米 0.4、水分 13.1 都必须判合格。"""
        rows, _ = _wide_rows("≥")
        verdicts = {r["item"]: _verdict(r) for r in rows}
        self.assertEqual(verdicts["黄粒米含量"], "合格")
        self.assertEqual(verdicts["谷外糙米含量"], "合格")
        self.assertEqual(verdicts["水分含量"], "合格")
        # 出糙率 76.0 ≥ 75.0，本来就是下限型，也合格
        self.assertEqual(verdicts["出糙率"], "合格")

    def test_sheet_direction_overridden_by_standard(self):
        """表里写 ≥，标准是 ≤ → 方向按标准，不按表。"""
        rows, notes = _wide_rows("≥")
        for r in rows:
            if r["item"] in ("水分含量", "黄粒米含量", "谷外糙米含量"):
                self.assertEqual(r["direction"], "≤", f"{r['item']} 应被纠正为 ≤")
        self.assertTrue(any("判定方向冲突" in n for n in notes), "必须留痕告知改了方向")

    def test_conflict_warned_once_per_item(self):
        """冲突提示按项目去重，不能每行刷一条。"""
        rows, notes = _wide_rows("≥")
        hits = [n for n in notes if "判定方向冲突" in n and "水分含量" in n]
        self.assertEqual(len(hits), 1, f"水分应只提示一次，实际 {len(hits)} 次")

    def test_no_sheet_direction_falls_back_to_config(self):
        """表里没这一列 → 从标准配置取方向（≤ / ≥ 各自正确）。"""
        rows, notes = _wide_rows("")
        by_item = {r["item"]: r["direction"] for r in rows}
        self.assertEqual(by_item["水分含量"], "≤")
        self.assertEqual(by_item["出糙率"], "≥")
        self.assertFalse(any("判定方向冲突" in n for n in notes))

    def test_upper_limit_still_catches_real_exceedance(self):
        """方向纠正不能矫枉过正：水分 14.6 超过 13.5 仍须判不合格。"""
        rows, _ = _wide_rows("≥", water=14.6)
        water = [r for r in rows if r["item"] == "水分含量"][0]
        self.assertEqual(_verdict(water), "不合格")


class TestDirectionNormalize(unittest.TestCase):
    def test_alias_forms(self):
        for raw in ("≤", "<=", "不大于", "不超过", "上限"):
            self.assertEqual(_norm_direction(raw), "≤", raw)
        for raw in ("≥", ">=", "不小于", "不低于", "下限"):
            self.assertEqual(_norm_direction(raw), "≥", raw)

    def test_unknown_returns_empty(self):
        self.assertEqual(_norm_direction("待定"), "")
        self.assertEqual(_norm_direction(None), "")

    def test_resolve_prefers_standard_when_sheet_blank(self):
        noted, notes = set(), []
        self.assertEqual(resolve_direction("", "≤", "水分含量", noted, notes), "≤")
        self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()

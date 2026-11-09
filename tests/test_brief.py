# -*- coding: utf-8 -*-
"""经理版日报（一屏定论）的行为约束。

这一版是给品管部经理在手机邮件里看的，判断标准和审核版完全不同：
审核版要求"每一项都查得到出处"，经理版要求"一屏看完知道要不要动手"。
所以这里锁死四件事：
    1. 不超过一屏（卡片最多 5 张，其余折叠成一行）；
    2. 不出放行结论（不许出现「可直接入库」「准予放行」这类话）；
    3. 没测出/存疑的批次算「待复核」，绝不能落进「合格」那堆；
    4. 未审核只能是草稿，审核后才变正式件。
"""
from __future__ import annotations

import unittest

from qc_daily.brief import build_brief, compute_stats
from qc_daily.models import DailyMetrics


def _rec(batch, item, value, limit, direction, verdict, ratio=1.0):
    return {
        "batch_id": batch, "product": "中晚籼稻谷", "item": item, "value": value,
        "unit": "%", "direction": direction, "limit_value": limit,
        "verdict": verdict, "ratio": ratio, "standard": "GB 1350—2025",
        "limit_source": "GB 1350—2025 表1 第3等 原文第5页",
    }


def _metrics(date="2026-11-09"):
    return DailyMetrics(report_date=date, plant="示例米厂（占位）",
                        generated_at="2026-11-09 08:00:00")


def _meta(qc, suspects=None, review=None):
    return {"run_id": "R-test", "fingerprint": "f" * 64,
            "qc_details": qc, "suspects": suspects or [],
            "review": review or {"status": "pending", "by": "", "at": ""}}


class TestBriefBasics(unittest.TestCase):
    def test_all_pass_says_so_without_promising_release(self):
        qc = [_rec("B1", "水分含量", 13.1, 13.5, "≤", "合格", 0.97),
              _rec("B1", "出糙率", 76.2, 75.0, "≥", "合格", 0.98)]
        brief = build_brief(_metrics(), _meta(qc))
        self.assertIn("全部指标在限值内", brief["text"])
        self.assertIn("全部合格", brief["subject"])
        # 系统不替人放行
        self.assertNotIn("可直接入库", brief["html"])

    def test_need_batch_listed_with_plain_language(self):
        qc = [
            _rec("B1", "水分含量", 14.6, 13.5, "≤", "不合格", 1.08),
            _rec("B2", "水分含量", 13.1, 13.5, "≤", "合格", 0.97),
        ]
        brief = build_brief(_metrics(), _meta(qc))
        self.assertEqual(brief["stats"]["need_n"], 1)
        self.assertIn("1 批需处理", brief["subject"])
        # 说人话：不是「水分含量不合格」，而是「水分偏高 14.6%（要求 ≤13.5%）」
        self.assertIn("水分偏高", brief["html"])
        self.assertIn("要求 ≤13.5%", brief["html"])
        self.assertIn("烘干达标后再入库", brief["html"])

    def test_unknown_falls_into_review_not_ok(self):
        """没测出/不判的批次不能算「合格」。"""
        qc = [_rec("B1", "水分含量", None, 13.5, "≤", "不判", 0.0),
              _rec("B2", "水分含量", 13.1, 13.5, "≤", "合格", 0.97)]
        stats = compute_stats(_metrics(), _meta(qc))
        self.assertEqual(stats["review_n"], 1)
        self.assertEqual(stats["ok_n"], 1)
        self.assertEqual([b["batch_id"] for b in stats["review"]], ["B1"])

    def test_no_release_wording_anywhere(self):
        """红线：经理版不得出现替人放行的措辞。"""
        qc = [_rec("B1", "杂质含量", 2.8, 1.0, "≤", "不合格", 2.8),
              _rec("B2", "水分含量", 13.1, 13.5, "≤", "合格", 0.97)]
        brief = build_brief(_metrics(), _meta(qc))
        for banned in ("可直接入库", "准予放行", "同意放行", "建议放行", "予以接收"):
            self.assertNotIn(banned, brief["html"], f"正文出现放行措辞：{banned}")
            self.assertNotIn(banned, brief["text"], f"纯文本出现放行措辞：{banned}")


class TestBriefFitsOneScreen(unittest.TestCase):
    def test_cards_capped_and_rest_folded(self):
        # 7 批全超标 → 只列 5 张卡，其余折成一行，且批次号仍然点名
        qc = [_rec(f"B{i}", "杂质含量", 2.0, 1.0, "≤", "不合格", 2.0) for i in range(1, 8)]
        brief = build_brief(_metrics(), _meta(qc))
        self.assertEqual(brief["stats"]["need_n"], 7)
        self.assertEqual(brief["html"].count("参考处置："), 5, "卡片数应被限制为 5")
        self.assertIn("另有 <b>2</b> 批同类问题", brief["html"])
        self.assertIn("B6", brief["html"])   # 折叠行里仍点名，不丢批次
        self.assertIn("B7", brief["html"])

    def test_each_card_shows_at_most_two_items(self):
        qc = [_rec("B1", "杂质含量", 2.8, 1.0, "≤", "不合格", 2.8),
              _rec("B1", "水分含量", 14.6, 13.5, "≤", "不合格", 1.08),
              _rec("B1", "出糙率", 68.0, 75.0, "≥", "不合格", 1.10),
              _rec("B1", "整精米率", 39.5, 44.0, "≥", "不合格", 1.11)]
        brief = build_brief(_metrics(), _meta(qc))
        self.assertIn("等 4 项不达标", brief["html"])
        self.assertEqual(brief["text"].count("参考处置："), 1)


class TestBriefReviewGate(unittest.TestCase):
    def test_pending_marks_draft_in_subject_and_body(self):
        qc = [_rec("B1", "水分含量", 14.6, 13.5, "≤", "不合格", 1.08)]
        brief = build_brief(_metrics(), _meta(qc))
        self.assertTrue(brief["subject"].startswith("【草稿·待审核】"))
        self.assertIn("未经班长审核", brief["html"])
        self.assertIn("请勿直接外发", brief["text"])

    def test_approved_shows_reviewer(self):
        qc = [_rec("B1", "水分含量", 14.6, 13.5, "≤", "不合格", 1.08)]
        review = {"status": "approved", "by": "张班长", "at": "2026-11-09 08:50"}
        brief = build_brief(_metrics(), _meta(qc, review=review))
        self.assertNotIn("草稿", brief["subject"])
        self.assertIn("已审核：班长 张班长", brief["html"])
        self.assertIn("2026-11-09 08:50", brief["html"])

    def test_subject_is_short_enough_for_phone(self):
        qc = [_rec(f"B{i}", "杂质含量", 2.0, 1.0, "≤", "不合格", 2.0) for i in range(1, 4)]
        brief = build_brief(_metrics(), _meta(qc))
        self.assertLessEqual(len(brief["subject"]), 40, f"主题过长：{brief['subject']}")


if __name__ == "__main__":
    unittest.main()

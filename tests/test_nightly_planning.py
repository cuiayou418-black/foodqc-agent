# -*- coding: utf-8 -*-
"""巡检脚本自身的两个回归（2026-11-09 修）。

1. 轮次归零：跨日必须从第 1 轮开始。昨晚就是因为接着前一晚的数，
   19:16 起只判读了 4 轮就收尾。
2. 并发锁：同一次触发被连点两次（相隔 17 秒）不该消耗两个轮次预算。
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("nightly_run_round", ROOT / "nightly" / "run_round.py")
nightly = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nightly)


class TestPlanNightly(unittest.TestCase):
    def test_new_day_restarts_from_round_one(self):
        n, tag, same_day = nightly.plan_nightly(
            {"last_date": "2026-11-08", "next_round": 5}, "2026-11-09")
        self.assertEqual(n, 1, "跨日必须归 1，否则一晚凑不满 6 轮")
        self.assertEqual(tag, "2026-11-09")
        self.assertFalse(same_day)

    def test_same_day_advances_with_b_suffix(self):
        n, tag, same_day = nightly.plan_nightly(
            {"last_date": "2026-11-09", "next_round": 3}, "2026-11-09")
        self.assertEqual(n, 3)
        self.assertEqual(tag, "2026-11-09-b")
        self.assertTrue(same_day)

    def test_empty_state_starts_at_one(self):
        n, tag, _ = nightly.plan_nightly({}, "2026-11-09")
        self.assertEqual((n, tag), (1, "2026-11-09"))


class TestNightlyLock(unittest.TestCase):
    def test_second_trigger_within_window_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / ".nightly.lock"
            old = nightly.NIGHTLY_LOCK
            nightly.NIGHTLY_LOCK = lock
            try:
                self.assertFalse(nightly._nightly_locked(), "第一次应当放行")
                self.assertTrue(lock.is_file(), "放行时必须落锁")
                self.assertTrue(nightly._nightly_locked(), "17 秒后连点必须被拦")
            finally:
                nightly.NIGHTLY_LOCK = old

    def test_stale_lock_does_not_block_forever(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / ".nightly.lock"
            lock.write_text(json.dumps({"ts": 1}), encoding="utf-8")   # 极旧的时间戳
            old = nightly.NIGHTLY_LOCK
            nightly.NIGHTLY_LOCK = lock
            try:
                self.assertFalse(nightly._nightly_locked(), "超过阈值应当自动放行")
            finally:
                nightly.NIGHTLY_LOCK = old


if __name__ == "__main__":
    unittest.main()

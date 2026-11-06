# -*- coding: utf-8 -*-
"""检验记录录入自查 · 6 条回归

项目没装 pytest，所以本文件可直接跑：
    .venv/Scripts/python.exe tests/test_validate.py
装了 pytest 时也能被收集（都是普通 test_* 函数 + 裸断言）：
    .venv/Scripts/python.exe -m pytest tests/test_validate.py -q

每条测试都断言「命中数正确」，并统一验证 Suspect 没改动原记录
（数值、verdict、整行 dict 都要和跑之前一模一样）。
"""
from __future__ import annotations

import copy
import os
import sys
from dataclasses import FrozenInstanceError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qc_daily.models import QCRecord              # noqa: E402
from qc_daily.validate import STATUS, validate_records  # noqa: E402


def rec(batch, item, value, unit="%", direction="≤", **kw):
    """造一行化验室记录（dict 形态，带 verdict，用来验证不被改写）。"""
    row = {
        "batch_id": batch, "product": "籼稻谷", "item": item, "value": value,
        "unit": unit, "direction": direction, "limit_value": 1.0,
        "standard": "GB 1350—2025", "limit_source": "GB 1350—2025 表1 第3等 原文第5页",
        "tested_at": "2026-11-05 08:00:00", "inspector": "张三", "lab": "化验室",
        "verdict": "合格",
    }
    row.update(kw)
    return row


def assert_unchanged(records, snapshot):
    """★ 只读铁律：Suspect 绝不改动原记录。"""
    assert records == snapshot, "validate_records 改动了原记录"
    for before, after in zip(snapshot, records):
        assert before["value"] == after["value"], "数值被改写"
        assert before["verdict"] == after["verdict"], "verdict 被改写"


def test_1_正常记录():
    records = [
        rec("B01", "出糙率", 75.0), rec("B01", "整精米率", 44.0),
        rec("B01", "水分含量", 13.5), rec("B01", "杂质含量", 0.8),
        rec("B02", "出糙率", 75.2), rec("B02", "整精米率", 44.3),
        rec("B02", "水分含量", 13.6), rec("B02", "杂质含量", 0.9),
        rec("B03", "出糙率", 74.8), rec("B03", "整精米率", 43.9),
        rec("B03", "水分含量", 13.4), rec("B03", "杂质含量", 0.7),
    ]
    snapshot = copy.deepcopy(records)
    out = validate_records(records)
    assert len(out) == 0, f"正常记录不该标疑，实际 {len(out)} 条"
    assert_unchanged(records, snapshot)

    # 安全指标：规范数字不标疑；读不出来 / 符号不规整一律「无法判定，转人工」
    assert validate_records([rec("B01", "镉", 0.15, unit="mg/kg")]) == []
    bad = [
        rec("B01", "镉", "<0.2", unit="mg/kg"),
        rec("B01", "无机砷", "未检出", unit="mg/kg"),
        rec("B01", "黄曲霉毒素B1", 0.5, unit="μg/kg", direction="≥"),
    ]
    bad_snapshot = copy.deepcopy(bad)
    bad_out = validate_records(bad)
    assert len(bad_out) == 3, f"安全指标应命中 3 条，实际 {len(bad_out)}"
    assert all("无法判定，转人工" in s.reason for s in bad_out)
    assert all(s.status == STATUS for s in bad_out)
    assert all(s.建议动作 == s.suggestion for s in bad_out)
    assert_unchanged(bad, bad_snapshot)

    # QCRecord 对象（agent 实际传入的形态）也能自查，且 verdict 由原公式保持不变
    qr = [QCRecord(batch_id="B01", product="籼稻谷", item="水分含量", value=13.5,
                   unit="%", direction="≤", limit_value=14.5, standard="GB 1350—2025",
                   limit_source="表1 原文第5页", verdict="", tested_at="2026-11-05",
                   inspector="张三", lab="化验室")]
    assert validate_records(qr) == []
    assert qr[0].verdict == "合格"

    # 疑点对象本身也不可改（frozen）
    try:
        bad_out[0].value = 999
        raise AssertionError("Suspect 应当不可变")
    except FrozenInstanceError:
        pass


def test_2_小数点敲错():
    # 14.5 敲成 1.45：小数点左移一位，10 倍偏小
    records = [rec("B01", "水分含量", 14.5),
               rec("B02", "水分含量", 14.5),
               rec("B03", "水分含量", 1.45)]
    snapshot = copy.deepcopy(records)
    out = validate_records(records)
    assert len(out) == 2, f"应命中 2 条（相邻两条都标疑），实际 {len(out)}"
    b03 = [s for s in out if s.batch_id == "B03"][0]
    assert "MAGNITUDE_JUMP" in b03.codes and "DECIMAL_SHIFT" in b03.codes
    assert "10 倍" in b03.reason
    assert all(s.status == STATUS for s in out)
    assert all("待人工确认" in s.suggestion for s in out)
    assert_unchanged(records, snapshot)


def test_3_10倍敲大():
    # 14.5 敲成 145：10 倍偏大，同时超百分类值域
    records = [rec("B01", "水分含量", 14.5),
               rec("B02", "水分含量", 14.5),
               rec("B03", "水分含量", 145)]
    snapshot = copy.deepcopy(records)
    out = validate_records(records)
    assert len(out) == 2, f"应命中 2 条，实际 {len(out)}"
    b03 = [s for s in out if s.batch_id == "B03"][0]
    assert {"RANGE", "MAGNITUDE_JUMP", "DECIMAL_SHIFT"} <= set(b03.codes)
    assert "145" in b03.reason and "待人工确认" in b03.suggestion
    assert_unchanged(records, snapshot)


def test_4_跨列矛盾():
    records = [rec("B01", "出糙率", 75.0), rec("B01", "整精米率", 80.0)]
    snapshot = copy.deepcopy(records)
    out = validate_records(records)
    assert len(out) == 1, f"应命中 1 条，实际 {len(out)}"
    assert out[0].codes == ("CROSS_COLUMN",)
    assert out[0].item == "整精米率" and "不可能" in out[0].reason
    assert_unchanged(records, snapshot)


def test_5_漏填():
    records = [rec("B01", "水分含量", ""), rec("", "出糙率", 75.0)]
    snapshot = copy.deepcopy(records)
    out = validate_records(records)
    assert len(out) == 2, f"应命中 2 条，实际 {len(out)}"
    assert all(s.codes == ("MISSING",) for s in out)
    assert all("空着" in s.reason for s in out)
    assert_unchanged(records, snapshot)


def test_6_重复():
    records = [rec("B01", "水分含量", 13.5),
               rec("B01", "水分含量", 13.5),
               rec("B02", "水分含量", 13.6)]
    snapshot = copy.deepcopy(records)
    out = validate_records(records)
    assert len(out) == 1, f"应命中 1 条，实际 {len(out)}"
    assert out[0].codes == ("DUPLICATE",)
    assert "2 行" in out[0].reason
    assert len(records) == 3, "不应该删掉重复行（只读）"
    assert_unchanged(records, snapshot)


_EXPECTED = {
    "test_1_正常记录": "疑点 0 条 + 安全指标转人工 3 条 + 只读断言",
    "test_2_小数点敲错": "疑点 2 条（14.5→1.45）",
    "test_3_10倍敲大": "疑点 2 条（14.5→145）",
    "test_4_跨列矛盾": "疑点 1 条（整精米率>出糙率）",
    "test_5_漏填": "疑点 2 条（实测值空 / 批次号空）",
    "test_6_重复": "疑点 1 条（同批次同项目两行）",
}


def _main():
    tests = [test_1_正常记录, test_2_小数点敲错, test_3_10倍敲大,
             test_4_跨列矛盾, test_5_漏填, test_6_重复]
    print()
    print("=" * 60)
    print("检验记录录入自查 · 回归测试（只读 / 只标待人工确认）")
    print("=" * 60)
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  [OK]   {t.__name__:<18} {_EXPECTED[t.__name__]}")
        except Exception as e:
            print(f"  [FAIL] {t.__name__:<18} {type(e).__name__}: {e}")
    print("-" * 60)
    if passed == len(tests):
        print(f"  {len(tests)} 条全绿（含 Suspect 不改动原记录的断言）")
    else:
        print(f"  仅 {passed}/{len(tests)} 条通过，未全绿")
    print()
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_main())

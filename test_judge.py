# -*- coding: utf-8 -*-
"""
test_judge.py —— Day15 回归测试（脚手架，我搭的，跑它就完事）

数据来源：gb-bot/_dump_GB1350-2025.txt 第 162–185 行
         = GB 1350-2025《稻谷》表 1 质量要求原文

跑法（每次新开终端都要先激活环境）：
    cd C:/Users/1/foodqc-agent
    . .venv/Scripts/activate
    python test_judge.py

为什么要 6 条而不是 4 条：
    - 3 条不达标 + 3 条合格  → 防止「一律返回不达标」这种偷懒改法蒙混过关
    - 含 2 条边界（正好等于限值）→ 抓 > 和 >= 写错的老毛病
"""
from judge_tool import judge

# (实测值, 限值原文, 期望结论, 指标说明)
CASES = [
    (76.5, "≥77.0",  "不达标", "出糙率 二等   低于下限"),
    (77.0, "≥77.0",  "合格",   "出糙率 二等   正好卡下限"),
    (45.0, "≥50.0",  "不达标", "整精米率 一等 低于下限"),
    (1.5,  "≤1.0",   "不达标", "杂质含量     超过上限"),
    (1.0,  "≤1.0",   "合格",   "杂质含量     正好卡上限"),
    (14.2, "≤13.5",  "不达标", "水分含量     超过上限"),
]


def main():
    ok = 0
    print()
    for value, limit_expr, expect, name in CASES:
        got = judge(value, limit_expr)
        verdict = got.split("（")[0].strip()
        passed = verdict == expect
        ok += passed
        flag = "OK " if passed else "FAIL"
        print(f"  [{flag}] {name}  实测 {value} vs 限值 {limit_expr}")
        print(f"         期望 {expect} / 实际 {verdict}")
    print()
    print(f"  通过 {ok} / {len(CASES)}")
    print()
    if ok < len(CASES):
        print("  还没修好 —— 去 judge_tool.py 里找标着「这里是 bug」的那段。")
        raise SystemExit(1)
    print("  全绿：判定逻辑已能正确处理 ≥ / ≤ 两个方向。")
    print()


if __name__ == "__main__":
    main()

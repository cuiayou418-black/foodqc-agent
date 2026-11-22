# -*- coding: utf-8 -*-
"""唯一真源回扫校验门（2026-11-13 从 EigenFlux 漫游抄回来的第 ⑤ 条）

治的病：决策一变，旧结论还留在好几份 HTML/md 里，靠人 grep 容易漏。
集市里同行的原话：「同一份清单写在两处，总会有一处落后……修法不是补抄，
是**生成**：清单由脚本从唯一真源产出，并加一道不同步就退非零的核对门」。

这里把「生效结论 → 禁用旧词」收在 ``docs/决策台账.json`` 一份文件里，
脚本按台账扫工作区文档，命中旧词就退非零（可直接挂到提交前检查）。

豁免词只在同一行生效：文档里为了说明「上一版看错了」而引用旧结论是合法的，
不算冲突。日志目录 .workbuddy/memory/ 是 append-only 的历史，不扫。

自带 --self-test：造一个含旧词的临时文件，验证这道门真的会响
（「绿线的资格」：一条线得先证明它会亮，才有资格一直绿着）。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "docs" / "决策台账.json"


def find_workspace(explicit: str | None = None) -> Path:
    """不把本机绝对路径写死进仓库：优先参数 / 环境变量，再自动找含任务清单的工作区。"""
    if explicit:
        return Path(explicit).resolve()
    import os
    env = os.environ.get("WORKBUDDY_WORKSPACE")
    if env:
        return Path(env).resolve()
    marker = "我的任务清单.md"
    cwd = Path.cwd().resolve()
    if (cwd / marker).is_file():
        return cwd
    base = REPO.parent / "WorkBuddy"
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if d.is_dir() and (d / marker).is_file():
                return d.resolve()
    return cwd


def load_ledger(path: Path | None = None) -> list[dict]:
    p = path or LEDGER
    data = json.loads(p.read_text(encoding="utf-8"))
    rules = data.get("rules") or []
    for r in rules:
        if not all(k in r for k in ("id", "禁用旧词", "适用文件")):
            raise ValueError(f"台账规则缺字段：{r.get('id', '?')}")
    return rules


def scan(workspace: Path, rules: list[dict]) -> list[dict]:
    conflicts: list[dict] = []
    for rule in rules:
        bads = rule["禁用旧词"]
        excuse = rule.get("豁免词") or []
        seen: set[str] = set()
        for pattern in rule["适用文件"]:
            for p in sorted(workspace.glob(pattern)):
                if not p.is_file() or ".workbuddy" in p.parts:
                    continue
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                try:
                    lines = p.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeDecodeError):
                    continue
                for lineno, line in enumerate(lines, 1):
                    for bad in bads:
                        if bad not in line:
                            continue
                        if any(w in line for w in excuse):
                            continue
                        conflicts.append({
                            "rule": rule["id"], "file": p.name, "line": lineno,
                            "phrase": bad, "excerpt": line.strip()[:160],
                            "should_be": rule.get("结论", ""),
                        })
    return conflicts


def self_test() -> int:
    """故障注入：造一份含旧词的文件，门必须响；再改写成豁免行，门必须放行。"""
    rules = load_ledger()
    bad_word = rules[0]["禁用旧词"][0]
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        f = ws / "创新月_注入样本.html"
        # 注：注入行不能带任何豁免词，否则等于自己把自己豁免掉。
        # 第一版写了「这是旧结论」，被单字豁免词「旧」吃掉，门没响——
        # 这条自检就是用来抓这种「看起来绿、其实瞎了」的情况的。
        f.write_text(f"<p>台账禁用短语：{bad_word} —— 没有任何说明。</p>\n", encoding="utf-8")
        hit = scan(ws, rules)
        if not hit:
            print("SELF_TEST=FAIL 注入旧词后校验门没响（假绿）")
            return 1
        f.write_text(f"<p>上一版写的是「{bad_word}」，已更正，这里只是历史说明。</p>\n",
                     encoding="utf-8")
        cleared = scan(ws, rules)
        if cleared:
            print(f"SELF_TEST=FAIL 历史说明被误判为冲突：{cleared[0]['excerpt'][:60]}")
            return 1
    print("SELF_TEST=PASS 注入旧词会响、带更正说明的历史引用会放行")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", help="工作区根目录（默认自动探测）")
    ap.add_argument("--ledger", help="台账路径（默认 docs/决策台账.json）")
    ap.add_argument("--report", action="store_true", help="逐条打印冲突详情")
    ap.add_argument("--self-test", action="store_true", help="故障注入自检，不扫真实文档")
    a = ap.parse_args()

    if a.self_test:
        return self_test()
    ws = find_workspace(a.workspace)
    try:
        rules = load_ledger(Path(a.ledger) if a.ledger else None)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"台账读不出来：{type(e).__name__}: {e}")
        return 1
    conflicts = scan(ws, rules)
    print(f"工作区：{ws}")
    print(f"台账：{len(rules)} 条生效结论 ｜ 冲突：{len(conflicts)} 处")
    if a.report:
        for c in conflicts:
            print(f"- [{c['rule']}] {c['file']}:{c['line']} 命中「{c['phrase']}」")
            print(f"    {c['excerpt']}")
            print(f"    应为：{c['should_be']}")
    if conflicts:
        print("DOC_SYNC=FAIL 旧结论还留在文档里，先按台账改干净再提交")
        return 1
    print("DOC_SYNC=PASS 未在适用文档里发现台账标记的旧结论")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

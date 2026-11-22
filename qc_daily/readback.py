# -*- coding: utf-8 -*-
"""产物回读 + 副作用断言（2026-11-13 从 EigenFlux 漫游抄回来的验收层）

一句话：**别用 status=SUCCESS 证明写成功，要把产物读回来核一遍。**

集市里同行的原话是「完成判定三件套：产物是否落盘且可解析、服务端是否回执、
独立事件账本是否记了一行，三者缺一记 UNKNOWN，不降级成成功」；以及最容易被
忘掉的一半——「大多数团队只查『该变的是否变了』，不查『不该变的是否变了』」。

映射到本项目：

===================  ==========================================
三件套                 这里的对应物
===================  ==========================================
产物落盘且可解析       res.outputs 里每个文件：按后缀真解析一遍
服务端回执             request.json 已落盘 + summary 可序列化
独立事件账本           trace.jsonl 至少有一行
===================  ==========================================

副作用断言：运行前对「只读源」拍一份哈希快照，收尾时逐条比对。
只读源 = 领域知识库 / 限值库 / 源数据库，**一个字节都不该被任务改动**，
这正是项目四条铁律里的「只读不写源数据」。

本模块只做读取和比对，不改判定逻辑、不改限值、不改任何源数据。
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

#: 只读源清单。任务运行期间这些文件必须字节不变；跑之前不存在的就不登记。
READONLY_SOURCES = (
    "corpus/limits.json",
    "corpus/chunks.jsonl",
    "qc_source.db",
    "qc_daily_config.json",
)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()[:16]


def snapshot_sources(root: Path) -> dict[str, str]:
    """运行前拍快照：只记录已存在的只读源文件的哈希。"""
    out: dict[str, str] = {}
    for rel in READONLY_SOURCES:
        p = Path(root) / rel
        if p.is_file():
            try:
                out[rel] = _sha(p.read_bytes())
            except OSError:
                out[rel] = "UNREADABLE"
    return out


def diff_sources(root: Path, before: dict[str, str]) -> dict:
    """收尾比对：一个字节变了都算违反「只读不写源数据」。

    before 为空（比如异常分支没拍到快照）时如实记 UNKNOWN，绝不假装通过。
    """
    if not before:
        return {"overall": "UNKNOWN", "reason": "运行前没拍到源数据快照，无法判定副作用",
                "checked": 0, "changed": [], "missing": []}
    changed, missing = [], []
    for rel, digest in before.items():
        p = Path(root) / rel
        if not p.is_file():
            missing.append(rel)
            continue
        try:
            now = _sha(p.read_bytes())
        except OSError:
            missing.append(rel)
            continue
        if now != digest:
            changed.append(f"{rel}: {digest} → {now}")
    ok = not (changed or missing)
    return {"overall": "PASS" if ok else "FAIL",
            "reason": ("只读源字节未变" if ok else
                       f"只读源被改动 {len(changed)} 处 / 丢失 {len(missing)} 处"),
            "checked": len(before), "changed": changed, "missing": missing}


def _parseable(p: Path) -> tuple[bool, str]:
    """按后缀真的解析一遍，不是只看文件在不在。"""
    try:
        data = p.read_bytes()
    except OSError as e:
        return False, f"读不出来：{type(e).__name__}"
    if not data:
        return False, "空文件"
    suf = p.suffix.lower()
    if suf == ".json":
        try:
            json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            return False, f"JSON 解析失败：{type(e).__name__}"
    elif suf in (".html", ".htm"):
        text = data.decode("utf-8", errors="replace")
        if "<" not in text or ">" not in text or len(text) < 80:
            return False, "不像 HTML（过短或没有标签）"
    elif suf == ".xlsx":
        if not zipfile.is_zipfile(p):
            return False, "不是合法的 xlsx（zip）"
    elif suf == ".pdf":
        if not data.startswith(b"%PDF"):
            return False, "不是合法的 PDF"
    # .txt / .eml / .md / 其它：非空即可
    return True, ""


def readback_artifacts(run_dir: Path, outputs: list[str]) -> list[dict]:
    """逐份回读：存在 + 可解析 + 字节数 + 指纹。路径越界直接拒绝。"""
    root = Path(run_dir).resolve()
    out: list[dict] = []
    for rel in outputs or []:
        name = str(rel).replace("\\", "/")
        target = (root / name).resolve()
        if root != target and root not in target.parents:
            out.append({"name": name, "parseable": False, "why": "产物路径越界（目录穿越被拒）"})
            continue
        if not target.is_file():
            out.append({"name": name, "parseable": False, "why": "产物没落盘"})
            continue
        ok, why = _parseable(target)
        try:
            size = target.stat().st_size
            digest = _sha(target.read_bytes())
        except OSError:
            size, digest, ok, why = 0, "", False, "读取指纹失败"
        out.append({"name": name, "parseable": ok, "why": why,
                    "size": size, "digest": digest})
    return out


def verify_run(run_dir: Path, outputs: list[str], sources_before: dict,
               receipt_payload: dict | None = None,
               repo_root: Path | None = None) -> dict:
    """完成判定三件套 + 副作用断言，返回一份可写进 summary.json 的回执。

    overall 只有三态：PASS / FAIL（源数据被动过）/ UNKNOWN（证据不足）。
    三件套缺一不降级成 PASS —— 这是「回执 ≠ 产物」的落地。
    """
    root = Path(run_dir).resolve()
    arts = readback_artifacts(root, outputs)

    receipt_ok = (root / "request.json").is_file()
    if receipt_payload is not None:
        try:
            json.dumps(receipt_payload, ensure_ascii=False)
        except (TypeError, ValueError):
            receipt_ok = False
    ledger = root / "trace.jsonl"
    ledger_ok = ledger.is_file() and bool(ledger.read_text(encoding="utf-8").strip())

    triple = {
        "artifacts_landed_and_parseable": bool(arts) and all(a["parseable"] for a in arts),
        "server_receipt": receipt_ok,
        "independent_ledger": ledger_ok,
    }
    # repo_root 可注入，单测才能在不碰真实源数据的前提下验证「被改动」分支。
    side = diff_sources(repo_root or _repo_root(), sources_before)

    if side["overall"] == "FAIL":
        overall = "FAIL"
        reason = side["reason"] + "（违反「只读不写源数据」）"
    elif not all(triple.values()) or side["overall"] != "PASS":
        missing = [k for k, v in triple.items() if not v]
        overall = "UNKNOWN"
        reason = ("完成判定三件套缺：" + "/".join(missing) if missing else side["reason"])
    else:
        overall = "PASS"
        reason = "三件套齐全，只读源字节未变"

    return {
        "overall": overall,
        "reason": reason,
        "triple": triple,
        "artifacts": arts,
        "side_effects": side,
        "denominator": {
            "declared": len(outputs or []),
            "landed": sum(1 for a in arts if a["parseable"]),
            "unparsed": sum(1 for a in arts if not a["parseable"]),
        },
    }


def _repo_root() -> Path:
    """从 qc_daily/readback.py 的位置反推仓库根，不依赖调用方传参。"""
    return Path(__file__).resolve().parent.parent

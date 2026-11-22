# -*- coding: utf-8 -*-
# artifacts.py：任务产物的「查看」层（配套，不是主入口）。
# 输入：run_id / output_root；输出：按 run_id 列本次产物 + 历史索引，全部相对路径。
# 只读扫描；任何路径先经白名单校验，防止 ../ 目录穿越读到仓库其它文件。
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _root(output_root: str) -> Path:
    p = Path(output_root)
    return p if p.is_absolute() else ROOT / p


def resolve_run_dir(run_id: str, output_root: str = "runs") -> Path:
    """run_id 只允许 [A-Za-z0-9_-]，杜绝路径穿越。"""
    if not run_id or not all(ch.isalnum() or ch in "-_" for ch in run_id):
        raise ValueError("非法 run_id")
    d = (_root(output_root) / run_id).resolve()
    if _root(output_root).resolve() not in d.parents:
        raise ValueError("run 目录越界")
    return d


#: 产物白名单的唯一口径。resolve_artifact（直链下载）与 list_artifacts（列表）
#: 必须共用它——早先直链只查「路径在不在 run 目录内」，不查「是不是产物」，
#: 于是被红线拒绝的 run，其 inputs/ 原表、snapshots/ 快照、input.db、request.json
#: 全部可以无鉴权直链取走，甚至把 C:\Users\… 本机路径一起给出去
#: （夜测 11-19 r05 立账、11-21 r05/r06 复验，连续 3 晚的严重项）。
#: 这些文件是「复盘素材」，不是给人下载的产物；审计自己用磁盘路径读，不走这层。
_SKIP_DIRS = {"inputs", "snapshots"}
_SKIP_FILES = {"input.db", ".DS_Store", "request.json", "audit_entry.json", "trace.jsonl"}


def _is_artifact(rel: str) -> bool:
    """相对 run 目录的路径是否属于「可下载产物」。两个入口共用这道闸。"""
    parts = rel.replace("\\", "/").split("/")
    return parts[0] not in _SKIP_DIRS and rel not in _SKIP_FILES


def resolve_artifact(run_id: str, name: str, output_root: str = "runs") -> Path:
    """产物必须落在 run 目录内，且属于可下载产物；复盘素材一律拒绝。"""
    run_dir = resolve_run_dir(run_id, output_root)
    target = (run_dir / name).resolve()
    if run_dir != target and run_dir not in target.parents:
        raise ValueError("产物路径越界（目录穿越被拒绝）")
    rel = target.relative_to(run_dir).as_posix()
    if not _is_artifact(rel):
        raise ValueError("这不是可下载产物（上传原件 / 快照 / 输入库仅供本机复盘）")
    if not target.is_file():
        raise FileNotFoundError(name)
    return target


def list_artifacts(run_id: str, output_root: str = "runs") -> list[dict]:
    """按 run_id 列本次产物；不回传本机绝对路径，只给相对 run 目录的名字。"""
    run_dir = resolve_run_dir(run_id, output_root)
    if not run_dir.is_dir():
        return []
    out = []
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(run_dir).as_posix()
        if not _is_artifact(rel):
            continue
        out.append({"name": rel, "size": p.stat().st_size,
                    "kind": p.suffix.lstrip(".") or "file"})
    return out


def index_runs(output_root: str = "runs", limit: int = 30) -> list[dict]:
    """历史索引：只读各 run 目录的 summary.json，缺了就跳过，不猜状态。"""
    root = _root(output_root)
    if not root.is_dir():
        return []
    out = []
    for d in sorted((x for x in root.iterdir() if x.is_dir()), reverse=True)[:limit]:
        f = d / "summary.json"
        if not f.is_file():
            continue
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # 终态事件码（no_rows / missing_limits / validation_error …）让历史列表
        # 能一眼分清「哪种错」，不用靠 errors[0] 前 22 字去猜（夜测 11-10 r05）。
        stop_code = ""
        for t in reversed(s.get("trace") or []):
            if str(t.get("state", "")) not in ("failed", "partial", "pending_review"):
                continue
            ev = str(t.get("event", ""))
            if ev and ev not in ("state_change", "finished", "report_written"):
                stop_code = ev
                break
        out.append({"run_id": s.get("run_id", d.name), "task_id": s.get("task_id", ""),
                    "status": s.get("status", ""), "report_date": s.get("report_date", ""),
                    "started_at": s.get("started_at", ""), "stop_code": stop_code,
                    # 历史列表要能说「错在哪」，否则同名的六条"出错了"完全看不出差别
                    "errors": [str(e) for e in (s.get("errors") or []) if str(e).strip()]})
    return out

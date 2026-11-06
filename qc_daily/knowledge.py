"""知识库可视化管理 · 核心层（只读现有库 + 本地待核对区）

三条设计红线（改动前先读）：
1. 绝不写入 corpus/limits.json、gb-bot/chunks.jsonl、corpus/*.md
   —— 现有知识库与判定链在本模块里一律只读。
2. 上传原件一律落在 corpus/_inbox/（整个目录被 .gitignore 排除）；
   公司内部材料再单独进 private/ 子目录，界面标红警示。
3. 上传后状态是「待核对」。confirm() 只把状态改成「已核对·未接入判定链」，
   不把任何数字写进判定链 —— 守住 corpus/README.md 的核对铁律。
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
INBOX = CORPUS / "_inbox"
PUBLIC_DIR = INBOX / "public"
PRIVATE_DIR = INBOX / "private"
MANIFEST = INBOX / "manifest.json"
LIMITS = CORPUS / "limits.json"
CHUNKS = ROOT / "gb-bot" / "chunks.jsonl"

ALLOWED_EXT = {".md", ".txt", ".pdf", ".csv", ".json", ".xlsx"}
MAX_BYTES = 20 * 1024 * 1024
PUBLIC_LABEL = "公开标准 / 自写笔记"
PRIVATE_LABEL = "公司内部材料（禁止外传）"
STATUS_PENDING = "待核对"
STATUS_CONFIRMED = "已核对 · 未接入判定链"

_RE_HEADING = re.compile(r"^(?:#{1,6}\s|\d+(?:\.\d+)*[\s\u3000])")
_RE_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _ensure_dirs() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(filename: str) -> str:
    """保留中文的文件名安全化（Flask 的 secure_filename 会吃掉中文）。"""
    name = os.path.basename(filename or "").strip()
    name = _RE_UNSAFE.sub("_", name)
    return name[:120] or "upload"


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def split_entries(text: str, limit: int = 500) -> list[str]:
    """通用启发式切块：md 标题或「3.1 / 1　范围」式条款号開新块，其余按空行分段。

    只用于界面展示条目数与预览，绝不写回 chunks.jsonl。
    """
    blocks: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        s = line.rstrip()
        if not s.strip():
            if buf:
                blocks.append("\n".join(buf).strip())
                buf = []
            continue
        if _RE_HEADING.match(s) and buf:
            blocks.append("\n".join(buf).strip())
            buf = []
        buf.append(s)
    if buf:
        blocks.append("\n".join(buf).strip())
    return [b for b in blocks if len(b) >= 8][:limit]


def _extract_text(path: Path, ext: str) -> tuple[str, str]:
    """返回 (文本, 警告)。警告非空时界面给黄条提示，不算失败。"""
    if ext in {".md", ".txt", ".csv", ".json"}:
        return _decode(path.read_bytes()), ""
    if ext == ".xlsx":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets[:3]:
                lines.append(f"# 工作表 {ws.title}")
                for row in ws.iter_rows(max_row=60, values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        lines.append(" | ".join(cells))
            wb.close()
            return "\n".join(lines), "仅预览前 3 张表、前 60 行；检验记录属业务数据，请走「数据导入」页"
        except Exception as e:
            return "", f"Excel 解析失败：{e}"
    if ext == ".pdf":
        try:
            import fitz
            doc = fitz.open(str(path))
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
            if len(text.strip()) < 20:
                return "", ("PDF 抽出的文字极少（多为扫描件，或像 GB 2716 / GB 5009.227 "
                            "那样缺 ToUnicode 映射）。文件已保存，但需人工核对原文，不自动切块。")
            return text, "PDF 文本由程序抽取，限值数字必须人工逐格核对后才能使用"
        except Exception as e:
            return "", f"PDF 解析失败：{e}"
    return "", f"暂不支持解析 {ext}"


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return path.name  # 测试把暂存区指到临时目录时不报错


def _file_meta(path: Path) -> tuple[int, str]:
    stat = path.stat()
    return stat.st_size, datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")


def _builtin_status(path: Path, text: str) -> str:
    if "待核对" in path.name or "待核档案" in text[:2000] or "尚未取得原文" in text[:2000]:
        return STATUS_PENDING
    return "已核对"


def scan_builtin() -> list[dict]:
    """只读扫描现有知识库，汇总「文件 / 条目 / 基本信息」。"""
    items: list[dict] = []
    for path in sorted(CORPUS.glob("*.md")):
        text = _decode(path.read_bytes())
        size, mtime = _file_meta(path)
        items.append({
            "id": f"builtin-{path.stem}", "name": path.name, "kind": "标准笔记",
            "origin": "内置知识库", "source": "public", "private": False,
            "status": _builtin_status(path, text), "entries": len(split_entries(text)),
            "size": size, "mtime": mtime,
            "note": "corpus/ 下的人工核对笔记；只读，不参与自动判定",
            "warning": "",
        })
    if LIMITS.exists():
        size, mtime = _file_meta(LIMITS)
        try:
            n = len(json.loads(_decode(LIMITS.read_bytes())))
        except Exception:
            n = 0
        items.append({
            "id": "builtin-limits", "name": "limits.json", "kind": "结构化限值库",
            "origin": "内置知识库", "source": "public", "private": False,
            "status": "已核对", "entries": n, "size": size, "mtime": mtime,
            "note": "⭐ 判定链唯一数据源，供代码读取；本模块绝不改写",
            "warning": "含 GB 1350-2025（2027-01-01 实施），现执行 2009 版，版本开关须核对",
        })
    if CHUNKS.exists():
        size, mtime = _file_meta(CHUNKS)
        n = sum(1 for ln in _decode(CHUNKS.read_bytes()).splitlines() if ln.strip())
        items.append({
            "id": "builtin-chunks", "name": "gb-bot/chunks.jsonl", "kind": "条款切块索引",
            "origin": "内置知识库", "source": "public", "private": False,
            "status": "已核对", "entries": n, "size": size, "mtime": mtime,
            "note": "GB 1350-2025 条款级切块，检索层用；本模块绝不改写",
            "warning": "",
        })
    return items


def load_manifest() -> list[dict]:
    _ensure_dirs()
    if not MANIFEST.exists():
        return []
    try:
        data = json.loads(_decode(MANIFEST.read_bytes()))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_manifest(records: list[dict]) -> None:
    _ensure_dirs()
    tmp = MANIFEST.with_name(MANIFEST.name + ".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MANIFEST)


def list_inbox() -> list[dict]:
    """待核对区条目。私有一律带警告，且不返回原始绝对路径给前端。"""
    out = []
    for r in load_manifest():
        item = dict(r)
        item.pop("stored", None)
        if item.get("private"):
            item["warning"] = "⚠️ 公司内部材料：仅本机暂存，禁止外传、禁止 push 到 GitHub"
        out.append(item)
    return out


def overview() -> dict:
    builtin = scan_builtin()
    inbox = list_inbox()
    return {
        "builtin": builtin,
        "inbox": inbox,
        "stats": {
            "builtin_files": len(builtin),
            "builtin_entries": sum(i["entries"] for i in builtin),
            "pending": sum(1 for i in inbox if i["status"] == STATUS_PENDING),
            "private": sum(1 for i in inbox if i.get("private")),
        },
    }


def add_upload(raw: bytes, filename: str, source: str) -> tuple[dict | None, str | None, str]:
    """保存上传文件到待核对区。返回 (新条目, 错误, 警告)。

    source = "public" | "private"；错误非空即失败，界面给红条。
    """
    _ensure_dirs()
    name = _safe_name(filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        return None, f"不支持 {ext or '(无扩展名)'}，仅支持 .md / .txt / .pdf / .csv / .json / .xlsx", ""
    if not raw:
        return None, "文件为空，请检查后重试", ""
    if len(raw) > MAX_BYTES:
        return None, f"文件 {len(raw) / 1048576:.1f}MB 超过 20MB 限制", ""
    if source not in {"public", "private"}:
        return None, "文件来源取值不合法", ""

    private = source == "private"
    target_dir = PRIVATE_DIR if private else PUBLIC_DIR
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stored = target_dir / f"{stamp}_{uuid.uuid4().hex[:6]}_{name}"
    try:
        stored.write_bytes(raw)
    except Exception as e:
        return None, f"文件保存失败：{e}", ""

    text, warning = _extract_text(stored, ext)
    entries = split_entries(text)
    record = {
        "id": uuid.uuid4().hex[:10],
        "name": name,
        "kind": ext.lstrip(".").upper() + " 文件",
        "origin": PRIVATE_LABEL if private else PUBLIC_LABEL,
        "source": source,
        "private": private,
        "status": STATUS_PENDING,
        "entries": len(entries),
        "size": len(raw),
        "mtime": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "已上传至待核对区；人工确认前不参与检索与判定",
        "warning": warning,
        "stored": _relative(stored),
        "preview": "\n\n".join(entries[:3])[:1500],
    }
    records = load_manifest()
    records.insert(0, record)
    _save_manifest(records)
    return record, None, warning


def confirm(record_id: str) -> tuple[dict | None, str | None]:
    """人工确认：只改状态，不写入 limits.json / chunks.jsonl / corpus/*.md。"""
    records = load_manifest()
    for r in records:
        if r.get("id") == record_id:
            if r.get("status") == STATUS_CONFIRMED:
                return r, None  # 幂等：重复点确认不算失败
            r["status"] = STATUS_CONFIRMED
            r["note"] = ("已人工核对，可被检索层引用；⚠️ 尚未接入判定链 —— "
                         "限值必须另行逐格核对后写入 limits.json，本操作不会自动完成")
            r["confirmed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _save_manifest(records)
            return r, None
    return None, "找不到这条记录（可能索引已被清理）"


def preview(record_id: str) -> tuple[str, str | None]:
    for r in load_manifest():
        if r.get("id") == record_id:
            return r.get("preview", "（没有可预览的文本，见该条警告）"), None
    return "", "找不到这条记录"

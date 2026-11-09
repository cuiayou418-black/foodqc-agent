# -*- coding: utf-8 -*-
"""经理版日报（一屏定论）—— 与班长审核版分开出。

为什么不共用一份：
    审核版是**核对底稿**：逐条实测值、限值、标准号、原文页码，班长拿它逐行核、签字。
    经理不看这些。他要的只有三句：今天收了几批、哪几批有问题、要不要他定。
    同一份内容塞给两种人，结果是班长嫌不够细、经理嫌太啰嗦。

经理版的三条硬规矩：
    1. **一屏能看完**。手机打开不滚动或只滚一屏，超过一屏就是失败。
    2. **说人话**。「出糙率偏低」写成「出米率偏低，碾出来米少」。
    3. **不给放行结论**。只报事实 + 参考处置方向，处置由品管部定；
       未审核时整版标草稿，不许看起来像正式件。

数据全部来自已经算好的 DailyMetrics / qc_details，本模块**不做任何判定**，
只做归集与措辞，避免第二处判定逻辑与主链路跑偏。
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from .models import DailyMetrics

# ---------------------------------------------------------------- 说人话
# 左：经理听得懂的结论；右：为什么这事要紧（一句话，不展开）
_PLAIN = {
    "出糙率":       ("出米率偏低", "同样一车稻谷，碾出来的米少"),
    "整精米率":     ("整米少、碎米多", "加工损耗大，出米率和卖相都受影响"),
    "水分含量":     ("水分偏高", "堆着容易发热霉变，不好存"),
    "杂质含量":     ("杂质偏多", "要清杂，实际可用粮少，还容易堵设备"),
    "黄粒米含量":   ("黄粒米偏多", "品质降级，影响卖相和售价"),
    "谷外糙米含量": ("谷外糙米偏多", "要再砻一遍，加工成本上升"),
    "互混率":       ("品种混掺偏多", "一致性差，影响定等"),
    "脂肪酸值":     ("脂肪酸值偏高", "有陈化迹象，不耐存"),
}

# 参考处置方向：**只给方向，不给结论**。每一行末尾统一挂「待品控复核」。
# 字数压到 8 字以内——手机上排三列时，长句会把「卡在哪」那列挤成竖排（2026-11-09 实测）。
_ADVICE = {
    "出糙率":       "降等收购或扣价",
    "整精米率":     "降等收购或扣价",
    "水分含量":     "烘干达标后再入库",
    "杂质含量":     "清杂后复检",
    "黄粒米含量":   "降等收购或拒收",
    "谷外糙米含量": "整理后复检",
    "互混率":       "按混掺比例扣价",
    "脂肪酸值":     "复检确认，必要时拒收",
}

_UNITLESS = "%"


def _num(v: Any) -> str:
    """数值转字符串：13.50 → 13.5，14.0 → 14，不补无意义的零。"""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        s = f"{float(v):.2f}".rstrip("0").rstrip(".")
        return s if s else "0"
    return str(v).strip()


def _limit_text(r: dict) -> str:
    d = r.get("direction") or ""
    unit = r.get("unit") or ""
    lim = r.get("limit_value")
    # 限值统一保留 1 位小数（1.0 / 13.5 / 75.0）：检验限值写成整数容易被误读成「约等于」
    lim_s = f"{float(lim):.1f}" if isinstance(lim, (int, float)) else _num(lim)
    return f"要求 {d}{lim_s}{unit}" if d else f"限值 {lim_s}{unit}"


def _fail_phrase(r: dict) -> str:
    """一行的「卡在哪」：水分偏高 14.6%（要求 ≤13.5%）。"""
    item = r.get("item") or ""
    plain = _PLAIN.get(item, (item, ""))[0]
    unit = r.get("unit") or ""
    return f"{plain} {_num(r.get('value'))}{unit}（{_limit_text(r)}）"


def _group_by_batch(qc: list[dict], suspects: list[dict]) -> "OrderedDict[str, dict]":
    """按批次归集。顺序按出现先后，保证同一份数据两次跑出来顺序相同。"""
    batches: "OrderedDict[str, dict]" = OrderedDict()
    for r in qc:
        bid = str(r.get("batch_id") or "").strip()
        if not bid:
            continue
        b = batches.setdefault(bid, {
            "batch_id": bid, "product": r.get("product") or "",
            "fails": [], "unknown": [], "near": [], "total": 0, "suspects": [],
        })
        b["total"] += 1
        if not b["product"]:
            b["product"] = r.get("product") or ""
        verdict = r.get("verdict")
        if verdict == "不合格":
            b["fails"].append(r)
        elif verdict == "不判":
            b["unknown"].append(r)
        elif float(r.get("ratio") or 0) >= 0.8:
            b["near"].append(r)
    for s in suspects or []:
        bid = str(s.get("batch_id") or "").strip()
        if bid in batches:
            batches[bid]["suspects"].append(s)
    return batches


def _advice_of(b: dict) -> str:
    """参考处置：取最严重那项的处置方向；多项时合并去重，最多两条。"""
    seen: list[str] = []
    for r in sorted(b["fails"], key=lambda x: -(float(x.get("ratio") or 0))):
        a = _ADVICE.get(r.get("item") or "", "")
        if a and a not in seen:
            seen.append(a)
    if not seen:
        return "待品控复核"
    return "；".join(seen[:2])


def compute_stats(m: DailyMetrics, meta: dict) -> dict:
    """经理版要的四个数字 + 批次名单。"""
    qc = meta.get("qc_details") or []
    suspects = meta.get("suspects") or []
    batches = _group_by_batch(qc, suspects)

    need, review, ok = [], [], []
    for b in batches.values():
        if b["fails"]:
            need.append(b)
        elif b["unknown"] or b["suspects"]:
            review.append(b)
        else:
            ok.append(b)

    fail_items: dict[str, int] = {}
    for b in need:
        for r in b["fails"]:
            fail_items[r.get("item") or ""] = fail_items.get(r.get("item") or "", 0) + 1

    return {
        "date": m.report_date,
        "plant": m.plant,
        "batch_total": len(batches),
        "need": need, "review": review, "ok": ok,
        "need_n": len(need), "review_n": len(review), "ok_n": len(ok),
        "fail_records": sum(len(b["fails"]) for b in need),
        "suspect_n": len(suspects),
        "fail_items": fail_items,
        "tests_total": len(qc),
    }


def _review_block(meta: dict) -> tuple[str, str]:
    """审核状态 → (HTML 片段, 纯文本片段)。未审核必须显眼，不能伪装成正式件。"""
    rv = meta.get("review") or {}
    if rv.get("status") == "approved" and rv.get("by"):
        when = rv.get("at") or ""
        txt = f"已审核：班长 {rv['by']}" + (f"　{when}" if when else "")
        html = (f"<div style=\"background:#eaf6ec;border-left:4px solid #1e7e34;padding:10px 14px;"
                f"font-size:13px;color:#1e7e34;margin:0 0 14px\">✓ {txt}　·　内容与审核版一致，可对外发送</div>")
        return html, txt
    txt = "草稿：未经班长审核，请勿直接外发"
    html = (f"<div style=\"background:#fff4e5;border-left:3px solid #d97706;padding:7px 10px;"
            f"font-size:12px;line-height:1.5;color:#8a5300;margin:0 0 8px\">⚠️ {txt}"
            f"　·　系统自动汇总，班长核对后才能发</div>")
    return html, txt


def _subject(stats: dict, m: DailyMetrics, approved: bool) -> str:
    """邮件主题：手机锁屏一行能看完（约 20 字内），细节留在正文里。"""
    prefix = "" if approved else "【草稿·待审核】"
    d = (m.report_date or "")[5:].replace("-", "/")   # 11-09 → 11/09
    if stats["need_n"] == 0 and stats["review_n"] == 0:
        return f"{prefix}【品控日报】{d} 到厂 {stats['batch_total']} 批，全部合格"
    parts = []
    if stats["need_n"]:
        parts.append(f"{stats['need_n']} 批需处理")
    if stats["review_n"]:
        parts.append(f"{stats['review_n']} 批待复核")
    return f"{prefix}【品控日报】{d} 到厂 {stats['batch_total']} 批，" + "，".join(parts)


def _headline(stats: dict) -> str:
    n, total = stats["need_n"], stats["batch_total"]
    if total == 0:
        return "今天没有到货验收记录。"
    if n == 0 and stats["review_n"] == 0:
        return f"今天到厂 {total} 批稻谷，全部指标在限值内。"
    if n == 0:
        return (f"今天到厂 {total} 批稻谷，{stats['ok_n']} 批指标在限值内；"
                f"{stats['review_n']} 批有项目没测出或数据存疑，需品控复核后才能定。")
    head = f"今天到厂 {total} 批稻谷，{stats['ok_n']} 批无超标项，{n} 批需要您定处置。"
    if stats["review_n"]:
        head += f"另有 {stats['review_n']} 批数据待复核。"
    return head


def _html(m: DailyMetrics, meta: dict, stats: dict) -> str:
    FONT = "-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif"
    review_html, _ = _review_block(meta)
    approved = (meta.get("review") or {}).get("status") == "approved"

    # ---- 四个数字 ----
    cells = [
        ("到厂批次", f"{stats['batch_total']}", "批", "#1f2329"),
        ("需处理", f"{stats['need_n']}", "批", "#c0392b" if stats["need_n"] else "#1e7e34"),
        ("超标项次", f"{stats['fail_records']}", "项", "#c0392b" if stats["fail_records"] else "#1e7e34"),
        ("数据疑点", f"{stats['suspect_n']}", "项", "#b7791f" if stats['suspect_n'] else "#1f2329"),
    ]
    last = len(cells) - 1
    cells_html = "".join(
        f"<td style=\"width:25%;text-align:center;padding:12px 4px"
        f"{'border-right:1px solid #eceef1' if i != last else ''}\">"
        f"<div style=\"font-size:24px;font-weight:700;line-height:1.2;color:{c}\">{v}</div>"
        f"<div style=\"font-size:12px;color:#6b7280;margin-top:2px\">{k}<span style=\"font-size:11px\">（{u}）</span></div>"
        f"</td>" for i, (k, v, u, c) in enumerate(cells))

    # ---- 需要处理的批次（卡片式）----
    # 一屏是硬指标：最多列 5 批，每批最多 2 项，超出折叠成一行（清单在审核版里）。
    # 三列表格在手机宽度下会把「参考处置」挤成竖排（2026-11-09 实测），所以每批一块。
    MAX_ROWS, MAX_ITEMS = 5, 2
    if stats["need"]:
        shown_batches = stats["need"][:MAX_ROWS]
        rest = stats["need"][MAX_ROWS:]
        cards = []
        for b in shown_batches:
            fails = sorted(b["fails"], key=lambda x: -(float(x.get("ratio") or 0)))
            shown = "；".join(_fail_phrase(r) for r in fails[:MAX_ITEMS])
            if len(fails) > MAX_ITEMS:
                shown += f"　等 {len(fails)} 项不达标"
            why = _PLAIN.get(fails[0].get("item") or "", ("", ""))[1]
            why_html = (f"<div style=\"font-size:12px;color:#8a8f99;margin-top:3px\">{why}</div>"
                        if why else "")
            prod = (f"<span style=\"font-weight:400;color:#9aa0a6;font-size:11px;margin-left:6px\">"
                    f"{b['product']}</span>" if b.get("product") else "")
            cards.append(
                "<tr><td style=\"padding:0 0 9px\">"
                "<table cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" style=\"border-collapse:collapse;"
                "border:1px solid #f2dcd8;border-left:3px solid #c0392b;border-radius:6px;background:#fffdfd\">"
                "<tr><td style=\"padding:10px 12px\">"
                f"<div style=\"font-family:ui-monospace,Consolas,monospace;font-size:13px;"
                f"font-weight:700;color:#1f2329\">{b['batch_id']}{prod}</div>"
                f"<div style=\"color:#c0392b;font-size:13px;font-weight:600;margin-top:5px;line-height:1.5\">{shown}</div>"
                f"{why_html}"
                "<div style=\"font-size:12px;color:#4b5563;margin-top:7px;border-top:1px dashed #f2dcd8;"
                f"padding-top:6px\">参考处置：<b>{_advice_of(b)}</b>"
                "<span style=\"color:#b7791f;margin-left:6px;font-size:11px\">待品控复核后执行</span></div>"
                "</td></tr></table></td></tr>")
        if rest:
            names = "、".join(b["batch_id"] for b in rest)
            cards.append(
                "<tr><td style=\"padding:0 2px 9px;font-size:12px;color:#6b7280\">"
                f"另有 <b>{len(rest)}</b> 批同类问题：{names}　（明细见审核版）</td></tr>")
        need_html = (
            "<div style=\"font-size:15px;font-weight:700;margin:22px 0 8px\">"
            f"需要处理的 {stats['need_n']} 批</div>"
            "<table cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" style=\"border-collapse:collapse\">"
            + "".join(cards) + "</table>")
    else:
        need_html = ("<div style=\"background:#eaf6ec;border-radius:8px;padding:14px;font-size:14px;"
                     "color:#1e7e34;margin:22px 0 0\">本日没有需要处理的批次。</div>")

    # ---- 待复核 ----
    review_html_block = ""
    if stats["review"]:
        names = "、".join(b["batch_id"] for b in stats["review"])
        review_html_block = (
            "<div style=\"background:#fef8e7;border-radius:8px;padding:12px 14px;font-size:13px;"
            "color:#8a5300;margin:12px 0 0\">🟡 <b>另有 "
            f"{stats['review_n']} 批数据待复核</b>：{names}<br>"
            "<span style=\"font-size:12px\">原因是有项目没测出/读数认不出，或录入存疑。"
            "这些批次<b>不是合格</b>，只是还判不了，请班长确认后再定去留。</span></div>")

    # ---- 正常批次 ----
    ok_html = ""
    if stats["ok"]:
        names = "、".join(b["batch_id"] for b in stats["ok"])
        ok_html = (
            "<div style=\"background:#f7f8fa;border-radius:8px;padding:12px 14px;font-size:13px;"
            f"color:#4b5563;margin:12px 0 0\">🟢 <b>{stats['ok_n']} 批全部指标在限值内（无超标项）</b>："
            f"<span style=\"color:#6b7280\">{names}</span></div>")

    footprint = meta.get("fingerprint") or ""
    run_id = meta.get("run_id") or ""
    warn = meta.get("warning_banner") or ""

    warn_html = ""
    if warn:
        # 标准版本门禁是硬话（“不得作为放行依据”），必须留着；但字号压小，
        # 不能让它把结论撑到第二屏——经理第一眼要看到的是「几批有问题」。
        warn_html = (f"<div style=\"background:#fdecea;border-left:3px solid #c0392b;padding:7px 10px;"
                     f"font-size:11px;line-height:1.5;color:#8f1d16;margin:0 0 8px\">{warn}</div>")

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{meta.get('title', '米粮品控日报')} {m.report_date} · 经理版</title></head>
<body style="margin:0;padding:0;background:#f4f5f7">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:16px 8px">
<tr><td align="center">
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:10px;overflow:hidden;font-family:{FONT};color:#1f2329;font-size:15px;line-height:1.6">
<tr><td style="padding:18px 20px 0">
  <div style="font-size:12px;color:#6b7280;letter-spacing:.5px">米粮部 · 原粮验收　|　{m.plant}</div>
  <div style="font-size:20px;font-weight:700;margin-top:2px">品控日报 · {m.report_date}</div>
</td></tr>
<tr><td style="padding:14px 20px 0">{warn_html}{review_html}</td></tr>
<tr><td style="padding:0 20px">
  <div style="font-size:19px;font-weight:700;line-height:1.5;padding:4px 0 6px">{_headline(stats)}</div>
</td></tr>
<tr><td style="padding:10px 20px 0">
  <table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;background:#fafbfc;border-radius:8px;border:1px solid #eceef1">
  <tr>{cells_html}</tr></table>
</td></tr>
<tr><td style="padding:0 20px">{need_html}{review_html_block}{ok_html}</td></tr>
<tr><td style="padding:20px 20px 0">
  <div style="border-top:1px solid #eceef1;padding-top:14px;font-size:12px;color:#8a8f99;line-height:1.7">
  判定依据：{_standards(meta)}。实测值到判定的每一步都由系统按标准算，<b>不由模型生成</b>；<br>
  查不到限值的项目一律不判、不猜，也不会写成合格。<br>
  逐项实测值、限值与标准原文页码见《品控日报》审核版（班长留存，可索取）。<br>
  <span style="font-size:11px;color:#b0b4bb">报告指纹 {footprint[:16]}　|　运行编号 {run_id}</span>
  </div>
</td></tr>
<tr><td style="padding:16px 20px 20px">
  <div style="font-size:11px;color:#b0b4bb;border-top:1px solid #f0f1f3;padding-top:10px">
  本页只报事实与参考处置方向，<b>放行与处置由品管部决定</b>。系统不签字、不替代审核。
  </div>
</td></tr>
</table>
</td></tr></table></body></html>"""


def _standards(meta: dict) -> str:
    """判定依据：把 qc_details 里出现过的标准号去重列出来，不硬写。"""
    seen: list[str] = []
    for r in meta.get("qc_details") or []:
        s = (r.get("standard") or "").strip()
        if s and s not in seen:
            seen.append(s)
    return "、".join(seen) if seen else "现行有效标准（见审核版逐条出处）"


def _text(m: DailyMetrics, meta: dict, stats: dict) -> str:
    _, review_txt = _review_block(meta)
    lines = [
        f"米粮部 · 原粮验收　|　{m.plant}",
        f"品控日报 · {m.report_date}（经理版）",
        "",
        _headline(stats),
        "",
        f"到厂批次 {stats['batch_total']} 批　|　需处理 {stats['need_n']} 批　|　"
        f"超标项次 {stats['fail_records']} 项　|　数据疑点 {stats['suspect_n']} 项",
        "",
    ]
    if stats["need"]:
        lines.append(f"—— 需要处理的 {stats['need_n']} 批 ——")
        for b in stats["need"][:5]:
            fails = sorted(b["fails"], key=lambda x: -(float(x.get("ratio") or 0)))
            shown = "；".join(_fail_phrase(r) for r in fails[:2])
            if len(fails) > 2:
                shown += f"　等 {len(fails)} 项不达标"
            lines.append(f"[{b['batch_id']}] {shown}")
            lines.append(f"    参考处置：{_advice_of(b)}（待品控复核后执行）")
        if len(stats["need"]) > 5:
            rest = "、".join(b["batch_id"] for b in stats["need"][5:])
            lines.append(f"另有 {len(stats['need']) - 5} 批同类问题：{rest}（明细见审核版）")
        lines.append("")
    if stats["review"]:
        lines.append(f"—— 待复核 {stats['review_n']} 批 ——")
        lines.append("　" + "、".join(b["batch_id"] for b in stats["review"]))
        lines.append("　有项目没测出/读数认不出或录入存疑：不是合格，是还判不了。")
        lines.append("")
    if stats["ok"]:
        lines.append(f"—— 无超标项 {stats['ok_n']} 批 ——")
        lines.append("　" + "、".join(b["batch_id"] for b in stats["ok"]))
        lines.append("")
    lines += [
        "—— 说明 ——",
        f"判定依据：{_standards(meta)}；数值由系统按标准判定，不由模型生成。",
        "逐项实测值、限值与标准页码见《品控日报》审核版。",
        f"{review_txt}",
        f"报告指纹：{(meta.get('fingerprint') or '')[:16]}　运行编号：{meta.get('run_id') or ''}",
    ]
    return "\n".join(lines)


def build_brief(m: DailyMetrics, meta: dict) -> dict:
    """生成经理版三件套：主题、HTML 正文、纯文本正文（另附统计，便于测试断言）。"""
    stats = compute_stats(m, meta)
    approved = (meta.get("review") or {}).get("status") == "approved"
    return {
        "subject": _subject(stats, m, approved),
        "html": _html(m, meta, stats),
        "text": _text(m, meta, stats),
        "stats": stats,
        "approved": approved,
    }


def write_brief(m: DailyMetrics, meta: dict, out_dir: str) -> dict:
    """落盘：HTML（邮件正文）、TXT（纯文本回退）、EML（双击即可在邮件客户端打开）。

    返回 {"html": 路径, "text": 路径, "eml": 路径, "subject": 主题}。
    """
    import os
    from email.message import EmailMessage
    from email.utils import formatdate

    brief = build_brief(m, meta)
    os.makedirs(out_dir, exist_ok=True)
    day = m.report_date
    paths = {
        "html": os.path.join(out_dir, f"经理日报_{day}.html"),
        "text": os.path.join(out_dir, f"经理日报_{day}.txt"),
        "eml": os.path.join(out_dir, f"经理日报_{day}.eml"),
    }
    with open(paths["html"], "w", encoding="utf-8") as f:
        f.write(brief["html"])
    with open(paths["text"], "w", encoding="utf-8") as f:
        f.write(brief["text"])

    msg = EmailMessage()
    msg["Subject"] = brief["subject"]
    msg["From"] = (meta.get("review") or {}).get("from") or "品控日报智能体 <no-reply@local>"
    to = (meta.get("review") or {}).get("to") or []
    msg["To"] = ", ".join(to) if isinstance(to, (list, tuple)) else str(to or "")
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(brief["text"])
    msg.add_alternative(brief["html"], subtype="html")
    with open(paths["eml"], "wb") as f:
        f.write(msg.as_bytes())

    return {**paths, "subject": brief["subject"]}

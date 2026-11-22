# -*- coding: utf-8 -*-
"""负控样本 + 三态分母（2026-11-13 从 EigenFlux 漫游抄回来的工程做法）

这段解决一件事：**怎么证明巡检的判据还活着，而不是一直在报假绿。**

背景：11-10 那次「稻谷水分限值是多少」一次甩 575 字整节，7 个 must_contain
全被喂饱记满分——断言不是没写，是没有一条「必定失败」的样本去戳它。
集市里同行的原话是「每条断言配一个必败样本，正控负控成对出现，缺一条整批记
UNKNOWN」，以及「绿线的资格：一条线得先证明它会亮，才有资格一直绿着」。

三个设计点，讲清为什么这么写：

1. ``criteria_digest`` 取 ``auto_check`` **源码正文**的哈希，不是版本号。
   规则改名、算法换掉、判据挪位置，版本号常常不变，旧负控就会静默全绿。
   源码一改，哈希就变，所有旧负控立刻判 CRITERIA_DRIFT，要求人工重跑。
2. 三列 ``arm_executed`` / ``criteria_digest`` / ``injected_fault``。
   ``arm_executed=false`` = 这次根本没执行，单列 NOT_EXECUTED，
   不混进「跑了但没东西」那一格。这是「没跑成」和「跑成了但没产出」的分界。
3. 分母三分开：executed（样本总数）/ touched（真正喂给判据的条数）/
   effective（判据确实拦住的条数）。别用「看起来一致」代替「结果一致」。

本模块只做校验，不改判定逻辑、不发请求、不碰任何真实业务数据；
所有 fixture 都是自造 DEMO。
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

CONTROLS_FILE = Path(__file__).with_name("negative_controls.jsonl")

UNKNOWN = "UNKNOWN"
NOT_EXECUTED = "NOT_EXECUTED"
OBSERVED = "OBSERVED"

#: 一条负控样本必须由这三列组成，缺一列就是「没资格当证据」。
REQUIRED_COLUMNS = ("arm_executed", "criteria_digest", "injected_fault")


def criteria_digest(checker, family: str) -> str:
    """判据内容哈希 = auto_check 源码 + 断言族名。

    取 16 位足够撞不出巧合；重点是「跟着源码变」而不是「跟着版本号变」。
    checker 拿不到源码时返回空串，verify 会把它当 CRITERIA_DRIFT 处理，
    绝不退化成放行。
    """
    try:
        src = inspect.getsource(checker)
    except (OSError, TypeError):
        return ""
    raw = (src + "|" + str(family)).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:16]


def classify_observation(scn: dict, facts: dict) -> tuple[str, str]:
    """把「没跑」「跑了没东西」「真观测到了」分成三格。

    为什么单列 NOT_EXECUTED：载荷被本地层拦下、请求根本没发出时，
    旧报表里它和「查了没事」长得一模一样（都 PASS、都 0 告警）。
    """
    if not isinstance(facts, dict) or not facts:
        return NOT_EXECUTED, "没有任何观测：请求可能根本没发出"
    has_http = isinstance(facts.get("http"), int)
    payload_keys = ("title", "lines", "paras", "boundary", "need",
                    "status", "run_id", "content", "error")
    has_payload = any(facts.get(k) for k in payload_keys)
    if not has_http and not has_payload:
        return NOT_EXECUTED, "既没有 HTTP 码也没有业务观测"
    if facts.get("error") and not has_http:
        return NOT_EXECUTED, "只有本地异常，没有服务端回执"
    # HTTP 400 + 中文错误是教科书级的正确拒收，它本身就是有效观测。
    if facts.get("http") == 400:
        return OBSERVED, ""
    # 回执正常、但业务观测一格都没有：典型的长输出被截断，不能记 PASS。
    if scn.get("kind") in ("chat", "page") and not any(
            facts.get(k) for k in ("title", "lines", "paras", "boundary", "content")):
        return UNKNOWN, "回执正常但业务观测为空（疑似截断），不记 PASS"
    return OBSERVED, ""


#: 故意弄坏的观测值。全部自造 DEMO，不含真实批次号 / 实测值 / 限值口径。
#: 每项 = (场景骨架, 坏观测, 期望模式, 这条例子在证明什么)
BAD_FIXTURES: dict[str, tuple[dict, dict, str, str]] = {
    "dual_limits_no_clarify": (
        {"id": "NC-01", "kind": "chat", "expect_clarify": True,
         "clarify_entity": "水分含量"},
        {"http": 200, "title": "水分含量 ≤13.5（表1）／≤14.5（表2）",
         "lines": ["两个限值都列出来了，自己挑一个。"], "boundary": []},
        "NOT_PASS",
        "籼粳两套限值并列贴数、不反问物类，expect_clarify 断言必须拦下"),
    "refusal_without_entity": (
        {"id": "NC-02", "kind": "chat", "expect_refuse": True,
         "question": "稻谷里砷的限值是多少"},
        {"http": 200, "title": "查到的相关条款",
         "lines": ["铬、亚硝酸盐：与稻谷验收关系弱，暂不收录。"], "boundary": []},
        "NOT_PASS",
        "拒答蹭了别处的否定词、没回扣被问的实体，实体断言必须拦下"),
    "http_5xx": (
        {"id": "NC-03", "kind": "chat"},
        {"http": 500, "error": "DEMO: upstream error"},
        "NOT_PASS",
        "5xx 必须硬失败，不能被当成「回答简短」放行"),
    "missing_must_contain": (
        {"id": "NC-04", "kind": "chat", "must_contain": ["0.2"]},
        {"http": 200, "title": "查到的相关条款",
         "lines": ["这一段里根本没有那个数字。"], "boundary": []},
        "NOT_PASS",
        "must_contain 缺词必须硬失败"),
    "silent_truncation": (
        {"id": "NC-05", "kind": "chat"},
        {"http": 200},
        "UNKNOWN",
        "回执 200 但业务观测全空，统计层必须降级 UNKNOWN 而不是 PASS"),
    "arm_not_executed": (
        {"id": "NC-06", "kind": "chat"},
        {},
        "NOT_EXECUTED",
        "样本没执行，单列 NOT_EXECUTED，不混进分母"),
}


def load_controls(path: Path | str | None = None) -> list[dict]:
    """读 JSONL；坏行不跳过、不猜测，原样标成坏样本交给 verify 记 UNKNOWN。"""
    p = Path(path) if path else CONTROLS_FILE
    out: list[dict] = []
    if not p.is_file():
        return out
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            out.append({"case_id": f"line-{lineno}", "arm_executed": False,
                        "criteria_digest": "", "injected_fault": "",
                        "_bad_line": f"第 {lineno} 行不是合法 JSON"})
            continue
        row.setdefault("case_id", f"line-{lineno}")
        out.append(row)
    return out


def refresh_controls(checker, path: Path | str | None = None) -> int:
    """判据改过之后重算 digest 并写回 JSONL（显式调用，不在自动巡检里偷写文件）。

    其余列一个字不动，便于 diff 看出「只是判据哈希变了」。
    """
    p = Path(path) if path else CONTROLS_FILE
    rows = load_controls(p)
    lines = []
    for row in rows:
        if row.get("_bad_line"):
            continue
        clean = {k: v for k, v in row.items() if not k.startswith("_")}
        clean["criteria_digest"] = criteria_digest(checker, clean.get("injected_fault", ""))
        lines.append(json.dumps(clean, ensure_ascii=False))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def verify(checker, cases: list[dict] | None = None) -> dict:
    """跑一轮负控，返回三态分母与逐条结果。

    overall=PASS 的条件：**每一条已执行的样本都真的戳中了判据**。
    出现 CRITERIA_DRIFT / SILENT_GREEN / 坏行，整批记 UNKNOWN，
    绝不降级成 PASS —— 这正是「缺一条整批记 UNKNOWN」的意思。
    """
    rows_in = cases if cases is not None else load_controls()
    out_rows: list[dict] = []
    executed = touched = effective = 0
    not_executed = drift = silent = bad = 0

    for case in rows_in:
        executed += 1
        cid = str(case.get("case_id", "?"))
        fault = str(case.get("injected_fault", ""))
        base = {"case_id": cid, "injected_fault": fault}

        if case.get("_bad_line"):
            bad += 1
            out_rows.append({**base, "state": "UNKNOWN",
                             "why": case["_bad_line"]})
            continue
        missing = [c for c in REQUIRED_COLUMNS if c not in case]
        if missing:
            bad += 1
            out_rows.append({**base, "state": "UNKNOWN",
                             "why": f"缺必需列：{'/'.join(missing)}"})
            continue
        if not case.get("arm_executed"):
            not_executed += 1
            out_rows.append({**base, "state": NOT_EXECUTED,
                             "why": "样本未执行，单列不进有效分母"})
            continue

        want = criteria_digest(checker, fault)
        if not want or case.get("criteria_digest") != want:
            drift += 1
            out_rows.append({**base, "state": "CRITERIA_DRIFT",
                             "why": f"判据源码变了（现为 {want or '取不到源码'}），旧负控作废，请跑 --refresh-controls 重核"})
            continue

        fixture = BAD_FIXTURES.get(fault)
        if not fixture:
            bad += 1
            out_rows.append({**base, "state": "UNKNOWN",
                             "why": f"没有登记这个注入点：{fault}"})
            continue

        scn, facts, mode, desc = fixture
        touched += 1
        if mode == "NOT_PASS":
            verdict, notes = checker(scn, facts)
            alive = verdict != "PASS"
            detail = f"auto_check 判 {verdict}；{'; '.join(notes[:2])}"
        elif mode == "UNKNOWN":
            state, why = classify_observation(scn, facts)
            alive = state == UNKNOWN
            detail = f"观测分类 {state}；{why}"
        elif mode == NOT_EXECUTED:
            state, why = classify_observation(scn, facts)
            alive = state == NOT_EXECUTED
            detail = f"观测分类 {state}；{why}"
        else:
            alive = False
            detail = f"未知的期望模式 {mode}"

        if alive:
            effective += 1
            out_rows.append({**base, "state": "EFFECTIVE", "why": detail,
                             "proves": desc})
        else:
            silent += 1
            out_rows.append({**base, "state": "SILENT_GREEN",
                             "why": f"判据没拦住这条必败样本：{detail}"})

    clean = not (drift or silent or bad)
    return {
        "overall": "PASS" if clean else "UNKNOWN",
        "denominator": {
            "executed": executed,
            "touched": touched,
            "effective": effective,
            "not_executed": not_executed,
            "criteria_drift": drift,
            "silent_green": silent,
            "bad_cases": bad,
        },
        "reason": ("每一条已执行的负控都戳中了判据" if clean else
                   "存在判据漂移 / 假绿 / 坏样本，整批记 UNKNOWN，不能算通过"),
        "cases": out_rows,
    }


def self_test(checker) -> dict:
    """--self-test 的入口：不联网、不起服务、不碰真表，只验判据会不会响。"""
    return verify(checker)

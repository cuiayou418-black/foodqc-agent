# -*- coding: utf-8 -*-
# tasks/registry.py：任务注册表，是本轮唯一的扩展点。
# 输入：task_id；输出：TaskSpec（八项规格字段 + 引擎指向）。
# 新增业务的正确姿势：写一个 TaskSpec + 一个 SOP 模块并注册进 TASKS，不改 runner 主干，
# 也不要往 importer.TABLE_COLUMNS 里硬塞第四类表。示例（本轮只留占位、不实现）：
#   TaskSpec(task_id="rice_product_inspection", name="大米成品检验（占位）",
#            table_types=("quality",), required_fields=("batch_id","item","value"),
#            knowledge_scope="大米成品检验 SOP（待补）", output_template="report.html",
#            validation_rules=("validate_collected","validate_records"),
#            human_review_conditions=("限值未核对",), engine="SOP_MODULE", enabled=False)
from __future__ import annotations

from dataclasses import dataclass, field

#: 一等任务：任务台首页展示这 3 张卡片
PRIMARY_TASKS = ("grain_daily_report", "grain_batch_judge", "quality_record_audit")


@dataclass(frozen=True)
class TaskSpec:
    """Phase 2 八字段：适用表格 / 必需字段 / 知识范围 / 输出模板 / 校验规则 / 转人工条件。"""

    task_id: str
    name: str
    table_types: tuple[str, ...]
    required_fields: tuple[str, ...]
    knowledge_scope: str
    output_template: str
    validation_rules: tuple[str, ...]
    human_review_conditions: tuple[str, ...]
    engine: str = "A"                 # A=agent.run / B=qa.run_qa / C=harness / D=validate_records
    auxiliary: bool = False           # True = 辅助只读，只放「更多」，不进首页一等列表
    enabled: bool = True
    #: 给使用人看的一句话：什么时候该点这个任务（页面卡片第二行）
    description: str = ""
    #: 要准备什么（页面「输入」区的提示，没有就留空）
    needs: str = ""
    #: 给验收人/开发看的实现口径（只在页面「技术信息」折叠里出现，不进主界面）
    tech_note: str = ""
    examples: tuple[str, ...] = field(default_factory=tuple)


TASKS: dict[str, TaskSpec] = {
    "grain_daily_report": TaskSpec(
        task_id="grain_daily_report", name="出今天的验收日报",
        table_types=("production", "quality", "warehouse"),
        required_fields=("batch_id", "item", "value", "direction", "limit_value", "tested_at"),
        knowledge_scope="稻谷（原粮）验收 + 米粮品控日报；GB 1350—2009 执行版，"
                        "GB 1350—2025 仅 2027-01-01 预置",
        output_template="report.html",
        validation_rules=("validate_collected", "validate_records", "fingerprint", "verify_report"),
        human_review_conditions=("录入疑点", "限值未人工核对", "error 级校验", "安全指标"),
        engine="A",
        description="今天收的粮、检测都做完了，要出一份能交上去的日报",
        needs="今天所有批次的验收记录表（Excel 或 CSV）",
        tech_note="给若干业务表 → 汇总分析 → 出规范日报与三段式结论（复用 A 全流程）",
        examples=("帮我把这几张表做成稻谷验收日报",)),
    "grain_batch_judge": TaskSpec(
        task_id="grain_batch_judge", name="判一批粮能不能收",
        table_types=("quality",),
        required_fields=("batch_id", "item", "value"),
        knowledge_scope="GB 1350 表1/表2 逐项限值；≥ 下限型与 ≤ 上限型分开判",
        output_template="report.html",
        validation_rules=("validate_records", "fingerprint", "verify_report"),
        human_review_conditions=("能收吗等放行决策", "限值未人工核对", "感官项"),
        engine="C",
        description="来了一车拿不准的，想逐项对标准看卡在哪一项",
        needs="这一批的检测记录（每行一个项目 + 实测值）",
        tech_note="逐项判定 + 标准出处 + 三段式（复用 C，DEMO 限批次）",
        examples=("DEMO-001 这批稻谷出糙率 76.5 能收吗",)),
    "quality_record_audit": TaskSpec(
        task_id="quality_record_audit", name="查录入有没有敲错",
        table_types=("quality",),
        required_fields=("batch_id", "item", "value"),
        knowledge_scope="五类录入疑点；只标「待人工确认」，不改数、不改 verdict",
        output_template="report.html",
        validation_rules=("validate_records", "fingerprint", "verify_report"),
        human_review_conditions=("任一疑点", "安全指标读不出来"),
        engine="D",
        description="表填完了心里没底，先让机器扫一遍有没有明显敲错的数",
        needs="要检查的检验记录表",
        tech_note="只对录入表做疑点自查，不出合格/不合格结论（复用 D）",
        examples=("帮我看看这张检验表有没有敲错的数",)),
    "ask_qa": TaskSpec(
        task_id="ask_qa", name="查标准 / 问概念",
        table_types=(),
        required_fields=(),
        knowledge_scope="corpus/ 国标与方法笔记 + limits.json",
        output_template="answer.txt",
        validation_rules=("fingerprint",),
        human_review_conditions=("查不到限值", "任何判定结论"),
        engine="B", auxiliary=True,
        description="想知道某个指标的限值是多少、某个词是什么意思",
        needs="不用表格，直接打字问就行",
        tech_note="辅助 · 只读：复用 B 链捕获 stdout 落 answer.txt，不出 8 块报告",
        examples=("出糙率 3 等限值是多少",)),
}


def list_tasks(include_auxiliary: bool = False) -> list[dict]:
    """任务台首页只拿一等任务；「更多」才带 auxiliary。"""
    out = []
    for spec in TASKS.values():
        if not spec.enabled or (spec.auxiliary and not include_auxiliary):
            continue
        out.append({
            "task_id": spec.task_id, "name": spec.name,
            "description": spec.description, "engine": spec.engine,
            "needs": spec.needs, "tech_note": spec.tech_note,
            "table_types": list(spec.table_types), "auxiliary": spec.auxiliary,
            "examples": list(spec.examples),
        })
    return out


def get_spec(task_id: str) -> TaskSpec | None:
    return TASKS.get(task_id)

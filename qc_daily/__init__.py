"""品控日报智能体（QC-Daily-Agent）

面向食品/油脂生产企业的品控日报自动化智能体：
- 从生产（MES）、质检（LIMS）、仓储（WMS）系统自动采集数据；
- 按预设模板生成含合格率、缺陷分布、异常批次、趋势分析的日报；
- 支持定时任务、异常预警、多格式导出（HTML/PDF/Excel）、邮件/Webhook 推送；
- 全链路可追溯：原始快照 + 只追加审计日志 + 报告指纹防篡改。

设计原则与 FoodQC-Agent 一致：数字全部由代码按配置限值计算，不经过模型推算；
数据校验失败时宁可不发报告，也绝不错发。
"""

from .agent import QCDailyAgent
from .config import QCDailyConfig

__version__ = "1.0.0"
__all__ = ["QCDailyAgent", "QCDailyConfig"]

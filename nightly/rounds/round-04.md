# 第 4/6 轮 · 2026-11-07T23:10:54

服务：复用已在运行的服务

## [PASS] TB-05 · 18 批 126 项（压力）
- 输入：`做成今天的日报`
    http: 200
    seconds: 0.04
    file: 20261107-231054_夜测_长表.csv
    file_kb: 18.1
    run_id: T20261107-0484388ad6
    run_seconds: 1.5
    status: done
    status_cn: 做完了
    headline: 18 批共 126 个检测项：126 项数值符合、0 项不符合；另有 2 条提醒
    files: 打开日报（网页） / 打开任务报告 / 下载 Excel 表
    groups: ('必须你亲自确认', 2) / ('机器算完、看不出疑点', 126) / ('没找到标准依据，机器不判', 3)
    alerts: 多项指标接近限值
    timeout: False

## [PASS] TB-06 · xlsx 多 sheet
- 输入：`做成日报`
    http: 200
    seconds: 0.05
    file: 20261107-231057_夜测_多sheet.xlsx
    file_kb: 6.3
    run_id: T20261107-73ee60cec5
    run_seconds: 1.5
    status: done
    status_cn: 做完了
    headline: 2 批共 14 个检测项：12 项数值符合、2 项不符合；另有 2 条提醒
    files: 打开日报（网页） / 打开任务报告 / 下载 Excel 表 / 下载 Excel 表
    groups: ('必须你亲自确认', 4) / ('机器算完、看不出疑点', 12) / ('没找到标准依据，机器不判', 3)
    alerts: 总体合格率跌破 95% / 多项指标接近限值
    timeout: False

## [WARN] BD-01 · 表头上方有标题行
- 输入：`做成日报`
    http: 200
    seconds: 0.05
    file: 20261107-231101_夜测_标题行.csv
    file_kb: 0.3
    run_id: T20261107-ced2a2489b
    run_seconds: 1.5
    status: failed
    status_cn: 出错了
    headline: 没做 —— 所有表头都无法映射到 production / quality / warehouse，拒绝执行
    files: （空）
    groups: ('必须你亲自确认', 0) / ('机器算完、看不出疑点', 0) / ('没找到标准依据，机器不判', 0)
    alerts: （空）
    timeout: False
    ! 任务失败：

## [WARN] BD-02 · 数值带百分号 13.2%
- 输入：`做成日报`
    http: 200
    seconds: 0.05
    file: 20261107-231102_夜测_百分号.csv
    file_kb: 0.3
    run_id: T20261107-3471e96a05
    run_seconds: 1.5
    status: failed
    status_cn: 出错了
    headline: 没做 —— error 级校验失败：按「宁可不发，不可错发」不发布报告
    files: （空）
    groups: ('必须你亲自确认', 0) / ('机器算完、看不出疑点', 0) / ('没找到标准依据，机器不判', 0)
    alerts: 报错：'<=' not supported between instances of 'NoneType' and 'float'
    timeout: False
    ! 任务失败：报错：'<=' not supported between instances of 'NoneType' and 'float'

## [WARN] BD-03 · 未检出 / — / / / ND 混用
- 输入：`做成日报`
    http: 200
    seconds: 0.05
    file: 20261107-231104_夜测_未检出.csv
    file_kb: 0.5
    run_id: T20261107-25edb3ac2a
    run_seconds: 1.5
    status: failed
    status_cn: 出错了
    headline: 没做 —— error 级校验失败：按「宁可不发，不可错发」不发布报告
    files: （空）
    groups: ('必须你亲自确认', 0) / ('机器算完、看不出疑点', 0) / ('没找到标准依据，机器不判', 0)
    alerts: 报错：'<=' not supported between instances of 'NoneType' and 'float'
    timeout: False
    ! 任务失败：报错：'<=' not supported between instances of 'NoneType' and 'float'

## [WARN] BD-04 · xlsx 纯公式列（无缓存值）
- 输入：`做成日报`
    http: 200
    seconds: 0.04
    file: 20261107-231105_夜测_公式列.xlsx
    file_kb: 5.0
    run_id: T20261107-840e841b35
    run_seconds: 1.5
    status: failed
    status_cn: 出错了
    headline: 没做 —— 输入表日期 [] 与 report_date=2026-11-07 不匹配；source.sql.queries 用 date(tested_at)=:report_date，静默跑会出空报告
    files: 下载 Excel 表
    groups: ('必须你亲自确认', 0) / ('机器算完、看不出疑点', 0) / ('没找到标准依据，机器不判', 0)
    alerts: （空）
    timeout: False
    ! 任务失败：

## [PASS] BD-05 · 日期与表内不一致
- 输入：`做成日报`
    http: 200
    seconds: 0.05
    file: 20261107-231107_夜测_长表.csv
    file_kb: 2.1
    run_id: T20261107-b9f493e396
    run_seconds: 1.5
    status: done
    status_cn: 做完了
    headline: 2 批共 14 个检测项：12 项数值符合、2 项不符合；另有 2 条提醒
    files: 打开日报（网页） / 打开任务报告 / 下载 Excel 表
    groups: ('必须你亲自确认', 4) / ('机器算完、看不出疑点', 12) / ('没找到标准依据，机器不判', 3)
    alerts: 总体合格率跌破 95% / 多项指标接近限值
    timeout: False

## [WARN] BD-06 · 重复批次号 3 次
- 输入：`做成日报`
    http: 200
    seconds: 0.05
    file: 20261107-231109_夜测_长表.csv
    file_kb: 3.1
    run_id: T20261107-e6c54f529e
    run_seconds: 1.5
    status: failed
    status_cn: 出错了
    headline: 没做 —— error 级校验失败：按「宁可不发，不可错发」不发布报告
    files: （空）
    groups: ('必须你亲自确认', 0) / ('机器算完、看不出疑点', 0) / ('没找到标准依据，机器不判', 0)
    alerts: （空）
    timeout: False
    ! 任务失败：

## [WARN] BD-07 · 缺限值列（只剩实测值）
- 输入：`做成日报`
    http: 200
    seconds: 0.03
    file: 20261107-231110_夜测_缺限值.csv
    file_kb: 0.1
    run_id: T20261107-a7f9cadc69
    run_seconds: 1.5
    status: failed
    status_cn: 出错了
    headline: 没做 —— 这些记录没取到限值，系统不猜限值、不判：水分含量（1 条）
    files: （空）
    groups: ('必须你亲自确认', 0) / ('机器算完、看不出疑点', 0) / ('没找到标准依据，机器不判', 0)
    alerts: （空）
    timeout: False
    ! 任务失败：

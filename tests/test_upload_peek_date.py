# -*- coding: utf-8 -*-
"""上传预览 _peek_table 的日期探测回归（2026-11-08 晚）。

化验室原表上面压着标题行/业务说明行，第 3 行才是表头。
旧版硬取第 1 行 → 找不到「日期」列 → 回退今天 → 与表内日期不匹配而拒跑。
"""
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import openpyxl

from qc_daily.task_web import _peek_table


def _make(path: Path, *, header_row: int, with_date: bool = True) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "送检水稻检测"
    if header_row == 3:
        ws.cell(1, 1, "送检水稻检测")
        ws.cell(2, 1, "黄色信息由采购提供，接样时需确认清楚")
    hdr = ["日期", "品种", "样品来源", "水分\n (%)", "出糙率\n（%）"]
    for j, h in enumerate(hdr, 1):
        ws.cell(header_row, j, h)
    for i in range(3):
        r = header_row + 1 + i
        ws.cell(r, 1, datetime(2026, 11, 9))
        ws.cell(r, 2, f"某品种{i}")
        ws.cell(r, 3, "某地某仓库")
        ws.cell(r, 4, 13.1 + i)
        ws.cell(r, 5, 76.2 + i)
    wb.save(path)


class TestPeekTableDate(unittest.TestCase):
    def test_header_on_first_row_still_works(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "plain.xlsx"
            _make(p, header_row=1)
            rows, got = _peek_table(p)
            self.assertEqual(rows, 3)
            self.assertEqual(got, "2026-11-09")

    def test_header_on_third_row_is_detected(self):
        """真实送检表：标题 + 说明 + 表头(第3行) → 必须读到表内日期，不能回退今天。"""
        with TemporaryDirectory() as d:
            p = Path(d) / "lab.xlsx"
            _make(p, header_row=3)
            rows, got = _peek_table(p)
            self.assertEqual(rows, 3)
            self.assertEqual(got, "2026-11-09")

    def test_csv_with_title_rows_above_header(self):
        """CSV 上面压着标题行/说明行时，也要和 xlsx 一样用同一套表头探测。

        夜测 2026-11-09 G1：xlsx 分支修了，CSV 分支还用 DictReader 硬吃首行。
        """
        with TemporaryDirectory() as d:
            p = Path(d) / "lab.csv"
            p.write_text(
                "某某米业有限公司\n"
                "检验日期：2026-11-10\n"
                "日期,品种,样品来源,出库水分,出糙率\n"
                "2026-11-10,某品种,某地某仓库,13.2,76.5\n"
                "2026-11-10,某品种,某地某仓库,13.4,75.8\n",
                encoding="utf-8-sig")
            rows, got = _peek_table(p)
            self.assertEqual(rows, 2, "数据行只算表头之后的两行")
            self.assertEqual(got, "2026-11-10", "不能回退今天")

    def test_real_demo_tables(self):
        repo = Path(__file__).resolve().parent.parent / "demo"
        for name in ("送检水稻检测_DEMO_可跑版.xlsx",
                     "送检水稻检测_DEMO_原表格式.xlsx",
                     "稻谷验收明细_演示.xlsx"):
            p = repo / name
            if p.is_file():
                with self.subTest(name=name):
                    _rows, got = _peek_table(p)
                    self.assertEqual(got, "2026-11-09")


if __name__ == "__main__":
    unittest.main()

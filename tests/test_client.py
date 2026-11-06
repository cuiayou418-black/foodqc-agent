"""独立对话客户端 /app · 回归测试（unittest）

跑法：.venv/Scripts/python.exe -m unittest discover -s tests -p "test_client.py" -v

重点验证：① 页面品牌是 FoodQC-Agent，不再带「品控日报智能体」外壳；
② chat 接的是 harness（DEMO 假数据 + 三段式 + 真实 trace）；
③ 不回传本机绝对路径；④ 现有日报路由仍可用（零破坏）。
"""
from __future__ import annotations

import unittest

from qc_daily.config import QCDailyConfig
from qc_daily.webapp import create_app


class TestClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app(QCDailyConfig.load()).test_client()

    def test_page_is_standalone_client(self):
        r = self.client.get("/app")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("FoodQC-Agent", html)
        self.assertIn("三段式输出", html)
        self.assertIn("/api/client/chat", html)
        self.assertNotIn("品控日报智能体", html, "不能复用日报的品牌外壳")
        self.assertNotIn("日报列表", html, "不能带日报导航")

    def test_knowledge_api(self):
        r = self.client.get("/api/client/knowledge")
        self.assertEqual(r.status_code, 200)
        self.assertIn("stats", r.get_json())

    def test_chat_returns_real_harness_result(self):
        r = self.client.post("/api/client/chat",
                             json={"question": "DEMO-001 这批稻谷出糙率 76.5 能收吗"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertTrue(d["batch_id"].startswith("DEMO-"))
        self.assertIn("已确认", d["sections"])
        self.assertIn("待人工复核", d["sections"])
        self.assertIn("未覆盖", d["sections"])
        self.assertGreater(len(d["trace_events"]), 0, "必须有真实工具调用留痕")
        self.assertGreater(d["steps"], 0)
        self.assertFalse(d["llm_used"], "默认不联网")

    def test_no_absolute_path_leaks(self):
        r = self.client.post("/api/client/chat",
                             json={"question": "出糙率 3 等限值是多少"})
        d = r.get_json()
        self.assertTrue(d["ok"])
        for key in ("trace_path", "summary_path", "report_path"):
            v = d.get(key) or ""
            self.assertNotIn("C:\\", v)
            self.assertNotIn("C:/", v)
        self.assertTrue(d["trace_path"].startswith("runs/"))

    def test_empty_question_rejected(self):
        r = self.client.post("/api/client/chat", json={"question": ""})
        self.assertEqual(r.status_code, 400)

    def test_existing_daily_routes_untouched(self):
        """新增客户端不能破坏日报后台。"""
        for path in ("/", "/reports", "/knowledge", "/agent"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


if __name__ == "__main__":
    unittest.main(verbosity=2)

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


class TestIntentRouter(unittest.TestCase):
    """入口理解层：先听得懂问题，再决定要不要进判定链。"""

    def setUp(self):
        from qc_daily import intent
        self.intent = intent

    def test_capability_question_is_understood(self):
        r = self.intent.route("你能干什么")
        self.assertIsNotNone(r)
        text = " ".join(r["answer"]["lines"])
        self.assertIn("出糙率", text)
        self.assertIn("整精米率", text)
        self.assertIn("不放行", " ".join(r["answer"]["boundary"]))

    def test_idle_chat_is_out_of_scope_not_rejected(self):
        r = self.intent.route("讲个笑话")
        self.assertIsNotNone(r)
        self.assertIn("不在我的业务范围", r["answer"]["title"])

    def test_judgement_request_goes_to_harness(self):
        self.assertIsNone(self.intent.route("DEMO-001 这批稻谷出糙率 76.5 能收吗"))
        self.assertIsNone(self.intent.route("出糙率 3 等限值是多少"))

    def test_knowledge_question_has_source(self):
        r = self.intent.route("整精米率和出糙率有什么区别")
        self.assertIsNotNone(r)
        body = " ".join(r["answer"]["lines"] + r["answer"]["boundary"])
        self.assertTrue(("出处" in body) or ("corpus" in body) or ("未覆盖" in r["answer"]["title"]),
                        "知识问答必须带出处或明确说明未覆盖")

    def test_open_ended_business_question_asks_for_info(self):
        """开放式问法不能回「语料未覆盖」——要听懂意图并追问。"""
        r = self.intent.route("帮我看一下这批粮怎么样")
        self.assertIsNotNone(r)
        self.assertIn("还差一点信息", r["answer"]["title"])
        self.assertNotIn("未覆盖", r["answer"]["title"])

    def test_greeting_is_answered(self):
        r = self.intent.route("你好")
        self.assertIsNotNone(r)
        self.assertIn("FoodQC-Agent", r["answer"]["title"])

    def test_llm_layer_offline_fallback(self):
        """QC_USE_LLM 默认关：绝不能因为没 key 就卡住对话。"""
        import os
        old = os.environ.pop("QC_USE_LLM", None)
        try:
            self.assertEqual(self.intent.llm_route("你能干什么")[0], "fallback")
        finally:
            if old is not None:
                os.environ["QC_USE_LLM"] = old

    def test_chat_api_capability_not_rejected(self):
        from qc_daily.config import QCDailyConfig
        from qc_daily.webapp import create_app
        c = create_app(QCDailyConfig.load()).test_client()
        d = c.post("/api/client/chat", json={"question": "你能干什么"}).get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["mode"], "chat")
        self.assertNotEqual(d["stop_reason"], "rejected")
        self.assertIn("出糙率", " ".join(d["answer"]["lines"]))

    def test_chat_api_judgement_still_runs_harness(self):
        from qc_daily.config import QCDailyConfig
        from qc_daily.webapp import create_app
        c = create_app(QCDailyConfig.load()).test_client()
        d = c.post("/api/client/chat",
                   json={"question": "DEMO-001 这批稻谷出糙率 76.5 能收吗"}).get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["mode"], "run")
        self.assertIn("待人工复核", d["sections"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

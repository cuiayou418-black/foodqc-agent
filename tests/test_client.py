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

    def test_page_has_conversation_management(self):
        """新建/切换/删除/刷新恢复：会话管理是客户端基本形态。"""
        html = self.client.get("/app").get_data(as_text=True)
        for marker in ("new-conv", "conv-list", "conv-title", "foodqc.sessions.v1",
                       "function newSession", "function delSession", "loadStore()",
                       "wcards", "welcome", "kb-toggle", "CARDS.forEach", "新建任务"):
            self.assertIn(marker, html, marker)
        self.assertNotIn("本次演示会话", html, "不能再是单个静态会话")

    def test_page_dsh_style_shell(self):
        """DSH 风格骨架：薄荷左栏分组、中央欢迎卡、底部浮动输入区。"""
        html = self.client.get("/app").get_data(as_text=True)
        for marker in ("side-scroll", "group", "wtitle", "wcard", "cbox",
                       "cbar", "ctools", "default-workspace", "探索未至之境"):
            self.assertIn(marker, html, marker)

    def test_backend_is_stateless_across_conversations(self):
        """会话只在前端，后端每次判定独立运行，互不串味。"""
        for q in ("你能干什么", "DEMO-001 这批稻谷出糙率 76.5 能收吗", "你好"):
            d = self.client.post("/api/client/chat", json={"question": q}).get_json()
            self.assertTrue(d["ok"], q)

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
        for path in ("/", "/reports", "/knowledge", "/import", "/alerts"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_old_agent_page_redirects_to_client(self):
        """旧 /agent 已废弃：必须 302 到独立客户端，不能留两个同名页面。"""
        r = self.client.get("/agent")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/app"))
        self.assertEqual(self.client.get("/agent", follow_redirects=True).status_code, 200)

    # ---- 真控件回归：判定模式 / LLM 开关 / 历史 / 折叠 ----

    def test_page_controls_are_real_not_decorative(self):
        """外围控件必须真有 id + 后端接口；纯装饰的 span 不能再假装可点。"""
        html = self.client.get("/app").get_data(as_text=True)
        for marker in ('id="mode-btn"', 'id="mode-menu"', 'id="llm-btn"',
                       'id="hist-up"', 'id="hist-down"', 'data-fold="pin"',
                       'data-fold="proj"', 'data-fold="recent"', "/api/client/state",
                       "function setMode", "function toggleLlm", "function histPrev"):
            self.assertIn(marker, html, marker)
        # 频道 / 定时：明标未开放，不再是无反应的裸 span
        self.assertNotIn("<span>频道</span>", html)
        self.assertIn('title="频道功能尚未开放"', html)
        # DeepSeek 从静态 span 升级为真按钮
        self.assertNotIn('<span class="model">', html)

    def test_concept_question_gets_human_answer(self):
        """概念问答给固定人话，不再把 markdown 表格行原样糊给用户。"""
        d = self.client.post("/api/client/chat", json={
            "question": "整精米率和出糙率有什么区别", "mode": "lookup"}).get_json()
        a = d["answer"]
        self.assertTrue(a.get("prose"))
        self.assertNotIn("业务知识检索", a["title"], "标题不该复读问题")
        text = "".join(a["lines"])
        self.assertIn("出糙率", text)
        self.assertIn("整精米率", text)
        self.assertNotIn("|---", text)
        self.assertFalse(text.startswith("["), "不该再挂 [文件名] 日志前缀")

    def test_limit_lookup_still_finds_table_but_readable(self):
        """限值问答仍要能命中表格，但要转成能读的一行，不是原始 markdown。"""
        d = self.client.post("/api/client/chat", json={
            "question": "出糙率 3 等限值是多少", "mode": "lookup"}).get_json()
        text = "".join(d["answer"]["lines"])
        self.assertNotIn("|---", text)
        self.assertNotIn("业务知识检索", d["answer"]["title"])

    def test_mode_chat_does_not_enter_chain(self):
        d = self.client.post("/api/client/chat", json={
            "question": "DEMO-001 这批稻谷出糙率 76.5 能收吗", "mode": "chat"}).get_json()
        self.assertEqual(d["mode"], "chat")
        self.assertEqual(d["mode_used"], "chat")
        self.assertEqual(d["router"], "mode", "问答模式必须挡住判定请求")
        self.assertEqual(d["batch_id"], "", "问答模式不能进判定链")
        self.assertIn("问答模式", d["answer"]["title"])

    def test_mode_lookup_only_searches_corpus(self):
        d = self.client.post("/api/client/chat", json={
            "question": "出糙率 3 等限值是多少", "mode": "lookup"}).get_json()
        self.assertEqual(d["mode"], "chat")
        self.assertEqual(d["mode_used"], "lookup")
        self.assertFalse(d["batch_id"], "只查标准不进判定链")
        self.assertTrue(d["answer"]["title"])

    def test_mode_judge_forces_chain(self):
        d = self.client.post("/api/client/chat", json={
            "question": "DEMO-001 这批稻谷出糙率 76.5 能收吗", "mode": "judge"}).get_json()
        self.assertEqual(d["mode"], "run")
        self.assertEqual(d["mode_used"], "judge")
        self.assertTrue(d["batch_id"].startswith("DEMO-"))
        self.assertIn("已确认", d["sections"])

    def test_unknown_mode_degrades_to_auto(self):
        d = self.client.post("/api/client/chat", json={
            "question": "DEMO-001 这批稻谷出糙率 76.5 能收吗", "mode": "???"}).get_json()
        self.assertEqual(d["mode_used"], "auto")

    def test_video_driven_controls(self):
        """对照 DSH 录屏补的交互：斜杠指令、结论/轨迹双视图、轻量提示。"""
        html = self.client.get("/app").get_data(as_text=True)
        for marker in ('id="cmd-panel"', "function renderCmd", "function execCmd",
                       "/判定", "/查标准", "/留痕", "function togglePane",
                       'class="toggles"', 'data-p="trace"', 'id="toast"',
                       'class="prose"', "d.brief"):
            self.assertIn(marker, html, marker)
        self.assertNotIn('class="rtabs"', html, "不该再把内部分类摆在最显眼处")
        self.assertNotIn("<details open>", html, "明细与轨迹都该默认收起")

    def test_run_answer_starts_with_human_brief(self):
        """判定回答必须先有一段人话，且明确说清为什么不给能收/不能收。"""
        d = self.client.post("/api/client/chat", json={
            "question": "DEMO-001 这批稻谷出糙率 76.5 能收吗", "mode": "judge"}).get_json()
        b = d["brief"]
        text = "".join(b["paras"])
        self.assertTrue(b["paras"])
        self.assertGreater(b["counts"]["passed"], 0, "合格项数由代码数出来，不是编的")
        self.assertGreater(b["counts"]["review"], 0)
        self.assertIn("能收", text, "要说清为什么不给放行结论")
        self.assertIn("人工核对", text, "版本红线要出现在人话里")

    def test_llm_switch_off_by_request(self):
        """请求级关闭必须生效，且不能因为开关而联网。"""
        d = self.client.post("/api/client/chat", json={
            "question": "整精米率和出糙率有什么区别", "mode": "chat",
            "use_llm": False}).get_json()
        self.assertFalse(d["llm_used"], "关了就不能调模型")
        self.assertEqual(d["llm_state"]["source"], "request")

    def test_state_endpoint_reports_real_llm(self):
        d = self.client.get("/api/client/state").get_json()
        self.assertTrue(d["ok"])
        self.assertIn("enabled", d["llm"])
        self.assertIn("has_key", d["llm"])
        self.assertEqual(len(d["modes"]), 4, "自动/判定/问答/只查标准")


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

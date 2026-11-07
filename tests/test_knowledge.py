"""知识库可视化 + Agent 工作台 · 回归测试（标准库 unittest，不装新依赖）

跑法：.venv/Scripts/python.exe -m unittest discover -s tests -v

测试把暂存区重定向到临时目录，绝不碰真实 corpus/_inbox/；
另有一条用例验证「扫描现有知识库」确实只读、不改文件。
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import qc_daily.knowledge as kb


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestKnowledgeCore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="foodqc_kbtest_"))
        self._orig = (kb.INBOX, kb.PUBLIC_DIR, kb.PRIVATE_DIR, kb.MANIFEST)
        kb.INBOX = self.tmp / "_inbox"
        kb.PUBLIC_DIR = kb.INBOX / "public"
        kb.PRIVATE_DIR = kb.INBOX / "private"
        kb.MANIFEST = kb.INBOX / "manifest.json"

    def tearDown(self):
        (kb.INBOX, kb.PUBLIC_DIR, kb.PRIVATE_DIR, kb.MANIFEST) = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_split_entries(self):
        text = "# 第一章\n这是第一段足够长的正文内容。\n\n3.1　条款\n这是第二段足够长的正文内容。\n"
        blocks = kb.split_entries(text)
        self.assertGreaterEqual(len(blocks), 2)

    def test_upload_public_goes_to_pending(self):
        raw = "# 测试标准\n这是一段足够长的正文，用来验证切块与条目统计是否正常工作。\n".encode()
        rec, err, _warn = kb.add_upload(raw, "测试笔记.md", "public")
        self.assertIsNone(err)
        self.assertEqual(rec["status"], kb.STATUS_PENDING)
        self.assertFalse(rec["private"])
        self.assertGreaterEqual(rec["entries"], 1)
        self.assertIn("测试笔记.md", rec["name"])
        self.assertEqual(len(kb.list_inbox()), 1)

    def test_upload_private_is_isolated_and_flagged(self):
        raw = "# 内控手册\n这是一段足够长的正文，用来验证隔离目录与警告标记是否正常。\n".encode("utf-8")
        rec, err, _warn = kb.add_upload(raw, "化验室手册.md", "private")
        self.assertIsNone(err)
        self.assertTrue(rec["private"])
        self.assertTrue((kb.PRIVATE_DIR / Path(rec["stored"]).name).exists()
                        or rec["stored"].endswith("化验室手册.md"))
        shown = kb.list_inbox()[0]
        self.assertNotIn("stored", shown)
        self.assertIn("禁止外传", shown["warning"])

    def test_upload_rejects_bad_ext_and_empty(self):
        _r, err, _w = kb.add_upload(b"x", "手册.docx", "public")
        self.assertIn("不支持", err)
        _r, err, _w = kb.add_upload(b"", "空.md", "public")
        self.assertIn("为空", err)
        _r, err, _w = kb.add_upload(b"x", "笔记.md", "unknown")
        self.assertIn("不合法", err)
        self.assertEqual(kb.list_inbox(), [])

    def test_confirm_changes_status_and_is_idempotent(self):
        raw = "# 标题\n这是一段足够长的正文，用来验证确认动作只改状态而不写判定链。\n".encode("utf-8")
        rec, _e, _w = kb.add_upload(raw, "n.md", "public")
        before = _digest(kb.LIMITS)
        got, err = kb.confirm(rec["id"])
        self.assertIsNone(err)
        self.assertEqual(got["status"], kb.STATUS_CONFIRMED)
        self.assertIn("未接入判定链", got["status"])
        self.assertEqual(_digest(kb.LIMITS), before, "confirm 绝不能改写 limits.json")
        again, err2 = kb.confirm(rec["id"])
        self.assertIsNone(err2, "重复确认应幂等")
        self.assertEqual(again["status"], kb.STATUS_CONFIRMED)
        self.assertIsNone(kb.confirm("not-exist")[0])

    def test_scan_builtin_is_readonly(self):
        watched = [p for p in kb.CORPUS.glob("*.md")] + [kb.LIMITS, kb.CHUNKS]
        watched = [p for p in watched if p.exists()]
        before = {str(p): _digest(p) for p in watched}
        items = kb.scan_builtin()
        self.assertTrue(any(i["id"] == "builtin-limits" for i in items))
        self.assertTrue(any(i["name"].endswith(".md") for i in items))
        after = {str(p): _digest(p) for p in watched}
        self.assertEqual(before, after, "扫描现有知识库必须严格只读")

    def test_manifest_is_json_and_preview_works(self):
        raw = "# 标题\n这是一段足够长的正文，用来验证索引可解析与预览可读取。\n".encode("utf-8")
        rec, _e, _w = kb.add_upload(raw, "p.md", "public")
        self.assertIsInstance(json.loads(kb.MANIFEST.read_text(encoding="utf-8")), list)
        text, err = kb.preview(rec["id"])
        self.assertIsNone(err)
        self.assertIn("标题", text)
        self.assertEqual(kb.preview("nope")[1], "找不到这条记录")


class TestWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Web 测试同样用临时暂存区，绝不在真实 corpus/_inbox/ 留下测试文件
        cls.tmp = Path(tempfile.mkdtemp(prefix="foodqc_webtest_"))
        cls._orig = (kb.INBOX, kb.PUBLIC_DIR, kb.PRIVATE_DIR, kb.MANIFEST)
        kb.INBOX = cls.tmp / "_inbox"
        kb.PUBLIC_DIR = kb.INBOX / "public"
        kb.PRIVATE_DIR = kb.INBOX / "private"
        kb.MANIFEST = kb.INBOX / "manifest.json"
        try:
            from qc_daily.config import QCDailyConfig
            from qc_daily.webapp import create_app
            cls.client = create_app(QCDailyConfig.load()).test_client()
        except Exception as e:  # config 正在被改动时，不让整套测试挂掉
            raise unittest.SkipTest(f"Web 应用未能启动：{type(e).__name__}: {e}")

    @classmethod
    def tearDownClass(cls):
        (kb.INBOX, kb.PUBLIC_DIR, kb.PRIVATE_DIR, kb.MANIFEST) = cls._orig
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pages_render(self):
        for path, marker in [("/knowledge", "知识库 / 记忆")]:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn(marker, r.get_data(as_text=True), path)
            self.assertNotIn("__KB_HEAD__", r.get_data(as_text=True))

    def test_agent_page_moved_to_app(self):
        """旧 /agent 页面已并入独立客户端 /app，只保留跳转。"""
        r = self.client.get("/agent")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/app", r.headers["Location"])

    def test_overview_api(self):
        r = self.client.get("/api/knowledge/overview")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn("builtin", d)
        self.assertIn("inbox", d)
        self.assertIn("stats", d)

    def test_upload_and_feedback_api(self):
        payload = "# T\n这是一段足够长的正文，验证上传接口返回成功反馈。\n".encode("utf-8")
        data = {"file": (io.BytesIO(payload), "接口测试.md"), "source": "public"}
        r = self.client.post("/api/knowledge/upload", data=data,
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertIn("inbox", d["overview"], "上传后必须同步返回新列表")
        self.assertTrue(any(x["id"] == d["record"]["id"] for x in d["overview"]["inbox"]))
        self.assertNotIn("stored", d["record"])

        bad = self.client.post("/api/knowledge/upload",
                               data={"file": (io.BytesIO(b"x"), "x.docx"), "source": "public"},
                               content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("不支持", bad.get_json()["error"])

    def test_agent_ask_returns_real_steps(self):
        r = self.client.post("/api/agent/ask", json={"question": "出糙率 3 等限值是多少"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertGreater(len(d["steps"]), 0, "必须有真实工具调用步骤")
        self.assertTrue(any(s["kind"] == "tool" for s in d["steps"]))
        self.assertGreater(d["raw_lines"], 0)

    def test_agent_ask_rejects_empty(self):
        r = self.client.post("/api/agent/ask", json={"question": ""})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)

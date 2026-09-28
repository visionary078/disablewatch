import os
import tempfile
import unittest

from services.task_memory import (
    TaskMemory,
    TaskMemoryStore,
    apply_revision,
    detect_intent,
    revise_main_task,
)


class TaskReviseTests(unittest.TestCase):
    def test_new_task_from_empty_memory(self):
        revision = revise_main_task(None, "帮我找可乐")
        self.assertEqual(revision.decision, "new")
        self.assertEqual(revision.intent, "find_product")
        self.assertIn("可乐", revision.question)

    def test_switch_when_purpose_changes(self):
        previous = TaskMemory(
            session_id="s1",
            intent="find_product",
            target="可乐",
            main_task="帮我找可乐",
            question="请帮我找：可乐。",
            utterances=["帮我找可乐"],
        )
        revision = revise_main_task(previous, "收银台在哪")
        self.assertEqual(revision.decision, "switch")
        self.assertEqual(revision.intent, "find_cashier")

    def test_refine_keeps_main_task(self):
        previous = TaskMemory(
            session_id="s1",
            intent="find_product",
            target="可乐",
            main_task="帮我找可乐",
            question="请帮我找：可乐。",
            utterances=["帮我找可乐"],
        )
        revision = revise_main_task(previous, "不是这个，再往左边一点")
        self.assertEqual(revision.decision, "refine")
        self.assertEqual(revision.intent, "find_product")
        self.assertIn("用户补充", revision.question)

    def test_keep_without_new_intent(self):
        previous = TaskMemory(
            session_id="s1",
            intent="find_product",
            target="可乐",
            main_task="帮我找可乐",
            question="请帮我找：可乐。",
        )
        revision = revise_main_task(previous, "看到了吗")
        self.assertIn(revision.decision, {"keep", "refine"})
        self.assertEqual(revision.intent, "find_product")

    def test_detect_intent(self):
        self.assertEqual(detect_intent("入口在哪"), "find_entrance")
        self.assertEqual(detect_intent("这个多少钱"), "read_price")

    def test_bare_product_name_becomes_find_task(self):
        revision = revise_main_task(None, "可乐")
        self.assertEqual(revision.intent, "find_product")
        self.assertIn("可乐", revision.question)
        self.assertIn("正在帮你找可乐", revision.question)

    def test_find_question_asks_model_to_search(self):
        revision = revise_main_task(None, "帮我找雪碧")
        self.assertIn("寻找", revision.question)
        self.assertIn("雪碧", revision.question)


class TaskStoreTests(unittest.TestCase):
    def test_sqlite_roundtrip(self):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite")
        handle.close()
        try:
            store = TaskMemoryStore(db_path=handle.name)
            previous = None
            revision = revise_main_task(previous, "帮我找雪碧")
            store.save(apply_revision(previous, revision, "sess-1"))
            store.close()
            loaded_store = TaskMemoryStore(db_path=handle.name)
            loaded = loaded_store.get("sess-1")
            loaded_store.close()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.intent, "find_product")
            self.assertIn("雪碧", loaded.main_task)
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()

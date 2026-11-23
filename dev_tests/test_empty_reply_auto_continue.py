"""The IDE chat finishes tasks end-to-end: an empty provider reply is
auto-continued (up to MAX rounds) instead of parking the task on the
user typing 'continue'.

Modeled on test_pending_decision_timeout.py's part-D harness.
Run with: QT_QPA_PLATFORM=offscreen python3 dev_tests/test_empty_reply_auto_continue.py
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread, Signal

import agent_overlay as ov
import agent_tools as at
import memory


def _pump_until(app, predicate, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.03)
    return predicate()


class StubAIWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)
    calls = []          # [(prompt, ide_scoped), ...]
    replies = []        # queued reply strings, popped in order

    def __init__(self, prompt, parent=None, summarize_only=False,
                 ide_scoped=False):
        super().__init__(parent)
        self._prompt = prompt
        StubAIWorker.calls.append((prompt, ide_scoped))

    def run(self):
        time.sleep(0.02)
        reply = (StubAIWorker.replies.pop(0)
                 if StubAIWorker.replies else "done")
        self.succeeded.emit(reply)


EMPTY = ov.EMPTY_REPLY_FALLBACK_NO_TRANSCRIPT


class DetectionTests(unittest.TestCase):
    def test_recognizes_all_empty_flavors(self):
        self.assertTrue(ov._is_empty_model_reply(EMPTY))
        self.assertTrue(ov._is_empty_model_reply(
            "  " + EMPTY + "  "))
        self.assertTrue(ov._is_empty_model_reply(
            "(My closing reply came back empty, but I did complete "
            "3 actions: write, run. Say 'continue' to keep going.)"))
        self.assertFalse(ov._is_empty_model_reply(
            "Built and tested the contact form, sir."))
        self.assertFalse(ov._is_empty_model_reply(None))


class AutoContinueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        ov._main_thread_invoker = ov._MainThreadInvoker()

    def setUp(self):
        memory.set_memory_dir(tempfile.mkdtemp())
        memory._clear_recent_turns()
        StubAIWorker.calls = []
        StubAIWorker.replies = []
        # silence the spoken announcement by patching the module function
        self._patcher = mock.patch.object(ov, "_announce_panel_event",
                                          lambda text: None)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    @staticmethod
    def _noop():
        pass

    def _ask_collect(self, text):
        results = []
        ov._ide_chat_ask(text, lambda ok, t: results.append((ok, t)))
        return results

    def test_empty_then_real_reply_finishes_without_user_continue(self):
        StubAIWorker.replies = [EMPTY, "Built the contact form, sir."]
        with mock.patch.object(ov, "AIWorker", StubAIWorker):
            results = self._ask_collect("build a contact form")
            self.assertTrue(_pump_until(self.app, lambda: len(results) == 1))
        self.assertEqual(results[0], (True, "Built the contact form, sir."))
        prompts = [p for p, _ in StubAIWorker.calls]
        self.assertEqual(prompts, ["build a contact form", "continue"])

    def test_two_empties_still_recovers(self):
        StubAIWorker.replies = [EMPTY, EMPTY,
                                "All files are written now, sir."]
        with mock.patch.object(ov, "AIWorker", StubAIWorker):
            results = self._ask_collect("make me a landing page")
            self.assertTrue(_pump_until(self.app, lambda: len(results) == 1))
        self.assertEqual(results[0],
                         (True, "All files are written now, sir."))
        prompts = [p for p, _ in StubAIWorker.calls]
        self.assertEqual(prompts, ["make me a landing page",
                                   "continue", "continue"])

    def test_retry_budget_is_bounded_and_eventually_honest(self):
        # Always-empty model: after AUTO_CONTINUE_EMPTY_REPLIES_MAX extra
        # rounds the honest placeholder is what finally reaches the chat,
        # not an infinite continue loop.
        StubAIWorker.replies = [EMPTY] * 12
        with mock.patch.object(ov, "AIWorker", StubAIWorker):
            results = self._ask_collect("build an editor")
            self.assertTrue(_pump_until(self.app, lambda: len(results) == 1))
        self.assertEqual(results[0], (True, EMPTY))
        self.assertEqual(len(StubAIWorker.calls),
                         1 + ov.AUTO_CONTINUE_EMPTY_REPLIES_MAX)

    def test_normal_replies_are_not_retriggered(self):
        StubAIWorker.replies = ["Everything is done, sir."]
        with mock.patch.object(ov, "AIWorker", StubAIWorker):
            results = self._ask_collect("fix the typo")
            self.assertTrue(_pump_until(self.app, lambda: len(results) == 1))
        self.assertEqual(len(StubAIWorker.calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

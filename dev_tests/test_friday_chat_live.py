"""Live Chat adapter contract and bounded transcript context; no network/GUI."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from friday_chat.live import _history_context, build_ide_handoff, handoff_to_ide, settings_reply_handler
from friday_chat.model import ReplyError


class Signal:
    def __init__(self):
        self.callback = None
    def connect(self, callback):
        self.callback = callback


class FakeWorker:
    def __init__(self, prompt, **kwargs):
        self.prompt = prompt
        self.kwargs = kwargs
        self.succeeded = Signal()
        self.failed = Signal()
        self.finished = Signal()
    def start(self):
        self.succeeded.callback("A live answer")
        self.finished.callback()
    def deleteLater(self):
        pass


class LiveChatTests(unittest.TestCase):
    def test_transcript_uses_prior_turns_and_filename_only_attachment_context(self):
        context = _history_context([
            {"role": "user", "content": "Earlier question", "attachments": ["brief.pdf"]},
            {"role": "assistant", "content": "Earlier answer", "attachments": []},
            {"role": "user", "content": "Current question", "attachments": []},
        ], "Current question")
        self.assertIn("User: Earlier question", context)
        self.assertIn("Friday: Earlier answer", context)
        self.assertIn("brief.pdf", context)
        self.assertIn("names only", context)
        self.assertNotIn("Current question", context)

    def test_history_context_is_bounded(self):
        history = [{"role": "user", "content": "x" * 5000} for _ in range(30)]
        self.assertLess(len(_history_context(history, "current")), 15000)

    def test_handler_constructs_worker_on_qt_invoker_with_context(self):
        workers = []
        class Runtime:
            _main_thread_invoker = object()
            _pending_chat_workers = set()
            AIWorker = lambda _self, prompt, **kwargs: workers.append(FakeWorker(prompt, **kwargs)) or workers[-1]
            @staticmethod
            def _on_qt_main_thread(callback):
                callback()
        with patch.dict(sys.modules, {"agent_overlay": Runtime()}):
            reply = settings_reply_handler("now", [{"role": "user", "content": "before"}])
        self.assertEqual(reply, "A live answer")
        self.assertEqual(workers[0].prompt, "now")
        self.assertIn("User: before", workers[0].kwargs["chat_history"])
        self.assertTrue(workers[0].kwargs["chat_surface"])

    def test_handoff_builds_development_brief_from_chat_and_unsent_draft(self):
        brief = build_ide_handoff("Prototype", [
            {"role": "user", "content": "Build a small dashboard", "attachments": ["requirements.md"]},
            {"role": "assistant", "content": "We agreed on a simple layout."},
            {"role": "user", "content": "Use the existing project."},
        ], "Add a dark theme")
        self.assertIn("carry it out in the IDE", brief)
        self.assertIn("Conversation title: Prototype", brief)
        self.assertIn("User: Build a small dashboard", brief)
        self.assertIn("Friday: We agreed on a simple layout.", brief)
        self.assertIn("requirements.md", brief)
        self.assertIn("file contents not transferred", brief)
        self.assertIn("Unsent user draft: Add a dark theme", brief)

    def test_handoff_transcript_is_bounded_and_empty_context_rejected(self):
        brief = build_ide_handoff("Long", [{"role": "user", "content": "x" * 5000}
                                            for _ in range(50)])
        self.assertLess(len(brief), 22000)
        with self.assertRaises(ReplyError):
            build_ide_handoff("Empty", [])

    def test_handoff_adapter_routes_only_the_brief_to_agent_tools(self):
        captured = []
        class Tools:
            @staticmethod
            def handoff_chat_to_ide(prompt):
                captured.append(prompt)
                return "Moved to IDE chat"
        with patch.dict(sys.modules, {"agent_tools": Tools()}):
            result = handoff_to_ide("Test", [{"role": "user", "content": "Make it"}])
        self.assertEqual(result, "Moved to IDE chat")
        self.assertIn("User: Make it", captured[0])

    def test_agent_tools_handoff_submits_to_active_ide_chat(self):
        import agent_tools
        class Panel:
            def __init__(self):
                self.sent = []
            def _submit_text(self, text):
                self.sent.append(text)
        class Workspace:
            _workspace_hidden = False
            _pill = None
            def __init__(self):
                self.chat_panel = Panel()
            def workspace_display_name(self):
                return "Demo Project"
        workspace = Workspace()
        with patch.object(agent_tools, "_ide_apps", [workspace]):
            with patch.object(agent_tools, "_ide_app", workspace):
                with patch.object(agent_tools, "_run_on_main", side_effect=lambda fn: fn()):
                    result = agent_tools.handoff_chat_to_ide("Development brief")
        self.assertIn("Demo Project", result)
        self.assertEqual(workspace.chat_panel.sent, ["Development brief"])

    def test_handoff_restores_a_minimized_ide_workspace(self):
        import agent_tools
        class Panel:
            def __init__(self):
                self.sent = []
            def _submit_text(self, text):
                self.sent.append(text)
        class Workspace:
            _workspace_hidden = True
            _pill = object()
            def __init__(self):
                self.chat_panel = Panel()
                self.focused = False
            def workspace_display_name(self):
                return "Hidden Project"
            def focus_force(self):
                self.focused = True
        workspace = Workspace()
        def restore():
            workspace._workspace_hidden = False
            workspace._pill = None
        with patch.object(agent_tools, "_ide_apps", [workspace]):
            with patch.object(agent_tools, "_ide_app", workspace):
                with patch.object(agent_tools, "_restore_workspace_group", side_effect=restore) as restore_group:
                    with patch.object(agent_tools, "_activate_workspace"):
                        with patch.object(agent_tools, "_run_on_main", side_effect=lambda fn: fn()):
                            agent_tools.handoff_chat_to_ide("Continue developing")
        restore_group.assert_called_once_with()
        self.assertTrue(workspace.focused)
        self.assertEqual(workspace.chat_panel.sent, ["Continue developing"])

    def test_handler_fails_fast_without_runtime(self):
        class Runtime:
            _main_thread_invoker = None
        with patch.dict(sys.modules, {"agent_overlay": Runtime()}):
            with self.assertRaises(ReplyError):
                settings_reply_handler("hello", [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""A completed build must never surface as "(empty response)".

Regression for a real incident: an IDE build turn (write requirements,
install attempts) ended with the model returning a completely empty
final message, and the chat panel displayed the literal placeholder
"(empty response)" -- no summary, no explanation, no next step.

Two coordinates of the fix are proven here against the REAL machinery:

  1. Recovery inside _run_tool_loop(): an empty final message is not
     accepted as an answer -- the model gets at most two explicit
     "your last message was empty, close out properly" turns, and its
     summary becomes the reply.
  2. Honest fallback: if even the recovery comes back empty, the
     caller's bubble is built by mining the REAL tool transcript of the
     turn -- counts, tool names, the last returned line -- and it says
     plainly that the closing reply was empty and how to proceed.

Run:
    DISPLAY=:99 QT_QPA_PLATFORM=offscreen python3 dev_tests/test_empty_final_reply.py
"""

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _msg(content=None, tool_calls=None):
    fn = None
    calls = None
    if tool_calls:
        calls = [
            types.SimpleNamespace(
                id=f"c{i}", type="function",
                function=types.SimpleNamespace(name=n, arguments=a))
            for i, (n, a) in enumerate(tool_calls)
        ]
    return types.SimpleNamespace(choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(content=content, tool_calls=calls))])


class RecoveryTests(unittest.TestCase):
    """_run_tool_loop() nudges an empty final reply into a real summary."""

    def setUp(self):
        import agent_overlay
        self.ov = agent_overlay
        self.calls = []
        import agent_tools
        self._swap = {"ide_write_file": agent_tools.TOOL_FUNCTIONS.get("ide_write_file")}
        agent_tools.TOOL_FUNCTIONS["ide_write_file"] = lambda **kw: "Wrote game.py"
        agent_tools.set_ide_task_step_handler(None)

    def tearDown(self):
        import agent_tools
        if self._swap["ide_write_file"]:
            agent_tools.TOOL_FUNCTIONS["ide_write_file"] = self._swap["ide_write_file"]

    def _loop(self, scripted, ide_scoped=True):
        replies = iter(scripted)
        sent = []

        def create_fn(messages):
            sent.append([m for m in messages])
            return next(replies)

        messages = [{"role": "system", "content": "sys"},
                    {"role": "user", "content": "build a game"}]
        text = self.ov.AIWorker._run_tool_loop(
            create_fn, messages, ide_scoped=ide_scoped, max_iterations=10)
        return text, sent, messages

    def test_empty_final_is_nudged_into_a_real_summary(self):
        text, sent, _ = self._loop([
            _msg("writing the game first",
                 [("ide_write_file", '{"path": "game.py"}')]),
            _msg(""),                              # <- the failure mode
            _msg("All set: game.py is written."),  # <- the recovery
        ])
        self.assertEqual(text, "All set: game.py is written.")
        self.assertEqual(len(sent), 3)
        nudge = sent[2][-1]
        self.assertEqual(nudge["role"], "user")
        self.assertIn("came back completely empty", nudge["content"])
        self.assertIn("anything still blocked", nudge["content"])

    def test_repeat_empty_recovery_uses_transcript_fallback(self):
        text, sent, messages = self._loop([
            _msg(None, [("ide_write_file", '{"path": "game.py"}')]),
            _msg(""),
            _msg(""),
            _msg(""),
        ])
        self.assertEqual(text, "")
        self.assertEqual(len(sent), 4)  # exactly two nudges, no more
        fallback = self.ov.AIWorker._empty_reply_fallback(messages)
        self.assertIn("closing reply came back empty", fallback)
        self.assertIn("1 action", fallback)
        self.assertIn("ide_write_file", fallback)
        self.assertIn("Wrote game.py", fallback)
        self.assertNotIn("(empty response)", fallback)

    def test_barely_empty_turn_gets_the_plain_fallback(self):
        text, sent, messages = self._loop([_msg(""), _msg(""), _msg("")])
        self.assertEqual(text, "")
        fallback = self.ov.AIWorker._empty_reply_fallback(messages)
        self.assertEqual(fallback, self.ov.EMPTY_REPLY_FALLBACK_NO_TRANSCRIPT)
        self.assertIn("without any text", fallback)

    def test_non_empty_final_never_triggers_a_nudge(self):
        text, sent, _ = self._loop([
            _msg(None, [("ide_write_file", '{"path": "game.py"}')]),
            _msg("The game is built."),
        ])
        self.assertEqual(text, "The game is built.")
        self.assertEqual(len(sent), 2)


class ProviderCallSiteTests(unittest.TestCase):
    """The provider paths hand the fallback to run(), not a placeholder."""

    def setUp(self):
        import agent_overlay
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication(sys.argv)
        self.ov = agent_overlay

    def test_groq_success_with_empty_reply_returns_fallback_not_placeholder(self):
        scripted = iter([
            _msg(None, [("ide_write_file", '{"path": "game.py"}')]),
            _msg(""), _msg(""), _msg(""),
        ])

        class _FakeCompletions:
            def create(self, **kw):
                return next(scripted)

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeGroq:
            def __init__(self, *a, **k):
                pass
            chat = _FakeChat()

        w = self.ov.AIWorker("build a game", parent=None, ide_scoped=True)
        w._load_memory_context = lambda: None
        fake_groq = types.SimpleNamespace(
            Groq=_FakeGroq,
            RateLimitError=type("RateLimitError", (Exception,), {}),
            AuthenticationError=type("AuthenticationError", (Exception,), {}),
            PermissionDeniedError=type("PermissionDeniedError", (Exception,), {}),
            NotFoundError=type("NotFoundError", (Exception,), {}),
            InternalServerError=type("InternalServerError", (Exception,), {}),
            APIConnectionError=type("APIConnectionError", (Exception,), {}),
            APITimeoutError=type("APITimeoutError", (Exception,), {}),
            APIStatusError=type("APIStatusError", (Exception,), {}),
        )
        import agent_tools
        orig = agent_tools.TOOL_FUNCTIONS.get("ide_write_file")
        agent_tools.TOOL_FUNCTIONS["ide_write_file"] = lambda **kw: "Wrote game.py"
        try:
            with mock.patch.dict(sys.modules, {"groq": fake_groq}):
                ok, text, err = w._try_groq_with("panel-key")
        finally:
            if orig:
                agent_tools.TOOL_FUNCTIONS["ide_write_file"] = orig
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertNotIn("(empty response)", text)
        self.assertIn("closing reply came back empty", text)
        self.assertIn("ide_write_file", text)

    def test_gemini_empty_reply_gets_the_honest_fixed_text(self):
        fake_client = types.SimpleNamespace(
            models=types.SimpleNamespace(
                generate_content=lambda **kw: types.SimpleNamespace(text="")
            )
        )
        fake_types_module = types.SimpleNamespace(
            HttpOptions=lambda timeout: types.SimpleNamespace(timeout=timeout),
            GenerateContentConfig=lambda **kw: types.SimpleNamespace(**kw),
        )
        fake_errors = types.SimpleNamespace(
            ServerError=type("ServerError", (Exception,), {}),
            ClientError=type("ClientError", (Exception,), {}),
        )
        fake_genai = types.SimpleNamespace(
            __name__="google.genai",
            Client=lambda **kw: fake_client,
            types=fake_types_module,
            errors=fake_errors,
        )
        fake_google = types.SimpleNamespace(__name__="google", genai=fake_genai)
        w = self.ov.AIWorker("hi", parent=None, ide_scoped=True)
        w._load_memory_context = lambda: None
        with mock.patch.dict(sys.modules, {
                "google": fake_google,
                "google.genai": fake_genai,
                "google.genai.types": fake_types_module,
                "google.genai.errors": fake_errors}):
            ok, text, err = w._try_gemini_with("panel-key")
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(text, self.ov.EMPTY_REPLY_FALLBACK_NO_TRANSCRIPT)


if __name__ == "__main__":
    unittest.main(verbosity=2)

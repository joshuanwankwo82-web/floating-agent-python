"""Task-steps trace: live step-by-step narration in the IDE chat panel.

Covers the whole chain without any live model call:

  1. agent_tools._step_label_for_tool / _step_finish_from_result --
     friendly per-tool labels and done/blocked verdicts mined from real
     tool result strings.
  2. agent_tools.call_tool(..., ide_scoped=True) emits an
     in_progress -> done/blocked pair per call through the registered
     task-step handler (ide_scoped=False stays silent).
  3. The Quiet chat panel renders those rows live into its task-steps
     card (with status glyph changes), starts a fresh card per user
     turn, and stamps a done/failed summary when the turn resolves.
  4. act_script._log_evidence forwards pipeline phases (thinking /
     planning / stepped verify / review) into the same card.

Run:
    xvfb-run -a python dev_tests/test_ide_task_steps.py
"""
import os
import sys
import time
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ide"))
os.environ.setdefault("DISPLAY", ":99")

import agent_tools  # noqa: E402


class LabelAndMiningTests(unittest.TestCase):
    def test_write_label_uses_path(self):
        self.assertEqual(
            agent_tools._step_label_for_tool("ide_write_file", {"path": "src/app.py"}),
            "Write src/app.py")

    def test_terminal_label_uses_command(self):
        self.assertEqual(
            agent_tools._step_label_for_tool(
                "ide_run_terminal_command", {"command": "python -m pytest -q"}),
            "Run: python -m pytest -q")

    def test_plain_tool_humanized(self):
        self.assertEqual(
            agent_tools._step_label_for_tool("ide_run_file", {}), "Run file")

    def test_error_result_blocks(self):
        status, detail = agent_tools._step_finish_from_result(
            "ide_run_file", "Error calling ide_run_file: boom")
        self.assertEqual(status, "blocked")
        self.assertIn("boom", detail)

    def test_traceback_blocks_with_failing_line(self):
        result = ("some output\nTraceback (most recent call last):\n"
                  "  File \"x.py\", line 1, in <module>\nAssertionError: nope")
        status, detail = agent_tools._step_finish_from_result(
            "ide_run_terminal_command", result)
        self.assertEqual(status, "blocked")
        self.assertEqual(detail, "AssertionError: nope")

    def test_test_summary_mined_for_run_kind(self):
        status, detail = agent_tools._step_finish_from_result(
            "ide_run_terminal_command", ".....\n12 passed in 0.42s\n")
        self.assertEqual(status, "done")
        self.assertIn("12 passed", detail)

    def test_read_kind_stays_quiet(self):
        status, detail = agent_tools._step_finish_from_result(
            "ide_read_file", "line one\nline two\n")
        self.assertEqual((status, detail), ("done", ""))


class CallToolNarrationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        agent_tools.set_ide_task_step_handler(
            lambda kind, label, status, detail, sid: self.events.append(
                (kind, label, status, detail, sid)))

    def tearDown(self):
        agent_tools.set_ide_task_step_handler(None)

    def _call_with_fake(self, name="ide_reveal_in_file_manager",
                        args=None, result="opened"):
        import agent_tools as at
        original = at.TOOL_FUNCTIONS.get(name)
        at.TOOL_FUNCTIONS[name] = (lambda **kw: result) if not isinstance(result, Exception) \
            else (_ for _ in ()).throw(result)
        try:
            return at.call_tool(name, args or {}, ide_scoped=True)
        finally:
            if original is None:
                at.TOOL_FUNCTIONS.pop(name, None)
            else:
                at.TOOL_FUNCTIONS[name] = original

    def test_start_finish_pair_same_step_id(self):
        self._call_with_fake(args={"path": "x.py"})
        starts = [e for e in self.events if e[2] == "in_progress"]
        ends = [e for e in self.events if e[2] == "done"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(ends), 1)
        self.assertEqual(starts[0][4], ends[0][4])
        self.assertEqual(starts[0][1], ends[0][1])

    def test_error_result_marks_step_blocked(self):
        self._call_with_fake(result="Error calling ide_foo: broke")
        ends = [e for e in self.events if e[2] == "blocked"]
        self.assertEqual(len(ends), 1)
        self.assertIn("broke", ends[0][3])

    def test_not_ide_scoped_stays_silent(self):
        agent_tools.call_tool("totally_unknown_tool", {}, ide_scoped=False)
        self.assertEqual(self.events, [])


class ActScriptForwardingTests(unittest.TestCase):
    def test_evidence_kinds_become_steps(self):
        import act_script
        events = []
        agent_tools.set_ide_task_step_handler(
            lambda kind, label, status, detail, sid: events.append(
                (kind, label, status, sid)))
        try:
            act_script._log_evidence("abc12345", "thinking", {"summary": "build a snake game"})
            act_script._log_evidence("abc12345", "planning", {"summary": "3 step(s) planned"})
            act_script._log_evidence("abc12345", "step", {
                "summary": "step 1: write game loop -> verify FAILED",
                "verify_passed": False})
            act_script._log_evidence("abc12345", "review", {"summary": "verdict ok"})
        finally:
            agent_tools.set_ide_task_step_handler(None)
        kinds = [e[0] for e in events]
        self.assertEqual(kinds, ["thought", "phase", "tool", "verify"])
        self.assertIn("Thought", events[0][1])
        self.assertIn("snake game", events[0][1])
        statuses = [e[2] for e in events]
        self.assertEqual(statuses[2], "blocked")   # failed verify shows blocked
        self.assertEqual(statuses[3], "done")


class PanelRenderTests(unittest.TestCase):
    """Real-widget coverage: drive the Quiet chat panel's step card the
    same way the results queue does and look at what's on screen."""

    def test_stages_render_and_finish(self):
        import tkinter as tk  # noqa: F401 -- real display via Xvfb
        import ide as V

        class Host:
            """Fake Friday: registers the panel's handlers, accepts asks
            but never resolves them on its own -- the test drives the
            lifecycle manually."""
            is_preview = True

            def __init__(self):
                self.asks = []

            def ask(self, text, on_done):
                self.asks.append((text, on_done))

            def register_ide_chat_notify(self, h):
                self.notify = h

            def register_ide_build_status(self, h):
                self.build = h

            def register_ide_task_steps(self, h):
                self.steps = h

        W = tempfile.mkdtemp(prefix="idesteps")
        host = Host()
        app = V.IDEApp(W, agent_host=host)
        panel = getattr(app, "chat_panel", None)
        if panel is None:
            app.toggle_chat()
            app.update()
            panel = app.chat_panel

        # Simulate a full task turn end to end: user asks, agent works
        # through visible steps, turn resolves.
        panel._submit_text("Build me a snake game")
        app.update()
        self.assertEqual(panel.state.steps, [])
        self.assertEqual(panel.state.task_title, "Build me a snake game")

        panel.update_task_step("thought", "Thought — I need a game loop first",
                               "done", "", "s1")
        panel.update_task_step("tool", "Write snake.py", "in_progress", "", "s2")
        panel.update_task_step("tool", "Write snake.py", "done", "", "s2")
        panel.update_task_step("run", "Run: python snake.py --self-test",
                               "blocked", "AssertionError: ticks", "s3")
        panel.update_task_step("run", "Run: python snake.py --self-test",
                               "done", "12 passed", "s4")
        app.update()

        texts = [lbl.cget("text") for lbl in panel._step_labels.values()]
        joined = "\n".join(texts)
        self.assertIn("Thought — I need a game loop first", joined)
        self.assertIn("✓  Write snake.py", joined)          # updated in place
        self.assertIn("!  Run: python snake.py --self-test — AssertionError: ticks", joined)
        self.assertIn("12 passed", joined)
        self.assertIn("working", panel._steps_sub.cget("text"))

        # Resolving the pending turn stamps the done summary.
        panel.state.pending_id = "deadbeef"
        panel._resolve_pending("All set — your game is ready.", True)
        app.update()
        sub = panel._steps_sub.cget("text")
        self.assertTrue(sub.startswith("✓ done"), sub)
        self.assertIn("4 steps", sub)

        # A new user turn opens a fresh card; the old step list is gone
        # from state (the finished card above stays as a record).
        panel.state.pending_id = None
        panel._submit_text("Now fix the speed bug")
        app.update()
        self.assertEqual(panel.state.steps, [])
        self.assertEqual(panel.state.task_title, "Now fix the speed bug")

        # Failure path: unresolved steps flip to blocked + red summary.
        panel.update_task_step("tool", "Write snake.py", "in_progress", "", "t1")
        panel.state.pending_id = "cafe"
        panel._resolve_pending("the model quota ran out", False)
        app.update()
        self.assertEqual(panel.state.steps[0]["status"], "blocked")
        self.assertTrue(panel._steps_sub.cget("text").startswith("! failed"))
        app.destroy()

    def test_red_review_turn_shows_issues_found_not_done(self):
        """Act Script honesty rule: a turn whose Review row went blocked
        (a red verdict like local_bug) must NOT stamp '✓ done' on the
        card just because the chat reply itself arrived fine -- the card
        tells the truth instead. Ordinary chat-loop hiccups (blocked
        non-review rows) must NOT trigger this downgrade."""
        import tkinter as tk  # noqa: F401 -- real display via Xvfb
        import ide as V

        class Host:
            is_preview = True

            def __init__(self):
                self.asks = []

            def ask(self, text, on_done):
                self.asks.append((text, on_done))

            def register_ide_chat_notify(self, h):
                self.notify = h

            def register_ide_build_status(self, h):
                self.build = h

            def register_ide_task_steps(self, h):
                self.steps = h

        W = tempfile.mkdtemp(prefix="idesteps-review")
        app = V.IDEApp(W, agent_host=Host())
        panel = getattr(app, "chat_panel", None)
        if panel is None:
            app.toggle_chat()
            app.update()
            panel = app.chat_panel

        # Turn 1: the exact rows a no-write Act Script run produces now.
        panel._submit_text("Build me a calculator web app")
        app.update()
        panel.update_task_step(
            "tool", "Step 2 — Work — step 1: create index.html -> "
            "verify FAILED", "blocked", "", "r1")
        panel.update_task_step(
            "verify", "Step 3 — Review — I checked the file system: no "
            "files were written", "blocked", "", "r2")
        panel.state.pending_id = "rev1"
        panel._resolve_pending("Review verdict: local_bug. No files "
                               "were written.", True)
        app.update()
        sub = panel._steps_sub.cget("text")
        self.assertTrue(sub.startswith("✗ issues found"), sub)
        self.assertFalse(sub.startswith("✓ done"), sub)

        # Turn 2 (control): a blocked NON-review row -- an ordinary chat
        # tool-loop hiccup the agent recovered from -- must NOT downgrade
        # a genuinely successful turn.
        panel.state.pending_id = None
        panel._submit_text("Tweak the styling")
        app.update()
        panel.update_task_step("run", "Run: python app.py", "blocked",
                               "boom", "c1")
        panel.state.pending_id = "rev2"
        panel._resolve_pending("Styled it anyway.", True)
        app.update()
        self.assertTrue(panel._steps_sub.cget("text").startswith("✓ done"),
                        panel._steps_sub.cget("text"))

        # Turn 3 (control): a green Review row never downgrades either.
        panel.state.pending_id = None
        panel._submit_text("One more pass")
        app.update()
        panel.update_task_step("verify", "Step 2 — Review — everything "
                               "verified on disk", "done", "", "g1")
        panel.state.pending_id = "rev3"
        panel._resolve_pending("Review verdict: done.", True)
        app.update()
        self.assertTrue(panel._steps_sub.cget("text").startswith("✓ done"),
                        panel._steps_sub.cget("text"))
        app.destroy()




class LoopNarrationE2ETests(unittest.TestCase):
    """The real AIWorker._run_tool_loop with a fully scripted provider:
    thought text + tool calls in, matching visible steps out -- the exact
    narration a user watches in the IDE chat panel during a task."""

    def test_thought_write_fail_fix_pass_sequence(self):
        agent_tools.begin_ide_task_step_sequence()
        import agent_overlay
        from types import SimpleNamespace

        events = []
        agent_tools.set_ide_task_step_handler(
            lambda kind, label, status, detail, sid: events.append(
                (kind, label, status, detail)))

        outcomes = iter([
            "Wrote game.py",
            "python game.py --test\nAssertionError: ticks",
            "Fixed game.py",
            "python game.py --test\n12 passed",
        ])
        scripts = {
            "ide_write_file": lambda **kw: next(outcomes),
            "ide_run_terminal_command": lambda **kw: next(outcomes),
        }
        originals = {k: agent_tools.TOOL_FUNCTIONS.get(k) for k in scripts}
        agent_tools.TOOL_FUNCTIONS.update(scripts)

        def tc(call_id, name, args):
            fn = SimpleNamespace(name=name, arguments=args)
            return SimpleNamespace(id=call_id, function=fn, type="function")

        replies = iter([
            SimpleNamespace(content="The game needs a loop and a self-test first.",
                            tool_calls=[tc("c1", "ide_write_file",
                                           {"path": "game.py"})]),
            SimpleNamespace(content="Now I'll verify it runs.",
                            tool_calls=[tc("c2", "ide_run_terminal_command",
                                           {"command": "python game.py --test"})]),
            SimpleNamespace(content="The tick counter is off by one -- fixing.",
                            tool_calls=[tc("c3", "ide_write_file",
                                           {"path": "game.py"})]),
            SimpleNamespace(content="And re-running the self-test.",
                            tool_calls=[tc("c4", "ide_run_terminal_command",
                                           {"command": "python game.py --test"})]),
            SimpleNamespace(content="Done -- the game works.", tool_calls=None),
        ])

        def create_fn(messages):
            message = next(replies)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        try:
            answer = agent_overlay.AIWorker._run_tool_loop(
                create_fn, [{"role": "user", "content": "build me a game"}],
                ide_scoped=True, max_iterations=10)
        finally:
            for name, original in originals.items():
                if original is None:
                    agent_tools.TOOL_FUNCTIONS.pop(name, None)
                else:
                    agent_tools.TOOL_FUNCTIONS[name] = original
            agent_tools.set_ide_task_step_handler(None)

        self.assertEqual(answer, "Done -- the game works.")
        thoughts = [e for e in events if e[0] == "thought"]
        self.assertEqual(len(thoughts), 4)
        self.assertIn("game needs a loop", thoughts[0][1])
        starts = [e for e in events if e[2] == "in_progress"]
        blocked = [e for e in events if e[2] == "blocked"]
        done = [e for e in events if e[2] == "done" and e[0] != "thought"]
        self.assertEqual(len(starts), 4)
        self.assertEqual(len(blocked), 1)
        self.assertIn("AssertionError", blocked[0][3])
        self.assertEqual(len(done), 3)
        self.assertTrue(any("12 passed" in e[3] for e in done))
        # Narration order must match the path the agent actually took.
        kinds = [e[0] for e in events[:6]]
        self.assertEqual(kinds[0], "thought")
        self.assertEqual(events[1], ("tool", "Step 1 — Write game.py",
                                     "in_progress", ""))
        self.assertEqual(events[2][:3], ("tool", "Step 1 — Write game.py",
                                         "done"))
        self.assertEqual(kinds[3], "thought")
        self.assertEqual(events[5][0], "run")
        self.assertTrue(events[5][1].startswith(
            "Step 2 — Run: python game.py --test"), events[5][1])
        self.assertEqual(events[5][2], "blocked")


class QueueContractTests(unittest.TestCase):
    """The registration lambda the Quiet workspace hands the host puts a
    ('__steps__', True, payload, None) tuple on the results queue -- the
    exact shape _poll routes to update_task_step."""

    def test_register_lambda_shape(self):
        seen = []
        agent_tools.begin_ide_task_step_sequence()
        agent_tools.set_ide_task_step_handler(
            lambda kind, label, status, detail, sid=None: seen.append(
                ("__steps__", True, (kind, label, status, detail, sid), None)))
        try:
            agent_tools.report_ide_task_step("tool", "Write x.py", "in_progress",
                                             "", "id9")
        finally:
            agent_tools.set_ide_task_step_handler(None)
        self.assertEqual(seen,
                         [("__steps__", True, ("tool", "Step 1 — Write x.py",
                                               "in_progress", "", "id9"), None)])

    def test_report_never_raises_without_handler(self):
        agent_tools.set_ide_task_step_handler(None)
        agent_tools.report_ide_task_step("tool", "anything")  # no panel: silent


if __name__ == "__main__":
    unittest.main(verbosity=2)

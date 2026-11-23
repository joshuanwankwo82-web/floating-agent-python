#!/usr/bin/env python3
"""Friday uses the shared connections in the Settings panel.

Two product rules the chat agent must obey, proven against the REAL
run() chain:

  1. "The API added in the Settings panel is what Friday calls with."
     Models previously matched to a task (Voice for the circle, Coding
     for the IDE's chat panel) are tried FIRST -- with full tool-calling
     for the builtin kinds. Other compatible connections are available
     as fallbacks. No environment-variable provider is used. Custom
     "+ Add model" entries keep their documented last-resort plain-text
     route through Act Script's dispatch, still scoped to the active surface.

  2. "Follow-ups keep the previous task."
     The IDE chat panel constructs a fresh AIWorker per message, so
     memory context (recent turns + recall) must load for ide-scoped
     workers too -- otherwise a follow-up like "continue" arrives with
     zero context and Friday visibly forgets the task it was doing.

No network: providers/creates are stubbed; memory is the in-process
ring plus local fixtures, same harness style as the other dev_tests.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

import agent_overlay as ov
import act_script
import agent_tools
import memory
import settings_store


def _app():
    return QApplication.instance() or QApplication(sys.argv)


def _worker(prompt="do the thing", ide_scoped=False):
    return ov.AIWorker(prompt, parent=None, ide_scoped=ide_scoped)


def _run(w):
    out = {}
    w.succeeded.connect(lambda t: out.setdefault("ok", t))
    w.failed.connect(lambda e: out.setdefault("err", e))
    w.run()  # synchronous call, same as the other suites' run-level parts
    return out


class RegionRoutingTests(unittest.TestCase):
    """Which task preference each surface requests from the shared pool."""

    def setUp(self):
        _app()
        ov._provider_last_connectivity_failure.clear()

    def _plan_role(self, worker):
        roles = []
        with mock.patch.object(
                act_script, "region_pool",
                lambda role: roles.append(role) or []):
            plan = worker._settings_pool_plan()
            assert plan == []
        return roles

    def test_ide_chat_draws_from_coding_region(self):
        roles = self._plan_role(_worker(ide_scoped=True))
        self.assertEqual(roles, ["coding"])

    def test_circle_chat_draws_from_voice_region(self):
        roles = self._plan_role(_worker(ide_scoped=False))
        self.assertEqual(roles, ["voice"])

    def test_ide_worker_reads_live_coding_settings_on_each_turn(self):
        first = {"id": "coding-a", "name": "First coding key",
                 "check_kind": "groq", "api_key": "key-a", "model": "model-a"}
        second = {"id": "coding-b", "name": "Updated coding key",
                  "check_kind": "groq", "api_key": "key-b", "model": "model-b"}
        empty = {faculty: [] for faculty in settings_store.FACULTIES}
        before = dict(empty, Coding=[first], Voice=[])
        after = dict(empty, Coding=[second], Voice=[first])
        worker = _worker(ide_scoped=True)
        with mock.patch.object(settings_store, "load",
                               side_effect=[before, after]):
            first_plan = worker._settings_pool_plan()
            second_plan = worker._settings_pool_plan()
        self.assertEqual(first_plan, [first])
        self.assertEqual(second_plan, [second, first])

    def test_plan_includes_builtin_kind_entries(self):
        entry = {"id": "custom-1", "name": "My Groq", "check_kind": "groq",
                 "api_key": "panel-key", "model": "mix-9", "base_url": ""}
        with mock.patch.object(act_script, "region_pool", lambda role: [entry]):
            plan = _worker(ide_scoped=True)._settings_pool_plan()
        self.assertEqual(plan, [entry])

    def test_duplicate_panel_entries_are_collapsed_once(self):
        """with the env tier gone there's nothing to dedup against env;
        identical (kind, key, model) panel entries still collapse inside
        one reply's plan so the same credential isn't dialed twice."""
        entry = {"id": "custom-1", "name": "My Groq", "check_kind": "groq",
                 "api_key": "same-key", "model": "mix-9", "base_url": ""}
        dup = dict(entry, id="custom-2")
        with mock.patch.object(act_script, "region_pool", lambda role: [entry, dup]):
            plan = _worker(ide_scoped=True)._settings_pool_plan()
        self.assertEqual(plan, [entry])

    def test_custom_generic_entries_are_in_the_shared_ide_plan(self):
        entry = {"id": "custom-9", "name": "OpenRouter", "check_kind": "generic_http",
                 "api_key": "sk-x", "model": "some/model", "base_url": "https://x/v1"}
        with mock.patch.object(act_script, "region_pool", lambda role: [entry]):
            plan = _worker(ide_scoped=True)._settings_pool_plan()
        self.assertEqual(plan, [entry])

    def test_ide_worker_sends_generic_models_through_scoped_tool_bridge(self):
        entry = {"id": "custom-9", "name": "OpenRouter",
                 "check_kind": "generic_http", "api_key": "sk-x",
                 "model": "some/model", "base_url": "https://x/v1"}
        worker = _worker("create a file", ide_scoped=True)
        with mock.patch.object(
                act_script, "region_pool", lambda role: [entry]), \
                mock.patch.object(ov, "_ide_act_script_protocol",
                                  return_value=(False, "")), \
                mock.patch.object(ov, "_run_text_tool_entry",
                                  return_value=(True, "created it", None)
                                  ) as text_tool, \
                mock.patch.object(memory, "build_context_block", lambda q: ""):
            out = _run(worker)
        self.assertEqual(out.get("ok"), "created it")
        text_tool.assert_called_once_with(
            entry, "coding", worker._system_prompt(), worker._prompt, True)

    def test_summarize_only_never_executes_generic_tool_markup(self):
        entry = {"id": "custom-9", "name": "OpenRouter",
                 "check_kind": "generic_http", "api_key": "sk-x",
                 "model": "some/model", "base_url": "https://x/v1"}
        worker = _worker("summarize", ide_scoped=False)
        worker._summarize_only = True
        result = act_script.CallResult(ok=True, text="summary only")
        with mock.patch.object(act_script, "call_model",
                               return_value=result) as call_model, \
                mock.patch.object(ov, "_run_text_tool_entry",
                                  side_effect=AssertionError(
                                      "summary must not execute tools")):
            out = worker._attempt_settings_entry(entry)
        self.assertEqual(out, (True, "summary only", None))
        call_model.assert_called_once_with(
            "voice", entry["id"], worker._system_prompt(), worker._prompt)


class RunChainTests(unittest.TestCase):
    """run() uses the shared pool for every surface."""

    def setUp(self):
        _app()
        ov._provider_last_connectivity_failure.clear()

    def _panel(self, entries):
        return mock.patch.object(act_script, "region_pool", lambda role: entries)

    def test_ide_can_use_a_model_previously_matched_to_voice(self):
        voice_entry = {"id": "voice-only", "name": "Voice key",
                       "check_kind": "groq", "api_key": "voice-key",
                       "model": "voice-model"}
        worker = _worker(ide_scoped=True)
        worker._attempt_settings_entry = lambda entry: (
            True, "shared model answer", None)
        with mock.patch.object(
                act_script, "region_pool",
                lambda role: [voice_entry]), \
                mock.patch.object(memory, "build_context_block", lambda q: ""):
            out = _run(worker)
        self.assertEqual(out.get("ok"), "shared model answer")

    def test_circle_can_use_a_model_previously_matched_to_coding(self):
        coding_entry = {"id": "coding-only", "name": "Coding key",
                        "check_kind": "groq", "api_key": "coding-key",
                        "model": "coding-model"}
        worker = _worker(ide_scoped=False)
        worker._attempt_settings_entry = lambda entry: (
            True, "shared model answer", None)
        with mock.patch.object(
                act_script, "region_pool",
                lambda role: [coding_entry]), \
                mock.patch.object(memory, "build_context_block", lambda q: ""):
            out = _run(worker)
        self.assertEqual(out.get("ok"), "shared model answer")

    def test_panel_key_is_attempted_first_and_succeeds(self):
        entry = {"id": "custom-1", "name": "My Groq", "check_kind": "groq",
                 "api_key": "panel-key", "model": "mix-9", "base_url": ""}
        w = _worker(ide_scoped=True)
        seen = {}
        w._try_groq_with = lambda key, model=None: (
            seen.update(key=key, model=model) or (True, "built it!", None))
        w._try_gemini = lambda: seen.setdefault("gemini_called", True) or (False, None, None)
        with                 self._panel([entry]), \
                mock.patch.object(memory, "build_context_block", lambda q: ""):
            out = _run(w)
        self.assertEqual(out.get("ok"), "built it!")
        self.assertEqual((seen.get("key"), seen.get("model")), ("panel-key", "mix-9"))
        self.assertNotIn("err", out)

    def test_panel_failure_moves_on_to_the_next_panel_entry_in_order(self):
        """No env tier exists anymore: a failing panel entry is recorded
        (key + friendly error) and run() moves on to the NEXT panel
        entry in order."""
        entry1 = {"id": "custom-1", "name": "My Groq", "check_kind": "groq",
                  "api_key": "panel-key-1", "model": "", "base_url": ""}
        entry2 = {"id": "custom-2", "name": "My Mistral", "check_kind": "mistral",
                  "api_key": "panel-key-2", "model": "", "base_url": ""}
        w = _worker(ide_scoped=False)
        order = []

        def _attempt(entry):
            order.append(entry["id"])
            if entry is entry1:
                return False, None, "⚠️ My Groq: down."
            return True, "second panel answer", None

        w._attempt_settings_entry = _attempt
        with self._panel([entry1, entry2]), \
                mock.patch.object(memory, "build_context_block", lambda q: ""):
            out = _run(w)
        self.assertEqual(out.get("ok"), "second panel answer")
        self.assertEqual(order, ["custom-1", "custom-2"])
        self.assertIn("panel-key-1", w._attempted_api_keys)

    def test_gate_does_not_short_circuit_with_coding_only_config(self):
        entry = {"id": "custom-1", "name": "My Mistral", "check_kind": "mistral",
                 "api_key": "coding-key", "model": "", "base_url": ""}
        w = _worker(ide_scoped=True)
        w._attempt_settings_entry = lambda e: (True, "coding done", None)
        with mock.patch.object(act_script, "region_pool",
                                  lambda role: [entry] if role == "coding" else []), \
                mock.patch.object(memory, "build_context_block", lambda q: ""):
            out = _run(w)
        self.assertEqual(out.get("ok"), "coding done")
        self.assertNotIn("No API key found", out.get("err", ""))

    def test_done_key_is_not_replayed_to_env_tier(self):
        """A panel key that already failed is recorded, so the
        plain-text extras tier below won't dress the same credential in
        a different costume and try it again."""
        entry = {"id": "custom-1", "name": "My Groq", "check_kind": "groq",
                 "api_key": "panel-key", "model": "", "base_url": ""}
        w = _worker(ide_scoped=False)
        w._try_groq_with = lambda key, model=None: (False, None, "⚠️ down.")
        w._try_gemini = lambda: (False, None, None)
        w._try_groq = lambda: (False, None, None)
        w._try_mistral = lambda: (False, None, None)
        with                 self._panel([entry]), \
                mock.patch.object(memory, "build_context_block", lambda q: ""):
            _run(w)
        self.assertIn("panel-key", w._attempted_api_keys)
        # And the extras tier honours that record, never re-trying the
        # same key dressed up as a settings entry.
        with mock.patch.object(act_script, "region_pool", lambda role: [entry]), \
                mock.patch.object(act_script, "call_model",
                                  side_effect=AssertionError("must not re-try")):
            ok, text, err = w._try_voice_extras()
        self.assertEqual((ok, text, err), (False, None, None))


class ExtrasRegionTests(unittest.TestCase):
    """The last-resort tier is region-aware per surface."""

    def setUp(self):
        _app()

    def _extras_role(self, worker):
        roles = []
        with mock.patch.object(
                act_script, "region_pool",
                lambda role: roles.append(role) or []):
            worker._try_voice_extras()
        return roles

    def test_ide_extras_draw_from_coding_region(self):
        self.assertEqual(self._extras_role(_worker(ide_scoped=True)), ["coding"])

    def test_circle_extras_draw_from_voice_region(self):
        self.assertEqual(self._extras_role(_worker(ide_scoped=False)), ["voice"])


class ContinuityTests(unittest.TestCase):
    """A follow-up message keeps the previous task in view."""

    def setUp(self):
        _app()
        ov._provider_last_connectivity_failure.clear()
        memory._clear_recent_turns()

    def tearDown(self):
        memory._clear_recent_turns()

    def test_ide_turn_sees_the_previous_exchange(self):
        memory.remember_recent_turn("user", "build me a snake game")
        memory.remember_recent_turn("friday", "Wrote snake.py (214 lines).")

        # The reply now flows through the panel entry's own api_key
        # (_try_gemini_with) -- nothing reads the process environment.
        panel_entry = {"id": "panel-gemini", "name": "Gemini",
                       "check_kind": "gemini", "api_key": "panel-key",
                       "model": "", "base_url": ""}
        w = _worker("continue", ide_scoped=True)
        prompts = []

        def _capture(key, model=None):
            prompts.append(w._system_prompt())
            return True, "Continuing the snake game now.", None

        w._try_gemini_with = _capture
        env_called = []
        with mock.patch.object(act_script, "region_pool",
                               lambda role: [panel_entry] if role == "coding" else []), \
                mock.patch.object(memory, "embed_text", side_effect=Exception("offline")), \
                mock.patch.object(agent_tools, "get_environment_snapshot",
                                  side_effect=lambda: env_called.append(True) or ""):
            out = _run(w)
        self.assertEqual(out.get("ok"), "Continuing the snake game now.")
        self.assertEqual(len(prompts), 1)
        self.assertIn("build me a snake game", prompts[0])
        self.assertIn("Wrote snake.py", prompts[0])

    def test_ide_turn_still_skips_the_screen_snapshot(self):
        panel_entry = {"id": "panel-gemini", "name": "Gemini",
                       "check_kind": "gemini", "api_key": "panel-key",
                       "model": "", "base_url": ""}
        w = _worker("continue", ide_scoped=True)
        w._try_gemini_with = lambda key, model=None: (True, "ok", None)
        with mock.patch.object(act_script, "region_pool",
                               lambda role: [panel_entry] if role == "coding" else []), \
                mock.patch.object(memory, "build_context_block", lambda q: ""), \
                mock.patch.object(agent_tools, "get_environment_snapshot",
                                  side_effect=AssertionError(
                                      "screen snapshot must not run for IDE turns")):
            out = _run(w)
        self.assertEqual(out.get("ok"), "ok")

    def test_circle_turn_still_gets_its_screen_snapshot(self):
        panel_entry = {"id": "panel-gemini", "name": "Gemini",
                       "check_kind": "gemini", "api_key": "panel-key",
                       "model": "", "base_url": ""}
        w = _worker("what's open?", ide_scoped=False)
        w._try_gemini_with = lambda key, model=None: (True, "ok", None)
        snapshots = []
        with mock.patch.object(act_script, "region_pool",
                               lambda role: [panel_entry] if role == "voice" else []), \
                mock.patch.object(memory, "build_context_block", lambda q: ""), \
                mock.patch.object(agent_tools, "get_environment_snapshot",
                                  side_effect=lambda: snapshots.append(True) or "screen state"):
            out = _run(w)
        self.assertEqual(out.get("ok"), "ok")
        self.assertEqual(snapshots, [True])


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Three wiring contracts:
A) every created/cloned workspace lives in the .friday workspace DB
B) Settings-panel keys are used ahead of the .env defaults (Tavily,
   embeddings)
C) the IDE chat follows the ACT Script protocol (Sequential Rotation /
   Fan-Out / Debate) for real builds, and keeps the normal chat loop for
   small talk.
Run with: QT_QPA_PLATFORM=offscreen python3 dev_tests/test_settings_and_protocol_wiring.py
"""
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import agent_tools as at
import memory


class WorkspaceDbTests(unittest.TestCase):
    def setUp(self):
        self.memdir = tempfile.mkdtemp(prefix="dbroot-")
        memory.set_memory_dir(self.memdir)

    def tearDown(self):
        shutil.rmtree(self.memdir, ignore_errors=True)

    def test_roots_live_under_dot_friday(self):
        roots = memory.workspace_db_dirs()
        self.assertEqual(roots["created"],
                         os.path.join(self.memdir, "workspace", "created"))
        self.assertEqual(roots["cloned"],
                         os.path.join(self.memdir, "workspace", "cloned"))

    def test_db_dir_ensures_the_folder_exists(self):
        created = memory.workspace_db_dir("created")
        self.assertTrue(os.path.isdir(created))
        cloned = memory.workspace_db_dir("cloned")
        self.assertTrue(os.path.isdir(cloned))

    def test_clone_defaults_into_the_db(self):
        # real ide_clone_repository flow, with git + IDE faked out
        scratch = tempfile.mkdtemp(prefix="fake-ide-")
        app = types.SimpleNamespace(
            root_dir=scratch,
            run_terminal_command=lambda cmd, timeout=None: "Cloning...",
            set_root=lambda p: None,
            refresh_explorer=lambda: None,
            _index_workspace=lambda: None)
        old_app = at._ide_app
        at._ide_app = app
        try:
            with mock.patch.object(at, "_run_on_main", lambda f: f()):
                def fake_move(url, dest):
                    os.makedirs(os.path.join(dest, ".git"), exist_ok=True)
                    return "Cloning into..."
                app.run_terminal_command = lambda cmd, timeout=None: (
                    fake_move(cmd.split('"')[1],
                              cmd.split('"')[3]))
                note = at.ide_clone_repository(
                    "https://github.com/x/sample-repo.git")
        finally:
            at._ide_app = old_app
            shutil.rmtree(scratch, ignore_errors=True)
        dest = os.path.join(self.memdir, "workspace", "cloned",
                            "sample-repo")
        self.assertTrue("Cloned 'sample-repo'" in note, note)
        self.assertTrue(os.path.isdir(os.path.join(dest, ".git")), dest)


class SettingsKeyPriorityTests(unittest.TestCase):
    def _settings_with(self, faculty, entries):
        return mock.patch("settings_store.load",
                          return_value={faculty: entries})

    def test_tavily_settings_key_beats_env(self):
        entries = [{"check_kind": "tavily", "api_key": "settings-key"}]
        with self._settings_with("Research", entries), \
             mock.patch.dict(os.environ, {"TAVILY_API_KEY": "env-key"}):
            self.assertEqual(at._settings_tavily_key(), "settings-key")
        # and the env value is only the fallback
        with self._settings_with("Research", []), \
             mock.patch.dict(os.environ, {"TAVILY_API_KEY": "env-key"}):
            self.assertEqual(at._settings_tavily_key(), "")

    def test_embed_settings_key_beats_env(self):
        entries = [{"check_kind": "gemini_embed",
                    "api_key": "settings-gem"}]
        with self._settings_with("Embedding", entries):
            self.assertEqual(memory._settings_embed_key("gemini_embed"),
                             "settings-gem")
        with self._settings_with("Embedding", []):
            self.assertEqual(memory._settings_embed_key("gemini_embed"), "")


class ActScriptProtocolTests(unittest.TestCase):
    def test_ide_chat_routes_into_protocol_pipelines(self):
        # stub act_script module entirely: classify says sequential,
        # runner returns the protocol report
        stub = types.SimpleNamespace(
            available_providers=lambda role: ["p1"],
            thinking_classify=lambda prompt, p: ("sequential", None),
            run_act_script=lambda prompt, mode="auto":
                f"**Act Script -- Sequential Rotation** ({mode}) done",
        )
        with mock.patch.dict(sys.modules, {"act_script": stub}):
            handled, text = __import__("agent_overlay")._ide_act_script_protocol(
                "build me a lamp UI")
        self.assertTrue(handled)
        self.assertIn("Sequential Rotation", text)

    def test_small_talk_stays_on_the_normal_chat_path(self):
        stub = types.SimpleNamespace(
            available_providers=lambda role: ["p1"],
            thinking_classify=lambda prompt, p: ("single", None),
            run_act_script=lambda *a, **k: "should never run",
        )
        with mock.patch.dict(sys.modules, {"act_script": stub}):
            handled, text = __import__("agent_overlay")._ide_act_script_protocol(
                "what does this function do?")
        self.assertFalse(handled)
        self.assertEqual(text, "")

    def test_simple_file_and_folder_requests_use_direct_tool_path(self):
        stub = types.SimpleNamespace(
            available_providers=lambda role: (_ for _ in ()).throw(
                AssertionError("simple filesystem operations should bypass ACT Script")),
            run_act_script=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("simple filesystem operations should bypass ACT Script")),
        )
        prompts = [
            "Could you create a file named greet.py?",
            "Please create a folder named assets.",
            "Write a new file in the project.",
        ]
        with mock.patch.dict(sys.modules, {"act_script": stub}):
            for prompt in prompts:
                handled, text = __import__(
                    "agent_overlay")._ide_act_script_protocol(prompt)
                self.assertFalse(handled)
                self.assertEqual(text, "")

    def test_how_to_question_is_not_forced_into_file_pipeline(self):
        stub = types.SimpleNamespace(
            available_providers=lambda role: ["p1"],
            thinking_classify=lambda prompt, p: ("single", None),
            run_act_script=lambda *a, **k: "should never run",
        )
        with mock.patch.dict(sys.modules, {"act_script": stub}):
            handled, text = __import__("agent_overlay")._ide_act_script_protocol(
                "How do I create a Python file?")
        self.assertFalse(handled)
        self.assertEqual(text, "")

    def test_non_code_creation_request_stays_in_chat(self):
        stub = types.SimpleNamespace(
            available_providers=lambda role: ["p1"],
            thinking_classify=lambda prompt, p: ("single", None),
            run_act_script=lambda *a, **k: "should never run",
        )
        with mock.patch.dict(sys.modules, {"act_script": stub}):
            handled, text = __import__("agent_overlay")._ide_act_script_protocol(
                "Could you make me laugh?")
        self.assertFalse(handled)
        self.assertEqual(text, "")

    def test_no_configured_provider_falls_through_quietly(self):
        stub = types.SimpleNamespace(
            available_providers=lambda role: [],
        )
        with mock.patch.dict(sys.modules, {"act_script": stub}):
            handled, _ = __import__("agent_overlay")._ide_act_script_protocol(
                "anything")
        self.assertFalse(handled)

    def test_empty_protocol_output_falls_through(self):
        stub = types.SimpleNamespace(
            available_providers=lambda role: ["p1"],
            thinking_classify=lambda p, x: ("debate", None),
            run_act_script=lambda p, mode="auto": "",
        )
        with mock.patch.dict(sys.modules, {"act_script": stub}):
            handled, _ = __import__("agent_overlay")._ide_act_script_protocol(
                "write the plan")
        self.assertFalse(handled)

    def test_ide_scoped_worker_emits_protocol_reply(self):
        # full AIWorker.run() with the protocol returning content must
        # succeed with the protocol text and never touch the provider loop
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        ov = __import__("agent_overlay")
        ov._main_thread_invoker = ov._MainThreadInvoker()
        stub = __import__("types").SimpleNamespace(
            available_providers=lambda role: ["p1"],
            thinking_classify=lambda p, x: ("single", None),
            region_pool=lambda role: [{"id": "p1"}],
        )
        out = []

        def _pump_until(pred, timeout=4.0):
            import time as _t
            deadline = _t.time() + timeout
            while _t.time() < deadline:
                app.processEvents()
                if pred():
                    return True
                _t.sleep(0.03)
            return pred()

        with mock.patch.dict(sys.modules, {"act_script": stub}), \
             mock.patch.object(ov, "_ide_act_script_protocol",
                               return_value=(True, "**protocol**) report")):
            build_done = []
            ov._on_qt_main_thread(lambda: build_done.append(True))
            self.assertTrue(_pump_until(lambda: build_done))
            worker = ov.AIWorker("build the lamp ui", ide_scoped=True)
            worker.succeeded.connect(lambda t: out.append(t))
            ov._on_qt_main_thread(worker.start)
            self.assertTrue(_pump_until(lambda: out))
        self.assertEqual(out, ["**protocol**) report"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Workspace-region wiring: folders the agent creates inside the open IDE
workspace must be findable by ide_open_folder, and projects built inside a
throwaway 'friday-empty-*' scratch root must be relocated to a persistent
home when opened (never stranded under %TEMP%).
Run with: python3 dev_tests/test_workspace_region_wiring.py
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import agent_tools as at
import memory


class _FakeApp:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.set_root_to = None

    def set_root(self, p):
        self.set_root_to = p
        self.root_dir = p

    def refresh_explorer(self):
        pass

    def _index_workspace(self):
        pass


class ResolveFolderHintTests(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="fake-ws-")
        self.home = tempfile.mkdtemp(prefix="fake-home-")
        self._ide_home = os.path.join(self.home, "Friday-Agent-Workspace")
        os.makedirs(self._ide_home, exist_ok=True)
        self.app = _FakeApp(self.ws)
        self._old_app = at._ide_app
        at._ide_app = self.app
        self._patch = mock.patch.object(at, "_IDE_HOME_DIR", self._ide_home)
        self._patch.start()

    def tearDown(self):
        at._ide_app = self._old_app
        self._patch.stop()
        shutil.rmtree(self.ws, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_workspace_child_found_by_bare_name(self):
        os.mkdir(os.path.join(self.ws, "switchable-lamp"))
        got = at._resolve_folder_hint("switchable-lamp")
        self.assertEqual(got,
                         os.path.abspath(os.path.join(self.ws,
                                                      "switchable-lamp")))

    def test_workspace_wins_over_common_locations(self):
        # same name in Friday-Agent-Workspace AND in the open workspace --
        # workspace must win (that's the folder the agent is actively using)
        os.mkdir(os.path.join(self.ws, "lamp-app"))
        other = os.path.join(self._ide_home, "lamp-app")
        os.mkdir(other)
        got = at._resolve_folder_hint("lamp-app")
        self.assertEqual(got, os.path.abspath(os.path.join(self.ws, "lamp-app")))

    def test_workspace_root_itself_resolves(self):
        got = at._resolve_folder_hint(os.path.basename(self.ws))
        self.assertEqual(got, os.path.abspath(self.ws))

    def test_fallback_to_ide_home_still_works(self):
        # not in the open workspace -> still found in Friday-Agent-Workspace
        target = os.path.join(self._ide_home, "old-project")
        os.mkdir(target)
        self.assertEqual(at._resolve_folder_hint("old-project"),
                         os.path.abspath(target))


class PlaceholderRelocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="faketemp-")
        self.home = tempfile.mkdtemp(prefix="fakehome-")
        self._ide_home = os.path.join(self.home, "Friday-Agent-Workspace")
        self.scratch = tempfile.mkdtemp(prefix="friday-empty-", dir=self.tmp)
        self.app = _FakeApp(self.scratch)
        self._old_app = at._ide_app
        at._ide_app = self.app
        self._patch = mock.patch.object(at, "_IDE_HOME_DIR", self._ide_home)
        self._patch.start()
        # the workspace DB lives under the memory root -- redirect it to a
        # test temp dir, exactly like every other memory-suite does
        self._memdir = tempfile.mkdtemp(prefix="fake-memroot-")
        memory.set_memory_dir(self._memdir)

    def tearDown(self):
        at._ide_app = self._old_app
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self._memdir, ignore_errors=True)

    def test_project_moves_out_of_temp_on_open(self):
        # create inside the scratch root, exactly like ide_create_folder does
        proj = os.path.join(self.scratch, "switchable-lamp")
        os.mkdir(proj)
        open(os.path.join(proj, "index.html"), "w").write("<html>")
        with mock.patch.object(at, "_run_on_main", lambda f: f()):
            note = at.ide_open_folder("switchable-lamp")
        moved = os.path.join(self._memdir, "workspace", "created",
                             "switchable-lamp")
        self.assertTrue(os.path.isdir(moved), note)
        self.assertTrue(os.path.isfile(os.path.join(moved, "index.html")))
        self.assertFalse(os.path.isdir(proj), "moved out of TEMP")
        self.assertEqual(self.app.set_root_to, moved)
        self.assertIn("switchable-lamp", note)

    def test_real_workspace_regions_are_untouched(self):
        # a non-placeholder workspace: nothing gets moved anywhere
        real_ws = tempfile.mkdtemp(prefix="real-ws-")
        self.app.root_dir = real_ws
        proj = os.path.join(real_ws, "game")
        os.mkdir(proj)
        new = at._relocate_from_placeholder(proj)
        self.assertEqual(new, proj)
        self.assertTrue(os.path.isdir(proj))
        shutil.rmtree(real_ws, ignore_errors=True)

    def test_collision_gets_a_suffix(self):
        proj1 = os.path.join(self.scratch, "app")
        os.mkdir(proj1)
        os.makedirs(os.path.join(self._memdir, "workspace", "created"),
                    exist_ok=True)
        existing = os.path.join(self._memdir, "workspace", "created", "app")
        os.mkdir(existing)
        new = at._relocate_from_placeholder(proj1)
        self.assertTrue(new.endswith("app-2") or new.endswith("app-3"),
                        f"expected suffixed dest, got {new}")

    def test_move_failure_falls_back_to_open_in_place(self):
        proj = os.path.join(self.scratch, "thing")
        os.mkdir(proj)
        with mock.patch("shutil.move", side_effect=OSError("nope")):
            new = at._relocate_from_placeholder(proj)
        self.assertEqual(new, proj)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Save-current-work + new standalone IDE workspace window regression."""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDE_DIR = os.path.join(ROOT, "ide")
if IDE_DIR not in sys.path:
    sys.path.insert(0, IDE_DIR)

try:
    import ide as ide_mod
except Exception as exc:  # GUI dependencies may be absent in CI
    ide_mod = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


@unittest.skipIf(ide_mod is None, "IDE GUI dependencies unavailable: %s" %
                 _IMPORT_ERROR)
class NewWorkspaceWindow(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp(prefix="new_workspace_save_")
        self.path = os.path.join(self.workspace, "keep.txt")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("old")
        self.app = ide_mod.IDEApp(self.workspace, show_chat=False)
        self.app.update_idletasks()
        self.app.update()
        self.app.open_file(self.path)
        self.app.editor.text.delete("1.0", "end")
        self.app.editor.text.insert("1.0", "saved before new workspace")
        self.app.mark_dirty()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def test_saves_current_work_then_spawns_fresh_ide_process(self):
        with mock.patch.object(ide_mod.subprocess, "Popen") as launch:
            self.assertTrue(self.app.new_workspace_window())
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "saved before new workspace")
        self.assertEqual(launch.call_count, 1)
        args, kwargs = launch.call_args
        self.assertTrue(args[0][1].endswith("launch_ide.py"))
        self.assertIn("--no-chat", args[0])
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual(self.app.root_dir, self.workspace)

    def test_cancelled_save_as_aborts_new_window(self):
        self.app.new_file()
        self.app.editor.text.insert("1.0", "unsaved untitled content")
        self.app.mark_dirty()
        with mock.patch.object(ide_mod.filedialog, "asksaveasfilename",
                               return_value=""), \
             mock.patch.object(ide_mod.subprocess, "Popen") as launch:
            self.assertFalse(self.app.new_workspace_window())
        launch.assert_not_called()
        self.assertEqual(self.app.current_buffer()["content"],
                         "unsaved untitled content")


if __name__ == "__main__":
    unittest.main()

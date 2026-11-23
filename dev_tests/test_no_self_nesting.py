
"""Self-nesting guard + open-folder relative-path hint.
Run with: DISPLAY=:99 python3 dev_tests/test_no_self_nesting.py
"""
import os, sys, tempfile, types, unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "ide"))

import agent_tools as at
import memory


class SelfNestingGuardTests(unittest.TestCase):
    def setUp(self):
        self.memdir = tempfile.mkdtemp(prefix="guard-mem-")
        memory.set_memory_dir(self.memdir)
        self.ws = tempfile.mkdtemp(prefix="contact-form-", dir=tempfile.gettempdir())
        # make basename exactly the bug's shape
        self.root = os.path.join(self.ws, "contact-form")
        os.makedirs(self.root)
        app = types.SimpleNamespace(root_dir=self.root)
        _fs = __import__("ide").WorkspaceFS(app)
        self.fs = _fs

    def test_repeating_root_name_collapses(self):
        got = self.fs.resolve("contact-form/index.html")
        self.assertEqual(got, os.path.join(os.path.abspath(self.root),
                                           "index.html"))

    def test_deeper_repeat_also_collapses(self):
        got = self.fs.resolve("contact-form/src/app.js")
        self.assertTrue(got.endswith(os.path.join("contact-form", "src",
                                                  "app.js")))

    def test_plain_paths_untouched(self):
        got = self.fs.resolve("src/app.js")
        self.assertTrue(got.endswith(os.path.join("contact-form", "src",
                                                  "app.js")))

    def test_single_segment_untouched(self):
        got = self.fs.resolve("index.html")
        self.assertTrue(got.endswith(os.path.join("contact-form",
                                                  "index.html")))

    def test_the_actual_two_file_bug_converges(self):
        # what the screenshot did: nested variant + root variant -> ONE file
        a = self.fs.resolve("contact-form/index.html")
        b = self.fs.resolve("index.html")
        self.assertEqual(a, b)
        self.fs.write("contact-form/index.html", "<h1>v2</h1>")
        self.fs.write("index.html", "<h1>v2-final</h1>")
        with open(a, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "<h1>v2-final</h1>")
        # and the nested folder was never even manufactured
        self.assertFalse(os.path.isdir(
            os.path.join(self.root, "contact-form")))

    def test_write_files_into_db_dest(self):
        # open_hint message sanity lives in agent_tools (string contract)
        src = open(os.path.join(_ROOT, "agent_tools.py"),
                   encoding="utf-8").read()
        self.assertIn("root is now this folder itself", src)
        self.assertIn("NEVER prefix paths with", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

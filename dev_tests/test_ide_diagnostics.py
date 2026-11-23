#!/usr/bin/env python3
"""The Problems tab now reports real diagnostics.

Syntax findings come from CPython's own compile stage (always present);
warning findings from pyflakes or ruff *if actually installed* -- a
missing linter is skipped honestly, never faked. These checks prove the
findings carry precise file:line:col + enum severity + honoring-source
labels, workspace aggregation stays wrapped, and the cap is real.

Run: python3 dev_tests/test_ide_diagnostics.py"""

import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ide"))

from quiet import diagnostics  # noqa: E402


def _write(directory, name, text):
    path = os.path.join(directory, name)
    with open(path, "w") as fh:
        fh.write(text)
    return path


class SyntaxLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ide-diag-")

    def test_clean_file_reports_nothing(self):
        path = _write(self.tmp, "clean.py", "x = 1\nprint(x)\n")
        findings, backend = diagnostics.analyze_file(path)
        self.assertEqual(findings, [])
        self.assertIn("python", backend.label)

    def test_syntax_error_location_is_exact(self):
        path = _write(self.tmp, "broken.py", "x = 1\ny = (\n")
        findings, _ = diagnostics.analyze_file(path)
        errors = [f for f in findings if f.severity == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].line, 2)
        self.assertEqual(errors[0].col, 4)  # CPython's reported caret, 0-based
        self.assertEqual(errors[0].source, "python")
        self.assertEqual(errors[0].file, os.path.abspath(path))

    def test_in_memory_text_needs_no_save(self):
        findings, _ = diagnostics.analyze_text("mem.py", "x = (1 + \n")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].file, os.path.abspath("mem.py"))

    def test_read_failure_is_reported_not_raised(self):
        findings, _ = diagnostics.analyze_file(
            os.path.join(self.tmp, "nope.py"))
        self.assertEqual(findings[0].code, "read")


class LinterLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ide-diag-")

    def test_unused_import_and_undefined_name_are_warnings(self):
        kind, _exe = diagnostics.linter_backend()
        if kind == "":
            self.skipTest("no linter backend installed in this environment")
        path = _write(self.tmp, "lint.py", textwrap.dedent("""\
            import os
            import sys
            def f():
                return undefined_name
            f(os.getcwd())
            """))
        findings, backend = diagnostics.analyze_file(path)
        by_code = {f.code for f in findings}
        self.assertTrue(any(c.startswith("F") for c in by_code),
                        f"expected pyflakes-style codes, got {by_code}")
        self.assertIn(kind, backend.label)
        warned = {(f.line, f.message) for f in findings
                  if f.severity == "warning"}
        self.assertTrue(any("undefined" in m.lower() for _, m in warned),
                        warned)
        self.assertTrue(any(f.line in (1, 2) for f in findings
                            if "imported but unused" in f.message),
                        findings)

    def test_backend_label_is_honest_when_nothing_installed(self):
        kind, _ = diagnostics.linter_backend()
        label = diagnostics.analyze_file(
            _write(self.tmp, "a.py", "x = 1\n"))[1].label
        if kind:
            self.assertIn(kind, label)
        else:
            self.assertIn("syntax only", label)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ide-diag-ws-")

    def test_aggregates_per_file_and_skips_lookaside_dirs(self):
        _write(self.tmp, "a.py", "ok_a = True\n")
        _write(self.tmp, "b.py", "broken = (\n")
        os.makedirs(os.path.join(self.tmp, "__pycache__"))
        _write(os.path.join(self.tmp, "__pycache__"), "c.py", "no = (\n")
        os.makedirs(os.path.join(self.tmp, ".hiddendir"))
        _write(os.path.join(self.tmp, ".hiddendir"), "d.py", "no2 = (\n")
        aggregate, label = diagnostics.analyze_workspace(self.tmp)
        files = {os.path.basename(f) for f in aggregate}
        self.assertEqual(files, {"b.py"})
        self.assertIn("python", label)

    def test_cap_is_honored(self):
        for i in range(5):
            _write(self.tmp, f"f{i}.py", f"ok_{i} = True\n")
        files = diagnostics.python_files(self.tmp, limit=2)
        self.assertEqual(len(files), 2)

    def test_findings_are_plain_dataclasses(self):
        path = _write(self.tmp, "x.py", "x = (\n")
        findings, _ = diagnostics.analyze_file(path)
        f = findings[0]
        self.assertEqual(f.file, os.path.abspath(path))
        self.assertEqual((f.line, f.col), (1, 4))  # CPython caret, 0-based
        self.assertEqual((f.severity, f.source), ("error", "python"))
        self.assertEqual(f.code, "syntax")
        self.assertIn("(' was never closed", f.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)

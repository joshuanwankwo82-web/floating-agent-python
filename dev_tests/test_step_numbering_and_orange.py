"""Step numbering (IDE chat cards read 'Step N — ...' like Friday's own
step-by-step action narration) and the orange 'working' indicator
mapping. Pure logic checks; only the minimized-pill color-table test
needs to import ide.py.
Run with: DISPLAY=:99 python3 dev_tests/test_step_numbering_and_orange.py
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import agent_tools as at


class StepNumberingTests(unittest.TestCase):
    def setUp(self):
        self.snap = []
        at.set_ide_task_step_handler(
            lambda kind, label, status, detail, step_id:
                self.snap.append(dict(kind=kind, label=label,
                                      status=status, step_id=step_id)))
        at.begin_ide_task_step_sequence()

    def tearDown(self):
        at.set_ide_task_step_handler(None)
        at.begin_ide_task_step_sequence()

    def _start_labels(self):
        return [d["label"] for d in self.snap if d["status"] == "in_progress"]

    def test_steps_number_from_1(self):
        at.report_ide_task_step("kind_file", "Read the open folder")
        at.report_ide_task_step("kind_file", "Write the files")
        at.report_ide_task_step("kind_file", "Run the tests")
        self.assertEqual(self._start_labels(),
                         ["Step 1 — Read the open folder",
                          "Step 2 — Write the files",
                          "Step 3 — Run the tests"])

    def test_new_task_resets_numbering(self):
        at.report_ide_task_step("kind_file", "First task action")
        at.begin_ide_task_step_sequence()
        at.report_ide_task_step("kind_file", "Second task action")
        self.assertEqual(self._start_labels(),
                         ["Step 1 — First task action",
                          "Step 1 — Second task action"])

    def test_thought_rows_stay_unnumbered(self):
        at.report_ide_task_step("thought", "checking the folder layout")
        at.report_ide_task_step("kind_file", "Write the file")
        self.assertEqual(self._start_labels(),
                         ["checking the folder layout",
                          "Step 1 — Write the file"])

    def test_completion_upserts_reuse_the_start_row_number(self):
        at.report_ide_task_step("kind_file", "Build the form", step_id="s1")
        at.report_ide_task_step("kind_file", "Build the form", step_id="s1",
                                status="done")
        self.assertEqual(self.snap[-1]["label"], "Step 1 — Build the form")
        self.assertEqual(self.snap[-2]["label"], "Step 1 — Build the form")
        self.assertEqual(len(self.snap), 2)
        # completions must not consume step numbers
        at.report_ide_task_step("kind_file", "Next action")
        self.assertEqual(self.snap[-1]["label"], "Step 2 — Next action")

    def test_numbering_accepts_a_real_tool_narration(self):
        # same call shape call_tool() uses for ide_chat tools
        at.report_ide_task_step("kind_file", "Write src/contact-form.html")
        at.report_ide_task_step("kind_run", "Run python manage.py test",
                                detail="12/12 passed")
        self.assertEqual(self._start_labels(),
                         ["Step 1 — Write src/contact-form.html",
                          "Step 2 — Run python manage.py test"])


class OrangeIndicatorTests(unittest.TestCase):
    def test_working_statuses_set(self):
        self.assertIn("working", at.PANEL_WORKING_STATUSES)
        self.assertIn("waiting", at.PANEL_WORKING_STATUSES)
        self.assertNotIn("done", at.PANEL_WORKING_STATUSES)

    def test_pill_color_map_has_working(self):
        sys.path.insert(0, os.path.join(_ROOT, "ide"))
        import ide
        self.assertEqual(
            ide.MinimizedPill._STATUS_COLORS.get("working"), "warn")


if __name__ == "__main__":
    unittest.main(verbosity=2)

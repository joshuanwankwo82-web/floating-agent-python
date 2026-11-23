"""Circle working-ring: while any panel is mid-task the idle circle shows
just the orange spinner; interactions (question asked/answered) pause the
spinner visually and it resumes when Friday is idle again. Offscreen Qt.
Run with: QT_QPA_PLATFORM=offscreen python3 dev_tests/test_working_ring.py
"""
import os
import sys
import time
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import agent_overlay as ov
import agent_tools as at
import memory
import tempfile


class WorkingRingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        memory.set_memory_dir(tempfile.mkdtemp())

    def setUp(self):
        self.overlay = ov.CircleOverlay()
        self.overlay._panels_working.clear()

    def tearDown(self):
        self.overlay.close()
        self.overlay.deleteLater()

    def _pump(self, secs=0.05):
        deadline = time.time() + secs
        while time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def test_status_listener_marks_and_clears_panels(self):
        at.report_panel_status("ide", "working")
        self.assertIn("ide", self.overlay._panels_working)
        at.report_panel_status("ide", "done")
        self.assertNotIn("ide", self.overlay._panels_working)

    def test_idle_starts_pulse_timer_while_working(self):
        self.overlay._anim_timer.stop()
        at.report_panel_status("ide", "working")
        self._pump()
        self.assertTrue(self.overlay._anim_timer.isActive())
        at.report_panel_status("ide", "done")
        self._pump()
        self.assertFalse(self.overlay._anim_timer.isActive())

    def test_waiting_counts_as_still_working(self):
        at.report_panel_status("ide", "waiting")
        self.assertIn("ide", self.overlay._panels_working)
        at.report_panel_status("ide", "error")
        self.assertNotIn("ide", self.overlay._panels_working)

    def test_question_then_answer_resumes_spinner(self):
        """Ask a question while a panel works: spinner pauses while Friday
        is busy with you, resumes automatically afterwards."""
        at.report_panel_status("ide", "working")
        self._pump()
        self.assertTrue(self.overlay._anim_timer.isActive())
        # user asks -> Loading/Responding supersedes (own timer running)
        self.overlay._enter_state(ov.STATE_LOADING)
        self.assertTrue(self.overlay._anim_timer.isActive())
        self.assertEqual(self.overlay._state, ov.STATE_LOADING)
        # answer completes -> back to idle -> still-working panel spins
        self.overlay._enter_state(ov.STATE_IDLE)
        self.assertTrue(self.overlay._anim_timer.isActive())
        at.report_panel_status("ide", "done")
        self._pump()
        self.assertFalse(self.overlay._anim_timer.isActive())

    def test_idle_repaint_runs_without_crash(self):
        # paintEvent(working+idle) executes the real code path
        at.report_panel_status("ide", "working")
        self.overlay.resize(120, 120)
        self.overlay.repaint()
        self._pump()
        self.assertFalse(self.overlay._last_was_error)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""agent_tools panel work-reports: store/take + restore listener dispatch,
all without Qt (the circle's voice delivery subscribes as one listener).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools as at


class ReportStoreTests(unittest.TestCase):
    def setUp(self):
        at._PANEL_REPORTS.clear()
        self.events = []
        self._listener = lambda key: self.events.append(key)
        at.register_panel_restore_listener(self._listener)

    def tearDown(self):
        at._PANEL_REPORTS.clear()
        if self._listener in at._PANEL_RESTORE_LISTENERS:
            at._PANEL_RESTORE_LISTENERS.remove(self._listener)

    def test_store_and_take_consumes(self):
        at.store_panel_report("ide", "Built the login form, sir.")
        self.assertTrue(at.has_panel_report("ide"))
        self.assertEqual(at.take_panel_report("ide"),
                         "Built the login form, sir.")
        self.assertFalse(at.has_panel_report("ide"))
        self.assertEqual(at.take_panel_report("ide"), "")

    def test_store_ignores_blank(self):
        at.store_panel_report("ide", "   ")
        self.assertFalse(at.has_panel_report("ide"))

    def test_reports_are_per_panel(self):
        at.store_panel_report("ide", "ide text")
        at.store_panel_report("chat", "chat text")
        self.assertEqual(at.take_panel_report("chat"), "chat text")
        self.assertEqual(at.take_panel_report("ide"), "ide text")

    def test_notify_fires_registered_listeners(self):
        at.notify_panel_restored("ide")
        at.notify_panel_restored("chat")
        self.assertEqual(self.events, ["ide", "chat"])

    def test_listener_crash_does_not_break_dispatch(self):
        bad = lambda key: (_ for _ in ()).throw(RuntimeError("x"))
        ok = []
        at.register_panel_restore_listener(bad)
        at.register_panel_restore_listener(ok.append)
        try:
            at.notify_panel_restored("ide")
        finally:
            at._PANEL_RESTORE_LISTENERS.remove(bad)
            at._PANEL_RESTORE_LISTENERS.remove(ok.append)
        self.assertEqual(ok, ["ide"])

    def test_register_dedupes_same_listener(self):
        at.register_panel_restore_listener(self._listener)
        at.notify_panel_restored("chat")
        self.assertEqual(self.events, ["chat"])  # fired once, not twice


if __name__ == "__main__":
    unittest.main(verbosity=2)

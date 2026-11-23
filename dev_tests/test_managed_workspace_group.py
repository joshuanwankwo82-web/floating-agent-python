"""Friday-managed IDE roots share one pill and restore as a workspace pair."""
import os
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import agent_tools
except Exception as exc:
    agent_tools = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


@unittest.skipIf(agent_tools is None, "agent tools unavailable: %s" % _IMPORT_ERROR)
@unittest.skipUnless(os.environ.get("DISPLAY"), "requires a Tk display")
class ManagedWorkspaceGroup(unittest.TestCase):
    def setUp(self):
        self.started = False
        agent_tools.set_circle_rect_provider(lambda: (100, 100, 64))
        result = agent_tools.agent_mode_on(tempfile.mkdtemp(prefix="workspace_pair_"))
        self.assertIn("Agent Mode is on", result)
        self.started = True

    def tearDown(self):
        if self.started:
            try:
                agent_tools.agent_mode_off()
            except Exception:
                pass
        agent_tools.set_circle_rect_provider(lambda: None)

    def _wait_for(self, predicate, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.04)
        return False

    def test_cap_group_pill_restore_and_survivor_promotion(self):
        first = agent_tools._ide_app
        self.assertTrue(agent_tools._run_on_main(first.new_workspace_window))
        self.assertTrue(self._wait_for(lambda: len(agent_tools._ide_apps) == 2))
        second = agent_tools._ide_apps[1]
        folder = tempfile.mkdtemp(prefix="K-C-Workspace-")
        agent_tools._run_on_main(lambda: second.set_root(folder))
        self.assertEqual(second.workspace_display_name(), os.path.basename(folder))
        self.assertFalse(agent_tools._run_on_main(
            agent_tools._ide_app.new_workspace_window))

        active = agent_tools._ide_app
        # Restore from the existing first-workspace pill while the second
        # window's collapse animation is still in flight. No stale callback
        # may hide it again or spawn a duplicate pill afterwards.
        agent_tools._run_on_main(active.minimize_to_pill)
        time.sleep(0.04)
        agent_tools._run_on_main(agent_tools._restore_workspace_group)
        time.sleep(0.35)
        visibility = agent_tools._run_on_main(lambda: [
            app._workspace_hidden for app in agent_tools._ide_apps])
        self.assertEqual(visibility, [False, False])
        self.assertIsNone(agent_tools._workspace_pill_owner)

        for app in list(agent_tools._ide_apps):
            agent_tools._run_on_main(app.minimize_to_pill)
        self.assertTrue(self._wait_for(lambda: agent_tools.is_panel_minimized("ide")),
                        [(app._workspace_hidden, app._pill is not None,
                          app._geom_anim_job) for app in agent_tools._ide_apps])
        owner = agent_tools._workspace_pill_owner
        labels = getattr(getattr(owner, "_pill", None), "_group_labels", [])
        self.assertEqual(len(labels), 2)
        self.assertIn(os.path.basename(folder), labels)
        pill = owner._pill
        self.assertEqual(agent_tools._run_on_main(
            lambda: pill.canvas.itemcget(pill._pill_text, "text")), "2 IDE")
        pill = owner._pill
        agent_tools._run_on_main(pill._show_workspace_peek)
        peek = agent_tools._run_on_main(lambda: pill._peek)
        self.assertIsNotNone(peek)
        self.assertEqual(agent_tools._run_on_main(
            lambda: len(pill._peek_canvas.find_withtag("workspace-status-dot"))), 2)
        self.assertLess(agent_tools._run_on_main(peek.winfo_width), 180)
        agent_tools._run_on_main(pill._on_click)
        self.assertFalse(agent_tools._run_on_main(peek.winfo_exists))

        positions = agent_tools._run_on_main(lambda: [
            (app.winfo_x(), app.winfo_width(), app._workspace_hidden)
            for app in agent_tools._ide_apps])
        self.assertEqual(len(positions), 2)
        self.assertTrue(all(not hidden for _, _, hidden in positions))
        self.assertGreater(positions[1][0], positions[0][0])

        minimizing = agent_tools._ide_apps[0]
        agent_tools._run_on_main(minimizing.minimize_to_pill)
        self.assertTrue(self._wait_for(lambda: minimizing._workspace_hidden))
        promoted_width = agent_tools._run_on_main(
            lambda: agent_tools._ide_apps[1].winfo_width())
        self.assertGreater(promoted_width, max(width for _, width, _ in positions))

        agent_tools._run_on_main(agent_tools._restore_workspace_group)
        closing = agent_tools._ide_apps[0]
        agent_tools._run_on_main(closing.close_window)
        self.assertTrue(self._wait_for(lambda: len(agent_tools._ide_apps) == 1))
        remaining_state = agent_tools._run_on_main(lambda: (
            agent_tools._ide_apps[0]._workspace_hidden,
            agent_tools._ide_apps[0]._pill,
            agent_tools._ide_apps[0].winfo_width()))
        self.assertFalse(remaining_state[0])
        self.assertIsNone(remaining_state[1])
        self.assertGreater(remaining_state[2], max(width for _, width, _ in positions))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""The quiet IDE's debug console now talks to a REAL debugger.

End-to-end against the actual machinery: a live _debug_driver.py
subprocess, a real TCP control socket, and the DebugSession facade the
IDE itself uses -- driving breakpoints, stepping, expression evaluation,
in-frame statement exec, stdout capture and termination against a real
sample script, no fakes anywhere in the debugging path itself.

Run: DISPLAY=:99 python3 dev_tests/test_ide_debug_driver.py"""

import os
import queue
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ide"))

from quiet import debug_session  # noqa: E402


SAMPLE = "\n".join([
    "import sys",
    "",
    "def add(a, b):",
    "    s = a + b",
    "    return s",
    "",
    "total = add(20, 3)",
    "print('total is', total)",
    "print('argv0 ok', sys.argv[0].endswith('.py'))",
])


def _sample_script():
    tmpdir = tempfile.mkdtemp(prefix="ide-debug-test-")
    path = os.path.join(tmpdir, "sample.py")
    with open(path, "w") as fh:
        fh.write(SAMPLE + "\n")
    return path


class DriverProtocolTests(unittest.TestCase):
    """One full live session through the real driver subprocess."""

    @classmethod
    def setUpClass(cls):
        cls.events = queue.Queue()
        cls.session = debug_session.DebugSession(
            on_event=lambda kind, payload: cls.events.put((kind, payload)))
        cls.script = _sample_script()
        cls.session.start(cls.script)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.session.stop()
        except Exception:  # noqa: BLE001
            pass

    def _next_event(self, timeout=10):
        kind, payload = self.events.get(timeout=timeout)
        return kind, payload

    def _stop(self, timeout=10):
        """Skip the chatter (vars echoes, output lines) until the
        debugger actually suspends again."""
        while True:
            kind, payload = self._next_event(timeout)
            if kind == "stopped":
                return payload

    def test_01_session_starts_and_reports_entry_stop(self):
        stop = self._stop()
        self.assertEqual(stop["reason"], "step")
        self.assertTrue(stop["file"].endswith("sample.py"))
        # The entry frame is line 1 of the module in the SAMPLE file --
        # no driver/bdb plumbing frames may leak into the shown stack.
        self.assertTrue(all(f["file"].endswith("sample.py")
                            for f in stop["stack"]))
        self.assertEqual(self.session.state, "paused")
        # Arm the real breakpoints this suite's later stages expect.
        self.session.set_breakpoint(self.script, 5, True)
        self.session.continue_()

    def test_02_breakpoint_fires_with_locals_and_trimmed_stack(self):
        stop = self._stop()
        self.assertEqual(stop["reason"], "breakpoint")
        self.assertEqual(stop["line"], 5)
        self.assertEqual(len(stop["stack"]), 2)  # <module> -> add, nothing else
        names = {v["name"] for v in stop["vars"]}
        self.assertTrue({"a", "b", "s"} <= names)
        by_name = {v["name"]: v["value"] for v in stop["vars"]}
        self.assertEqual(by_name["s"], "23")
        ev = self.session.evaluate("a + b")
        self.assertTrue(ev["ok"], ev)
        self.assertEqual(ev["value"], "23")

    def test_03_in_frame_exec_and_readback(self):
        ev = self.session.evaluate("s = 99")
        self.assertTrue(ev["ok"], ev)
        ev2 = self.session.evaluate("s")
        self.assertTrue(ev2["ok"], ev2)
        self.assertEqual(ev2["value"], "99")
        err = self.session.evaluate("a +")
        self.assertFalse(err["ok"])
        self.assertIn("error", err)

    def test_04_step_over_lands_in_caller_at_the_next_line(self):
        self.session.step_over()
        stop = self._stop()
        self.assertEqual(stop["line"], 8)
        # and the exec'd s==99 from the earlier test flows through
        self.session.continue_()

    def test_05_target_output_and_termination(self):
        texts = []
        code = None
        while True:
            kind, payload = self._next_event()
            if kind == "output":
                texts.append(payload["text"])
            elif kind == "terminated":
                code = payload["code"]
                break
            # any extra stop would need a continue; none is expected
        flat = "".join(texts)
        self.assertIn("total is 99", flat)
        self.assertIn("argv0 ok True", flat)
        self.assertEqual(code, 0)
        self.assertEqual(self.session.state, "ended")


class SessionFacadeTests(unittest.TestCase):
    """The DebugSession wrapper itself, at the seam the IDE relies on."""

    def test_breakpoint_cache_persists_across_sessions(self):
        sess = debug_session.DebugSession()
        sess.set_breakpoint("/tmp/x.py", 10, True)
        sess.set_breakpoint("/tmp/x.py", 12, True)
        sess.set_breakpoint("/tmp/x.py", 10, False)
        self.assertEqual(sess.breakpoints(), {"/tmp/x.py": [12]})
        sess.set_breakpoint("/tmp/x.py", 12, False)
        self.assertEqual(sess.breakpoints(), {})

    def test_unmap_commands_are_silent_when_idle(self):
        sess = debug_session.DebugSession()
        self.assertIsNone(sess.continue_())
        self.assertIsNone(sess.step_over())
        self.assertFalse(sess.alive)
        ev = sess.evaluate("1 + 1")
        self.assertFalse(ev["ok"])
        self.assertIn("no paused frame", ev["error"])

    def test_live_initializer_breakpoints_are_uploaded_on_start(self):
        script = _sample_script()
        events = queue.Queue()
        sess = debug_session.DebugSession(
            on_event=lambda kind, payload: events.put((kind, payload)))
        sess.set_breakpoint(script, 7, True)   # seeded BEFORE start
        sess.start(script)
        stops = 0
        try:
            while True:
                kind, payload = events.get(timeout=10)
                if kind == "stopped":
                    stops += 1
                    if stops == 1:
                        self.assertEqual(payload["line"], 1)  # entry stop
                        sess.continue_()
                    else:
                        self.assertEqual(payload["reason"], "breakpoint")
                        self.assertEqual(payload["line"], 7)  # preseeded bp
                        break
        finally:
            sess.stop()
        self.assertEqual(stops, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

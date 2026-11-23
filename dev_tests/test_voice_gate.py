"""Headless test of the circle's voice-chat gate: STT stays off by default
and only powers up on an explicit toggle. No Qt, no mic, no audio stack.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice_gate import VoiceChatGate, ENABLE_MESSAGE, DISABLE_MESSAGE


class _FakeWorker:
    def __init__(self):
        self.started = 0
        self.paused = 0
        self.resumed = 0
        self.command_capture = None

    def isRunning(self):
        return self.started > 0

    def start(self):
        self.started += 1

    def pause(self):
        self.paused += 1

    def resume(self):
        self.resumed += 1

    def set_command_capture(self, active):
        self.command_capture = active


class GateTests(unittest.TestCase):
    def _make(self, factory):
        self.said = []
        self.changes = []
        return VoiceChatGate(factory, self.said.append,
                             on_changed=self.changes.append)

    def test_default_is_off_and_silent(self):
        gate = self._make(lambda: {"ok": True, "worker": _FakeWorker()})
        self.assertFalse(gate.enabled)
        self.assertIsNone(gate.worker)
        self.assertEqual(self.said, [])

    def test_toggle_on_creates_worker_lazily_and_announces(self):
        w = _FakeWorker()
        calls = []

        def factory():
            calls.append(1)
            return {"ok": True, "worker": w}

        gate = self._make(factory)
        self.assertEqual(calls, [])              # nothing built at startup
        self.assertTrue(gate.toggle())
        self.assertEqual(calls, [1])             # built on first enable ONLY
        self.assertEqual(w.started, 1)
        self.assertEqual(w.resumed, 1)
        self.assertTrue(gate.enabled)
        self.assertIs(gate.worker, w)
        self.assertEqual(self.said, [ENABLE_MESSAGE])
        self.assertEqual(self.changes, [True])

    def test_toggle_off_pauses_and_announces(self):
        w = _FakeWorker()
        gate = self._make(lambda: {"ok": True, "worker": w})
        gate.toggle()
        self.assertFalse(gate.toggle())
        self.assertFalse(gate.enabled)
        self.assertIsNone(gate.worker)            # callers see "no worker"
        self.assertEqual(w.paused, 1)
        self.assertEqual(w.command_capture, False)
        self.assertEqual(self.said, [ENABLE_MESSAGE, DISABLE_MESSAGE])
        self.assertEqual(self.changes, [True, False])

    def test_reenable_reuses_existing_worker(self):
        w = _FakeWorker()
        gate = self._make(lambda: {"ok": True, "worker": w})
        gate.toggle(); gate.toggle(); gate.toggle()
        self.assertEqual(w.started, 1)            # not re-created
        self.assertEqual(w.resumed, 2)

    def test_factory_failure_stays_off_and_reports_honest_reason(self):
        gate = self._make(lambda: {"ok": False,
                                   "reason": "no microphone found"})
        self.assertFalse(gate.toggle())
        self.assertFalse(gate.enabled)
        self.assertIn("no microphone found", self.said[0])
        self.assertIn("couldn't turn on voice chat", self.said[0])
        self.assertEqual(self.changes, [False])

    def test_factory_exception_is_swallowed_into_reason(self):
        def boom():
            raise RuntimeError("PyAudio exploded")

        gate = self._make(boom)
        self.assertFalse(gate.toggle())
        self.assertIn("PyAudio exploded", self.said[0])

    def test_set_enabled_is_idempotent(self):
        gate = self._make(lambda: {"ok": True, "worker": _FakeWorker()})
        gate.set_enabled(True)
        gate.set_enabled(True)
        gate.set_enabled(False)
        gate.set_enabled(False)
        self.assertEqual(self.changes, [True, False])


if __name__ == "__main__":
    unittest.main(verbosity=2)

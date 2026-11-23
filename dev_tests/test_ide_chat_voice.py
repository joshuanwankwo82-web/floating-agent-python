"""One-shot IDE-chat voice input: record -> transcribe -> (text, error)
normalized contract; all with injected fakes -- no mic, no model, no network.
"""
import os
import queue
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ide"))

from quiet import chat_voice


class _FakeRec:
    """Stands in for a live _PaRecorder: instant start, wav written on stop."""
    def __init__(self, wav_path):
        self.wav_path = wav_path
        self.error = None
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        return True

    def stop(self, join_timeout=1.0):
        self.stopped = True
        with open(self.wav_path, "wb") as fh:
            fh.write(b"RIFFfake")


class RecorderFlowTests(unittest.TestCase):
    def _recorder(self, **kw):
        kw.setdefault("audio_source_factory", _FakeRec)
        kw.setdefault("transcriber", lambda p: ("hello ide world", None))
        return chat_voice.ChatVoiceRecorder(**kw)

    def test_start_sets_recording_and_pause_state(self):
        rec = self._recorder()
        ok, reason = rec.start()
        self.assertTrue(ok)
        self.assertTrue(rec.recording)
        # double-start is a no-op, not a second microphone open
        ok2, _ = rec.start()
        self.assertTrue(ok2)

    def test_stop_transcribes_and_reports_engine(self):
        rec = self._recorder()
        rec.start()
        seen = queue.Queue()
        rec.stop(lambda text, err, engine: seen.put((text, err, engine)))
        text, err, engine = seen.get(timeout=5)
        self.assertEqual((text, err), ("hello ide world", None))
        self.assertTrue(engine)

    def test_error_path_from_transcriber_reaches_callback(self):
        rec = self._recorder(
            transcriber=lambda p: (None, "online transcription failed: offline"))
        rec.start()
        seen = queue.Queue()
        rec.stop(lambda text, err, engine: seen.put((text, err)))
        text, err = seen.get(timeout=5)
        self.assertIsNone(text)
        self.assertEqual(err, "online transcription failed: offline")

    def test_empty_transcript_becomes_honest_message(self):
        rec = self._recorder(transcriber=lambda p: ("", None))
        rec.start()
        seen = queue.Queue()
        rec.stop(lambda text, err, engine: seen.put((text, err)))
        text, err = seen.get(timeout=5)
        self.assertIsNone(text)
        self.assertEqual(err, "I heard nothing I could turn into words.")

    def test_transcriber_crash_is_contained(self):
        def boom(_):
            raise OSError("model directory vanished")

        rec = self._recorder(transcriber=boom)
        rec.start()
        seen = queue.Queue()
        rec.stop(lambda text, err, engine: seen.put((text, err)))
        text, err = seen.get(timeout=5)
        self.assertIsNone(text)
        self.assertIn("vanished", err)

    def test_recorder_start_failure_reports_reason(self):
        class _BadRec(_FakeRec):
            def start(self):
                self.error = "mic is busy"
                return False

        rec = chat_voice.ChatVoiceRecorder(
            audio_source_factory=_BadRec)
        ok, reason = rec.start()
        self.assertFalse(ok)
        self.assertEqual(reason, "mic is busy")
        self.assertFalse(rec.recording)


class NormalizedTranscribeTests(unittest.TestCase):
    """The default transcribe() contract: exactly one of (text, error)."""

    def test_no_engines_reports_install_hint(self):
        orig_w, orig_s = chat_voice.whisper_available, chat_voice.sr_available
        chat_voice.whisper_available = lambda: False
        chat_voice.sr_available = lambda: False
        try:
            text, error = chat_voice.transcribe("whatever.wav")
        finally:
            chat_voice.whisper_available, chat_voice.sr_available = \
                orig_w, orig_s
        self.assertIsNone(text)
        self.assertIn("pip install", error)

    def test_whisper_result_normalized(self):
        orig = chat_voice._transcribe_whisper
        chat_voice.whisper_available = lambda: True
        chat_voice._transcribe_whisper = staticmethod(
            lambda p: "transcribed locally")
        try:
            text, error = chat_voice.transcribe("x.wav")
        finally:
            chat_voice._transcribe_whisper = orig
        self.assertEqual((text, error), ("transcribed locally", None))


if __name__ == "__main__":
    unittest.main(verbosity=2)

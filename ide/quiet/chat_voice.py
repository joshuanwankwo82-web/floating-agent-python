"""IDE-chat push-to-talk voice input (click mic -> record -> click again ->
transcribed text lands EDITABLE in the composer; user confirms with Enter).

Deliberately separate from Friday's always-listening wake-word stack
(overlay_speech.py/VoiceListenWorker): voice chat in the circle is gated
behind the long-press voice-chat toggle; this one-shot recorder is the
IDE chat box's own mic button flow. No Qt (the IDE is Tk); dependencies
are optional and probed lazily so the IDE ships without them.

Backends (honest, in this order):
  1. faster-whisper -- full local STT, no network.
  2. speech_recognition.recognize_google -- free fallback when whisper
     isn't installed (requires internet).
If neither is importable, `recorder()` returns an availability dict with
the exact pip command, and the UI reports that instead of pretending to
record.

Test seams: `audio_source_factory` (the WAV recorder) and `transcriber`
are injectable, so suites exercise the whole record->transcribe flow with
no mic and no model.
"""
import os
import tempfile
import threading
import wave

RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2          # 16-bit samples
CHUNK = 1024

INSTALL_HINT = ("pip install faster-whisper PyAudioWPatch SpeechRecognition "
                "(PyAudioWPatch is what works on newer Pythons; classic "
                "PyAudio is fine too)")


# ---------------------------------------------------------------------------
# optional dependency probing
# ---------------------------------------------------------------------------
def _pyaudio_module():
    try:
        import pyaudio  # noqa: F401
        return __import__("pyaudio")
    except ImportError:
        pass
    try:
        # Friday already documents PyAudioWPatch as the maintained fork
        # that ships wheels for newer CPython sooner; the two are
        # API-compatible for the small capture surface below.
        import pyaudiowpatch
        return pyaudiowpatch
    except ImportError:
        return None


def whisper_available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def sr_available():
    try:
        import speech_recognition as sr  # noqa: F401
        return True
    except ImportError:
        return False


def availability():
    """(ok, reason) -- reason is an exact, honest why + the install hint."""
    if _pyaudio_module() is None:
        return False, f"no PyAudio-compatible mic capture. {INSTALL_HINT}"
    if not (whisper_available() or sr_available()):
        return False, (f"no local or online speech-to-text engine. "
                       f"{INSTALL_HINT}")
    return True, ""


def engine_label():
    if whisper_available():
        return "faster-whisper (local)"
    if sr_available():
        return "Google speech (online fallback)"
    return "unavailable"


# ---------------------------------------------------------------------------
# WAV capture (pyaudio-family streams, stop-event driven)
# ---------------------------------------------------------------------------
class _PaRecorder:
    """One-shot recorder: start() opens the stream; stop() finishes and
    the wav file is at .wav_path. Runs its capture loop on a thread."""

    def __init__(self, wav_path):
        self.wav_path = wav_path
        self._pa = None
        self._stream = None
        self._stop = threading.Event()
        self._thread = None
        self.error = None

    def start(self):
        pa_mod = _pyaudio_module()
        if pa_mod is None:
            self.error = "PyAudio is not available"
            return False
        try:
            self._pa = pa_mod.PyAudio()
            self._stream = self._pa.open(
                format=self._format(pa_mod), channels=CHANNELS, rate=RATE,
                input=True, frames_per_buffer=CHUNK)
        except Exception as ex:  # noqa: BLE001 -- mic busy, denied, absent
            self.error = str(ex)
            self._cleanup()
            return False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    @staticmethod
    def _format(pa_mod):
        fmt = getattr(pa_mod, "paInt16", None)
        return fmt if fmt is not None else 8  # paInt16 constant value

    def _loop(self):
        frames = []
        try:
            while not self._stop.is_set():
                try:
                    frames.append(
                        self._stream.read(CHUNK, exception_on_overflow=False))
                except OSError:
                    continue
        finally:
            try:
                with wave.open(self.wav_path, "wb") as wh:
                    wh.setnchannels(CHANNELS)
                    wh.setsampwidth(SAMPLE_WIDTH)
                    wh.setframerate(RATE)
                    wh.writeframes(b"".join(frames))
            except Exception as ex:  # noqa: BLE001
                self.error = str(ex)
            self._cleanup()

    def _cleanup(self):
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:  # noqa: BLE001
            pass
        self._stream = None
        self._pa = None

    def stop(self, join_timeout=3.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(join_timeout)


# ---------------------------------------------------------------------------
# transcription
# ---------------------------------------------------------------------------
def _transcribe_whisper(wav_path):
    from faster_whisper import WhisperModel
    model_size = os.environ.get("WHISPER_MODEL_SIZE", "base")
    compute = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
    model = WhisperModel(model_size, device="cpu", compute_type=compute)
    segments, _info = model.transcribe(wav_path, beam_size=5)
    return " ".join(seg.text.strip() for seg in segments).strip()


def _transcribe_google(wav_path):
    import speech_recognition as sr
    recognizer = sr.Recognizer()
    with sr.AudioFile(wav_path) as source:
        audio = recognizer.record(source)
    return (recognizer.recognize_google(audio) or "").strip()


def transcribe(wav_path):
    """Normalized (text, error): exactly one of the two slots is set.
    The engine used is reported separately via engine_label()."""
    if whisper_available():
        try:
            return _transcribe_whisper(wav_path), None
        except Exception as ex:  # noqa: BLE001 -- model download etc
            # note it and try the online engine if it exists
            if not sr_available():
                return None, f"local transcription failed: {ex}"
    if sr_available():
        try:
            return _transcribe_google(wav_path), None
        except Exception as ex:  # noqa: BLE001 -- network, noise
            return None, f"online transcription failed: {ex}"
    return None, f"no speech-to-text engine available. {INSTALL_HINT}"


# ---------------------------------------------------------------------------
# the UI-facing recorder (one recording at a time per instance)
# ---------------------------------------------------------------------------
class ChatVoiceRecorder:
    """start() -> True/False(reason); stop(cb) -> cb(text, error, engine)
    on the callback thread (the IDE marshals to Tk)."""

    def __init__(self, audio_source_factory=None, transcriber=transcribe):
        self._audio_source_factory = audio_source_factory
        self._transcriber = transcriber
        self._active = None
        self._tmp_dir = tempfile.mkdtemp(prefix="ide-chat-voice-")

    @property
    def recording(self):
        return self._active is not None

    def start(self):
        if self._active is not None:
            return True, ""
        ok, reason = availability()
        if self._audio_source_factory is None and not ok:
            return False, reason
        factory = self._audio_source_factory or _PaRecorder
        rec = factory(os.path.join(self._tmp_dir, "take.wav"))
        if not rec.start():
            return False, rec.error or "microphone did not start"
        self._active = rec
        return True, ""

    def stop(self, on_done):
        assert callable(on_done)
        rec, self._active = self._active, None
        if rec is None:
            return
        rec.stop()

        def work():
            error = rec.error
            text = None
            if not error:
                text, error = self._safe_transcribe(rec.wav_path)
            on_done(text, error, engine_label())

        threading.Thread(target=work, daemon=True).start()

    def _safe_transcribe(self, wav_path):
        try:
            text, error = self._transcriber(wav_path)
        except Exception as ex:  # noqa: BLE001
            return None, str(ex)
        if text:
            return text, None
        return None, error or "I heard nothing I could turn into words."

"""Voice-chat gate: STT/mic listening is OFF by default and only powers up
on an explicit user toggle (the circle's long-press). Pure logic, no Qt.

CircleOverlay owns a VoiceListenWorker *driver* exposing the small surface
this class needs; the gate itself is testable headless -- no QApplication,
no mic, no audio stack required.

State machine is intentionally tiny and honest:

    off --toggle--> on        (driver ensured + resumed; announces ON)
    on  --toggle--> off       (driver paused;            announces OFF)

If the local STT stack cannot actually run (no faster-whisper / speech
recognition / PyAudio), turning on reports that fact instead of pretending
to listen -- same honest-control rule the rest of Friday follows.
"""

ENABLE_MESSAGE = "Voice chat turned on, sir."
DISABLE_MESSAGE = "Voice chat turned off, sir."


class VoiceChatGate:
    def __init__(self, driver_factory, announce, on_changed=None):
        """driver_factory() -> dict

             {"ok": True,  "worker": worker}          or
             {"ok": False, "reason": human-readable why}

           Called lazily the first time the user enables voice chat, and
           only then -- the mic stay completely untouched until the user
           asks for it.

        announce(text) is called with the spoken user-facing status line.
        on_changed(enabled: bool) is an optional hook (UI indication)."""
        self._driver_factory = driver_factory
        self._announce = announce
        self._on_changed = on_changed or (lambda enabled: None)
        self._enabled = False
        self._worker = None          # built on first successful enable
        self._last_error = None      # most recent honest failure reason

    # ---------------- state queries ----------------
    @property
    def enabled(self):
        return self._enabled

    @property
    def worker(self):
        """The live VoiceListenWorker while voice chat is on, else None --
        every voice-path in CircleOverlay already tolerates a missing
        worker; this property keeps that pattern honest (a paused-but-
        created worker that is disabled reports as None to callers)."""
        return self._worker if self._enabled else None

    @property
    def last_error(self):
        return self._last_error

    # ---------------- transitions ----------------
    def toggle(self):
        return self.set_enabled(not self._enabled)

    def set_enabled(self, enabled):
        if bool(enabled) == self._enabled:
            return self._enabled
        if not enabled:
            self._pause_worker()
            self._enabled = False
            self._announce(DISABLE_MESSAGE)
            self._on_changed(False)
            return self._enabled
        # enabling: ensure the worker exists (lazy) and is running
        worker = self._ensure_worker()
        if worker is None:
            # stay off; report the honest reason instead
            self._enabled = False
            reason = self._last_error or "the microphone stack did "
            "not start"
            self._announce(
                f"Sir, I couldn't turn on voice chat — {reason}.")
            self._on_changed(False)
            return self._enabled
        self._enabled = True
        self._announce(ENABLE_MESSAGE)
        self._on_changed(True)
        return self._enabled

    # ---------------- internals ----------------
    def _ensure_worker(self):
        if self._worker is not None:
            self._resume_worker(self._worker)
            return self._worker
        try:
            result = self._driver_factory()
        except Exception as ex:  # noqa: BLE001 -- never crash the toggle
            self._last_error = str(ex)
            return None
        if not result or not result.get("ok"):
            self._last_error = (result or {}).get(
                "reason", "voice input is unavailable")
            return None
        self._worker = result["worker"]
        self._resume_worker(self._worker)
        return self._worker

    @staticmethod
    def _resume_worker(worker):
        try:
            if not worker.isRunning():
                worker.start()
            worker.resume()
        except RuntimeError:
            # Qt thread object died between checks; caller treats as error.
            raise

    def _pause_worker(self):
        worker = self._worker
        if worker is None:
            return
        try:
            worker.set_command_capture(False)
            worker.pause()
        except Exception:  # noqa: BLE001
            pass

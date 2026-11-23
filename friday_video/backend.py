"""Backend seam for Quiet Video.

The default backend is a real local motion renderer. A future AI provider can
implement the same small bracket (`name` + `render`) without the interface
having to pretend that provider exists already.
"""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Callable, Protocol

from .model import VideoRequest
from .renderer import RenderedOutput, render_video


ProgressCallback = Callable[[int, int], None]


class VideoBackend(Protocol):
    """Provider/backend contract used by the panel's worker thread."""

    name: str
    description: str
    badge: str

    def render(self, request: VideoRequest, output_dir: Path,
               progress_callback: ProgressCallback | None = None,
               cancel_event: threading.Event | None = None) -> RenderedOutput:
        """Render and return an output; raise a clear exception on failure."""


class LocalMotionBackend:
    """Delivered renderer: deterministic local MP4/GIF motion studies."""

    name = "Local motion renderer"
    description = ("Creates MP4/GIF videos locally from images, style controls, "
                   "motion, and a deterministic seed. The prompt is title text, "
                   "not an AI scene description.")
    badge = "local renderer · no AI provider connected"

    def render(self, request: VideoRequest, output_dir: Path,
               progress_callback: ProgressCallback | None = None,
               cancel_event: threading.Event | None = None) -> RenderedOutput:
        return render_video(request, output_dir, progress_callback=progress_callback,
                            cancel_event=cancel_event)


class ProviderPlaceholderMessage(RuntimeError):
    """A deliberate answer for accidentally selected unavailable backends."""

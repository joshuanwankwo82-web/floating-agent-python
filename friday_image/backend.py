"""Backend seam for Quiet Image.

The default backend is a real local art renderer. Real AI providers can
implement the same small bracket (`name` + `render`); the delivered
Cloudflare Workers AI adapter lives in friday_image/providers.py and is
wired to the user's own Settings key by image_backend_resolver.py.
"""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Callable, Protocol

from .model import ImageRequest
from .renderer import RenderedImage, render_image


ProgressCallback = Callable[[int, int], None]


class ImageBackend(Protocol):
    """Provider/backend contract used by the panel's worker thread."""

    name: str
    description: str
    badge: str

    def render(self, request: ImageRequest, output_dir: Path,
               progress_callback: ProgressCallback | None = None,
               cancel_event: threading.Event | None = None) -> RenderedImage:
        """Render and return an output; raise a clear exception on failure."""


class LocalArtBackend:
    """Delivered renderer: deterministic local PNG/JPEG/WebP art studies."""

    name = "Local art renderer"
    provider_kind = "local"
    description = (
        "Creates images locally from style, composition, detail, a reference "
        "image, and a deterministic seed. The prompt is title text, not an AI "
        "scene description."
    )
    badge = "local renderer · no AI provider connected"

    def render(self, request: ImageRequest, output_dir: Path,
               progress_callback: ProgressCallback | None = None,
               cancel_event: threading.Event | None = None) -> RenderedImage:
        return render_image(request, output_dir, progress_callback=progress_callback,
                            cancel_event=cancel_event)


class ProviderPlaceholderMessage(RuntimeError):
    """A deliberate answer for accidentally selected unavailable backends."""

"""Optional cloud image providers for Friday's Image Generation panel.

This module contains the real, explicit provider adapters the panel's
``ImageBackend`` seam was designed for. The first delivered provider is
Cloudflare Workers AI (its free daily tier makes it the sensible default
first integration): a user adds their own account base URL + API token +
model under Settings -> Image Generation, and the native Image panel then
generates genuinely AI-produced images over HTTPS. Without a configured
entry the panel keeps using the honest local art renderer
(renderer.py); nothing here fakes a provider response, and the local
renderer is never presented as AI generation.

No settings/memory imports happen in this module: adapters are built
from plain credential values (or a settings entry dict handed in by the
caller), so friday_image stays independently importable and testable.
"""
from __future__ import annotations

import base64
import io
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from PIL import Image

from .model import ImageRequest
from .renderer import (RenderCancelled, RenderError, RenderedImage,
                       _output_paths, _save_output, target_size,
                       thumbnail_final_path)


DEFAULT_CF_MODEL = "@cf/black-forest-labs/flux-1-schnell"
DEFAULT_TIMEOUT = 180

SETUP_HINT = (
    "In Friday's Settings -> Image Generation, set the API base URL to "
    "https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID> , the model "
    f"to {DEFAULT_CF_MODEL} (or another Workers AI image model), and paste "
    "your own Cloudflare API token as the API key. See "
    "docs/CLOUD_IMAGE_PROVIDER.md."
)


def cloud_size(aspect: str, quality: str) -> tuple[int, int]:
    """Provider-safe output dimensions.

    Cloudflare's image models require multiples of 8 within [256, 2048];
    the local renderer's per-aspect targets are quantized to satisfy that
    while keeping the chosen aspect composition recognisable.
    """
    width, height = target_size(aspect, quality)

    def quantize(value: int) -> int:
        return max(256, min(2048, int(round(value / 8)) * 8))

    return quantize(width), quantize(height)


def cloud_num_steps(quality: str, model: str) -> int:
    """Quality knob mapped onto each model family's real step parameter."""
    if "flux" in (model or "").lower():
        # FLUX.1 [schnell] is capped at 8 steps on Workers AI.
        return 8 if quality == "Studio" else 4
    return 24 if quality == "Studio" else 12


def normalize_cloudflare_base(base_url: str) -> str:
    """Accept the Cloudflare *dashboard* URL users naturally copy
    (https://dash.cloudflare.com/<ACCOUNT_ID>/...) and hand back the real
    API account root. Everything else passes through unchanged."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return base
    parsed = urllib.parse.urlparse(base if "://" in base else f"https://{base}")
    if parsed.netloc.lower() == "dash.cloudflare.com":
        first = parsed.path.strip("/").split("/")[0]
        if not first:
            raise ValueError(
                "That Cloudflare dashboard URL does not include the account "
                "ID segment. " + SETUP_HINT
            )
        return f"https://api.cloudflare.com/client/v4/accounts/{first}"
    return base


def build_endpoint(base_url: str, model: str) -> str:
    """Normalise the Settings entry's base URL into a model run URL.

    Accepted forms (the latter three are conveniences for advanced users):
      * https://dash.cloudflare.com/<ACCOUNT_ID>/...   (auto-normalized)
      * https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>
      * .../accounts/<ACCOUNT_ID>/ai/run            (model appended)
      * .../accounts/<ACCOUNT_ID>/ai/run/<model>    (used verbatim)
    """
    base = normalize_cloudflare_base(base_url)
    model = (model or "").strip() or DEFAULT_CF_MODEL
    if not base:
        raise ValueError("The API base URL field is empty. " + SETUP_HINT)
    if "/ai/run/" in base:
        return base
    if base.endswith("/ai/run"):
        return f"{base}/{model}"
    if re.search(r"/accounts/[^/]+$", base):
        return f"{base}/ai/run/{model}"
    raise ValueError(
        "That API base URL does not look like a Cloudflare Workers AI "
        f"account URL ({base!r}). " + SETUP_HINT
    )


def cloud_payload(request: ImageRequest, model: str) -> dict:
    """The real request body for the configured Workers AI image model."""
    model_name = (model or "").lower()
    if "flux" in model_name:
        return {"prompt": request.prompt,
                "steps": cloud_num_steps(request.quality, model)}
    width, height = cloud_size(request.aspect, request.quality)
    payload = {
        "prompt": request.prompt,
        "width": width,
        "height": height,
        "num_steps": cloud_num_steps(request.quality, model),
        "guidance": 7.5,
    }
    if request.seed is not None:
        payload["seed"] = int(request.seed)
    return payload


class CloudflareImageBackend:
    """Real AI text-to-image generation through the user's own Cloudflare
    Workers AI account (free daily tier), satisfying the ImageBackend
    bracket. The prompt is sent to Cloudflare over HTTPS; everything else
    stays local. Image-to-image/reference and style presets are local-
    renderer concepts and are honestly not sent to this provider.
    """

    name = "Cloudflare Workers AI"
    provider_kind = "cloud"
    accent_badge = True
    description = (
        "Generates real AI images over HTTPS with the Cloudflare Workers AI "
        "token saved in Friday's encrypted Settings. The written brief and "
        "chosen size/quality are sent to Cloudflare; no other panel data "
        "leaves this machine."
    )

    def __init__(self, api_key: str, base_url: str,
                 model: str = DEFAULT_CF_MODEL, entry_name: str = "",
                 timeout: int = DEFAULT_TIMEOUT,
                 urlopen: Callable | None = None):
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValueError(
                "The API key field is empty -- paste your own Cloudflare "
                "API token. " + SETUP_HINT
            )
        self.api_key = api_key
        self.model = (model or "").strip() or DEFAULT_CF_MODEL
        self.entry_name = entry_name or self.model
        self.endpoint = build_endpoint(base_url, self.model)
        self.timeout = int(timeout)
        self._urlopen = urlopen or urllib.request.urlopen
        short_model = self.model.rsplit("/", 1)[-1]
        self.badge = f"cloud · {short_model}"

    @classmethod
    def from_entry(cls, entry: dict, urlopen: Callable | None = None) -> "CloudflareImageBackend":
        """Build a backend directly from a Settings Image Generation entry."""
        return cls(api_key=entry.get("api_key", ""),
                   base_url=entry.get("base_url", ""),
                   model=entry.get("model", "") or DEFAULT_CF_MODEL,
                   entry_name=entry.get("name", "") or entry.get("model", ""),
                   urlopen=urlopen)

    def render(self, request: ImageRequest, output_dir: Path,
               progress_callback=None,
               cancel_event: threading.Event | None = None) -> RenderedImage:
        """Generate and persist one AI image; raise RenderError on failure."""
        request = request.validated()
        if not request.prompt:
            raise RenderError(
                "Cloud image generation needs a written brief -- describe "
                "the image you want in the prompt field first."
            )
        total = 6
        cancel_event = cancel_event or threading.Event()
        temp_path, final_path, thumb_temp = _output_paths(request, output_dir)
        thumb_final = thumbnail_final_path(final_path)
        started = time.monotonic()
        done = 0

        def check_cancel() -> None:
            if cancel_event.is_set():
                raise RenderCancelled("Render cancelled.")

        def stage() -> None:
            nonlocal done
            check_cancel()
            done = min(total, done + 1)
            if progress_callback:
                progress_callback(done, total)

        stage()  # validate request
        payload = cloud_payload(request, self.model)
        body = json.dumps(payload).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json, image/*",
            },
        )
        stage()  # prepare request
        # Note: like every blocking HTTP call, cancel mid-flight applies at
        # the next stage boundary; there is no pretending otherwise.
        try:
            with self._urlopen(http_request, timeout=self.timeout) as response:
                content_type = ""
                headers = getattr(response, "headers", None)
                if headers is not None and hasattr(headers, "get_content_type"):
                    content_type = headers.get_content_type() or ""
                data = response.read()
        except urllib.error.HTTPError as error:
            raise RenderError(self._http_error_message(error)) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise RenderError(
                f"Couldn't reach {self.name} ({reason}). Check the account "
                "base URL and your network connection."
            ) from error
        stage()  # generate (server round-trip)
        image_bytes = self._extract_image_bytes(data, content_type)
        stage()  # receive/decode payload
        try:
            image = Image.open(io.BytesIO(image_bytes))
            image.load()
            image = image.convert("RGB")
        except Exception as error:
            raise RenderError(
                f"{self.name} returned data that is not a readable image."
            ) from error
        try:
            _save_output(image, temp_path, request)
            stage()  # encode output
            thumbnail = image.copy()
            thumbnail.thumbnail((520, 520), Image.Resampling.LANCZOS)
            thumbnail.save(thumb_temp, "PNG", optimize=True)
            check_cancel()
            temp_path.replace(final_path)
            thumb_temp.replace(thumb_final)
            size_bytes = final_path.stat().st_size
            if progress_callback:
                progress_callback(total, total)
            return RenderedImage(
                output_path=str(final_path), thumbnail_path=str(thumb_final),
                detail_steps=total, elapsed_seconds=time.monotonic() - started,
                size_bytes=size_bytes, format=request.format,
            )
        finally:
            if cancel_event.is_set():
                temp_path.unlink(missing_ok=True)
                thumb_temp.unlink(missing_ok=True)

    def _extract_image_bytes(self, data: bytes, content_type: str) -> bytes:
        """Workers AI answers either raw image bytes (SDXL/DreamShaper) or a
        JSON envelope with base64 image data (FLUX); both are handled."""
        if content_type.startswith("image/"):
            return data
        try:
            payload = json.loads(data.decode("utf-8", "replace"))
        except ValueError as error:
            raise RenderError(
                f"{self.name} returned an unexpected response (neither an "
                "image nor JSON)."
            ) from error
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            message = errors[0].get("message", "unknown provider error") \
                if isinstance(errors[0], dict) else str(errors[0])
            raise RenderError(f"{self.name} reported an error: {message}")
        encoded = None
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            encoded = result.get("image")
        if not encoded and isinstance(payload, dict):
            encoded = payload.get("image")
        if not isinstance(encoded, str) or not encoded:
            raise RenderError(
                f"{self.name} answered without an image payload."
            )
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise RenderError(
                f"{self.name} returned malformed image data."
            ) from error

    def _http_error_message(self, error: urllib.error.HTTPError) -> str:
        detail = ""
        try:
            payload = json.loads(error.read().decode("utf-8", "replace"))
            errors = payload.get("errors") or []
            if errors and isinstance(errors[0], dict):
                detail = errors[0].get("message", "")
        except Exception:  # noqa: BLE001 -- best-effort detail extraction
            pass
        if error.code in (401, 403):
            detail = detail or (
                "the API token was rejected -- create a Cloudflare token "
                "with Workers AI permissions and update it in Settings."
            )
        return (f"{self.name} refused the request (HTTP {error.code})"
                f"{': ' + detail if detail else '.'}")


STABILITY_BASE_URL = "https://api.stability.ai"
DEFAULT_STABILITY_MODEL = "sd3.5-flash"

STABILITY_SETUP_HINT = (
    "In Friday's Settings -> Image Generation, set the API base URL to "
    "https://api.stability.ai (that's the API host -- the "
    "platform.stability.ai page is only where you CREATE the key), the "
    "model to sd3.5-flash (cheapest option), and paste your own Stability "
    "API key. See docs/CLOUD_IMAGE_PROVIDER.md."
)

# The panel's aspect choices mapped onto Stability's accepted set.
_STABILITY_ASPECTS = {
    "16:9": "16:9", "4:3": "3:2", "1:1": "1:1", "3:4": "2:3", "9:16": "9:16",
}


def detect_image_provider(entry: dict) -> str | None:
    """Which delivered provider a Settings entry describes, judged from its
    base URL host. Returns 'cloudflare', 'stability', 'openai', 'ideogram',
    or None -- the resolver then refuses honestly instead of guessing."""
    base = (entry.get("base_url") or "").strip()
    if not base:
        return None
    host = urllib.parse.urlparse(base if "://" in base else f"https://{base}")\
        .netloc.lower()
    if "cloudflare.com" in host:
        return "cloudflare"
    if "stability.ai" in host:
        return "stability"
    if "ideogram.ai" in host:
        return "ideogram"
    if "openai.com" in host or "openrouter." in host or "/v1" in base:
        # OpenAI itself and any OpenAI-compatible images API (OpenRouter,
        # proxies, self-hosted gateways) share one request shape.
        return "openai"
    return None


def _multipart_form_body(fields: dict, boundary: str) -> bytes:
    """Encode simple string fields as multipart/form-data (what Stability's
    image endpoints require) with zero external dependencies."""
    lines: list[str] = []
    for name, value in fields.items():
        if value is None:
            continue
        lines.extend((
            f"--{boundary}",
            f'Content-Disposition: form-data; name="{name}"',
            "",
            str(value),
        ))
    lines.append(f"--{boundary}--")
    return "\r\n".join(lines).encode("utf-8")


class StabilityImageBackend:
    """Real AI text-to-image generation through the user's own Stability AI
    account (25 free starter credits), satisfying the ImageBackend bracket.
    The prompt, aspect ratio, seed and output format are sent over HTTPS as
    multipart form data; style/composition/detail/reference remain honest
    local-renderer concepts that are not sent.
    """

    name = "Stability AI"
    provider_kind = "cloud"
    accent_badge = True
    description = (
        "Generates real AI images over HTTPS with the Stability AI key "
        "saved in Friday's encrypted Settings. Each render spends that "
        "account's own credits (a new account starts with 25 free). No "
        "other panel data leaves this machine."
    )

    def __init__(self, api_key: str, base_url: str,
                 model: str = DEFAULT_STABILITY_MODEL, entry_name: str = "",
                 timeout: int = DEFAULT_TIMEOUT,
                 urlopen: Callable | None = None):
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValueError(
                "The API key field is empty -- paste your own Stability API "
                "key. " + STABILITY_SETUP_HINT
            )
        base = (base_url or "").strip().rstrip("/")
        host = urllib.parse.urlparse(base if "://" in base else f"https://{base}")\
            .netloc.lower()
        if host != "api.stability.ai":
            raise ValueError(
                "That API base URL is not Stability's API host. " +
                STABILITY_SETUP_HINT
            )
        self.api_key = api_key
        self.base_url = STABILITY_BASE_URL
        self.model = (model or "").strip().lower() or DEFAULT_STABILITY_MODEL
        self.entry_name = entry_name or self.model
        self.timeout = int(timeout)
        self._urlopen = urlopen or urllib.request.urlopen
        self._model_field: str | None = None
        if self.model == "ultra":
            self.endpoint = f"{self.base_url}/v2beta/stable-image/generate/ultra"
        elif self.model.startswith("sd3"):
            self.endpoint = f"{self.base_url}/v2beta/stable-image/generate/sd3"
            self._model_field = self.model
        else:
            # "core" (SDXL-based) and anything unrecognized land on Core.
            if self.model not in ("core", ""):
                self.model = "core"
            self.endpoint = f"{self.base_url}/v2beta/stable-image/generate/core"
        self.badge = f"cloud · {self.model}"

    @classmethod
    def from_entry(cls, entry: dict, urlopen: Callable | None = None) -> "StabilityImageBackend":
        """Build a backend directly from a Settings Image Generation entry."""
        return cls(api_key=entry.get("api_key", ""),
                   base_url=entry.get("base_url", ""),
                   model=entry.get("model", "") or DEFAULT_STABILITY_MODEL,
                   entry_name=entry.get("name", "") or entry.get("model", ""),
                   urlopen=urlopen)

    def render(self, request: ImageRequest, output_dir: Path,
               progress_callback=None,
               cancel_event: threading.Event | None = None) -> RenderedImage:
        """Generate and persist one AI image; raise RenderError on failure."""
        request = request.validated()
        if not request.prompt:
            raise RenderError(
                "Cloud image generation needs a written brief -- describe "
                "the image you want in the prompt field first."
            )
        total = 6
        cancel_event = cancel_event or threading.Event()
        temp_path, final_path, thumb_temp = _output_paths(request, output_dir)
        thumb_final = thumbnail_final_path(final_path)
        started = time.monotonic()
        done = 0

        def check_cancel() -> None:
            if cancel_event.is_set():
                raise RenderCancelled("Render cancelled.")

        def stage() -> None:
            nonlocal done
            check_cancel()
            done = min(total, done + 1)
            if progress_callback:
                progress_callback(done, total)

        stage()  # validate request
        output_format = {"jpg": "jpeg", "png": "png", "webp": "webp"}[request.format]
        fields = {
            "prompt": request.prompt,
            "output_format": output_format,
            "aspect_ratio": _STABILITY_ASPECTS.get(request.aspect, "1:1"),
            "seed": str(int(request.seed)) if request.seed is not None else None,
        }
        if self._model_field:
            fields["model"] = self._model_field
            fields["mode"] = "text-to-image"
        boundary = f"friday-{secrets.token_hex(8)}"
        body = _multipart_form_body(fields, boundary)
        http_request = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "image/*",
            },
        )
        stage()  # prepare request
        try:
            with self._urlopen(http_request, timeout=self.timeout) as response:
                content_type = ""
                headers = getattr(response, "headers", None)
                if headers is not None and hasattr(headers, "get_content_type"):
                    content_type = headers.get_content_type() or ""
                data = response.read()
        except urllib.error.HTTPError as error:
            raise RenderError(self._http_error_message(error)) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise RenderError(
                f"Couldn't reach {self.name} ({reason}). Check your network "
                "connection."
            ) from error
        stage()  # generate (server round-trip)
        image_bytes = self._extract_image_bytes(data, content_type)
        stage()  # receive/decode payload
        try:
            image = Image.open(io.BytesIO(image_bytes))
            image.load()
            image = image.convert("RGB")
        except Exception as error:
            raise RenderError(
                f"{self.name} returned data that is not a readable image."
            ) from error
        try:
            _save_output(image, temp_path, request)
            stage()  # encode output
            thumbnail = image.copy()
            thumbnail.thumbnail((520, 520), Image.Resampling.LANCZOS)
            thumbnail.save(thumb_temp, "PNG", optimize=True)
            check_cancel()
            temp_path.replace(final_path)
            thumb_temp.replace(thumb_final)
            size_bytes = final_path.stat().st_size
            if progress_callback:
                progress_callback(total, total)
            return RenderedImage(
                output_path=str(final_path), thumbnail_path=str(thumb_final),
                detail_steps=total, elapsed_seconds=time.monotonic() - started,
                size_bytes=size_bytes, format=request.format,
            )
        finally:
            if cancel_event.is_set():
                temp_path.unlink(missing_ok=True)
                thumb_temp.unlink(missing_ok=True)

    def _extract_image_bytes(self, data: bytes, content_type: str) -> bytes:
        """With Accept: image/*, success is raw image bytes; a JSON body
        here means an error envelope (or, defensively, base64 output)."""
        if content_type.startswith("image/"):
            return data
        try:
            payload = json.loads(data.decode("utf-8", "replace"))
        except ValueError as error:
            raise RenderError(
                f"{self.name} returned an unexpected response (neither an "
                "image nor JSON)."
            ) from error
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            raise RenderError(
                f"{self.name} reported an error: "
                f"{'; '.join(str(item) for item in errors)}"
            )
        encoded = payload.get("image") if isinstance(payload, dict) else None
        if isinstance(encoded, str) and encoded:
            try:
                return base64.b64decode(encoded, validate=True)
            except ValueError as error:
                raise RenderError(
                    f"{self.name} returned malformed image data."
                ) from error
        raise RenderError(f"{self.name} answered without an image payload.")

    def _http_error_message(self, error: urllib.error.HTTPError) -> str:
        detail = ""
        try:
            payload = json.loads(error.read().decode("utf-8", "replace"))
            errors = payload.get("errors") or []
            if errors:
                detail = "; ".join(str(item) for item in errors)
        except Exception:  # noqa: BLE001 -- best-effort detail extraction
            pass
        if error.code in (401, 403):
            detail = detail or (
                "the API key was rejected -- create a fresh key at "
                "platform.stability.ai -> Account -> API Keys and update it "
                "in Settings."
            )
        elif error.code == 402:
            detail = detail or (
                "out of credits -- the free starter balance is spent; see "
                "platform.stability.ai for top-up options."
            )
        return (f"{self.name} refused the request (HTTP {error.code})"
                f"{': ' + detail if detail else '.'}")


OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-image-1"

OPENAI_SETUP_HINT = (
    "In Friday's Settings -> Image Generation, set the API base URL to an "
    "OpenAI-compatible API root such as https://api.openai.com/v1 (or your "
    "OpenRouter/proxy /v1 endpoint), the model to gpt-image-1 / dall-e-3 "
    "(or the equivalent for that gateway), and paste your API key."
)

IDEOGRAM_BASE_URL = "https://api.ideogram.ai"
DEFAULT_IDEOGRAM_MODEL = "V_3"


class _JsonCloudBackend:
    """Shared mechanics for the JSON-in/JSON-out OpenAI-style providers:
    POST, parse an image reference (base64 or URL), persist honestly."""

    name = "Cloud image provider"
    provider_kind = "cloud"
    accent_badge = True

    # --- hooks subclasses override ------------------------------------
    def payload(self, request: ImageRequest) -> dict:  # pragma: no cover
        raise NotImplementedError

    def headers(self) -> dict:  # pragma: no cover
        raise NotImplementedError

    def extract_image_ref(self, payload: dict):  # pragma: no cover
        return None

    # ------------------------------------------------------------------
    def _get(self, url: str) -> bytes:
        try:
            with self._urlopen(url, timeout=self.timeout) as response:
                return response.read()
        except Exception as error:  # noqa: BLE001
            raise RenderError(f"Couldn't download the generated image "
                              f"from {self.name}: {error}") from error

    def _persist(self, image_bytes: bytes, request: ImageRequest,
                 temp_path: Path, final_path: Path, thumb_temp: Path,
                 thumb_final: Path, started: float, total: int,
                 progress_callback, cancel_event: threading.Event) -> RenderedImage:
        try:
            image = Image.open(io.BytesIO(image_bytes))
            image.load()
            image = image.convert("RGB")
        except Exception as error:
            raise RenderError(
                f"{self.name} returned data that is not a readable image."
            ) from error
        try:
            _save_output(image, temp_path, request)
            if progress_callback:
                progress_callback(total - 1, total)
            thumbnail = image.copy()
            thumbnail.thumbnail((520, 520), Image.Resampling.LANCZOS)
            thumbnail.save(thumb_temp, "PNG", optimize=True)
            if cancel_event.is_set():
                raise RenderCancelled("Render cancelled.")
            temp_path.replace(final_path)
            thumb_temp.replace(thumb_final)
            if progress_callback:
                progress_callback(total, total)
            return RenderedImage(
                output_path=str(final_path), thumbnail_path=str(thumb_final),
                detail_steps=total, elapsed_seconds=time.monotonic() - started,
                size_bytes=final_path.stat().st_size, format=request.format,
            )
        finally:
            if cancel_event.is_set():
                temp_path.unlink(missing_ok=True)
                thumb_temp.unlink(missing_ok=True)

    def render(self, request: ImageRequest, output_dir: Path,
               progress_callback=None,
               cancel_event: threading.Event | None = None) -> RenderedImage:
        request = request.validated()
        if not request.prompt:
            raise RenderError(
                "Cloud image generation needs a written brief -- describe "
                "the image you want in the prompt field first."
            )
        total = 6
        cancel_event = cancel_event or threading.Event()
        temp_path, final_path, thumb_temp = _output_paths(request, output_dir)
        thumb_final = thumbnail_final_path(final_path)
        started = time.monotonic()
        done = 0

        def stage() -> None:
            if cancel_event.is_set():
                raise RenderCancelled("Render cancelled.")
            nonlocal done
            done = min(total, done + 1)
            if progress_callback:
                progress_callback(done, total)

        stage()  # validate
        body = json.dumps(self.payload(request)).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={**self.headers(), "Content-Type": "application/json",
                     "Accept": "application/json"})
        stage()  # prepare
        try:
            with self._urlopen(http_request, timeout=self.timeout) as response:
                data = response.read()
        except urllib.error.HTTPError as error:
            raise RenderError(self._http_error_message(error)) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise RenderError(
                f"Couldn't reach {self.name} ({reason}). Check the API base "
                "URL and your network connection."
            ) from error
        stage()  # server round-trip
        image_bytes = self._extract_bytes(data)
        stage()  # decode / download
        return self._persist(image_bytes, request, temp_path, final_path,
                             thumb_temp, thumb_final, started, total,
                             progress_callback, cancel_event)

    def _extract_bytes(self, data: bytes) -> bytes:
        try:
            payload = json.loads(data.decode("utf-8", "replace"))
        except ValueError as error:
            raise RenderError(
                f"{self.name} returned an unexpected non-JSON response."
            ) from error
        encoded = self.extract_image_ref(payload)
        if encoded is None:
            raise RenderError(f"{self.name} answered without an image payload.")
        kind, value = encoded
        if kind == "url":
            return self._get(value)
        try:
            return base64.b64decode(value, validate=True)
        except ValueError as error:
            raise RenderError(
                f"{self.name} returned malformed image data."
            ) from error

    def _http_error_message(self, error: urllib.error.HTTPError) -> str:
        detail = ""
        try:
            payload = json.loads(error.read().decode("utf-8", "replace"))
            detail = (((payload.get("error") or {}).get("message"))
                      or "; ".join(str(e) for e in (payload.get("errors") or []))
                      or payload.get("message") or "")
        except Exception:  # noqa: BLE001 -- best-effort detail extraction
            pass
        if error.code in (401, 403):
            detail = detail or (
                "the API key was rejected -- check it in Friday's Settings."
            )
        elif error.code == 402:
            detail = detail or "out of credits/quota with this provider."
        return (f"{self.name} refused the request (HTTP {error.code})"
                f"{': ' + str(detail).strip() if detail else '.'}")


class OpenAIImageBackend(_JsonCloudBackend):
    """OpenAI Images API (gpt-image-1 / dall-e-3), and by construction any
    OpenAI-compatible gateway (OpenRouter, proxies) whose base URL is an
    OpenAI-style /v1 root. Bearer auth, JSON in/out."""

    name = "OpenAI Images"
    description = (
        "Generates AI images over HTTPS through the OpenAI-compatible images "
        "endpoint saved in Friday's encrypted Settings (OpenAI, OpenRouter, "
        "or a compatible gateway), using that account's own quota/billing."
    )

    def __init__(self, api_key: str, base_url: str,
                 model: str = DEFAULT_OPENAI_MODEL, entry_name: str = "",
                 timeout: int = DEFAULT_TIMEOUT, urlopen: Callable | None = None):
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValueError("The API key field is empty. " + OPENAI_SETUP_HINT)
        base = (base_url or "").strip().rstrip("/")
        if "/v1" not in base:
            raise ValueError(
                "That base URL is not an OpenAI-compatible /v1 API root. "
                + OPENAI_SETUP_HINT
            )
        self.api_key = api_key
        self.model = (model or "").strip() or DEFAULT_OPENAI_MODEL
        self.entry_name = entry_name or self.model
        self.endpoint = f"{base}/images/generations"
        self.timeout = int(timeout)
        self._urlopen = urlopen or urllib.request.urlopen
        self.badge = f"cloud · {self.model}"

    @classmethod
    def from_entry(cls, entry: dict, urlopen: Callable | None = None) -> "OpenAIImageBackend":
        return cls(api_key=entry.get("api_key", ""),
                   base_url=entry.get("base_url", ""),
                   model=entry.get("model", "") or DEFAULT_OPENAI_MODEL,
                   entry_name=entry.get("name", "") or entry.get("model", ""),
                   urlopen=urlopen)

    def _size(self, request: ImageRequest) -> str:
        wide = request.aspect == "16:9"
        tall = request.aspect == "9:16"
        if "dall" in self.model.lower():
            return "1792x1024" if wide else "1024x1792" if tall else "1024x1024"
        return "1536x1024" if wide else "1024x1536" if tall else "1024x1024"

    def payload(self, request: ImageRequest) -> dict:
        body = {"model": self.model, "prompt": request.prompt, "n": 1,
                "size": self._size(request)}
        if "dall" in self.model.lower():
            body["response_format"] = "b64_json"
        return body

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def extract_image_ref(self, payload: dict):
        items = payload.get("data") if isinstance(payload, dict) else None
        if items and isinstance(items[0], dict):
            if isinstance(items[0].get("b64_json"), str) and items[0]["b64_json"]:
                return ("b64", items[0]["b64_json"])
            if isinstance(items[0].get("url"), str) and items[0]["url"]:
                return ("url", items[0]["url"])
        return None


class IdeogramImageBackend(_JsonCloudBackend):
    """Ideogram's generate API: Api-Key header auth, prompt + aspect ratio
    in, image URL out (downloaded immediately)."""

    name = "Ideogram"
    description = (
        "Generates AI images over HTTPS with the Ideogram key saved in "
        "Friday's encrypted Settings, spending that account's own credits."
    )

    def __init__(self, api_key: str, base_url: str,
                 model: str = DEFAULT_IDEOGRAM_MODEL, entry_name: str = "",
                 timeout: int = DEFAULT_TIMEOUT, urlopen: Callable | None = None):
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValueError(
                "The API key field is empty -- paste your Ideogram API key "
                "and use https://api.ideogram.ai as the base URL."
            )
        base = (base_url or "").strip().rstrip("/") or IDEOGRAM_BASE_URL
        self.api_key = api_key
        self.model = (model or "").strip() or DEFAULT_IDEOGRAM_MODEL
        self.entry_name = entry_name or self.model
        self.endpoint = base if "/generate" in base else \
            f"{IDEOGRAM_BASE_URL}/v1/ideogram-v3/generate"
        self.timeout = int(timeout)
        self._urlopen = urlopen or urllib.request.urlopen
        self.badge = f"cloud · ideogram-{self.model.lower()}"

    @classmethod
    def from_entry(cls, entry: dict, urlopen: Callable | None = None) -> "IdeogramImageBackend":
        return cls(api_key=entry.get("api_key", ""),
                   base_url=entry.get("base_url", ""),
                   model=entry.get("model", "") or DEFAULT_IDEOGRAM_MODEL,
                   entry_name=entry.get("name", "") or entry.get("model", ""),
                   urlopen=urlopen)

    def payload(self, request: ImageRequest) -> dict:
        aspect = {"16:9": "16x9", "4:3": "4x3", "1:1": "1x1",
                  "3:4": "3x4", "9:16": "9x16"}.get(request.aspect, "1x1")
        body = {"prompt": request.prompt, "aspect_ratio": aspect}
        if self.model:
            body["model"] = self.model
        return body

    def headers(self) -> dict:
        return {"Api-Key": self.api_key}

    def extract_image_ref(self, payload: dict):
        items = payload.get("data") if isinstance(payload, dict) else None
        if items and isinstance(items[0], dict) and items[0].get("url"):
            return ("url", items[0]["url"])
        return None


def build_image_backend(entry: dict, urlopen: Callable | None = None):
    """Construct the delivered provider adapter a Settings entry describes.

    Raises ValueError with fix guidance when the entry doesn't describe a
    provider Friday genuinely supports -- callers surface that honestly
    rather than guessing at an API shape.
    """
    provider = detect_image_provider(entry)
    if provider == "cloudflare":
        return CloudflareImageBackend.from_entry(entry, urlopen=urlopen)
    if provider == "stability":
        return StabilityImageBackend.from_entry(entry, urlopen=urlopen)
    if provider == "ideogram":
        return IdeogramImageBackend.from_entry(entry, urlopen=urlopen)
    if provider == "openai":
        return OpenAIImageBackend.from_entry(entry, urlopen=urlopen)
    raise ValueError(
        "That entry doesn't describe a supported image provider. Friday's "
        "Image Generation supports Cloudflare Workers AI "
        "(https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>), "
        "Stability AI (https://api.stability.ai), OpenAI-compatible /v1 "
        "APIs (https://api.openai.com/v1, OpenRouter, ...) and Ideogram "
        "(https://api.ideogram.ai). See docs/CLOUD_IMAGE_PROVIDER.md."
    )


class NoProviderBackend:
    """The cloud-only panel's empty state: no usable Settings entry exists.
    The panel never renders locally -- every Generate raises the one
    honest, fixable instruction instead."""

    name = "No image provider configured"
    provider_kind = "none"
    accent_badge = False
    badge = "no provider key · add one in Settings"
    description = (
        "Friday's Image panel is cloud-only: add your own Cloudflare "
        "Workers AI or Stability AI key in Friday's Settings -> Image "
        "Generation to enable real AI generation."
    )
    model = ""

    def render(self, request: ImageRequest, output_dir: Path,
               progress_callback=None,
               cancel_event: threading.Event | None = None) -> RenderedImage:
        raise RenderError(
            "No usable image provider is configured yet. Add your own "
            "Cloudflare or Stability AI key in Friday's Settings -> Image "
            "Generation (see docs/CLOUD_IMAGE_PROVIDER.md), pick it in the "
            "panel's ENGINE dropdown, and Generate again."
        )


class MisconfiguredProviderBackend:
    """Honest stand-in used when Settings Image Generation entries exist
    but none is usable yet: every render attempt raises the real, fixable
    problem instead of silently pretending to generate."""
    """Honest stand-in used when a Settings Image Generation entry exists
    but is not usable yet: every render attempt raises the real, fixable
    problem instead of silently pretending to generate."""

    name = "Cloud image provider (setup needed)"
    provider_kind = "cloud"
    accent_badge = False
    badge = "image provider entry needs attention"
    description = (
        "An Image Generation entry exists in Settings but is missing "
        "required connection details, so cloud generation cannot start."
    )

    def __init__(self, entry_name: str, problem: str):
        self.entry_name = entry_name
        self.problem = problem

    def render(self, request: ImageRequest, output_dir: Path,
               progress_callback=None,
               cancel_event: threading.Event | None = None) -> RenderedImage:
        raise RenderError(
            f"The Settings entry \"{self.entry_name}\" is not usable yet: "
            f"{self.problem}"
        )

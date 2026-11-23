"""Deterministic local video renderer for Quiet Video.

This module is real rendering code, not a fake AI avatar. It produces MP4 or
GIF motion studies locally with Pillow and, for MP4, imageio's FFmpeg
backend. A text prompt is used only as the on-screen title and as a stable
art seed; no semantic text-to-image model is bundled or claimed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import random
import re
import shutil
import tempfile
import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .model import VideoRequest


class RenderCancelled(Exception):
    """Raised after the user cancels a queued/running render."""


class RenderError(RuntimeError):
    """A clear, user-facing render failure."""


@dataclass(frozen=True)
class RenderedOutput:
    output_path: str
    poster_path: str
    frames_written: int
    elapsed_seconds: float
    size_bytes: int
    format: str


PALETTES = {
    "Mist": {
        "top": (19, 19, 22), "bottom": (54, 53, 49),
        "accents": ((145, 161, 151), (177, 174, 165), (95, 111, 116)),
        "ink": (228, 226, 219), "vignette": (0, 0, 0), "grain": 9, "blur": 14,
        "rings": (84, 92, 91),
    },
    "Glass": {
        "top": (17, 22, 28), "bottom": (60, 70, 67),
        "accents": ((160, 197, 199), (194, 187, 166), (111, 139, 154)),
        "ink": (230, 234, 231), "vignette": (5, 12, 15), "grain": 7, "blur": 18,
        "rings": (128, 154, 156),
    },
    "Paper": {
        "top": (232, 229, 219), "bottom": (188, 181, 168),
        "accents": ((83, 91, 97), (132, 118, 101), (92, 111, 106)),
        "ink": (47, 47, 46), "vignette": (105, 95, 83), "grain": 18, "blur": 8,
        "rings": (116, 106, 95),
    },
    "Ember": {
        "top": (21, 17, 15), "bottom": (74, 42, 30),
        "accents": ((202, 122, 82), (228, 180, 118), (131, 62, 45)),
        "ink": (241, 229, 212), "vignette": (17, 7, 4), "grain": 11, "blur": 16,
        "rings": (177, 105, 78),
    },
}


def ffmpeg_available() -> bool:
    try:
        import imageio_ffmpeg
        return Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file()
    except Exception:
        return False


def _even(value: float | int) -> int:
    value = max(2, int(round(value)))
    return value if value % 2 == 0 else value + 1


def target_size(aspect: str, quality: str) -> tuple[int, int]:
    """Return sensible local-render dimensions; sharp enough, still fast."""
    base = {"16:9": (960, 540), "1:1": (720, 720), "9:16": (640, 1136)}
    scale = 0.67 if quality == "Draft" else 1.0
    width, height = base[aspect]
    return _even(width * scale), _even(height * scale)


def _gradient(size: tuple[int, int], top: tuple[int, int, int],
              bottom: tuple[int, int, int], phase: float) -> Image.Image:
    width, height = size
    y = np.linspace(0, 1, height, dtype=np.float32)
    wave = 0.5 + 0.5 * np.sin((y * 2.2 + phase * math.tau) * math.pi)
    mix = np.clip(0.88 * y + 0.12 * wave, 0, 1)
    top_arr = np.array(top, dtype=np.float32)
    bottom_arr = np.array(bottom, dtype=np.float32)
    columns = mix[:, None] * bottom_arr + (1.0 - mix)[:, None] * top_arr
    rows = columns.astype(np.uint8)[:, None, :]
    return Image.fromarray(np.repeat(rows, width, axis=1), "RGB")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "Arial Bold.ttf" if bold else "Arial.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _draw_glow(draw: ImageDraw.ImageDraw, center: tuple[float, float],
               radius: float, fill: tuple[int, int, int], blur: int,
               layer: Image.Image) -> None:
    x, y = center
    local = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    local_draw = ImageDraw.Draw(local)
    local_draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                       fill=fill + (120,))
    blurred = local.filter(ImageFilter.GaussianBlur(blur))
    layer.alpha_composite(blurred)
    draw.ellipse((x - radius * 0.28, y - radius * 0.28,
                 x + radius * 0.28, y + radius * 0.28), fill=fill + (66,))


def _draw_rings(draw: ImageDraw.ImageDraw, size: tuple[int, int], progress: float,
                accent: tuple[int, int, int], style: str, phase: float) -> None:
    width, height = size
    cx, cy = width * (0.55 + 0.08 * math.sin(phase * math.tau)), height * 0.47
    ring_count = 5 if style != "Paper" else 6
    for index in range(ring_count):
        radius = (18 + index * 7 + progress * 26) / 100
        rx = width * (0.34 + radius)
        ry = height * (0.38 + radius * 1.28)
        start = (progress * 360 + index * 47) % 360
        extent = 84 + 12 * index
        alpha = max(18, 75 - index * 10)
        draw.arc((cx - rx, cy - ry, cx + rx, cy + ry), start=start,
                 end=start + extent, fill=accent + (alpha,), width=max(1, round(min(size) * .003)))


def _wrap_title(prompt: str, fallback: str, width_chars: int = 30) -> list[str]:
    words = (prompt or fallback).split()
    if not words:
        words = [fallback]
    lines: list[str] = []
    current = ""
    for word in words[:18]:
        candidate = (current + " " + word).strip()
        if len(candidate) > width_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if not lines:
        lines = [fallback]
    return lines[:3]


def _draw_title(draw: ImageDraw.ImageDraw, size: tuple[int, int], prompt: str,
                style: str, ink: tuple[int, int, int], accent: tuple[int, int, int]) -> None:
    width, height = size
    label_font = _font(max(11, round(height * 0.023)), bold=False)
    headline_font = _font(max(22, round(height * 0.055)), bold=True)
    lines = _wrap_title(prompt, f"A {style} motion study")
    measured = [draw.textbbox((0, 0), line, font=headline_font) for line in lines]
    heights = [box[3] - box[1] for box in measured]
    spacing = max(8, round(height * 0.018))
    total = sum(heights) + spacing * (len(lines) - 1)
    top = height * 0.48 - total / 2

    caption = f"{style.upper()}  ·  LOCAL MOTION RENDER"
    cap_box = draw.textbbox((0, 0), caption, font=label_font)
    cap_width = cap_box[2] - cap_box[0]
    cap_height = cap_box[3] - cap_box[1]
    chip_x = (width - cap_width) / 2
    chip_y = max(height * 0.13, top - cap_height - height * 0.08)
    draw.rounded_rectangle((chip_x - 14, chip_y - 7, chip_x + cap_width + 14,
                           chip_y + cap_height + 8),
                           radius=max(10, round(cap_height * .9)),
                           fill=(*accent, 25), outline=(*accent, 80), width=1)
    draw.text((chip_x, chip_y), caption, font=label_font, fill=(*accent, 210))

    accent_y = top - max(6, round(height * 0.015))
    draw.line((width * .43, accent_y, width * .57, accent_y), fill=(*accent, 120),
              width=max(1, round(height * .004)))
    for index, line in enumerate(lines):
        box = measured[index]
        text_width = box[2] - box[0]
        x = (width - text_width) / 2
        y = top + sum(heights[:index]) + spacing * index
        draw.text((x + 1, y + 2), line, font=headline_font, fill=(0, 0, 0, 90))
        draw.text((x, y), line, font=headline_font, fill=(*ink, 235))

    credit_font = _font(max(10, round(height * 0.017)))
    credit = "Prompt text is the title · no AI provider connected"
    cbox = draw.textbbox((0, 0), credit, font=credit_font)
    draw.text(((width - (cbox[2] - cbox[0])) / 2, height * .87),
              credit, font=credit_font, fill=(*ink, 118))


def _cover_crop(source: Image.Image, size: tuple[int, int], progress: float,
                zoom: float, pan_x: float, pan_y: float) -> Image.Image:
    width, height = size
    source_ratio = source.width / source.height
    target_ratio = width / height
    if source_ratio >= target_ratio:
        crop_height = source.height / zoom
        crop_width = crop_height * target_ratio
    else:
        crop_width = source.width / zoom
        crop_height = crop_width / target_ratio
    crop_width = min(float(source.width), crop_width)
    crop_height = min(float(source.height), crop_height)
    max_x = source.width - crop_width
    max_y = source.height - crop_height
    left = max_x / 2 + pan_x * max_x / 2
    top = max_y / 2 + pan_y * max_y / 2
    box = (left, top, left + crop_width, top + crop_height)
    return source.resize((width, height), Image.Resampling.LANCZOS,
                         box=box).convert("RGB")


def _image_frame(source: Image.Image, size: tuple[int, int], request: VideoRequest,
                 progress: float, palette: dict) -> Image.Image:
    phase = (progress - .5) * 2
    if request.motion == "Push in":
        zoom, pan_x, pan_y = 1.04 + .13 * progress, 0, -0.06 * progress
    elif request.motion == "Pan right":
        zoom, pan_x, pan_y = 1.12, -0.45 + .9 * progress, 0
    elif request.motion == "Orbit":
        zoom, pan_x, pan_y = 1.11, .18 * math.cos(progress * math.tau), .12 * math.sin(progress * math.tau)
    elif request.motion == "Drift":
        zoom, pan_x, pan_y = 1.06 + .04 * math.sin(progress * math.pi), .12 * phase, .08 * math.sin(progress * math.tau)
    else:
        zoom, pan_x, pan_y = 1.04, 0, 0
    frame = _cover_crop(source, size, progress, zoom, pan_x, pan_y)
    # Keep the style palette present without mutating the user's image.
    tint = Image.new("RGBA", size, palette["top"] + (46,))
    frame = Image.alpha_composite(frame.convert("RGBA"), tint)
    overlay = _gradient(size, (0, 0, 0), (0, 0, 0), progress).convert("RGBA")
    overlay.putalpha(95)
    # Directional darkening at top and bottom gives the title room.
    frame = Image.alpha_composite(frame, overlay)
    return frame


def _abstract_frame(size: tuple[int, int], request: VideoRequest, progress: float,
                    palette: dict, rng: random.Random) -> Image.Image:
    top, bottom = palette["top"], palette["bottom"]
    frame = _gradient(size, top, bottom, progress).convert("RGBA")
    draw = ImageDraw.Draw(frame, "RGBA")
    accents = palette["accents"]
    width, height = size
    phase = progress * math.tau
    speed = 0.0 if request.motion == "Still" else (0.74 if request.motion == "Drift" else 1.0)
    for index, color in enumerate(accents):
        angle = phase * speed * (1 + index * .21) + index * 2.1
        if request.motion == "Pan right":
            x = width * (.18 + .64 * ((progress * speed + index * .24) % 1))
            y = height * (.31 + .30 * math.sin(phase + index))
        elif request.motion == "Push in":
            scale = .5 + .42 * progress
            x = width * (.50 + .17 * math.cos(angle) * (1 - progress * .4))
            y = height * (.46 + .17 * math.sin(angle) * (1 - progress * .4))
        elif request.motion == "Orbit":
            scale = 1.0
            x = width * (.50 + .27 * math.cos(angle))
            y = height * (.48 + .22 * math.sin(angle))
        else:
            scale = 1.0
            x = width * (.50 + .25 * math.cos(angle))
            y = height * (.48 + .20 * math.sin(angle))
        radius_value = min(size) * (0.31 + index * .105) * (1.10 if request.style == "Paper" else 1.0)
        radius_value *= ((.57 + .45 * progress) if request.motion == "Push in" and index == 0 else 1.0)
        _draw_glow(draw, (x, y), radius_value * (1 + .08 * math.sin(angle + index)), color,
                   max(4, round(min(size) * palette["blur"] / 70)), frame)
    if request.style == "Paper":
        # Restrained paper folds and geometric cuts.
        for index in range(7):
            offset = index / 7
            x = (offset + progress * .18) % 1.25 * width - width * .12
            fill = (55, 61, 64, 46) if index % 2 == 0 else (250, 248, 240, 64)
            points = [(x, 0), (x + width * .18, height), (x + width * .31, height),
                      (x + width * .12, 0)]
            draw.polygon(points, fill=fill)
        accent = accents[0]
        draw.line((width * .12, height * .70, width * .88, height * .70),
                  fill=(*accent, 86), width=max(1, round(height * .006)))
    else:
        _draw_rings(draw, size, progress, palette["rings"], request.style, progress)
        if request.style == "Ember":
            for index in range(5):
                x = width * (.12 + .2 * index + .02 * math.sin(phase * 2 + index))
                y = height * (.95 - .34 * ((progress + index * .18) % 1))
                draw.line((x, y + height * .18, x, y), fill=(*accents[0], 46),
                          width=max(1, round(width * .003)))
        else:
            for index in range(6):
                x = width * (.08 + index * .17 + .02 * math.sin(phase + index))
                y = height * (.32 + .32 * math.sin(phase * 1.2 + index * .9))
                radius = min(size) * .012
                draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                             fill=(*accents[index % len(accents)], 92))
    return frame


def _vignette(size: tuple[int, int], strength: float, color: tuple[int, int, int]) -> Image.Image:
    width, height = size
    y, x = np.ogrid[-1:1:complex(0, height), -1:1:complex(0, width)]
    distance = np.sqrt((x * 0.82) ** 2 + (y * 1.05) ** 2)
    alpha = np.clip((distance - .42) * 255 * strength, 0, 145).astype(np.uint8)
    rgb = np.zeros((height, width, 4), dtype=np.uint8)
    rgb[..., 0], rgb[..., 1], rgb[..., 2] = color
    rgb[..., 3] = alpha
    return Image.fromarray(rgb, "RGBA")


def _add_grain(frame: Image.Image, request: VideoRequest, frame_index: int,
               palette: dict) -> Image.Image:
    amount = palette["grain"] + (2 if request.quality == "Studio" else 0)
    if amount <= 0:
        return frame.convert("RGBA")
    size = (max(32, frame.width // 2), max(32, frame.height // 2))
    generator = np.random.default_rng((request.seed or 0) + frame_index * 7919)
    noise = generator.normal(127, amount, size + (1,)).clip(0, 255).astype(np.uint8)
    noise = np.repeat(noise, 3, axis=2)
    layer = Image.fromarray(noise, "RGB").resize(frame.size, Image.Resampling.NEAREST)
    matte = layer.convert("RGBA")
    matte.putalpha(12 if frame.mode == "RGB" else 14)
    return Image.alpha_composite(frame.convert("RGBA"), matte)


def _frame(request: VideoRequest, index: int, total: int,
           size: tuple[int, int], source: Image.Image | None,
           rng: random.Random) -> Image.Image:
    progress = 0 if total <= 1 else index / (total - 1)
    palette = PALETTES[request.style]
    if source is not None:
        frame = _image_frame(source, size, request, progress, palette)
    else:
        frame = _abstract_frame(size, request, progress, palette, rng)
    frame = Image.alpha_composite(frame, _vignette(size, .54 if source is not None else .38,
                                                  palette["vignette"]))
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    _draw_title(draw, size, request.prompt, request.style, palette["ink"],
                palette["accents"][0])
    frame = Image.alpha_composite(frame, overlay)
    return _add_grain(frame, request, index, palette).convert("RGB")


def _slug(prompt: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")
    return (value or "quiet-motion")[:44].strip("-") or "quiet-motion"


def _output_paths(request: VideoRequest, output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{_slug(request.brief_label)}-{request.seed:08x}"
    return output_dir / f".{base}.tmp.{request.format}", output_dir / f"{base}.{request.format}", output_dir / f"{base}-poster.png"


def render_video(request: VideoRequest, output_dir: Path,
                 progress_callback=None, cancel_event: threading.Event | None = None) -> RenderedOutput:
    """Render an actual local MP4/GIF and return its persisted output paths."""
    request = request.validated()
    size = target_size(request.aspect, request.quality)
    total = request.frame_count
    temp_path, final_path, poster_path = _output_paths(request, output_dir)
    cancel_event = cancel_event or threading.Event()
    generator = random.Random((request.seed or 0) + 17)
    source = None
    if request.source_image:
        try:
            loaded = Image.open(request.source_image)
            loaded.load()
            source = ImageOps.exif_transpose(loaded).convert("RGB")
        except Exception as error:
            raise RenderError(f"Couldn't read the reference image: {error}") from error
    started = time.monotonic()
    written = 0
    writer = None
    gif_frames: list[Image.Image] = []
    poster_frame: Image.Image | None = None

    def check_cancel() -> None:
        if cancel_event.is_set():
            raise RenderCancelled("Render cancelled.")

    try:
        if request.format == "mp4":
            try:
                import imageio.v2 as imageio
            except Exception as error:
                raise RenderError("MP4 export requires imageio. Install requirements.txt or use GIF output.") from error
            try:
                writer = imageio.get_writer(str(temp_path), fps=request.fps,
                                            codec="libx264", quality=7 if request.quality == "Studio" else 5,
                                            macro_block_size=1, pixelformat="yuv420p")
            except Exception as error:
                raise RenderError("MP4 export requires the imageio-ffmpeg package/FFmpeg backend. "
                                  "Install requirements.txt or use GIF output.") from error
        elif not ffmpeg_available():
            # GIF is Pillow-native, so no external encoder is required.
            pass

        midpoint = max(0, total // 2)
        for index in range(total):
            check_cancel()
            frame = _frame(request, index, total, size, source, generator)
            if index == midpoint:
                poster_frame = frame.copy()
            if writer is not None:
                writer.append_data(np.asarray(frame, dtype=np.uint8))
            else:
                # Quantize each frame consistently enough for stable animated GIFs.
                gif_frames.append(frame)
            written += 1
            if progress_callback is not None:
                progress_callback(written, total)

        if request.format == "gif":
            durations = round(1000 / request.fps)
            first, rest = gif_frames[0], gif_frames[1:]
            first.save(temp_path, save_all=True, append_images=rest,
                       duration=durations, loop=0, optimize=True)
        if poster_frame is None:
            poster_frame = gif_frames[total // 2].copy()
        poster_frame.save(poster_path, format="PNG", optimize=True)
        check_cancel()
        if writer is not None:
            # MP4 muxers write their final index atoms on close; close before
            # renaming the temporary path to the visible deliverable.
            writer.close()
            writer = None
        temp_path.replace(final_path)
        elapsed = time.monotonic() - started
        return RenderedOutput(output_path=str(final_path), poster_path=str(poster_path),
                              frames_written=written, elapsed_seconds=elapsed,
                              size_bytes=final_path.stat().st_size, format=request.format)
    except RenderCancelled:
        temp_path.unlink(missing_ok=True)
        sim = final_path.with_suffix(final_path.suffix + ".part")
        sim.unlink(missing_ok=True)
        raise
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        # Some Pillow codecs retain temporary source handles.
        if source is not None:
            try:
                source.close()
            except Exception:
                pass
        shutil.rmtree(tempfile.gettempdir() + "/quiet-video-unused", ignore_errors=True)


def render_still(request: VideoRequest, size: tuple[int, int] | None = None) -> Image.Image:
    """One poster-quality still used by the panel's live preview."""
    request = request.validated()
    size = size or target_size(request.aspect, request.quality)
    source = None
    if request.source_image:
        loaded = Image.open(request.source_image)
        loaded.load()
        source = ImageOps.exif_transpose(loaded).convert("RGB")
    try:
        return _frame(request, 0, 1, size, source, random.Random(request.seed or 0))
    finally:
        if source is not None:
            source.close()

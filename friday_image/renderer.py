"""Deterministic local image renderer for Quiet Image.

This module is real rendering code, not a fake AI text-to-image model. It
creates PNG/JPEG/WebP art studies locally with Pillow. A prompt is used only
as composited title text and as a stable art seed; no semantic image model is
bundled or claimed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import random
import re
import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .model import ImageRequest


class RenderCancelled(Exception):
    """Raised after the user cancels a queued/running render."""


class RenderError(RuntimeError):
    """A clear, user-facing render failure."""


@dataclass(frozen=True)
class RenderedImage:
    output_path: str
    thumbnail_path: str
    detail_steps: int
    elapsed_seconds: float
    size_bytes: int
    format: str


PALETTES = {
    "Mist": {
        "top": (19, 19, 22), "bottom": (55, 54, 50),
        "accents": ((145, 161, 151), (181, 178, 168), (96, 113, 120)),
        "ink": (228, 226, 219), "vignette": (0, 0, 0),
        "grain": 11, "blur": 18, "lines": (99, 111, 115),
    },
    "Glass": {
        "top": (17, 22, 28), "bottom": (61, 70, 67),
        "accents": ((160, 197, 199), (196, 186, 164), (112, 140, 155)),
        "ink": (231, 235, 231), "vignette": (5, 12, 15),
        "grain": 8, "blur": 22, "lines": (132, 158, 160),
    },
    "Paper": {
        "top": (233, 230, 220), "bottom": (190, 182, 168),
        "accents": ((83, 91, 97), (133, 118, 101), (92, 112, 107)),
        "ink": (48, 48, 46), "vignette": (103, 93, 81),
        "grain": 20, "blur": 9, "lines": (116, 106, 95),
    },
    "Ember": {
        "top": (22, 17, 15), "bottom": (77, 43, 31),
        "accents": ((203, 124, 82), (229, 181, 119), (132, 64, 45)),
        "ink": (242, 229, 211), "vignette": (18, 8, 5),
        "grain": 13, "blur": 20, "lines": (181, 108, 78),
    },
}


def _even(value: float | int) -> int:
    value = max(2, int(round(value)))
    return value if value % 2 == 0 else value + 1


def target_size(aspect: str, quality: str) -> tuple[int, int]:
    """Return deterministic desktop-friendly output dimensions."""
    base = {
        "16:9": (1280, 720),
        "4:3": (1120, 840),
        "1:1": (960, 960),
        "3:4": (840, 1120),
        "9:16": (720, 1280),
    }[aspect]
    scale = 0.62 if quality == "Draft" else 1.0
    return _even(base[0] * scale), _even(base[1] * scale)


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


def _gradient(size: tuple[int, int], top: tuple[int, int, int],
              bottom: tuple[int, int, int], phase: float) -> Image.Image:
    width, height = size
    y = np.linspace(0, 1, height, dtype=np.float32)
    wave = 0.5 + 0.5 * np.sin((y * 1.7 + phase * math.tau) * math.pi)
    x_wave = np.linspace(-0.08, 0.08, width, dtype=np.float32)
    mix = np.clip(0.86 * y[:, None] + 0.14 * wave[:, None] + x_wave[None, :], 0, 1)
    top_arr = np.array(top, dtype=np.float32)
    bottom_arr = np.array(bottom, dtype=np.float32)
    pixels = bottom_arr[None, None, :] * mix[..., None] + top_arr[None, None, :] * (1 - mix[..., None])
    return Image.fromarray(pixels.astype(np.uint8), "RGB")


def _draw_glow(layer: Image.Image, center: tuple[float, float],
               radius: float, color: tuple[int, int, int], blur: int) -> None:
    x, y = center
    local = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(local)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color + (115,))
    layer.alpha_composite(local.filter(ImageFilter.GaussianBlur(blur)))
    draw = ImageDraw.Draw(layer, "RGBA")
    draw.ellipse((x - radius * .24, y - radius * .24,
                  x + radius * .24, y + radius * .24), fill=color + (58,))


def _cover_crop(source: Image.Image, size: tuple[int, int],
                rng: random.Random) -> Image.Image:
    width, height = size
    source_ratio = source.width / source.height
    target_ratio = width / height
    if source_ratio >= target_ratio:
        crop_height = source.height / 1.04
        crop_width = crop_height * target_ratio
    else:
        crop_width = source.width / 1.04
        crop_height = crop_width / target_ratio
    crop_width = min(source.width, crop_width)
    crop_height = min(source.height, crop_height)
    max_x = max(0, source.width - crop_width)
    max_y = max(0, source.height - crop_height)
    left = max_x * (0.42 + 0.16 * rng.random())
    top = max_y * (0.42 + 0.16 * rng.random())
    return source.resize(size, Image.Resampling.LANCZOS,
                         box=(left, top, left + crop_width, top + crop_height)).convert("RGB")


def _source_art(source: Image.Image, size: tuple[int, int],
                request: ImageRequest, palette: dict, rng: random.Random) -> Image.Image:
    frame = _cover_crop(source, size, rng).convert("RGBA")
    tint = Image.new("RGBA", size, palette["top"] + (54,))
    frame = Image.alpha_composite(frame, tint)
    # Quiet directional grade keeps the title readable without pretending a
    # semantic AI transformation happened to the source image.
    overlay = _gradient(size, (0, 0, 0), (0, 0, 0), .16).convert("RGBA")
    overlay.putalpha(88)
    return Image.alpha_composite(frame, overlay)


def _seed_points(rng: random.Random, count: int) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for _ in range(count):
        points.append((0.12 + 0.76 * rng.random(), 0.15 + 0.70 * rng.random()))
    return points


def _composition_layers(canvas: Image.Image, request: ImageRequest,
                        palette: dict, rng: random.Random) -> None:
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas, "RGBA")
    accents = palette["accents"]
    composition = request.composition
    detail_points = max(4, int(round(request.detail / 9)))
    points = _seed_points(rng, min(10, detail_points))

    horizon = height * (.55 + .06 * math.sin((request.seed or 0) * .0001))
    if composition == "Horizon":
        draw.polygon(((0, horizon), (width, horizon - height * .09), (width, height), (0, height)),
                     fill=(*accents[2], 42))
        draw.line((0, horizon, width, horizon - height * .09),
                  fill=(*accents[0], 110), width=max(2, round(height * .006)))
        centers = [(width * .50, height * .38, .34), (points[0][0] * width, points[0][1] * height, .22)]
    elif composition == "Center":
        centers = [(width * .50, height * .47, .38),
                   (width * (.40 + .20 * points[1][0]), height * (.38 + .20 * points[1][1]), .18)]
    elif composition == "Rings":
        centers = [(width * .52, height * .48, .33), (width * .42, height * .43, .17)]
        for index in range(5):
            radius = min(canvas.size) * (.18 + .13 * index)
            alpha = max(30, 92 - index * 12)
            x, y = centers[0][0], centers[0][1]
            draw.ellipse((x-radius, y-radius, x+radius, y+radius),
                         outline=(*palette["lines"], alpha), width=max(1, round(min(canvas.size)*.004)))
    else:  # Cuts
        centers = [(width * .54, height * .48, .28), (width * .35, height * .35, .14)]
        for index in range(7):
            offset = index / 7
            x = (offset * 1.24 - .12) * width
            fill = (36, 40, 42, 49) if index % 2 == 0 else (246, 244, 238, 66)
            draw.polygon(((x, 0), (x + width * .18, height),
                          (x + width * .31, height), (x + width * .12, 0)), fill=fill)
        draw.line((width * .12, height * .72, width * .88, height * .72),
                  fill=(*accents[0], 88), width=max(2, round(height * .006)))

    for index, color in enumerate(accents):
        center = centers[min(index, len(centers)-1)]
        _draw_glow(canvas, center[:2], min(canvas.size) * center[2] *
                   (1 + index * .14), color, palette["blur"])

    # Seed-derived small marks are deterministic art scaffolding, not a claim
    # that the text was understood by a model.
    for index, (px, py) in enumerate(points):
        color = accents[index % len(accents)]
        x, y = px * width, py * height
        if request.style == "Paper" and index % 2 == 0:
            size = min(canvas.size) * (.018 + (index % 3) * .006)
            draw.polygon(((x, y-size), (x+size, y), (x, y+size), (x-size, y)),
                         fill=(*color, 94))
        else:
            radius = min(canvas.size) * (.008 + (index % 3) * .003)
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(*color, 118))
            if composition != "Cuts":
                draw.line((x, y, width * .5, height * .47), fill=(*color, 26),
                          width=max(1, round(min(canvas.size) * .0018)))

    if request.style == "Ember":
        for index in range(6):
            x = width * (.12 + .15 * index) + width * .02 * math.sin(index)
            y = height * (.92 - .06 * index)
            draw.line((x, y + height * .12, x, y), fill=(*accents[0], 55),
                      width=max(1, round(width * .003)))
    elif request.style == "Glass":
        for index in range(4):
            x0 = width * (.18 + index * .16)
            draw.rounded_rectangle((x0, height * .17, x0 + width * .07, height * .70),
                                   radius=round(width * .018), outline=(*palette["lines"], 52),
                                   width=max(1, round(width * .0025)))


def _abstract_art(size: tuple[int, int], request: ImageRequest,
                  palette: dict, rng: random.Random) -> Image.Image:
    base = _gradient(size, palette["top"], palette["bottom"],
                     ((request.seed or 0) % 97) / 97).convert("RGBA")
    _composition_layers(base, request, palette, rng)
    return base


def _vignette(size: tuple[int, int], strength: float,
              color: tuple[int, int, int]) -> Image.Image:
    width, height = size
    y, x = np.ogrid[-1:1:complex(0, height), -1:1:complex(0, width)]
    distance = np.sqrt((x * .78) ** 2 + (y * 1.02) ** 2)
    alpha = np.clip((distance - .38) * 255 * strength, 0, 148).astype(np.uint8)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = color
    rgba[..., 3] = alpha
    return Image.fromarray(rgba, "RGBA")


def _wrap_title(prompt: str, fallback: str, width_chars: int = 28) -> list[str]:
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
    return lines[:3] or [fallback]


def _draw_title(canvas: Image.Image, request: ImageRequest,
                palette: dict) -> None:
    size = canvas.size
    width, height = size
    draw = ImageDraw.Draw(canvas, "RGBA")
    label_font = _font(max(10, round(height * .022)))
    headline_font = _font(max(20, round(height * .052)), bold=True)
    lines = _wrap_title(request.prompt, f"A {request.style} art study")
    boxes = [draw.textbbox((0, 0), line, font=headline_font) for line in lines]
    heights = [box[3] - box[1] for box in boxes]
    spacing = max(7, round(height * .015))
    total = sum(heights) + spacing * (len(lines)-1)
    top = height * .50 - total / 2
    accent, ink = palette["accents"][0], palette["ink"]

    caption = f"{request.style.upper()}  ·  LOCAL ART RENDER"
    cap_box = draw.textbbox((0, 0), caption, font=label_font)
    cap_width = cap_box[2] - cap_box[0]
    cap_height = cap_box[3] - cap_box[1]
    chip_x = (width - cap_width) / 2
    chip_y = max(height * .12, top - cap_height - height * .075)
    draw.rounded_rectangle((chip_x-13, chip_y-6, chip_x+cap_width+13, chip_y+cap_height+7),
                           radius=max(9, round(cap_height * .85)),
                           fill=(*accent, 25), outline=(*accent, 82), width=1)
    draw.text((chip_x, chip_y), caption, font=label_font, fill=(*accent, 211))
    draw.line((width*.42, top - height*.018, width*.58, top - height*.018),
              fill=(*accent, 122), width=max(1, round(height*.004)))
    for index, line in enumerate(lines):
        box = boxes[index]
        text_width = box[2] - box[0]
        x = (width - text_width) / 2
        y = top + sum(heights[:index]) + spacing * index
        draw.text((x+1, y+2), line, font=headline_font, fill=(0, 0, 0, 92))
        draw.text((x, y), line, font=headline_font, fill=(*ink, 237))

    credit_font = _font(max(9, round(height * .016)))
    credit = "Prompt text is the title · no AI provider connected"
    credit_box = draw.textbbox((0, 0), credit, font=credit_font)
    draw.text(((width - (credit_box[2]-credit_box[0])) / 2, height * .875),
              credit, font=credit_font, fill=(*ink, 120))


def _add_grain(canvas: Image.Image, seed: int, palette: dict,
               quality: str) -> Image.Image:
    amount = palette["grain"] + (3 if quality == "Studio" else 0)
    if amount <= 0:
        return canvas
    small = (max(24, canvas.width // 2), max(24, canvas.height // 2))
    generator = np.random.default_rng(seed ^ 0x5A17)
    noise = generator.normal(127, amount, small + (1,)).clip(0, 255).astype(np.uint8)
    noise = np.repeat(noise, 3, axis=2)
    layer = Image.fromarray(noise, "RGB").resize(canvas.size, Image.Resampling.NEAREST).convert("RGBA")
    layer.putalpha(13)
    return Image.alpha_composite(canvas, layer)


def render_still(request: ImageRequest, size: tuple[int, int] | None = None) -> Image.Image:
    """Render one deterministic local art frame for preview or export."""
    request = request.validated()
    size = size or target_size(request.aspect, request.quality)
    size = (_even(size[0]), _even(size[1]))
    rng = random.Random((request.seed or 0) ^ 971)
    palette = PALETTES[request.style]
    if request.source_image:
        try:
            loaded = Image.open(request.source_image)
            loaded.load()
            source = ImageOps.exif_transpose(loaded).convert("RGB")
        except Exception as error:
            raise RenderError(f"Couldn't read the reference image: {error}") from error
        canvas = _source_art(source, size, request, palette, rng)
        # Keep local art marks over references, but make them subtle.
        _draw_title(canvas, request, palette)
    else:
        canvas = _abstract_art(size, request, palette, rng)
        _draw_title(canvas, request, palette)
    canvas = Image.alpha_composite(canvas, _vignette(size, .52, palette["vignette"]))
    return _add_grain(canvas, request.seed or 0, palette, request.quality).convert("RGB")


def _slug(prompt: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")
    return (value or "quiet-art")[:44].strip("-") or "quiet-art"


THUMBNAIL_FOLDER = Path.home() / ".friday" / "generated" / "thumbnails"


def thumbnail_final_path(final_path: Path) -> Path:
    """Where a render's queue thumbnail lives: Friday's private cache,
    NEVER beside the user's image. The chosen output folder keeps exactly
    one file per prompt -- the rendered image itself."""
    THUMBNAIL_FOLDER.mkdir(parents=True, exist_ok=True, mode=0o700)
    return THUMBNAIL_FOLDER / f"{final_path.stem}-preview.png"


def _output_paths(request: ImageRequest, output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    THUMBNAIL_FOLDER.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{_slug(request.prompt_label)}-{request.seed:08x}"
    return (output_dir / f".{base}.tmp.{request.format}",
            output_dir / f"{base}.{request.format}",
            THUMBNAIL_FOLDER / f".{base}.thumb.tmp.png")


def _save_output(image: Image.Image, path: Path, request: ImageRequest) -> None:
    if request.format == "jpg":
        image.save(path, "JPEG", quality=93 if request.quality == "Studio" else 82,
                   optimize=True, progressive=True)
    elif request.format == "webp":
        image.save(path, "WEBP", quality=92 if request.quality == "Studio" else 78,
                   method=6)
    else:
        image.save(path, "PNG", optimize=True, compress_level=6 if request.quality == "Studio" else 3)


def render_image(request: ImageRequest, output_dir: Path,
                 progress_callback=None, cancel_event: threading.Event | None = None) -> RenderedImage:
    """Render an actual local image and return its persisted output paths."""
    request = request.validated()
    total = request.steps_total
    cancel_event = cancel_event or threading.Event()
    temp_path, final_path, thumb_temp = _output_paths(request, output_dir)
    thumb_final = thumbnail_final_path(final_path)
    finished: list[Path] = []
    started = time.monotonic()
    done = 0

    def check_cancel() -> None:
        if cancel_event.is_set():
            raise RenderCancelled("Render cancelled.")

    def stage(label: str) -> None:
        nonlocal done
        check_cancel()
        done = min(total, done + 1)
        if progress_callback:
            progress_callback(done, total)

    stage("validate request")
    size = target_size(request.aspect, request.quality)
    stage("choose dimensions")

    source: Image.Image | None = None
    if request.source_image:
        try:
            loaded = Image.open(request.source_image)
            loaded.load()
            source = ImageOps.exif_transpose(loaded).convert("RGB")
        except Exception as error:
            raise RenderError(f"Couldn't read the reference image: {error}") from error
    stage("load reference" if source is not None else "compose palette")

    rng = random.Random((request.seed or 0) ^ 977)
    palette = PALETTES[request.style]
    if source is not None:
        canvas = _source_art(source, size, request, palette, rng)
    else:
        canvas = _gradient(size, palette["top"], palette["bottom"],
                           ((request.seed or 0) % 97) / 97).convert("RGBA")
    stage("prepare canvas")

    if source is not None:
        detail_rng = random.Random((request.seed or 0) ^ 8191)
        # Add restrained deterministic marks over references too.
        marks = max(2, int(round(request.detail / 22)))
        draw = ImageDraw.Draw(canvas, "RGBA")
        for index in range(marks):
            x, y = (0.16 + .68*detail_rng.random()) * size[0], (0.16 + .68*detail_rng.random()) * size[1]
            color = palette["accents"][index % len(palette["accents"])]
            radius = min(size) * (.012 + index * .004)
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(*color, 92))
    else:
        _composition_layers(canvas, request, palette, rng)
    stage("compose forms")

    _draw_title(canvas, request, palette)
    stage("compose title")
    canvas = Image.alpha_composite(canvas, _vignette(size, .52, palette["vignette"]))
    stage("apply vignette")
    canvas = _add_grain(canvas, request.seed or 0, palette, request.quality)
    stage("add texture")

    # Extra deterministic refinement stages make progress/cancellation real
    # even though the base image itself is quick.
    while done < total - 2:
        stage("refine texture")
    image = canvas.convert("RGB")
    try:
        _save_output(image, temp_path, request)
        stage("encode image")
        thumbnail = image.copy()
        thumbnail.thumbnail((520, 520), Image.Resampling.LANCZOS)
        thumbnail.save(thumb_temp, "PNG", optimize=True)
        stage("encode thumbnail")
        check_cancel()
        temp_path.replace(final_path)
        thumb_temp.replace(thumb_final)
        finished.extend((final_path, thumb_final))
        size_bytes = final_path.stat().st_size
        if progress_callback:
            progress_callback(total, total)
        return RenderedImage(
            output_path=str(final_path), thumbnail_path=str(thumb_final),
            detail_steps=total, elapsed_seconds=time.monotonic() - started,
            size_bytes=size_bytes, format=request.format,
        )
    except Exception:
        raise
    finally:
        if cancel_event.is_set():
            temp_path.unlink(missing_ok=True)
            thumb_temp.unlink(missing_ok=True)
        else:
            for path in (temp_path, thumb_temp):
                path.unlink(missing_ok=True)

"""Line icons for Friday's native media panels (Image and Video), drawn
in-app with Pillow.

No icon fonts, network assets, or downloaded SVGs are required. Pure Pillow
bitmaps are global/cached, while every native Tk image is owned by the panel
interpreter that created its Friday child view.
"""
from __future__ import annotations

from functools import lru_cache
import math

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from friday_chat.icons import _PanelImage


@lru_cache(maxsize=256)
def _raster(name: str, color: str = "#B5B4BB", size: int = 18) -> Image.Image:
    scale = 4
    size = max(10, min(64, int(size)))
    unit = size * scale / 24
    image = Image.new("RGBA", (size * scale, size * scale))
    draw = ImageDraw.Draw(image)

    def points(values):
        return [(x * unit, y * unit) for x, y in values]

    def line(values, width=1.65, closed=False):
        values = list(values)
        if closed and values:
            values.append(values[0])
        xy = points(values)
        stroke = max(1, round(width * unit))
        draw.line(xy, fill=color, width=stroke, joint="curve")
        for x, y in xy[1:-1] if closed else xy:
            radius = stroke / 2
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

    def box(bounds, radius=3, width=1.6):
        draw.rounded_rectangle(tuple(v * unit for v in bounds), radius=radius * unit,
                               outline=color, width=max(1, round(width * unit)))

    def circle(bounds, width=1.55, fill=None):
        draw.ellipse(tuple(v * unit for v in bounds), outline=color if fill is None else None,
                     fill=fill, width=max(1, round(width * unit)))

    if name == "video":
        box((2.5, 5.5, 15.5, 18.5), radius=3)
        line([(16, 10), (21.5, 6.8), (21.5, 17.2), (16, 14)], closed=True, width=1.45)
    elif name == "film":
        box((3, 4, 21, 20), radius=2.5)
        line([(8, 4), (8, 20)], width=1.35)
        line([(16, 4), (16, 20)], width=1.35)
        for y in (7, 12, 17):
            line([(4.4, y), (6.6, y)], width=1.15)
            line([(17.4, y), (19.6, y)], width=1.15)
    elif name == "image":
        box((3, 4, 21, 20), radius=3)
        circle((6.4, 7.4, 9.4, 10.4), width=1.25)
        line([(4.5, 17), (10, 11.5), (14.5, 16), (17, 13.5), (20.5, 17)])
    elif name == "spark":
        line([(11, 3), (13.5, 9.5), (20, 12), (13.5, 14.5),
              (11, 21), (8.5, 14.5), (2, 12), (8.5, 9.5)], closed=True, width=1.4)
        line([(20, 2.5), (20, 6)], width=1.3)
        line([(18.3, 4.3), (21.7, 4.3)], width=1.3)
    elif name == "plus":
        line([(12, 5), (12, 19)])
        line([(5, 12), (19, 12)])
    elif name == "close":
        line([(6, 6), (18, 18)])
        line([(18, 6), (6, 18)])
    elif name == "copy":
        box((8, 8, 21, 21), radius=2.5)
        line([(5.5, 16), (3, 16), (3, 3), (16, 3), (16, 5.5)], width=1.45)
    elif name == "trash":
        line([(4, 6), (20, 6)])
        line([(9, 3), (15, 3)])
        line([(6, 6), (7, 21), (17, 21), (18, 6)])
        line([(10, 10), (10, 17)], width=1.25)
        line([(14, 10), (14, 17)], width=1.25)
    elif name == "play":
        line([(7, 4), (20, 12), (7, 20)], closed=True, width=1.75)
    elif name == "stop":
        box((6, 6, 18, 18), radius=2)
    elif name == "download":
        line([(12, 3), (12, 15)])
        line([(7, 10), (12, 15), (17, 10)])
        line([(4, 16.5), (4, 21), (20, 21), (20, 16.5)])
    elif name == "open":
        box((4, 5, 20, 21), radius=3)
        line([(10, 14), (18, 6)], width=1.8)
        line([(13, 5.5), (19, 5.5), (19, 11.5)], width=1.8)
    elif name == "folder":
        line([(2.5, 7), (2.5, 4), (8.7, 4), (11.4, 7.2), (21.5, 7.2),
              (21.5, 20), (2.5, 20), (2.5, 7)], closed=True)
    elif name == "file":
        line([(14, 3), (5, 3), (5, 21), (19, 21), (19, 8), (14, 3)], closed=True)
        line([(14, 3), (14, 8), (19, 8)], width=1.35)
        line([(9, 14), (15, 14)], width=1.25)
    elif name == "settings":
        pts = []
        for i in range(24):
            angle = math.pi * i / 12
            radius = 9.0 if i % 2 == 0 else 7.2
            pts.append((12 + radius * math.cos(angle), 12 + radius * math.sin(angle)))
        line(pts, closed=True, width=1.4)
        circle((9, 9, 15, 15), width=1.2)
    elif name == "clock":
        circle((3, 3, 21, 21))
        line([(12, 7), (12, 12), (16, 14)])
    elif name == "check":
        line([(4.5, 12.5), (9.8, 17.5), (19.5, 6.5)], width=1.9)
    elif name == "warning":
        line([(12, 3), (21.5, 20.5), (2.5, 20.5)], closed=True, width=1.45)
        line([(12, 8.5), (12, 14.5)], width=1.5)
        circle((11, 17, 13, 19), fill=color)
    elif name == "info":
        circle((3, 3, 21, 21))
        line([(12, 11), (12, 17)])
        circle((11, 6.5, 13, 8.5), fill=color)
    elif name == "refresh":
        draw.arc(tuple(v * unit for v in (4, 4, 20, 20)), 42, 315,
                 fill=color, width=max(1, round(1.65 * unit)))
        line([(17.5, 3), (21, 7), (16.5, 8.2)], width=1.45)
    elif name == "eye":
        line([(2.5, 12), (6.8, 7.1), (12, 5.2), (17.2, 7.1), (21.5, 12),
              (17.2, 16.9), (12, 18.8), (6.8, 16.9), (2.5, 12)], closed=True)
        circle((9.2, 9.2, 14.8, 14.8), width=1.35)
    elif name == "scissors":
        circle((4.5, 5, 9.5, 10), width=1.25)
        circle((4.5, 14, 9.5, 19), width=1.25)
        line([(9, 9), (20, 5)], width=1.45)
        line([(9, 15), (20, 19)], width=1.45)
        line([(9, 9), (13.1, 12), (9, 15)], width=1.35)
    elif name == "wand":
        line([(4, 20), (15, 9)], width=1.8)
        line([(16.5, 8.5), (17.8, 4.4)], width=1.4)
        line([(17, 9), (21, 10.3)], width=1.4)
        line([(14.5, 8), (13.3, 4)], width=1.4)
        line([(15, 10.5), (12, 13.5)], width=1.4)
    else:
        raise ValueError(f"Unknown panel icon: {name}")

    image = image.resize((size * 2, size * 2), Image.Resampling.LANCZOS)
    return image


class PanelImage(ctk.CTkImage):
    """Rectangular preview/poster image owned by this panel's Tk interpreter."""
    def __init__(self, master, image, size):
        self._master = master
        super().__init__(light_image=image, dark_image=image, size=size)

    def _get_scaled_light_photo_image(self, scaled_size):
        if scaled_size not in self._scaled_light_photo_images:
            self._scaled_light_photo_images[scaled_size] = ImageTk.PhotoImage(
                self._light_image.resize(scaled_size, Image.Resampling.LANCZOS),
                master=self._master)
        return self._scaled_light_photo_images[scaled_size]

    def _get_scaled_dark_photo_image(self, scaled_size):
        if scaled_size not in self._scaled_dark_photo_images:
            self._scaled_dark_photo_images[scaled_size] = ImageTk.PhotoImage(
                self._dark_image.resize(scaled_size, Image.Resampling.LANCZOS),
                master=self._master)
        return self._scaled_dark_photo_images[scaled_size]

    def clear(self):
        self._scaled_light_photo_images.clear()
        self._scaled_dark_photo_images.clear()


class MediaIconBank:
    """Per-view native image cache; only pure Pillow rasters are global."""
    def __init__(self, master):
        self.master = master
        self.images = {}

    def __call__(self, name, color="#B5B4BB", size=18):
        key = (name, color, size)
        if key not in self.images:
            self.images[key] = _PanelImage(self.master, _raster(*key), size)
        return self.images[key]

    def clear(self):
        for image in self.images.values():
            image._scaled_light_photo_images.clear()
            image._scaled_dark_photo_images.clear()
        self.images.clear()

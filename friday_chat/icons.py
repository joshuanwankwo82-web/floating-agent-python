"""Small, antialiased line icons. No icon fonts or downloaded assets required."""
from functools import lru_cache

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk


@lru_cache(maxsize=128)
def _raster(name: str, color: str = "#B5B4BB", size: int = 18) -> Image.Image:
    scale = 4
    unit = size * scale / 24
    image = Image.new("RGBA", (size * scale, size * scale))
    draw = ImageDraw.Draw(image)

    def points(values):
        return [(x * unit, y * unit) for x, y in values]

    def line(values, width=1.7, closed=False):
        values = list(values)
        if closed:
            values.append(values[0])
        xy = points(values)
        stroke = max(1, round(width * unit))
        draw.line(xy, fill=color, width=stroke, joint="curve")
        radius = stroke / 2
        for x, y in xy:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

    def box(bounds, radius=3, width=1.6):
        draw.rounded_rectangle(tuple(v * unit for v in bounds), radius=radius * unit,
                               outline=color, width=max(1, round(width * unit)))

    def circle(bounds, width=1.6, fill=None):
        draw.ellipse(tuple(v * unit for v in bounds), outline=color if fill is None else None,
                     fill=fill, width=max(1, round(width * unit)))

    if name == "spark":
        line([(11, 3), (13.4, 9.4), (20, 12), (13.4, 14.6),
              (11, 21), (8.6, 14.6), (2, 12), (8.6, 9.4)], closed=True, width=1.45)
        line([(20, 2), (20, 6)], width=1.4)
        line([(18, 4), (22, 4)], width=1.4)
    elif name == "chat":
        box((3, 3, 21, 18), radius=4)
        line([(6, 18), (6, 22), (11, 18)], width=1.5)
    elif name == "plus":
        line([(12, 5), (12, 19)])
        line([(5, 12), (19, 12)])
    elif name == "arrow_up":
        line([(12, 19), (12, 5)], width=1.9)
        line([(6, 11), (12, 5), (18, 11)], width=1.9)
    elif name == "download":
        line([(12, 3), (12, 15)])
        line([(7, 10), (12, 15), (17, 10)])
        line([(4, 16), (4, 21), (20, 21), (20, 16)])
    elif name == "more":
        for x in (5, 12, 19):
            circle((x - 1.3, 10.7, x + 1.3, 13.3), fill=color)
    elif name == "clip":
        # An open paperclip, following one continuous rounded stroke.
        line([(8, 16), (16, 8), (17.5, 8), (19, 9.5), (19, 11),
              (10, 20), (7, 21), (4, 19), (3, 16), (4, 13),
              (14, 3), (17, 2), (20, 3), (22, 6), (22, 9), (12, 19)], width=1.55)
    elif name == "copy":
        box((8, 8, 21, 21), radius=2.5)
        line([(5, 16), (3, 16), (3, 3), (16, 3), (16, 5)], width=1.5)
    elif name == "trash":
        line([(4, 6), (20, 6)])
        line([(9, 3), (15, 3)])
        line([(6, 6), (7, 21), (17, 21), (18, 6)])
        line([(10, 10), (10, 17)], width=1.3)
        line([(14, 10), (14, 17)], width=1.3)
    elif name == "close":
        line([(6, 6), (18, 18)])
        line([(18, 6), (6, 18)])
    elif name == "info":
        circle((3, 3, 21, 21))
        line([(12, 11), (12, 17)])
        circle((11, 6.5, 13, 8.5), fill=color)
    elif name == "file":
        line([(14, 3), (5, 3), (5, 21), (19, 21), (19, 8), (14, 3)], closed=True)
        line([(14, 3), (14, 8), (19, 8)], width=1.4)
        line([(9, 13), (15, 13)], width=1.3)
        line([(9, 17), (13, 17)], width=1.3)
    elif name == "lock":
        box((5, 10, 19, 21), radius=2.5)
        draw.arc(tuple(v * unit for v in (8, 2, 16, 15)), 180, 360,
                 fill=color, width=max(1, round(1.6 * unit)))
        line([(8, 7), (8, 10)], width=1.6)
        line([(16, 7), (16, 10)], width=1.6)
        line([(12, 14), (12, 17)], width=1.4)
    elif name == "sun":
        circle((8, 8, 16, 16), width=1.5)
        for coords in [[(12, 2), (12, 4)], [(12, 20), (12, 22)],
                       [(2, 12), (4, 12)], [(20, 12), (22, 12)],
                       [(5, 5), (6.5, 6.5)], [(17.5, 17.5), (19, 19)],
                       [(5, 19), (6.5, 17.5)], [(17.5, 6.5), (19, 5)]]:
            line(coords, width=1.4)
    elif name == "mic":
        box((9, 2, 15, 14), radius=3, width=1.6)
        draw.arc(tuple(v * unit for v in (5, 7, 19, 20)), 0, 180,
                 fill=color, width=max(1, round(1.6 * unit)))
        line([(12, 20), (12, 23)], width=1.6)
        line([(8, 23), (16, 23)], width=1.6)
    elif name == "speaker":
        line([(3, 9), (7, 9), (12, 4), (12, 20), (7, 15), (3, 15)], closed=True)
        draw.arc(tuple(v * unit for v in (10, 6, 19, 18)), -65, 65,
                 fill=color, width=max(1, round(1.6 * unit)))
        draw.arc(tuple(v * unit for v in (10, 2, 24, 22)), -60, 60,
                 fill=color, width=max(1, round(1.6 * unit)))
    elif name == "sidebar":
        box((3, 4, 21, 20), radius=3)
        line([(9, 4), (9, 20)], width=1.5)
    elif name == "pen":
        line([(4, 20), (5, 15), (16, 4), (20, 8), (9, 19), (4, 20)], closed=True)
        line([(13, 7), (17, 11)], width=1.4)
    else:
        raise ValueError(f"Unknown icon: {name}")

    image = image.resize((size * 2, size * 2), Image.Resampling.LANCZOS)
    return image


class _PanelImage(ctk.CTkImage):
    """A scale-aware CTkImage with an explicit owning Tk interpreter.

    Stock CTkImage creates ImageTk.PhotoImage without a master, which
    selects tkinter's global default root. That may be Friday's IDE root,
    not the app-panel host. Never share this object between interpreters.
    """
    def __init__(self, master, image, size):
        self._master = master
        super().__init__(light_image=image, dark_image=image, size=(size, size))

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


class IconBank:
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

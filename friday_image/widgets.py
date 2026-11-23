"""Small CustomTkinter primitives shared by Friday's native image panel."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence

import customtkinter as ctk

from friday_chat.widgets import PanelButton, PanelScrollFrame, _OwnedTimers


class PanelLabel(_OwnedTimers, ctk.CTkLabel):
    """CTkLabel whose sizing callbacks are cancelled with its panel."""


class ImageScrollFrame(PanelScrollFrame):
    """PanelScrollFrame whose CTk-internal label also owns its timers."""
    def __init__(self, *args, **kwargs):
        module = __import__(
            "customtkinter.windows.widgets.ctk_scrollable_frame", fromlist=["CTkLabel"])
        original = module.CTkLabel
        module.CTkLabel = PanelLabel
        try:
            super().__init__(*args, **kwargs)
        finally:
            module.CTkLabel = original


def _panel_owner(widget):
    node = widget
    while getattr(node, "master", None) is not None:
        node = node.master
        if hasattr(node, "icon_bank"):
            return node
    return None


def _icon(parent, name, color, size):
    owner = _panel_owner(parent)
    if owner is None or owner.icon_bank is None:
        raise RuntimeError("Quiet Image icons require an owning panel view")
    return owner.icon_bank(name, color, size)


C = {
    "outside": "#101012",
    "background": "#151517",
    "sidebar": "#1B1B1E",
    "panel": "#1F1F23",
    "panel_alt": "#222226",
    "surface": "#28282D",
    "surface_alt": "#2E2E34",
    "hover": "#35353B",
    "selected": "#3A3934",
    "border": "#35353B",
    "border_soft": "#28282D",
    "edge": "#1D1D20",
    "text": "#E8E6E2",
    "body": "#CDCBD0",
    "secondary": "#AFADB5",
    "muted": "#8F8E98",
    "dim": "#70707A",
    "accent": "#C7C3BC",
    "accent_hover": "#DDD9D3",
    "accent_text": "#252528",
    "success": "#A8BCA5",
    "warning": "#C9B99B",
    "danger": "#CE9B9D",
    "track": "#3A3A40",
    "transparent": "transparent",
}


def _quiet_font(parent, size: float, weight: str = "normal"):
    """Tuple fonts resolve in the panel interpreter, never the default root."""
    owner = _panel_owner(parent)
    family = getattr(owner, "family", "Arial")
    return (family, int(round(size)), weight)


class Tooltip:
    """Small floating help card, matching the Quiet panels."""

    def __init__(self, widget, text: str):
        self.widget, self.text = widget, text
        self.job = None
        self.window = None
        self.owner = _panel_owner(widget)
        if self.owner is not None and hasattr(self.owner, "_tooltips"):
            self.owner._tooltips.append(self)
        widget.bind("<Enter>", self.schedule, add="+")
        for sequence in ("<Leave>", "<ButtonPress>", "<Destroy>"):
            widget.bind(sequence, self.hide, add="+")

    def schedule(self, _event=None):
        self.hide()
        self.job = self.widget.after(500, self.show)

    def show(self):
        self.job = None
        if not self.widget.winfo_exists():
            return
        self.window = ctk.CTkToplevel(self.widget)
        self.window.overrideredirect(True)
        self.window.configure(fg_color=C["surface"])
        self.window.attributes("-topmost", True)
        label = PanelLabel(self.window, text=self.text, font=("Helvetica", 11),
                             text_color=C["body"], padx=12, pady=7)
        label.pack()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.window.geometry(f"+{x}+{y}")

    def hide(self, _event=None):
        if self.job is not None:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None


def section(parent, title: str, note: str | None = None) -> ctk.CTkFrame:
    card = ctk.CTkFrame(parent, fg_color=C["panel"], corner_radius=16,
                        border_width=1, border_color=C["border_soft"])
    card.grid_columnconfigure(0, weight=1)
    header = ctk.CTkFrame(card, fg_color="transparent")
    header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 5))
    header.grid_columnconfigure(0, weight=1)
    PanelLabel(header, text=title.upper(), font=_quiet_font(parent, 10.5, "bold"),
                 text_color=C["secondary"], height=22).grid(row=0, column=0, sticky="w")
    if note:
        PanelLabel(header, text=note, font=_quiet_font(parent, 10), text_color=C["muted"],
                     wraplength=410, justify="left").grid(row=1, column=0, sticky="w", pady=(0, 3))
    return card


def icon_button(parent, name: str, command, tooltip: str = "",
                width: int = 32, size: int = 17):
    button = PanelButton(parent, text="", image=_icon(parent, name, C["secondary"], size),
                           font=_quiet_font(parent, 10),
                           command=command, width=width, height=31,
                           corner_radius=9, fg_color="transparent", hover_color=C["surface"])
    if tooltip:
        Tooltip(button, tooltip)
    return button


def accent_button(parent, text: str, command, image_name: str | None = None,
                  width: int | None = None, enabled: bool = True):
    return PanelButton(
        parent, text=text, command=command,
        image=_icon(parent, image_name, C["accent_text"], 16) if image_name else None,
        width=width, height=36, corner_radius=10, font=_quiet_font(parent, 12, "bold"),
        fg_color=C["accent"], hover_color=C["accent_hover"],
        text_color=C["accent_text"], text_color_disabled=C["muted"],
        border_width=0, state="normal" if enabled else "disabled",
    )


def choice_group(parent, title: str, choices: Sequence[str], initial: str,
                 on_change: Callable[[str], None] | None = None,
                 compact: bool = False) -> tuple[ctk.CTkFrame, str]:
    """Render selectable quiet pills; returns container and selected value.

    Access the live value through `container.selected`.
    """
    control = ctk.CTkFrame(parent, fg_color="transparent")
    control.grid_columnconfigure(0, weight=1)
    if title:
        PanelLabel(control, text=title, font=_quiet_font(parent, 10),
                     text_color=C["muted"], anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 6))
    row = ctk.CTkFrame(control, fg_color="transparent")
    row.grid(row=1 if title else 0, column=0, sticky="ew")
    control.selected = initial
    control.buttons = {}

    def select(value: str):
        control.selected = value
        for candidate, button in control.buttons.items():
            selected = candidate == value
            button.configure(fg_color=C["accent"] if selected else C["surface"],
                             text_color=C["accent_text"] if selected else C["secondary"],
                             hover_color=C["accent_hover"] if selected else C["hover"])
        if on_change:
            on_change(value)

    for column, value in enumerate(choices):
        selected = value == initial
        button = PanelButton(row, text=value, width=max(61, len(str(value)) * 7 + 20),
                               height=29 if compact else 32, corner_radius=9,
                               font=_quiet_font(parent, 10.5, "bold" if selected else "normal"),
                               fg_color=C["accent"] if selected else C["surface"],
                               text_color=C["accent_text"] if selected else C["secondary"],
                               hover_color=C["accent_hover"] if selected else C["hover"],
                               command=lambda v=value: select(v))
        button.grid(row=0, column=column, sticky="w", padx=(0, 6 if column < len(choices)-1 else 0))
        control.buttons[value] = button
    return control, initial


class ValueSlider(ctk.CTkFrame):
    def __init__(self, parent, title: str, minimum: float, maximum: float,
                 value: float, fmt: Callable[[float], str], on_change=None,
                 steps: int | None = None):
        super().__init__(parent, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.format = fmt
        self.on_change = on_change
        PanelLabel(self, text=title, font=_quiet_font(parent, 10), text_color=C["muted"],
                     anchor="w").grid(row=0, column=0, sticky="w")
        self.display = PanelLabel(self, text=fmt(value), font=_quiet_font(parent, 10, "bold"),
                                    text_color=C["secondary"], width=64, anchor="e")
        self.display.grid(row=0, column=1, sticky="e")
        self.slider = ctk.CTkSlider(self, from_=minimum, to=maximum,
                                    number_of_steps=steps if steps else int((maximum-minimum)*10),
                                    height=16, corner_radius=8, button_length=18,
                                    button_corner_radius=9,
                                    button_color=C["accent"], fg_color=C["track"],
                                    progress_color=C["accent"],
                                    command=self._update)
        self.slider.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.slider.set(value)

    def _update(self, value):
        self.display.configure(text=self.format(float(value)))
        if self.on_change:
            self.on_change(float(value))

    def get(self):
        return self.slider.get()

    def set(self, value):
        self.slider.set(value)
        self.display.configure(text=self.format(float(value)))


def divider(parent, row: int | None = None, column: int | None = None):
    line = ctk.CTkFrame(parent, height=1, corner_radius=0, fg_color=C["border_soft"])
    if row is not None:
        line.grid(row=row, column=column or 0, sticky="ew", padx=18)
    return line


def stat_pill(parent, text: str, color: str = "muted"):
    return PanelLabel(parent, text=text, font=_quiet_font(parent, 10),
                        fg_color=C["surface"], text_color=C.get(color, C["muted"]),
                        height=25, corner_radius=10, padx=10)

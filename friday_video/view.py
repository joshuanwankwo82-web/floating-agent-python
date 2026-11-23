"""Friday's native Quiet Video child view, built with CustomTkinter.

The included renderer is honest and local: it generates animated motion
studies from a brief, optional reference image, style controls, and a seed.
It does not pretend the brief is sent to an AI provider.
"""
from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
import random
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox

import customtkinter as ctk
from PIL import Image

from friday_chat.widgets import PanelButton, PanelTextbox
from .backend import LocalMotionBackend, VideoBackend
from .icons import PanelImage, VideoIconBank
from .model import (AppSettings, FORMAT_CHOICES, FPS_CHOICES, IMAGE_EXTENSIONS,
                    MOTION_CHOICES, QUALITY_CHOICES, STYLE_CHOICES,
                    ASPECT_CHOICES, VideoProject, VideoRequest, RenderJob,
                    export_project_text)
from .renderer import RenderCancelled, render_still
from .storage import EncryptedVideoStore
from .widgets import (C, PanelLabel, Tooltip, VideoScrollFrame,
                      accent_button, choice_group, divider, icon_button, section,
                      stat_pill, ValueSlider)


DEFAULT_BRIEF = "A river at dusk, moving like brushed copper"


class QuietVideoView(ctk.CTkFrame):
    """The full Quiet Video application drawn inside Friday's native panel."""

    def __init__(self, master, initial_content: str = "",
                 backend: VideoBackend | None = None):
        super().__init__(master, fg_color=C["outside"], corner_radius=0)
        self.backend = backend or LocalMotionBackend()
        self.storage = EncryptedVideoStore()
        self.state_error = None
        try:
            self.projects, self.active_id, self.settings = self.storage.load()
        except Exception as error:
            self.state_error = ("Saved state couldn't be decrypted, so this session is temporary. "
                                "The old record was left untouched.")
            self.projects = [VideoProject()]
            self.active_id = self.projects[0].id
            self.settings = AppSettings(output_dir=self.storage.default_output_dir())
        self._persistent = self.state_error is None
        if self._persistent:
            Path(self.settings.output_dir).expanduser().mkdir(parents=True, exist_ok=True, mode=0o700)

        families = set(tkfont.families(self))
        self.family = next((name for name in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "DejaVu Sans")
                            if name in families), "Arial")
        self.mono_family = next((name for name in ("Cascadia Code", "Consolas", "Menlo", "DejaVu Sans Mono")
                                 if name in families), "Courier")
        self._save_job = None
        self._preview_job = self._poll_job = self._toast_job = self._layout_job = None
        self._after_jobs: list = []
        self._toast = None
        self._dialogs = []
        self._tooltips = []
        self.icon_bank = VideoIconBank(self)
        self._closed = False
        self._preview_photo = None
        self._poster_photo = None
        self.reference_image = None
        self.seed_display = random.SystemRandom().randrange(1_000_000, 9_999_999)
        self.worker_queue: Queue = Queue()
        self.render_cancel = None
        self.render_thread = None
        self.running_job_id = None
        self.current_progress = 0.0
        self.last_preview_signature = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_shell()
        self._restore_active_project()
        if initial_content:
            self.use_prompt(initial_content, persist=False)
        self._refresh_projects()
        self._refresh_jobs()
        self.update_preview(force=True)
        self._poll_job = self._call_after(80, self._poll_render_events)
        self._layout_job = self._call_after(120, self._responsive_layout)
        self._bind_shortcuts()

    # ------------------------------------------------------------------
    # Data/state helpers
    # ------------------------------------------------------------------
    @property
    def project(self) -> VideoProject:
        found = next((project for project in self.projects if project.id == self.active_id), None)
        if found is None:
            found = self.projects[0]
            self.active_id = found.id
        return found

    def font(self, size: float, weight: str = "normal"):
        # Font tuples are resolved in this panel's interpreter; a stock
        # CTkFont would grab the global default Tk root instead.
        return (self.family, int(round(size)), weight)

    def mono(self, size: float, weight: str = "normal"):
        return (self.mono_family, int(round(size)), weight)

    def _call_after(self, milliseconds, callback):
        job = None
        def invoke():
            nonlocal job
            if job in self._after_jobs:
                self._after_jobs.remove(job)
            if not self._closed:
                callback()
        job = self.after(milliseconds, invoke)
        self._after_jobs.append(job)
        return job

    def _cancel_call(self, job):
        if job is None:
            return
        try:
            self.after_cancel(job)
        except (tk.TclError, ValueError):
            pass
        if job in self._after_jobs:
            self._after_jobs.remove(job)

    def _restore_active_project(self):
        self.prompt_box.delete("1.0", "end")
        brief = self.project.brief or DEFAULT_BRIEF
        self.prompt_box.insert("1.0", brief)
        self.char_exit.configure(text=f"{len(brief)} characters")
        latest = self.project.latest_job
        self.reference_image = None
        self._set_reference(None)
        if latest is not None:
            raw = latest.request
            self.style_group.selected = raw.get("style", self.style_group.selected)
            self._choice_select(self.style_group, raw.get("style", "Mist"))
            self._choice_select(self.aspect_group, raw.get("aspect", "16:9"))
            self._choice_select(self.motion_group, raw.get("motion", "Drift"))
            self._choice_select(self.quality_group, raw.get("quality", "Studio"))
            self._choice_select(self.format_group, str(raw.get("format", "mp4")).lower())
            self._choice_select(self.fps_group, str(raw.get("fps", 18)))
            self.duration_slider.set(float(raw.get("seconds", 4.0)))
            self.seed_entry.delete(0, "end")
            self.seed_entry.insert(0, str(raw.get("seed", self.seed_display)))
            self._set_reference(raw.get("source_image"))
        else:
            self.seed_entry.delete(0, "end")
            self.seed_entry.insert(0, str(self.seed_display))

    def use_prompt(self, text: str, persist: bool = False):
        """Fill the creative brief as an editable draft, not a submitted render."""
        content = str(text or "")
        self.prompt_box.delete("1.0", "end")
        self.prompt_box.insert("1.0", content)
        self._on_brief_changed()
        if persist:
            self._schedule_save()

    def _schedule_save(self):
        if not self._persistent or self._closed:
            return
        if self._save_job:
            self._cancel_call(self._save_job)
        self._save_job = self._call_after(350, self._save_now)

    def _save_now(self):
        self._save_job = None
        if not self._persistent or self._closed:
            return
        try:
            self.project.brief = self.prompt_box.get("1.0", "end-1c").strip()
            self.storage.save(self.projects, self.active_id, self.settings)
        except Exception as error:
            self._status(f"Couldn't save panel state: {error}", warn=True)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_shell(self):
        self.shell = ctk.CTkFrame(self, fg_color=C["background"], corner_radius=20,
                                  border_width=1, border_color=C["border_soft"])
        self.shell.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.shell.grid_columnconfigure(1, weight=1)
        self.shell.grid_rowconfigure(2, weight=1)
        self._build_header()
        ctk.CTkFrame(self.shell, height=1, corner_radius=0,
                     fg_color=C["border_soft"]).grid(row=1, column=0, columnspan=3,
                                                     sticky="ew", padx=16)
        self._build_project_rail()
        self._build_workbench()
        self._build_timing_bay()
        self._build_statusbar()

    def _build_header(self):
        header = ctk.CTkFrame(self.shell, fg_color="transparent", height=69)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", padx=18, pady=(6, 4))
        header.grid_columnconfigure(1, weight=1)
        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w", pady=(7, 8))
        mark = ctk.CTkFrame(brand, width=38, height=38, corner_radius=11,
                            fg_color=C["surface"], border_width=1,
                            border_color=C["border"])
        mark.grid(row=0, column=0, padx=(0, 10))
        mark.grid_propagate(False)
        PanelLabel(mark, text="", image=self.icon_bank("video", C["accent"], 22),
                     font=self.font(10)).place(
            relx=.5, rely=.5, anchor="center")
        PanelLabel(brand, text="quiet", font=self.font(24, "bold"),
                     text_color=C["text"]).grid(row=0, column=1)
        PanelLabel(brand, text="VIDEO", font=self.font(9, "bold"),
                     text_color=C["muted"], height=20).grid(row=0, column=2,
                                                            padx=(7, 0), sticky="s", pady=(0, 5))
        center = ctk.CTkFrame(header, fg_color="transparent")
        center.grid(row=0, column=1, sticky="w", padx=(28, 0))
        self.provider_badge = PanelLabel(
            center, text=f"  {self.backend.badge}  ",
            image=self.icon_bank("spark", C["warning"], 14), compound="left", font=self.font(10.5),
            text_color=C["warning"], fg_color=C["surface"], corner_radius=12, height=29)
        self.provider_badge.grid(row=0, column=0, sticky="w")
        Tooltip(self.provider_badge, "MP4/GIF rendering is local; prompt text never leaves this panel.")
        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=2, sticky="e")
        self.header_action_button = PanelButton(
            actions, text="Generate", image=self.icon_bank("video", C["accent_text"], 16),
            command=self.render_action, width=124, height=36, corner_radius=10,
            fg_color=C["accent"], hover_color=C["accent_hover"],
            text_color=C["accent_text"], text_color_disabled=C["muted"],
            font=self.font(11, "bold"))
        self.header_action_button.grid(row=0, column=0, padx=(0, 10))
        Tooltip(self.header_action_button, "Start or cancel the local render · Ctrl+Enter")
        icon_button(actions, "folder", self.choose_output_folder,
                    "Choose render output folder").grid(row=0, column=1, padx=(0, 7))
        icon_button(actions, "settings", self.show_output_status,
                    "Storage and renderer details").grid(row=0, column=2)

    def _build_project_rail(self):
        self.rail = ctk.CTkFrame(self.shell, fg_color=C["sidebar"], corner_radius=15,
                                 border_width=1, border_color=C["border_soft"], width=286)
        self.rail.grid(row=2, column=0, sticky="nsew", padx=(12, 0), pady=12)
        self.rail.grid_propagate(False)
        self.rail.grid_columnconfigure(0, weight=1)
        self.rail.grid_rowconfigure(3, weight=1)
        top = ctk.CTkFrame(self.rail, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=15, pady=(16, 8))
        top.grid_columnconfigure(0, weight=1)
        PanelLabel(top, text="Projects", font=self.font(19, "bold"),
                     text_color=C["text"]).grid(row=0, column=0, sticky="w")
        PanelLabel(top, text="Drafts, seeds and finished renders",
                     font=self.font(10.5), text_color=C["muted"], anchor="w").grid(
            row=1, column=0, sticky="w", pady=(1, 0))
        new_button = accent_button(top, "New", self.new_project, "plus", width=76)
        new_button.grid(row=0, column=1, rowspan=2, sticky="e")
        ctk.CTkFrame(self.rail, height=1, corner_radius=0,
                     fg_color=C["border_soft"]).grid(row=2, column=0, sticky="ew", padx=16)
        self.project_list = VideoScrollFrame(self.rail, label_font=self.font(11), fg_color="transparent",
                                                   corner_radius=0, scrollbar_button_color=C["surface"],
                                                   scrollbar_button_hover_color=C["hover"])
        self.project_list.grid(row=3, column=0, sticky="nsew", padx=10, pady=5)
        self.project_list.grid_columnconfigure(0, weight=1)
        lower = ctk.CTkFrame(self.rail, fg_color="transparent")
        lower.grid(row=4, column=0, sticky="ew", padx=15, pady=(5, 16))
        lower.grid_columnconfigure(0, weight=1)
        self.storage_line = PanelLabel(lower, text="Local storage enabled",
                                         font=self.font(10.5), text_color=C["secondary"],
                                         anchor="w", justify="left", wraplength=225)
        self.storage_line.grid(row=0, column=0, sticky="w")
        self.storage_hint = PanelLabel(lower, text="History contains project settings and output paths; no source images are copied.",
                                         font=self.font(9.5), text_color=C["dim"],
                                         anchor="w", justify="left", wraplength=225)
        self.storage_hint.grid(row=1, column=0, sticky="w", pady=(3, 0))

    def _build_workbench(self):
        self.center = VideoScrollFrame(self.shell, label_font=self.font(11), fg_color="transparent",
                                             corner_radius=0, scrollbar_button_color=C["surface"],
                                             scrollbar_button_hover_color=C["hover"])
        self.center.grid(row=2, column=1, sticky="nsew", padx=18, pady=12)
        self.center.grid_columnconfigure(0, weight=1)

        brief = section(self.center, "Creative brief",
                        "Prompt text is used as the title and art seed in the local renderer. No AI request is made.")
        brief.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        brief.grid_columnconfigure(0, weight=1)
        self.prompt_box = PanelTextbox(
            brief, width=420, height=142, corner_radius=10, border_width=1,
            fg_color=C["background"], border_color=C["border"], text_color=C["body"],
            font=self.font(13), wrap="word")
        self.prompt_box.grid(row=1, column=0, columnspan=3, sticky="ew", padx=18, pady=(0, 8))
        self.prompt_box.bind("<KeyRelease>", self._on_brief_changed)
        prompt_actions = ctk.CTkFrame(brief, fg_color="transparent")
        prompt_actions.grid(row=2, column=0, columnspan=3, sticky="ew", padx=18, pady=(0, 16))
        prompt_actions.grid_columnconfigure(0, weight=1)
        self.char_exit = PanelLabel(prompt_actions, text="0 characters",
                                      font=self.font(10), text_color=C["muted"])
        self.char_exit.grid(row=0, column=0, sticky="w")
        suggestion = PanelButton(prompt_actions, text="Softer lighting", width=128,
                                   height=28, corner_radius=8, font=self.font(10),
                                   fg_color=C["surface"], hover_color=C["hover"],
                                   text_color=C["secondary"],
                                   command=lambda: self.apply_suggestion(
                                       "Slow perspective, soft edge light, quiet atmosphere"))
        suggestion.grid(row=0, column=1, sticky="e", padx=(5, 0))
        Tooltip(suggestion, "Add a quiet cinematography note without sending anything.")

        source = section(self.center, "Reference image",
                         "Optional. If selected, the video uses pan/zoom of your local image; it is never copied or uploaded.")
        source.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        source.grid_columnconfigure(0, weight=1)
        row = ctk.CTkFrame(source, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
        row.grid_columnconfigure(0, weight=1)
        self.image_chip = PanelLabel(row, text="No image selected",
                                       image=self.icon_bank("image", C["muted"], 15), compound="left",
                                       font=self.font(10.5), text_color=C["muted"],
                                       fg_color=C["background"], corner_radius=9, height=34,
                                       anchor="w", padx=12)
        self.image_chip.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.attach_button = PanelButton(
            row, text="Choose…", width=96, height=34, corner_radius=9,
            font=self.font(11), fg_color=C["surface"], hover_color=C["hover"],
            text_color=C["body"], command=self.choose_reference_image)
        self.attach_button.grid(row=0, column=1)
        self.clear_image_button = icon_button(row, "close", self.clear_reference_image,
                                              "Remove reference image", width=34, size=14)
        self.clear_image_button.grid(row=0, column=2, padx=(8, 0))
        self.clear_image_button.grid_remove()

        art = section(self.center, "Art direction", "Every control below affects the actual local renderer output.")
        art.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        art.grid_columnconfigure(0, weight=1)
        choices = ctk.CTkFrame(art, fg_color="transparent")
        choices.grid(row=1, column=0, sticky="ew", padx=18, pady=(4, 8))
        choices.grid_columnconfigure(1, weight=1)
        self.style_group, _ = choice_group(choices, "Style", STYLE_CHOICES, "Mist",
                                           lambda _v: self._widgets_changed())
        self.style_group.grid(row=0, column=0, sticky="w", padx=(0, 24), pady=(0, 14))
        self.motion_group, _ = choice_group(choices, "Camera motion", MOTION_CHOICES, "Drift",
                                            lambda _v: self._widgets_changed())
        self.motion_group.grid(row=0, column=1, sticky="w", pady=(0, 14))
        self.aspect_group, _ = choice_group(choices, "Aspect", ASPECT_CHOICES, "16:9",
                                            lambda _v: self._widgets_changed())
        self.aspect_group.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(0, 14))
        fps_values = [str(v) for v in FPS_CHOICES]
        self.fps_group, _ = choice_group(choices, "Rate", fps_values, "18",
                                         lambda _v: self._widgets_changed())
        self.fps_group.grid(row=1, column=1, sticky="w", pady=(0, 14))
        self.quality_group, _ = choice_group(choices, "Quality", QUALITY_CHOICES, "Studio",
                                             lambda _v: self._widgets_changed())
        self.quality_group.grid(row=2, column=0, sticky="w", padx=(0, 24), pady=(0, 14))
        self.format_group, _ = choice_group(choices, "Output", FORMAT_CHOICES, "mp4",
                                            lambda _v: self._widgets_changed())
        self.format_group.grid(row=2, column=1, sticky="w", pady=(0, 14))

        settings_row = ctk.CTkFrame(art, fg_color="transparent")
        settings_row.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))
        settings_row.grid_columnconfigure(0, weight=1)
        settings_row.grid_columnconfigure(1, weight=0)
        self.duration_slider = ValueSlider(settings_row, "Duration", 1, 8, 4.0,
                                            lambda v: f"{v:.1f} s", self._widgets_changed, steps=70)
        self.duration_slider.grid(row=0, column=0, sticky="ew", padx=(0, 22))
        seed_row = ctk.CTkFrame(settings_row, fg_color="transparent")
        seed_row.grid(row=0, column=1, sticky="e")
        PanelLabel(seed_row, text="Art seed", font=self.font(10),
                     text_color=C["muted"], anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 5))
        seed_controls = ctk.CTkFrame(seed_row, fg_color="transparent")
        seed_controls.grid(row=1, column=0, sticky="e")
        self.seed_entry = ctk.CTkEntry(seed_controls, width=122, height=34,
                                       font=self.mono(11), fg_color=C["background"],
                                       border_color=C["border"], text_color=C["body"],
                                       corner_radius=9)
        self.seed_entry.grid(row=0, column=0)
        self.seed_entry.insert(0, str(self.seed_display))
        self.seed_entry.bind("<KeyRelease>", lambda _e: self._widgets_changed())
        icon_button(seed_controls, "refresh", self.new_seed, "New random art seed",
                    width=34, size=15).grid(row=0, column=1, padx=(6, 0))

        cta = ctk.CTkFrame(self.center, fg_color="transparent")
        cta.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        cta.grid_columnconfigure(0, weight=1)
        self.generate_button = PanelButton(
            cta, text="Generate video locally", image=self.icon_bank("video", C["accent_text"], 19),
            command=self.start_render, height=50, corner_radius=13,
            fg_color=C["accent"], hover_color=C["accent_hover"],
            text_color=C["accent_text"], text_color_disabled=C["muted"],
            font=self.font(13, "bold"))
        self.generate_button.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.cancel_button = PanelButton(
            cta, text="Cancel", image=self.icon_bank("stop", C["danger"], 14), width=108,
            command=self.cancel_render, height=50, corner_radius=13,
            fg_color=C["surface"], hover_color=C["hover"], text_color=C["body"],
            font=self.font(12), state="disabled")
        self.cancel_button.grid(row=0, column=1)

        self._build_preview()

    def _build_preview(self):
        preview = section(self.center, "Preview and console", "Posters refresh without starting a render; console lines report real encoder progress.")
        preview.grid(row=4, column=0, sticky="nsew")
        preview.grid_columnconfigure(0, weight=1)
        preview.grid_rowconfigure(2, weight=1)
        header = ctk.CTkFrame(preview, fg_color="transparent")
        header.grid(row=1, column=0, sticky="ew", padx=18, pady=(4, 3))
        header.grid_columnconfigure(0, weight=1)
        self.preview_meta = PanelLabel(header, text="Ready", font=self.mono(9.5),
                                         text_color=C["muted"], anchor="w")
        self.preview_meta.grid(row=0, column=0, sticky="w")
        self.refresh_preview_button = icon_button(header, "refresh", lambda: self.update_preview(force=True),
                                                  "Redraw poster preview", width=32, size=15)
        self.refresh_preview_button.grid(row=0, column=1, sticky="e")
        self.preview_card = ctk.CTkFrame(preview, fg_color=C["background"], corner_radius=12,
                                         border_width=1, border_color=C["border_soft"],
                                         height=410)
        self.preview_card.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 12))
        self.preview_card.grid_columnconfigure(0, weight=1)
        self.preview_card.grid_rowconfigure(0, weight=1)
        self.preview_label = PanelLabel(self.preview_card, text="Poster preview appears here",
                                          font=self.font(13), text_color=C["dim"])
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self.console = PanelTextbox(preview, height=86, corner_radius=10, border_width=1,
                                     fg_color=C["background"], border_color=C["border_soft"],
                                     text_color=C["muted"], font=self.mono(10))
        self.console.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 16))
        self.console.insert("1.0", "Local rendering engine ready. Connect a provider-backed renderer later in code if needed; this panel never fakes one.\n")
        self.console.configure(state="disabled")

    def _build_timing_bay(self):
        self.timing = ctk.CTkFrame(self.shell, fg_color=C["sidebar"], corner_radius=15,
                                   border_width=1, border_color=C["border_soft"], width=310)
        self.timing.grid(row=2, column=2, sticky="nsew", padx=(0, 12), pady=12)
        self.timing.grid_propagate(False)
        self.timing.grid_columnconfigure(0, weight=1)
        self.timing.grid_rowconfigure(3, weight=1)
        PanelLabel(self.timing, text="Render queue", font=self.font(19, "bold"),
                     text_color=C["text"]).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 0))
        PanelLabel(self.timing, text="Real files only · no hidden uploads",
                     font=self.font(10.5), text_color=C["muted"], anchor="w").grid(
            row=1, column=0, sticky="w", padx=18, pady=(0, 11))
        ctk.CTkFrame(self.timing, height=1, corner_radius=0,
                     fg_color=C["border_soft"]).grid(row=2, column=0, sticky="ew", padx=17)
        self.queue_list = VideoScrollFrame(self.timing, label_font=self.font(11), fg_color="transparent",
                                                 corner_radius=0, scrollbar_button_color=C["surface"],
                                                 scrollbar_button_hover_color=C["hover"])
        self.queue_list.grid(row=3, column=0, sticky="nsew", padx=10, pady=6)
        self.queue_list.grid_columnconfigure(0, weight=1)
        note = ctk.CTkFrame(self.timing, fg_color=C["surface"], corner_radius=11)
        note.grid(row=4, column=0, sticky="ew", padx=15, pady=(5, 15))
        note.grid_columnconfigure(0, weight=1)
        PanelLabel(note, text="", image=self.icon_bank("info", C["warning"], 17),
                     font=self.font(10)).grid(
            row=0, column=0, padx=12, pady=12, sticky="ne")
        PanelLabel(note, text="This panel renders the configured motion template locally. AI video generation requires a future, explicit backend integration.",
                     font=self.font(10.2), text_color=C["secondary"], justify="left",
                     wraplength=220, anchor="w").grid(row=0, column=1, padx=(0, 12), pady=10)

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self.shell, fg_color="transparent", height=34)
        bar.grid(row=3, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 10))
        bar.grid_columnconfigure(0, weight=1)
        self.status = PanelLabel(bar, text="Ready", font=self.mono(10.5),
                                   text_color=C["muted"], anchor="w")
        self.status.grid(row=0, column=0, sticky="w")
        version = PanelLabel(bar, text="QUIET VIDEO · LOCAL UI", font=self.font(9, "bold"),
                               text_color=C["dim"], anchor="e")
        version.grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------------
    # Choice/state change handlers
    # ------------------------------------------------------------------
    def _choice_select(self, control, value):
        if value in getattr(control, "buttons", {}):
            control.selected = value
            for candidate, button in control.buttons.items():
                selected = candidate == value
                button.configure(fg_color=C["accent"] if selected else C["surface"],
                                 text_color=C["accent_text"] if selected else C["secondary"],
                                 hover_color=C["accent_hover"] if selected else C["hover"])

    def _on_brief_changed(self, _event=None):
        brief = self.prompt_box.get("1.0", "end-1c")
        self.char_exit.configure(text=f"{len(brief)} characters")
        self.project.touch(brief)
        self.last_preview_signature = None
        self._schedule_save()
        self._schedule_preview()

    def _widgets_changed(self, *_args):
        self.last_preview_signature = None
        self._schedule_save()
        self._schedule_preview()

    def _schedule_preview(self):
        if self._preview_job:
            self._cancel_call(self._preview_job)
        self._preview_job = self._call_after(180, self.update_preview)

    def apply_suggestion(self, text: str):
        box = self.prompt_box
        current = box.get("1.0", "end-1c").rstrip()
        insertion = ("\n\n" if current else "") + text
        box.insert("end", insertion)
        self._on_brief_changed()

    def new_seed(self):
        self.seed_display = random.SystemRandom().randrange(1_000_000, 9_999_999)
        self.seed_entry.delete(0, "end")
        self.seed_entry.insert(0, str(self.seed_display))
        self._widgets_changed()

    def choose_reference_image(self):
        suffixes = " ".join("*" + ext for ext in sorted(IMAGE_EXTENSIONS))
        path = filedialog.askopenfilename(
            parent=self, title="Choose a local reference image",
            filetypes=(("Images", suffixes), ("All files", "*.*")))
        if path:
            self._set_reference(path)
            self._widgets_changed()

    def clear_reference_image(self):
        self._set_reference(None)
        self._widgets_changed()

    def _set_reference(self, path: str | None):
        self.reference_image = path
        if path:
            name = Path(path).name
            self.image_chip.configure(
                text=f"{name[:31]}{'…' if len(name) > 31 else ''}",
                text_color=C["body"], image=self.icon_bank("image", C["secondary"], 16))
            Tooltip(self.image_chip, str(Path(path).expanduser()))
            self.clear_image_button.grid()
        else:
            self.image_chip.configure(text="No image selected", text_color=C["muted"],
                                      image=self.icon_bank("image", C["muted"], 15))
            self.clear_image_button.grid_remove()

    # ------------------------------------------------------------------
    # Local preview rendering
    # ------------------------------------------------------------------
    def _request_from_controls(self) -> VideoRequest:
        try:
            seconds = float(self.duration_slider.get())
        except Exception:
            seconds = 4.0
        try:
            seed = int(self.seed_entry.get().strip())
        except ValueError as error:
            raise ValueError("The art seed must be a whole number.") from error
        return VideoRequest(
            prompt=self.prompt_box.get("1.0", "end-1c").strip(),
            style=self.style_group.selected,
            aspect=self.aspect_group.selected,
            quality=self.quality_group.selected,
            seconds=seconds,
            fps=int(self.fps_group.selected),
            motion=self.motion_group.selected,
            seed=seed,
            source_image=self.reference_image,
            format=self.format_group.selected.lower(),
        ).validated()

    def update_preview(self, force: bool = False):
        self._preview_job = None
        try:
            request = self._request_from_controls()
        except ValueError as error:
            self.preview_label.configure(text=str(error), image=None)
            self._preview_photo = None
            return
        signature = (request.prompt, request.style, request.aspect, request.quality,
                     request.motion, request.seed, request.source_image)
        if not force and signature == self.last_preview_signature:
            return
        self.last_preview_signature = signature
        width, height = self._preview_bounds()
        try:
            still = render_still(request, (width, height))
        except Exception as error:
            self.preview_label.configure(text=f"Preview unavailable: {error}", image=None)
            self._preview_photo = None
            return
        self._preview_photo = PanelImage(self, still, (width, height))
        self.preview_label.configure(text="", image=self._preview_photo)
        self.preview_meta.configure(
            text=f"{request.aspect} · {request.fps} fps · {request.frame_count} frames · local poster")

    def _preview_bounds(self) -> tuple[int, int]:
        width = max(360, min(720, self.preview_card.winfo_width() - 10))
        height = max(230, min(410, self.preview_card.winfo_height() - 10))
        aspect = self.aspect_group.selected
        if aspect == "1:1":
            side = min(width, height)
            return _even(shrink=side), _even(shrink=side)
        if aspect == "9:16":
            return _even(shrink=min(width, int(height * 9 / 16))), _even(shrink=height)
        return _even(shrink=width), _even(shrink=min(height, int(width * 9 / 16)))

    # ------------------------------------------------------------------
    # Project rail
    # ------------------------------------------------------------------
    def _refresh_projects(self):
        for child in self.project_list.winfo_children():
            child.destroy()
        for index, project in enumerate(self.projects):
            selected = project.id == self.active_id
            card = ctk.CTkFrame(self.project_list, fg_color=C["surface"] if selected else "transparent",
                                corner_radius=12, border_width=1,
                                border_color=C["border"] if selected else C["border_soft"])
            card.grid(row=index, column=0, sticky="ew", padx=5, pady=4)
            card.grid_columnconfigure(0, weight=1)
            count = len(project.jobs)
            finished = sum(1 for job in project.jobs if job.status == "succeeded")
            title = PanelButton(card, text=project.title if len(project.title) < 28 else project.title[:27] + "…",
                                  image=self.icon_bank("film", C["secondary" if selected else "muted"], 16),
                                  anchor="w", font=self.font(11, "bold" if selected else "normal"),
                                  text_color=C["text"] if selected else C["muted"],
                                  fg_color="transparent", hover_color=C["hover"],
                                  command=lambda key=project.id: self.select_project(key))
            title.grid(row=0, column=0, sticky="ew", padx=(10, 5), pady=(10, 0))
            note = PanelLabel(card, text=f"{finished}/{count} renders" if count else "No renders yet",
                                font=self.font(9.5), text_color=C["dim"], anchor="w")
            note.grid(row=1, column=0, sticky="w", padx=34, pady=(0, 9))
            icon_button(card, "trash", lambda key=project.id: self.delete_project(key),
                        "Delete this project", width=29, size=14).grid(row=0, column=1,
                                                                                rowspan=2, padx=(0, 5), sticky="e")
        self.storage_line.configure(
            text=("Temporary session; history won't overwrite a corrupt record"
                  if not self._persistent else "State: Friday encrypted memory"))

    def select_project(self, key: str):
        if self.running_job_id:
            self._status("Wait for the active render or cancel it before switching projects.", warn=True)
            return
        if key == self.active_id:
            return
        self.active_id = key
        self._restore_active_project()
        self._refresh_projects()
        self._refresh_jobs()
        self.update_preview(force=True)
        self._save_now()

    def new_project(self):
        if self.running_job_id:
            self._status("Finish or cancel the current render first.", warn=True)
            return
        project = VideoProject()
        self.projects.insert(0, project)
        self.active_id = project.id
        self._restore_active_project()
        self._refresh_projects()
        self._refresh_jobs()
        self.update_preview(force=True)
        self._save_now()
        self._status("New untitled film project created.")

    def delete_project(self, key: str):
        project = next((project for project in self.projects if project.id == key), None)
        if project is None:
            return
        if any(job.running for job in project.jobs):
            self._status("Cancel this project's running render before deleting it.", warn=True)
            return
        if not messagebox.askyesno("Delete project",
                                   f"Delete “{project.title}” and its saved render history?\n\n"
                                   "Rendered files on disk are kept.", parent=self):
            return
        self.projects = [item for item in self.projects if item.id != key]
        if not self.projects:
            self.projects = [VideoProject()]
        self.active_id = self.projects[0].id
        self._restore_active_project()
        self._refresh_projects()
        self._refresh_jobs()
        self._save_now()

    # ------------------------------------------------------------------
    # Render execution and queue
    # ------------------------------------------------------------------
    def render_action(self):
        """Header keeps the primary action visible while either pane is scrolled."""
        if self.running_job_id:
            self.cancel_render()
        else:
            self.start_render()

    def start_render(self):
        if self.running_job_id:
            self._status("A render is already running.", warn=True)
            return
        try:
            request = self._request_from_controls()
        except ValueError as error:
            self._status(str(error), warn=True)
            return
        output_dir = Path(self.settings.output_dir).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except Exception as error:
            self._status(f"Couldn't prepare the output folder: {error}", warn=True)
            return
        self.project.touch(request.prompt)
        job = RenderJob(request=request.wire(), frames_total=request.frame_count)
        self.project.add_job(job)
        self.running_job_id = job.id
        self.render_cancel = threading.Event()
        self.current_progress = 0.0
        self.generate_button.configure(state="disabled", text="Rendering…")
        self.header_action_button.configure(text="Cancel", image=self.icon_bank("stop", C["accent_text"], 14),
                                            state="normal")
        self.cancel_button.configure(state="normal")
        self._console(f"Started {request.format.upper()} · {request.frame_count} frames · {request.seed}")
        self._refresh_projects()
        self._refresh_jobs()
        self._status("Local renderer running…")
        self._save_now()
        target_dir = Path(self.settings.output_dir).expanduser()
        self.render_thread = threading.Thread(
            target=self._render_worker, args=(job, request, target_dir), daemon=True,
            name="quiet-video-renderer")
        self.render_thread.start()

    def _render_worker(self, job: RenderJob, request: VideoRequest, output_dir: Path):
        def progress(done, total):
            self.worker_queue.put(("progress", job.id, done, total))
        try:
            result = self.backend.render(request, output_dir,
                                         progress_callback=progress,
                                         cancel_event=self.render_cancel)
            self.worker_queue.put(("success", job.id, result))
        except RenderCancelled:
            self.worker_queue.put(("cancelled", job.id, None))
        except Exception as error:
            self.worker_queue.put(("error", job.id, str(error)))

    def _poll_render_events(self):
        if self._closed:
            return
        try:
            while True:
                event = self.worker_queue.get_nowait()
                self._handle_render_event(event)
        except Empty:
            pass
        self._poll_job = self._call_after(75, self._poll_render_events)

    def _find_job(self, job_id: str) -> RenderJob | None:
        for project in self.projects:
            for job in project.jobs:
                if job.id == job_id:
                    return job
        return None

    def _handle_render_event(self, event):
        kind = event[0]
        job = self._find_job(event[1]) or self.project.latest_job
        if job is None:
            return
        timing = f"{job.frames_done}/{job.frames_total}"
        if kind == "progress":
            _kind, _job_id, done, total = event
            job.progress = 0.0 if total <= 0 else done / total
            job.frames_done, job.frames_total = done, total
            job.status = "running"
            if done == total or done % max(1, total // 10) == 0:
                self._console(f"{done}/{total} frames rendered")
        else:
            self.current_progress = 1.0 if kind == "success" else job.progress
            if kind == "success":
                result = event[2]
                job.mark("succeeded", progress=1.0, frames_done=result.frames_written,
                         output_path=result.output_path, poster_path=result.poster_path,
                         elapsed_seconds=result.elapsed_seconds, size_bytes=result.size_bytes)
                self._console(f"Finished {Path(result.output_path).name} · "
                              f"{result.size_bytes/1024:.1f} KB · {result.elapsed_seconds:.1f}s")
                self._show_poster(result.poster_path)
                self._status("Video rendered locally.")
            elif kind == "cancelled":
                job.mark("cancelled", progress=0.0, error="Cancelled by you.")
                self._console("Render cancelled. No partial output was kept.")
                self._status("Render cancelled.", warn=True)
            else:
                _kind, _job_id, message = event
                job.mark("failed", error=message)
                self._console(f"Render failed: {message}")
                self._status(message, warn=True)
            self.running_job_id = None
            self.render_cancel = None
            self.render_thread = None
            self.generate_button.configure(state="normal", text="Generate video locally")
            self.header_action_button.configure(text="Generate",
                                                image=self.icon_bank("video", C["accent_text"], 16),
                                                state="normal")
            self.cancel_button.configure(state="disabled")
            self._refresh_projects()
            self._refresh_jobs()
            self._save_now()
            return
        self._refresh_jobs()

    def cancel_render(self):
        if not self.running_job_id or self.render_cancel is None:
            return
        self.render_cancel.set()
        self.cancel_button.configure(text="Cancelling…", state="disabled")
        self.header_action_button.configure(text="Cancelling…", state="disabled")
        self._status("Cancelling current render…", warn=True)

    # ------------------------------------------------------------------
    # Render list/actions
    # ------------------------------------------------------------------
    def _refresh_jobs(self):
        for child in self.queue_list.winfo_children():
            child.destroy()
        jobs = self.project.jobs
        if not jobs:
            empty = ctk.CTkFrame(self.queue_list, fg_color="transparent")
            empty.grid(row=0, column=0, sticky="ew", pady=(22, 0))
            PanelLabel(empty, text="", image=self.icon_bank("clock", C["dim"], 38),
                         font=self.font(10)).pack(pady=(0, 10))
            PanelLabel(empty, text="No renders yet", font=self.font(15, "bold"),
                         text_color=C["secondary"]).pack()
            PanelLabel(empty, text="Configure the panel and start a local render.",
                         font=self.font(10), text_color=C["muted"], wraplength=230).pack(pady=(4, 0))
            return
        for row, job in enumerate(jobs):
            card = ctk.CTkFrame(self.queue_list, fg_color=C["surface"] if job.id == self.running_job_id else C["panel"],
                                corner_radius=11, border_width=1,
                                border_color=C["border"] if job.running else C["border_soft"])
            card.grid(row=row, column=0, sticky="ew", padx=5, pady=4)
            card.grid_columnconfigure(0, weight=1)
            title = job.title
            PanelLabel(card, text=title if len(title) <= 30 else title[:29] + "…",
                         font=self.font(11, "bold"), text_color=C["body"],
                         anchor="w", justify="left").grid(row=0, column=0, columnspan=4,
                                                            sticky="ew", padx=12, pady=(11, 2))
            status_color = {"succeeded": "success", "failed": "danger", "cancelled": "danger",
                            "running": "warning", "queued": "warning"}.get(job.status, "muted")
            pill = stat_pill(card, f"{job.status}", status_color)
            pill.grid(row=1, column=0, sticky="w", padx=12, pady=(1, 8))
            request = job.request
            PanelLabel(card, text=f"{request.get('style','Mist')} · {request.get('aspect','16:9')} · "
                                    f"{request.get('format','mp4')} · {request.get('fps','?')} fps",
                            font=self.font(9.5), text_color=C["muted"], anchor="w").grid(
                row=1, column=1, columnspan=3, sticky="w", padx=(82, 11), pady=(1, 8))
            if job.running:
                bar = ctk.CTkProgressBar(card, progress_color=C["accent"],
                                         fg_color=C["track"], corner_radius=3, height=7)
                bar.grid(row=2, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 12))
                bar.set(job.progress)
            elif job.output_path:
                actions = ctk.CTkFrame(card, fg_color="transparent")
                actions.grid(row=3, column=0, columnspan=4, sticky="e", padx=10, pady=(0, 10))
                icon_button(actions, "eye", lambda p=job.poster_path: self._open_path(p),
                            "Open poster", width=28, size=15).grid(row=0, column=0, padx=2)
                icon_button(actions, "open", lambda p=job.output_path: self._open_path(p),
                            "Open rendered video", width=28, size=15).grid(row=0, column=1, padx=2)
                icon_button(actions, "folder", lambda p=job.output_path: self._open_folder(p),
                            "Reveal output folder", width=28, size=15).grid(row=0, column=2, padx=2)
                icon_button(actions, "copy", lambda p=job.output_path: self._copy_to_clipboard(p),
                            "Copy file path", width=28, size=15).grid(row=0, column=3, padx=2)
            elif job.error:
                PanelLabel(card, text=job.error, font=self.font(9.5),
                             text_color=C["danger"], anchor="w", justify="left",
                             wraplength=230).grid(row=2, column=0, columnspan=4,
                                                   sticky="ew", padx=12, pady=(0, 11))

    def _show_poster(self, poster_path: str | None):
        if not poster_path:
            return
        poster = Path(poster_path)
        if not poster.is_file():
            return
        try:
            width, height = self._preview_bounds()
            image = Image.open(poster).convert("RGB")
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (width, height), tuple(int(C["background"][i:i+2], 16) for i in (1, 3, 5)))
            canvas.paste(image, ((width-image.width)//2, (height-image.height)//2))
            self._poster_photo = PanelImage(self, canvas, (width, height))
            self.preview_label.configure(text="", image=self._poster_photo)
            self.preview_meta.configure(text=f"Latest result · {poster.name}")
        except Exception as error:
            self._status(f"Poster couldn't load: {error}", warn=True)

    def _console(self, line: str):
        self.console.configure(state="normal")
        self.console.insert("end", line.rstrip() + "\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    # ------------------------------------------------------------------
    # File/system actions
    # ------------------------------------------------------------------
    def choose_output_folder(self):
        path = filedialog.askdirectory(parent=self, title="Choose render output folder",
                                       initialdir=self.settings.output_dir)
        if path:
            self.settings.output_dir = str(Path(path).expanduser())
            Path(self.settings.output_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
            self._schedule_save()
            self._status(f"Render destination changed to {self.settings.output_dir}")

    def show_output_status(self):
        messagebox.showinfo(
            "Quiet Video storage",
            "Friday stores project metadata and rendered file paths in his encrypted local kv memory.\n\n"
            f"Render destination:\n{Path(self.settings.output_dir).expanduser()}\n\n"
            f"Panel history:\n{self.settings.state_path or 'Friday encrypted memory'}\n\n"
            "Reference images are not copied into state, and no provider credentials are used.",
            parent=self)

    def _open_path(self, path: str | None):
        if not path:
            return
        target = Path(path).expanduser()
        if not target.exists():
            self._status("That file no longer exists.", warn=True)
            return
        try:
            if sys.platform.startswith("win"):
                import os
                os.startfile(str(target))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as error:
            self._status(f"Couldn't open the file: {error}", warn=True)

    def _open_folder(self, path: str | None):
        if path:
            self._open_path(str(Path(path).expanduser().parent))

    def _copy_to_clipboard(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self._status("Path copied to clipboard.")

    def export_notes(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Export project notes", defaultextension=".txt",
            filetypes=(("UTF-8 text", "*.txt"), ("All files", "*.*")))
        if not path:
            return
        try:
            Path(path).expanduser().write_text(export_project_text(self.project),
                                               encoding="utf-8")
            self._status(f"Project notes exported to {path}")
        except Exception as error:
            self._status(f"Couldn't export notes: {error}", warn=True)

    # ------------------------------------------------------------------
    # Feedback/layout/lifecycle
    # ------------------------------------------------------------------
    def _status(self, text: str, warn: bool = False):
        self.status.configure(text=text, text_color=C["danger"] if warn else C["muted"])

    def _responsive_layout(self):
        # Hide the right inspector first on smaller screens; keep the creator workbench usable.
        width = self.winfo_width()
        if width < 1180 and self.timing.winfo_manager():
            self.timing.grid_remove()
        elif width >= 1180 and not self.timing.winfo_manager():
            self.timing.grid()
        self._layout_job = self._call_after(500, self._responsive_layout)

    def _bind_shortcuts(self):
        modifier = "Command" if sys.platform == "darwin" else "Control"
        self.bind(f"<{modifier}-Return>", lambda _e: self.start_render())
        self.bind(f"<{modifier}-N>", lambda _e: self.new_project())

    def _close(self):
        """Stop UI work, mark an interrupted render honestly, and persist."""
        if self._closed:
            return
        if self.running_job_id and self.render_cancel is not None:
            self.render_cancel.set()
            job = self._find_job(self.running_job_id)
            if job is not None and job.running:
                job.mark("cancelled", progress=job.progress,
                         error="Panel closed before the local renderer finished.")
        self._save_now()
        self._closed = True
        for job in list(self._after_jobs):
            self._cancel_call(job)
        for tooltip in list(self._tooltips):
            try:
                tooltip.hide()
            except tk.TclError:
                pass
        for dialog in list(getattr(self, "_dialogs", [])):
            try:
                dialog.destroy()
            except tk.TclError:
                pass
    def destroy(self):
        self._close()
        try:
            super().destroy()
        except tk.TclError:
            pass
        # Native image rasters are deleted only after every widget-owned
        # sizing callback above has been cancelled, preventing CTk's
        # private tk.Label from referencing a freed pyimage name.
        self.icon_bank.clear()
        for image in (self._preview_photo, self._poster_photo):
            if hasattr(image, "clear"):
                image.clear()
        self._preview_photo = None
        self._poster_photo = None


def _even(shrink: int) -> int:
    shrink = max(2, int(shrink))
    return shrink if shrink % 2 == 0 else shrink + 1

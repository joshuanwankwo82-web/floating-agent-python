"""Friday's native Quiet Image child view, built with CustomTkinter.

The panel is cloud-only: it generates real AI images through the provider
the user configures in Settings -> Image Generation (choose per render in
the ENGINE dropdown). With no usable key it shows honest setup guidance --
it never renders a local stand-in image.
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
from .backend import ImageBackend
from .icons import ImageIconBank, PanelImage
from .model import (ASPECT_CHOICES, COMPOSITION_CHOICES, FORMAT_CHOICES,
                    IMAGE_EXTENSIONS, QUALITY_CHOICES, STYLE_CHOICES,
                    AppSettings, ImageJob, ImageProject, ImageRequest,
                    export_project_text)
from .providers import NoProviderBackend
from .renderer import RenderCancelled
from .storage import EncryptedImageStore
from .widgets import (C, ImageScrollFrame, PanelLabel, Tooltip, accent_button,
                      choice_group, icon_button, section, stat_pill,
                      ValueSlider)


DEFAULT_BRIEF = "An evening garden held in glass"


class QuietImageView(ctk.CTkFrame):
    """The full Quiet Image application drawn inside Friday's native panel."""

    # ------------------------------------------------------------------
    # Backend plumbing: an optional Settings-aware factory lets the panel
    # re-resolve its backend right before every render, so a key added (or
    # removed) in Settings is picked up without any restart. An explicitly
    # injected backend (tests/integrators) always wins over the factory.
    # ------------------------------------------------------------------
    @property
    def backend(self):
        return self._backend

    @backend.setter
    def backend(self, value):
        self._backend = value
        self._backend_is_custom = True

    def _resolve_factory_backend(self, entry_id=None):
        if self.backend_factory is None:
            return NoProviderBackend()
        try:
            try:
                resolved = self.backend_factory(entry_id)
            except TypeError:
                # Back-compat with a zero-argument factory.
                resolved = self.backend_factory()
        except Exception:  # noqa: BLE001 -- never break the panel over settings
            return NoProviderBackend()
        return resolved or NoProviderBackend()

    def refresh_provider(self):
        """Adopt the backend Settings + the engine picker currently
        describe (no-op for an explicitly injected backend)."""
        if self._backend_is_custom or self.backend_factory is None:
            return self.backend
        self._refresh_engine_menu()
        self._backend = self._resolve_factory_backend(self._engine_selected_id())
        self._refresh_backend_badge()
        return self.backend

    # ------------------------------------------------------------------
    # Engine picker: choose WHICH Settings provider generates the next
    # image -- one provider ("Select provider" mode) or two side by side
    # ("Side by side" mode). Choices persist in Friday's encrypted state;
    # "Automatic" keeps the first-usable-entry behaviour.
    # ------------------------------------------------------------------
    def _engine_options(self) -> list:
        options = [("Automatic (first working provider)", "auto")]
        if self.engine_lister is not None:
            try:
                for item in (self.engine_lister() or []):
                    label = item.get("label") or item.get("id") or "provider"
                    if item.get("usable"):
                        options.append((label, item.get("id")))
                    else:
                        options.append((f"⚠ {label}", item.get("id")))
            except Exception:  # noqa: BLE001 -- picker degrades to automatic
                pass
        return options

    def _refresh_engine_menu(self):
        if getattr(self, "engine_menu", None) is None:
            return
        options = self._engine_options()
        self._engine_choice_map = dict(options)
        labels = list(self._engine_choice_map.keys())
        compare = bool(getattr(self.settings, "compare", False))
        for menu, setting_name, fallback in (
                (self.engine_menu, "engine", "auto"),
                (getattr(self, "engine_b_menu", None), "engine_b",
                 "auto")):
            if menu is None:
                continue
            menu.configure(values=labels)
            current = getattr(self.settings, setting_name, fallback) or fallback
            if compare and setting_name == "engine_b" and \
                    current == getattr(self.settings, "engine", "auto"):
                # Side-by-side gains nothing from the same engine twice;
                # suggest the next distinct option.
                distinct = next((key for _lbl, key in options
                                 if key != current), None)
                if distinct is not None:
                    current = distinct
            label = next((lbl for lbl, key in options if key == current),
                         labels[0])
            menu.set(label)
        if getattr(self, "engine_b_label", None) is not None:
            if compare:
                self.engine_b_label.grid(row=1, column=0, sticky="w",
                                         padx=12, pady=(0, 10))
                self.engine_b_menu.grid(row=1, column=1, sticky="ew",
                                        padx=12, pady=(0, 10))
            else:
                self.engine_b_label.grid_remove()
                self.engine_b_menu.grid_remove()
        if getattr(self, "engine_selector", None) is not None:
            if bool(getattr(self.settings, "engine_open", False)):
                self.engine_selector.grid()
            else:
                self.engine_selector.grid_remove()

    def _set_engine_mode(self, mode: str):
        compare = mode == "compare"
        self.settings.compare = compare
        self.settings.engine_open = True
        self._schedule_save()
        if getattr(self, "select_provider_btn", None) is not None:
            on, off = C["hover"], C["surface"]
            self.select_provider_btn.configure(fg_color=on if not compare else off)
            self.compare_btn.configure(fg_color=on if compare else off)
        self._refresh_engine_menu()
        self._backend_is_custom = False
        self.refresh_provider()
        self._status("Side-by-side compare on: two engines, one brief."
                     if compare else "Single provider mode.")

    def _engine_selected(self, label):
        key = self._engine_choice_map.get(label, "auto")
        self.settings.engine = key
        self._schedule_save()
        # A picker change is a deliberate re-choice, not an override.
        self._backend_is_custom = False
        self.refresh_provider()
        self._status(f"Provider set to {label.rsplit(' (', 1)[0]}.")

    def _engine_b_selected(self, label):
        key = self._engine_choice_map.get(label, "auto")
        self.settings.engine_b = key
        self._schedule_save()
        self._backend_is_custom = False
        self.refresh_provider()
        self._status(f"Provider B set to {label.rsplit(' (', 1)[0]}.")

    def _menu_key(self, menu):
        if menu is None:
            return None
        key = self._engine_choice_map.get(menu.get(), "auto")
        return None if key == "auto" else key

    def _engine_selected_id(self):
        return self._menu_key(getattr(self, "engine_menu", None))

    def _engines_for_generation(self) -> list:
        """Ordered, de-duplicated resolver keys for the next Generate."""
        keys = [self._engine_selected_id()]
        if bool(getattr(self.settings, "compare", False)):
            second = self._menu_key(getattr(self, "engine_b_menu", None))
            if second not in keys:
                keys.append(second)
        engines = [self._resolve_factory_backend(key) for key in keys]
        return engines or [NoProviderBackend()]

    def _engine_kind(self) -> str:
        return getattr(self.backend, "provider_kind", "none")

    def _badge_tooltip_text(self) -> str:
        if self._engine_kind() == "cloud":
            return ("AI images come from the provider key saved in Settings -> "
                    "Image Generation; the written brief is sent to that "
                    "provider over HTTPS only.")
        return ("No image provider key is configured. Add your own Cloudflare "
                "or Stability AI key in Settings -> Image Generation to enable "
                "real AI generation; nothing renders locally.")

    def _engine_note_text(self) -> str:
        if self._engine_kind() == "cloud":
            short = str(getattr(self.backend, "model", "provider")).rsplit("/", 1)[-1]
            return (f"AI generation is active via {short}. The brief is sent "
                    "to your configured provider; pick another engine or "
                    "Automatic in the ENGINE dropdown to switch.")
        return ("This panel generates only through a cloud provider you "
                "configure -- add your key in Settings -> Image Generation, "
                "then pick it in the ENGINE dropdown. It never renders "
                "locally.")

    def _generate_cta_text(self) -> str:
        if self._engine_kind() == "cloud":
            short = str(getattr(self.backend, "model", "")).rsplit("/", 1)[-1]
            return f"Generate image" + (f" · {short}" if short else "")
        return "Set up image provider…"

    def _engine_console_line(self) -> str:
        if self._engine_kind() == "cloud":
            return f"Cloud image backend ready: {self.backend.name}.\n"
        return ("No image provider configured. Add a key in Settings -> "
                "Image Generation, then Generate.\n")

    def _refresh_backend_badge(self):
        if getattr(self, "provider_badge", None) is None:
            return
        badge_color = C["accent"] if getattr(self.backend, "accent_badge", False) \
            else C["warning"]
        self.provider_badge.configure(
            text=f"  {self.backend.badge}  ", text_color=badge_color,
            image=self.icon_bank("spark", badge_color, 14))
        if getattr(self, "_badge_tooltip", None) is not None:
            self._badge_tooltip.text = self._badge_tooltip_text()
        if getattr(self, "engine_note", None) is not None:
            self.engine_note.configure(text=self._engine_note_text())
        idle_states = ("disabled", "normal")
        if getattr(self, "generate_button", None) is not None and \
                str(self.generate_button.cget("state")) in idle_states and \
                self.running_job_id is None:
            self.generate_button.configure(text=self._generate_cta_text())

    def __init__(self, master, initial_content: str = "",
                 backend: ImageBackend | None = None,
                 backend_factory=None, engine_lister=None):
        super().__init__(master, fg_color=C["outside"], corner_radius=0)
        self._backend_is_custom = False
        self.backend_factory = backend_factory
        self.engine_lister = engine_lister
        if backend is not None:
            self.backend = backend  # explicit injection wins (see setter)
        else:
            self._backend = self._resolve_factory_backend()
        self.storage = EncryptedImageStore()
        self.state_error = None
        try:
            self.projects, self.active_id, self.settings = self.storage.load()
        except Exception:
            self.state_error = ("Saved state couldn't be decrypted, so this session is temporary. "
                                "The old record was left untouched.")
            self.projects = [ImageProject()]
            self.active_id = self.projects[0].id
            self.settings = AppSettings(output_dir=self.storage.default_output_dir())
        self._persistent = self.state_error is None
        if self._persistent:
            Path(self.settings.output_dir).expanduser().mkdir(parents=True, exist_ok=True,
                                                               mode=0o700)

        families = set(tkfont.families(self))
        self.family = next((name for name in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "DejaVu Sans")
                            if name in families), "Arial")
        self.mono_family = next((name for name in ("Cascadia Code", "Consolas", "Menlo", "DejaVu Sans Mono")
                                 if name in families), "Courier")
        self._save_job = None
        self._preview_job = self._poll_job = self._layout_job = None
        self._after_jobs: list = []
        self._tooltips: list[Tooltip] = []
        self._dialogs: list = []
        self.icon_bank = ImageIconBank(self)
        self._closed = False
        self._preview_photo: PanelImage | None = None
        self._result_photo: PanelImage | None = None
        self.reference_image: str | None = None
        self.seed_display = random.SystemRandom().randrange(1_000_000, 9_999_999)
        self.worker_queue: Queue = Queue()
        self.render_cancel: threading.Event | None = None
        self.render_thread: threading.Thread | None = None
        self.running_job_id: str | None = None
        self.last_preview_signature = None
        self._render_batch: list = []
        self._batch_index = 0
        self._batch_abort = False
        self._job_photos: dict = {}
        self._console_log: list = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_shell()
        self._restore_active_project()
        if initial_content:
            self.use_prompt(initial_content, persist=False)
        # Apply the persisted engine mode/choice and paint badge + CTA.
        compare = bool(getattr(self.settings, "compare", False))
        if getattr(self, "select_provider_btn", None) is not None:
            on, off = C["hover"], C["surface"]
            self.select_provider_btn.configure(fg_color=off if compare else on)
            self.compare_btn.configure(fg_color=on if compare else off)
        self.refresh_provider()
        self._refresh_projects()
        self._refresh_jobs()
        self.update_preview(force=True)
        self._poll_job = self._call_after(80, self._poll_render_events)
        self._layout_job = self._call_after(120, self._responsive_layout)
        self._bind_shortcuts()
        if self.state_error:
            self._status(self.state_error, warn=True)
            self._console(self.state_error)

    # ------------------------------------------------------------------
    # Data/state helpers
    # ------------------------------------------------------------------
    @property
    def project(self) -> ImageProject:
        found = next((project for project in self.projects if project.id == self.active_id), None)
        if found is None:
            found = self.projects[0]
            self.active_id = found.id
        return found

    def font(self, size: float, weight: str = "normal"):
        # Font tuples resolve in this panel interpreter; a stock CTkFont can
        # otherwise attach itself to another Friday Tk root.
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
        self.char_count.configure(text=f"{len(brief)} characters")
        self.reference_image = None
        self._set_reference(None)
        latest = self.project.latest_job
        if latest is not None:
            raw = latest.request
            self._choice_select(self.style_group, str(raw.get("style", "Mist")))
            self._choice_select(self.composition_group, str(raw.get("composition", "Horizon")))
            self._choice_select(self.aspect_group, str(raw.get("aspect", "16:9")))
            self._choice_select(self.quality_group, str(raw.get("quality", "Studio")))
            self._choice_select(self.format_group, str(raw.get("format", "png")).lower())
            self.detail_slider.set(float(raw.get("detail", 42.0)))
            self.seed_entry.delete(0, "end")
            self.seed_entry.insert(0, str(raw.get("seed", self.seed_display)))
            self._set_reference(raw.get("source_image"))
        else:
            self.seed_entry.delete(0, "end")
            self.seed_entry.insert(0, str(self.seed_display))
            self.detail_slider.set(42.0)

    def use_prompt(self, text: str, persist: bool = False):
        """Fill the brief as an editable draft, not a submitted render."""
        self.prompt_box.delete("1.0", "end")
        self.prompt_box.insert("1.0", str(text or ""))
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
        self._build_image_bay()
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
        PanelLabel(mark, text="", image=self.icon_bank("image", C["accent"], 22),
                   font=self.font(10)).place(relx=.5, rely=.5, anchor="center")
        PanelLabel(brand, text="quiet", font=self.font(24, "bold"),
                   text_color=C["text"]).grid(row=0, column=1)
        PanelLabel(brand, text="IMAGE", font=self.font(9, "bold"),
                   text_color=C["muted"], height=20).grid(row=0, column=2,
                                                          padx=(7, 0), sticky="s", pady=(0, 5))
        center = ctk.CTkFrame(header, fg_color="transparent")
        center.grid(row=0, column=1, sticky="w", padx=(28, 0))
        initial_badge_color = C["accent"] if getattr(self.backend, "accent_badge", False) \
            else C["warning"]
        self.provider_badge = PanelLabel(
            center, text=f"  {self.backend.badge}  ",
            image=self.icon_bank("spark", initial_badge_color, 14), compound="left",
            font=self.font(10.5), text_color=initial_badge_color, fg_color=C["surface"],
            corner_radius=12, height=29)
        self.provider_badge.grid(row=0, column=0, sticky="w")
        self._badge_tooltip = Tooltip(self.provider_badge, self._badge_tooltip_text())
        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=2, sticky="e")
        icon_button(actions, "folder", self.choose_output_folder,
                    "Choose image output folder").grid(row=0, column=0, padx=(0, 7))
        icon_button(actions, "settings", self.show_output_status,
                    "Storage and renderer details").grid(row=0, column=1)

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
        PanelLabel(top, text="Drafts, seeds and finished images",
                   font=self.font(10.5), text_color=C["muted"], anchor="w").grid(
            row=1, column=0, sticky="w", pady=(1, 0))
        accent_button(top, "New", self.new_project, "plus", width=76).grid(
            row=0, column=1, rowspan=2, sticky="e")
        ctk.CTkFrame(self.rail, height=1, corner_radius=0,
                     fg_color=C["border_soft"]).grid(row=2, column=0, sticky="ew", padx=16)
        self.project_list = ImageScrollFrame(
            self.rail, label_font=self.font(11), fg_color="transparent", corner_radius=0,
            scrollbar_button_color=C["surface"], scrollbar_button_hover_color=C["hover"])
        self.project_list.grid(row=3, column=0, sticky="nsew", padx=10, pady=5)
        self.project_list.grid_columnconfigure(0, weight=1)
        lower = ctk.CTkFrame(self.rail, fg_color="transparent")
        lower.grid(row=4, column=0, sticky="ew", padx=15, pady=(5, 16))
        lower.grid_columnconfigure(0, weight=1)
        self.storage_line = PanelLabel(lower, text="Local storage enabled",
                                       font=self.font(10.5), text_color=C["secondary"],
                                       anchor="w", justify="left", wraplength=225)
        self.storage_line.grid(row=0, column=0, sticky="w")
        self.storage_hint = PanelLabel(
            lower, text="History contains project settings and output paths; no source images are copied.",
            font=self.font(9.5), text_color=C["dim"], anchor="w", justify="left",
            wraplength=225)
        self.storage_hint.grid(row=1, column=0, sticky="w", pady=(3, 0))

    def _build_workbench(self):
        self.center = ImageScrollFrame(
            self.shell, label_font=self.font(11), fg_color="transparent", corner_radius=0,
            scrollbar_button_color=C["surface"], scrollbar_button_hover_color=C["hover"])
        self.center.grid(row=2, column=1, sticky="nsew", padx=18, pady=12)
        self.center.grid_columnconfigure(0, weight=1)

        brief = section(self.center, "Creative brief",
                        "Your brief is sent to the chosen cloud provider to create the image.")
        brief.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        brief.grid_columnconfigure(0, weight=1)
        self.prompt_box = PanelTextbox(
            brief, width=420, height=126, corner_radius=10, border_width=1,
            fg_color=C["background"], border_color=C["border"], text_color=C["body"],
            font=self.font(13), wrap="word")
        self.prompt_box.grid(row=1, column=0, columnspan=3, sticky="ew",
                             padx=18, pady=(0, 8))
        self.prompt_box.bind("<KeyRelease>", self._on_brief_changed)
        prompt_actions = ctk.CTkFrame(brief, fg_color="transparent")
        prompt_actions.grid(row=2, column=0, columnspan=3, sticky="ew",
                            padx=18, pady=(0, 16))
        prompt_actions.grid_columnconfigure(0, weight=1)
        self.char_count = PanelLabel(prompt_actions, text="0 characters",
                                     font=self.font(10), text_color=C["muted"])
        self.char_count.grid(row=0, column=0, sticky="w")
        suggestion = PanelButton(
            prompt_actions, text="Softer palette", width=126, height=28, corner_radius=8,
            font=self.font(10), fg_color=C["surface"], hover_color=C["hover"],
            text_color=C["secondary"],
            command=lambda: self.apply_suggestion("Soft palette, quiet forms, breathing room"))
        suggestion.grid(row=0, column=1, sticky="e", padx=(5, 0))
        Tooltip(suggestion, "Add a quiet art note without sending anything.")

        source = section(self.center, "Reference image",
                         "Reserved for a future image-to-image provider; cloud text-to-image engines ignore it.")
        source.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        source.grid_columnconfigure(0, weight=1)
        row = ctk.CTkFrame(source, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
        row.grid_columnconfigure(0, weight=1)
        self.image_chip = PanelLabel(row, text="No image selected",
                                     image=self.icon_bank("image", C["muted"], 15),
                                     compound="left", font=self.font(10.5),
                                     text_color=C["muted"], fg_color=C["background"],
                                     corner_radius=9, height=34, anchor="w", padx=12)
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

        art = section(self.center, "Art direction",
                      "Cloud engines honor prompt, aspect, quality, seed and format; the other presets stay inert.")
        art.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        art.grid_columnconfigure(0, weight=1)
        choices = ctk.CTkFrame(art, fg_color="transparent")
        choices.grid(row=1, column=0, sticky="ew", padx=18, pady=(4, 8))
        choices.grid_columnconfigure(1, weight=1)
        self.style_group, _ = choice_group(choices, "Style", STYLE_CHOICES, "Mist",
                                           lambda _v: self._widgets_changed())
        self.style_group.grid(row=0, column=0, sticky="w", padx=(0, 24), pady=(0, 14))
        self.composition_group, _ = choice_group(
            choices, "Composition", COMPOSITION_CHOICES, "Horizon",
            lambda _v: self._widgets_changed())
        self.composition_group.grid(row=0, column=1, sticky="w", pady=(0, 14))
        self.aspect_group, _ = choice_group(choices, "Aspect", ASPECT_CHOICES, "16:9",
                                            lambda _v: self._widgets_changed())
        self.aspect_group.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(0, 14))
        self.quality_group, _ = choice_group(choices, "Quality", QUALITY_CHOICES, "Studio",
                                             lambda _v: self._widgets_changed())
        self.quality_group.grid(row=1, column=1, sticky="w", pady=(0, 14))
        self.format_group, _ = choice_group(choices, "Output", FORMAT_CHOICES, "png",
                                            lambda _v: self._widgets_changed())
        self.format_group.grid(row=2, column=0, sticky="w", padx=(0, 24), pady=(0, 14))

        settings_row = ctk.CTkFrame(art, fg_color="transparent")
        settings_row.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))
        settings_row.grid_columnconfigure(0, weight=1)
        settings_row.grid_columnconfigure(1, weight=0)
        self.detail_slider = ValueSlider(
            settings_row, "Texture detail", 10, 100, 42,
            lambda v: f"{int(round(v))}%", self._widgets_changed, steps=90)
        self.detail_slider.grid(row=0, column=0, sticky="ew", padx=(0, 22))
        seed_row = ctk.CTkFrame(settings_row, fg_color="transparent")
        seed_row.grid(row=0, column=1, sticky="e")
        PanelLabel(seed_row, text="Art seed", font=self.font(10),
                   text_color=C["muted"], anchor="w").grid(row=0, column=0,
                   sticky="w", pady=(0, 5))
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

        engine = section(self.center, "Engine",
                         "Pick one provider, or compare two on the same brief.")
        engine.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        engine.grid_columnconfigure(0, weight=1)
        toggles = ctk.CTkFrame(engine, fg_color="transparent")
        toggles.grid(row=1, column=0, sticky="ew", padx=18, pady=(4, 8))
        toggles.grid_columnconfigure((0, 1), weight=1, uniform="engine-toggles")
        self.select_provider_btn = PanelButton(
            toggles, text="Select provider",
            image=self.icon_bank("spark", C["body"], 14),
            command=lambda: self._set_engine_mode("single"), height=36,
            corner_radius=9, fg_color=C["hover"], hover_color=C["track"],
            text_color=C["body"], font=self.font(11, "bold"))
        self.select_provider_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.compare_btn = PanelButton(
            toggles, text="Side by side",
            image=self.icon_bank("image", C["body"], 14),
            command=lambda: self._set_engine_mode("compare"), height=36,
            corner_radius=9, fg_color=C["surface"], hover_color=C["track"],
            text_color=C["body"], font=self.font(11, "bold"))
        self.compare_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.engine_selector = ctk.CTkFrame(engine, fg_color=C["background"],
                                            corner_radius=10, border_width=1,
                                            border_color=C["border_soft"])
        self.engine_selector.grid(row=2, column=0, sticky="ew", padx=18,
                                  pady=(0, 8))
        self.engine_selector.grid_columnconfigure(1, weight=1)
        PanelLabel(self.engine_selector, text="PROVIDER", font=self.font(9, "bold"),
                   text_color=C["muted"]).grid(row=0, column=0, sticky="w",
                                               padx=12, pady=(10, 0))
        self.engine_menu = ctk.CTkOptionMenu(
            self.engine_selector, values=["Automatic (first working provider)"],
            command=self._engine_selected, height=32, corner_radius=9,
            dynamic_resizing=False, font=self.font(10.5),
            fg_color=C["surface"], button_color=C["hover"],
            button_hover_color=C["hover"], text_color=C["text"],
            dropdown_fg_color=C["surface"], dropdown_text_color=C["text"],
            dropdown_hover_color=C["hover"])
        self.engine_menu.grid(row=0, column=1, sticky="ew", padx=12,
                              pady=(10, 0))
        self.engine_b_label = PanelLabel(self.engine_selector, text="PROVIDER B",
                                         font=self.font(9, "bold"),
                                         text_color=C["muted"])
        self.engine_b_menu = ctk.CTkOptionMenu(
            self.engine_selector, values=["Automatic (first working provider)"],
            command=self._engine_b_selected, height=32, corner_radius=9,
            dynamic_resizing=False, font=self.font(10.5),
            fg_color=C["surface"], button_color=C["hover"],
            button_hover_color=C["hover"], text_color=C["text"],
            dropdown_fg_color=C["surface"], dropdown_text_color=C["text"],
            dropdown_hover_color=C["hover"])
        self._engine_choice_map = {}
        actions = ctk.CTkFrame(engine, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 10))
        actions.grid_columnconfigure(0, weight=1)
        self.generate_button = PanelButton(
            actions, text=self._generate_cta_text(),
            image=self.icon_bank("image", C["accent_text"], 19),
            command=self.start_render, height=50, corner_radius=13,
            fg_color=C["accent"], hover_color=C["accent_hover"],
            text_color=C["accent_text"], text_color_disabled=C["muted"],
            font=self.font(13, "bold"))
        self.generate_button.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.cancel_button = PanelButton(
            actions, text="Cancel", image=self.icon_bank("stop", C["danger"], 14),
            width=108, command=self.cancel_render, height=50, corner_radius=13,
            fg_color=C["surface"], hover_color=C["hover"], text_color=C["body"],
            font=self.font(12), state="disabled")
        self.cancel_button.grid(row=0, column=1)
    def _build_image_bay(self):
        self.bay = ctk.CTkFrame(self.shell, fg_color=C["sidebar"], corner_radius=15,
                                border_width=1, border_color=C["border_soft"], width=310)
        self.bay.grid(row=2, column=2, sticky="nsew", padx=(0, 12), pady=12)
        self.bay.grid_propagate(False)
        self.bay.grid_columnconfigure(0, weight=1)
        self.bay.grid_rowconfigure(3, weight=1)
        PanelLabel(self.bay, text="Image queue", font=self.font(19, "bold"),
                   text_color=C["text"]).grid(row=0, column=0, sticky="w",
                   padx=18, pady=(16, 0))
        PanelLabel(self.bay, text="Real files only · no hidden uploads",
                   font=self.font(10.5), text_color=C["muted"], anchor="w").grid(
            row=1, column=0, sticky="w", padx=18, pady=(0, 11))
        ctk.CTkFrame(self.bay, height=1, corner_radius=0,
                     fg_color=C["border_soft"]).grid(row=2, column=0, sticky="ew", padx=17)
        self.queue_list = ImageScrollFrame(
            self.bay, label_font=self.font(11), fg_color="transparent", corner_radius=0,
            scrollbar_button_color=C["surface"], scrollbar_button_hover_color=C["hover"])
        self.queue_list.grid(row=3, column=0, sticky="nsew", padx=10, pady=6)
        self.queue_list.grid_columnconfigure(0, weight=1)
        note = ctk.CTkFrame(self.bay, fg_color=C["surface"], corner_radius=11)
        note.grid(row=4, column=0, sticky="ew", padx=15, pady=(5, 15))
        note.grid_columnconfigure(0, weight=1)
        PanelLabel(note, text="", image=self.icon_bank("info", C["warning"], 17),
                   font=self.font(10)).grid(row=0, column=0, padx=12,
                   pady=12, sticky="ne")
        self.engine_note = PanelLabel(note, text=self._engine_note_text(),
                                      font=self.font(10.2), text_color=C["secondary"],
                                      justify="left", wraplength=220, anchor="w")
        self.engine_note.grid(row=0, column=1, padx=(0, 12), pady=10)

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self.shell, fg_color="transparent", height=34)
        bar.grid(row=3, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 10))
        bar.grid_columnconfigure(0, weight=1)
        self.status = PanelLabel(bar, text="Ready", font=self.mono(10.5),
                                 text_color=C["muted"], anchor="w")
        self.status.grid(row=0, column=0, sticky="w")
        PanelLabel(bar, text="QUIET IMAGE · LOCAL UI", font=self.font(9, "bold"),
                   text_color=C["dim"], anchor="e").grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------------
    # Choice/state change handlers
    # ------------------------------------------------------------------
    def _choice_select(self, control, value: str):
        if value in getattr(control, "buttons", {}):
            control.selected = value
            for candidate, button in control.buttons.items():
                selected = candidate == value
                button.configure(fg_color=C["accent"] if selected else C["surface"],
                                 text_color=C["accent_text"] if selected else C["secondary"],
                                 hover_color=C["accent_hover"] if selected else C["hover"])

    def _on_brief_changed(self, _event=None):
        brief = self.prompt_box.get("1.0", "end-1c")
        self.char_count.configure(text=f"{len(brief)} characters")
        self.project.touch(brief)
        self.last_preview_signature = None
        self._refresh_projects()
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
        current = self.prompt_box.get("1.0", "end-1c").rstrip()
        self.prompt_box.insert("end", ("\n\n" if current else "") + text)
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
    def _request_from_controls(self) -> ImageRequest:
        try:
            seed = int(self.seed_entry.get().strip())
        except ValueError as error:
            raise ValueError("The art seed must be a whole number.") from error
        return ImageRequest(
            prompt=self.prompt_box.get("1.0", "end-1c").strip(),
            style=self.style_group.selected,
            aspect=self.aspect_group.selected,
            quality=self.quality_group.selected,
            composition=self.composition_group.selected,
            detail=float(self.detail_slider.get()),
            seed=seed,
            source_image=self.reference_image,
            format=self.format_group.selected.lower(),
        ).validated()

    def update_preview(self, force: bool = False):
        """No standalone preview exists in the cloud-only layout; this now
        just validates the current request (callers/tests rely on the
        validation side effect) and caches the control signature."""
        self._preview_job = None
        try:
            request = self._request_from_controls()
        except Exception:
            self.last_preview_signature = None
            return
        self.last_preview_signature = (
            request.prompt, request.style, request.aspect, request.quality,
            request.composition, request.detail, request.seed,
            request.source_image)

    def _preview_bounds(self) -> tuple[int, int]:
        width, height = 480, 300
        ratios = {"16:9": 16/9, "4:3": 4/3, "1:1": 1.0, "3:4": 3/4, "9:16": 9/16}
        ratio = ratios[self.aspect_group.selected]
        if width / height > ratio:
            width = int(height * ratio)
        else:
            height = int(width / ratio)
        return _even(width), _even(height)

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
            title = PanelButton(
                card, text=project.title if len(project.title) < 28 else project.title[:27] + "…",
                image=self.icon_bank("image", C["secondary" if selected else "muted"], 16),
                anchor="w", font=self.font(11, "bold" if selected else "normal"),
                text_color=C["text"] if selected else C["muted"],
                fg_color="transparent", hover_color=C["hover"],
                command=lambda key=project.id: self.select_project(key))
            title.grid(row=0, column=0, sticky="ew", padx=(10, 5), pady=(10, 0))
            note = PanelLabel(card, text=f"{finished}/{count} images" if count else "No images yet",
                              font=self.font(9.5), text_color=C["dim"], anchor="w")
            note.grid(row=1, column=0, sticky="w", padx=34, pady=(0, 9))
            icon_button(card, "trash", lambda key=project.id: self.delete_project(key),
                        "Delete this project", width=29, size=14).grid(
                row=0, column=1, rowspan=2, padx=(0, 5), sticky="e")
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
        project = ImageProject()
        self.projects.insert(0, project)
        self.active_id = project.id
        self._restore_active_project()
        self._refresh_projects()
        self._refresh_jobs()
        self.update_preview(force=True)
        self._save_now()
        self._status("New untitled artwork project created.")

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
            self.projects = [ImageProject()]
        self.active_id = self.projects[0].id
        self._restore_active_project()
        self._refresh_projects()
        self._refresh_jobs()
        self._save_now()

    # ------------------------------------------------------------------
    # Render execution and queue
    # ------------------------------------------------------------------
    def render_action(self):
        if self.running_job_id:
            self.cancel_render()
        else:
            self.start_render()

    def start_render(self):
        if self.running_job_id:
            self._status("A render is already running.", warn=True)
            return
        # Re-read Settings so a freshly added/removed provider key applies
        # to exactly this batch; no restart needed.
        self.refresh_provider()
        try:
            request = self._request_from_controls()
            request = request.validated()
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
        # One engine in single mode, two in side-by-side mode. An injected
        # backend (tests/integrators) always runs alone.
        if self._backend_is_custom:
            engines = [self.backend]
        else:
            engines = self._engines_for_generation()
        self._render_batch = []
        for backend in engines:
            job = ImageJob(request=dict(request.wire()),
                           steps_total=request.steps_total)
            job.request["engine"] = getattr(backend, "badge", backend.name)
            job.request["engine_name"] = backend.name
            self.project.add_job(job)
            self._render_batch.append((job, request, backend))
        self._batch_index = 0
        self._batch_abort = False
        self.render_cancel = threading.Event()
        self.generate_button.configure(state="disabled", text="Rendering…")
        self.cancel_button.configure(state="normal")
        count = len(self._render_batch)
        self._console(f"Started {count} render{'s' if count > 1 else ''} · "
                      f"{request.format.upper()} · seed {request.seed}")
        self._refresh_projects()
        self._refresh_jobs()
        self._save_now()
        self._launch_next_render()

    def _launch_next_render(self):
        if self._batch_abort or self._batch_index >= len(self._render_batch):
            self._finish_batch()
            return
        job, request, backend = self._render_batch[self._batch_index]
        total = len(self._render_batch)
        self.running_job_id = job.id
        self._status(f"{backend.name} running…"
                     + (f" ({self._batch_index + 1}/{total})" if total > 1 else ""))
        self._refresh_jobs()
        target_dir = Path(self.settings.output_dir).expanduser()
        self.render_thread = threading.Thread(
            target=self._render_worker, args=(job, request, backend, target_dir),
            daemon=True, name="quiet-image-renderer")
        self.render_thread.start()

    def _finish_batch(self):
        self.running_job_id = None
        self.render_cancel = None
        self.render_thread = None
        self._render_batch = []
        self._batch_index = 0
        self._batch_abort = False
        self.generate_button.configure(state="normal",
                                       text=self._generate_cta_text())
        self.cancel_button.configure(state="disabled", text="Cancel")
        self._refresh_projects()
        self._refresh_jobs()
        self._save_now()

    def _render_worker(self, job: ImageJob, request: ImageRequest,
                       backend, output_dir: Path):
        def progress(done, total):
            self.worker_queue.put(("progress", job.id, done, total))
        try:
            result = backend.render(request, output_dir,
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
                self._handle_render_event(self.worker_queue.get_nowait())
        except Empty:
            pass
        self._poll_job = self._call_after(75, self._poll_render_events)

    def _find_job(self, job_id: str) -> ImageJob | None:
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
        if kind == "progress":
            _kind, _job_id, done, total = event
            job.progress = 0.0 if total <= 0 else done / total
            job.steps_done, job.steps_total = done, total
            job.status = "running"
            if done == total or done % max(1, total // 5) == 0:
                self._console(f"{done}/{total} render stages complete")
        else:
            if kind == "success":
                result = event[2]
                job.mark("succeeded", progress=1.0, steps_done=result.detail_steps,
                         output_path=result.output_path,
                         thumbnail_path=result.thumbnail_path,
                         elapsed_seconds=result.elapsed_seconds,
                         size_bytes=result.size_bytes)
                self._console(f"Finished {Path(result.output_path).name} · "
                              f"{result.size_bytes/1024:.1f} KB · {result.elapsed_seconds:.1f}s")
                self._status(f"Image ready · {job.request.get('engine_name', '')}.")
            elif kind == "cancelled":
                job.mark("cancelled", progress=0.0, error="Cancelled by you.")
                self._console("Render cancelled. No partial output was kept.")
                self._status("Render cancelled.", warn=True)
                self._batch_abort = True
            else:
                _kind, _job_id, message = event
                job.mark("failed", error=message)
                self._console(f"Render failed: {message}")
                self._status(message, warn=True)
            # Chain the next engine in a side-by-side batch (or finish).
            self._batch_index += 1
            self._launch_next_render()
            return
        self._refresh_jobs()

    def cancel_render(self):
        if not self.running_job_id or self.render_cancel is None:
            return
        self._batch_abort = True
        self.render_cancel.set()
        self.cancel_button.configure(text="Cancelling…", state="disabled")
        self._status("Cancelling render batch…", warn=True)

    def _job_photo(self, job):
        """Card image for a finished job: the real rendered image (or its
        cached thumbnail), cover-cropped to the card ratio with a darkened
        lower strip so overlaid content text stays readable."""
        source = job.thumbnail_path or job.output_path
        if not source or not Path(source).is_file():
            source = job.output_path
        if not source or not Path(source).is_file():
            return None
        try:
            width, height = 242, 136
            image = Image.open(source).convert("RGB")
            src = image.width / image.height
            dst = width / height
            if src > dst:
                new_w = max(1, int(image.height * dst))
                x0 = (image.width - new_w) // 2
                image = image.crop((x0, 0, x0 + new_w, image.height))
            else:
                new_h = max(1, int(image.width / dst))
                y0 = (image.height - new_h) // 2
                image = image.crop((0, y0, image.width, y0 + new_h))
            image = image.resize((width, height), Image.Resampling.LANCZOS)
            strip = Image.new("RGB", (width, int(height * 0.58)), (9, 9, 11))
            image.paste(strip, (0, height - strip.height))
            photo = PanelImage(self, image, (width, height))
            self._job_photos[job.id] = photo
            return photo
        except Exception:  # noqa: BLE001 -- a corrupt file just falls back
            return None

    # ------------------------------------------------------------------
    # Render list/actions
    # ------------------------------------------------------------------
    def _refresh_jobs(self):
        self._job_photos = {}
        for child in self.queue_list.winfo_children():
            child.destroy()
        jobs = self.project.jobs
        if not jobs:
            empty = ctk.CTkFrame(self.queue_list, fg_color="transparent")
            empty.grid(row=0, column=0, sticky="ew", pady=(22, 0))
            PanelLabel(empty, text="", image=self.icon_bank("clock", C["dim"], 38),
                       font=self.font(10)).pack(pady=(0, 10))
            PanelLabel(empty, text="No images yet", font=self.font(15, "bold"),
                       text_color=C["secondary"]).pack()
            PanelLabel(empty, text="Configure a provider key, then Generate.",
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
            stat_pill(card, job.status, status_color).grid(row=1, column=0,
                                                            sticky="w", padx=12, pady=(1, 8))
            request = job.request
            if not job.output_path:
                PanelLabel(card, text=f"{request.get('style','Mist')} · {request.get('aspect','16:9')} · "
                                      f"{request.get('format','png').upper()} · seed {request.get('seed','?')}",
                           font=self.font(9.5), text_color=C["muted"], anchor="w").grid(
                    row=1, column=1, columnspan=3, sticky="w", padx=(78, 11), pady=(1, 8))
            if job.running:
                bar = ctk.CTkProgressBar(card, progress_color=C["accent"],
                                         fg_color=C["track"], corner_radius=3, height=7)
                bar.grid(row=2, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 12))
                bar.set(job.progress)
            elif job.output_path:
                # The card carries the actual image with its content
                # overlaid on top -- engine, aspect, format and seed.
                photo = self._job_photo(job)
                if photo is not None:
                    engine = job.request.get("engine") or \
                        job.request.get("engine_name") or "cloud"
                    overlay = (f"{request.get('aspect','16:9')} · "
                               f"{request.get('format','png').upper()} · "
                               f"seed {request.get('seed','?')}\n{engine}")
                    PanelLabel(card, text=overlay, image=photo, compound="center",
                               font=self.font(9, "bold"), text_color="#FFFFFF",
                               justify="left", anchor="s", wraplength=228,
                               corner_radius=10).grid(
                        row=2, column=0, columnspan=4, sticky="ew",
                        padx=10, pady=(0, 6))
                else:
                    PanelLabel(card, text=f"{Path(job.output_path).name} · {job.size_bytes/1024:.1f} KB",
                               font=self.font(9.5), text_color=C["dim"], anchor="w").grid(
                        row=2, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 4))
                actions = ctk.CTkFrame(card, fg_color="transparent")
                actions.grid(row=3, column=0, columnspan=4, sticky="e", padx=10, pady=(0, 10))
                icon_button(actions, "open", lambda p=job.output_path: self._open_path(p),
                            "Open rendered image", width=28, size=15).grid(row=0, column=0, padx=2)
                icon_button(actions, "folder", lambda p=job.output_path: self._open_folder(p),
                            "Reveal output folder", width=28, size=15).grid(row=0, column=1, padx=2)
                icon_button(actions, "copy", lambda p=job.output_path: self._copy_to_clipboard(p),
                            "Copy file path", width=28, size=15).grid(row=0, column=2, padx=2)
            elif job.error:
                PanelLabel(card, text=job.error, font=self.font(9.5),
                           text_color=C["danger"], anchor="w", justify="left",
                           wraplength=230).grid(row=2, column=0, columnspan=4,
                                                sticky="ew", padx=12, pady=(0, 11))

    def _console(self, line: str):
        """Rendering stage log (kept in memory; the console widget was
        removed with the preview section, but callers/tests still log)."""
        self._console_log.append(str(line).rstrip())
        del self._console_log[:-200]

    def choose_output_folder(self):
        path = filedialog.askdirectory(parent=self, title="Choose image output folder",
                                       initialdir=self.settings.output_dir)
        if path:
            self.settings.output_dir = str(Path(path).expanduser())
            Path(self.settings.output_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
            self._schedule_save()
            self._status(f"Render destination changed to {self.settings.output_dir}")

    def show_output_status(self):
        if self._engine_kind() == "cloud":
            credentials_line = ("Cloud provider credentials are read from "
                                "Friday's encrypted Settings memory and are "
                                "sent only to that provider over HTTPS.")
        else:
            credentials_line = ("Reference images are not copied into state, "
                                "and no provider credentials are used.")
        messagebox.showinfo(
            "Quiet Image storage",
            "Friday stores project metadata and rendered file paths in his encrypted local kv memory.\n\n"
            f"Render destination:\n{Path(self.settings.output_dir).expanduser()}\n\n"
            f"Panel history:\n{self.settings.state_path or 'Friday encrypted memory'}\n\n"
            + credentials_line,
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
        width = self.winfo_width()
        if width < 1180 and self.bay.winfo_manager():
            self.bay.grid_remove()
        elif width >= 1180 and not self.bay.winfo_manager():
            self.bay.grid()
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
                         error="Panel closed before the renderer finished.")
            self.render_thread = None
            self.render_cancel = None
            self.running_job_id = None
        self._save_now()
        self._closed = True
        for job in list(self._after_jobs):
            self._cancel_call(job)
        for tooltip in list(self._tooltips):
            try:
                tooltip.hide()
            except tk.TclError:
                pass
        for dialog in list(self._dialogs):
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
        # Native image rasters are deleted only after widget callbacks above
        # are cancelled; this avoids stale CTk sizing callbacks reaching a
        # destroyed Tk image name.
        self.icon_bank.clear()
        for image in (self._preview_photo, self._result_photo,
                      *self._job_photos.values()):
            if hasattr(image, "clear"):
                image.clear()
        self._preview_photo = None
        self._result_photo = None
        self._job_photos = {}


def _even(shrink: int) -> int:
    shrink = max(2, int(shrink))
    return shrink if shrink % 2 == 0 else shrink + 1

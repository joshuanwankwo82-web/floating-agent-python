"""Friday's Settings panel -- one flat list of every configured AI
model/API key and service, with a live green-check/red-x connection
status per entry. Chat-capable models are shared across tasks: entries
best matched to a task are tried first, then other compatible models.
Specialized services remain limited to the tasks they support.

Split out into its own file for the same reason notepad_panel.py is its
own file rather than living in app_panels.py (see that file's own
docstring): it's a fully native CustomTkinter panel, conceptually distinct
from the real-process-embedding machinery the rest of app_panels.py is
about. Shared panel geometry and titlebar styling live in panel_ui.py.

Data lives in settings_store.py (encrypted on disk, same mechanism as
the rest of Friday's memory) and every green-check status shown here
comes from a real, on-demand backend call in settings_checks.py -- never
just "a key string is non-empty". Chat-capable entries are shared across
Friday chat and Act Script tasks. Image, search, embedding, audio, and
transcription entries continue to use their existing task-specific
integrations; the Video panel remains a plainly labeled deterministic
local renderer.
"""
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk

import settings_store
import settings_checks
import provider_autodetect as pa

# Same bootstrap notepad_panel.py and app_panels.py themselves already
# do -- ide.py's color palette and pulsing-active-border animator are
# already fully generic, reused as-is here instead of duplicated again.
_IDE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ide")
if _IDE_DIR not in sys.path:
    sys.path.insert(0, _IDE_DIR)
import ide as _ide_module  # noqa: E402
C = _ide_module.C
_ActiveGlowAnimator = _ide_module._ActiveGlowAnimator
_update_active_panel_glow = _ide_module._update_active_panel_glow
import panel_ui
import touchpad_scroll

# Keep "Check all" responsive without flooding providers or creating a
# burst of simultaneous connections when a large model list is configured.
_SETTINGS_CHECK_SLOTS = threading.BoundedSemaphore(4)

def _style_entry(entry):
    """One look for every text field on the settings pages: the IDE's
    own input-well colors (dark, dim placeholder, soft border) instead
    of customtkinter's bright light-theme defaults, whose white border
    and grey fill visibly glitch on this dark panel."""
    entry.configure(fg_color=C["input"], text_color=C["fg"],
                    placeholder_text_color=C["fg_faint"],
                    border_width=1, border_color=C["border"],
                    corner_radius=8, font=("Segoe UI", 10))
    return entry


# Content column: follows the panel width (user-directed, F36 -- the
# F34 fixed 660px column left huge dead margins on a wide Settings
# window and clipped long model ids). Capped at _CONTENT_CAP only for
# readability on ultrawide windows; the F35 right-anchored actions keep
# Edit/Remove unclippable at ANY width, so filling is safe.
_CONTENT_CAP = 1280
_CONTENT_PAD = 18


_STATUS_STYLE = {
    "connected": ("\u2713", C["success"], "Connected"),
    "error": ("\u2715", C["danger"], "Not connected"),
    "unknown": ("\u25cb", C["fg_dim"], "Not checked yet"),
    "checking": ("\u27f3", C["warn"], "Checking\u2026"),
}


class _FieldDialog(tk.Toplevel):
    """The "+ Add Model" form, rebuilt for the any-key dream:
    paste ANY API key in ANY provider's format and the dialog recognizes
    the provider from the key's shape (provider_autodetect), fills its
    base URL itself, and says what it did; keys with no recognizable
    shape are served by the provider quick-pick at the top instead
    (OpenRouter, DeepSeek, xAI, Groq, Anthropic, local Ollama/LM
    Studio...). A "Fetch models" button asks the provider for its own
    live model catalog into a picker and can add all classified free and
    trial entries in one batch. Everything stays overridable -- detection
    is a helper, never a lock. Calls `on_submit(name, model, api_key,
    base_url, api_style)` on save.
    """

    def __init__(self, master, title, faculty, on_submit, existing=None,
                 on_add_many=None):
        super().__init__(master)
        self.title(title)
        self.configure(bg=C["panel"])
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self._faculty = faculty
        self._on_submit = on_submit
        self._on_add_many = on_add_many
        self._bulk_models = []
        # Batteries replace this with a stub -- the real one is
        # settings_checks.fetch_model_list.
        self._fetcher = None

        existing = existing or {}
        self._name_var = tk.StringVar(value=existing.get("name", ""))
        self._model_var = tk.StringVar(value=existing.get("model", ""))
        self._base_url_var = tk.StringVar(value=existing.get("base_url", ""))
        self._key_var = tk.StringVar(value=existing.get("api_key", ""))
        self._api_style = existing.get("api_style") or ""
        self._fetching = False
        # prefix-label -> raw model id resolution for tiered displays
        # (the picker shows "[FREE] id" but saves "id").
        self._display_to_id = {}

        pad = {"padx": 14, "pady": (10, 0)}
        ctk.CTkLabel(
            self, text=title, text_color=C["fg"],
            font=("Segoe UI", 10, "bold")).pack(anchor="w", **pad)

        # -- provider quick-pick --------------------------------------
        ctk.CTkLabel(
            self, text="Provider (pick one, or paste any key below and "
            "let it be detected)",
            text_color=C["fg_dim"], font=("Segoe UI", 10)).pack(
            anchor="w", padx=14, pady=(10, 0))
        provider_names = [p["name"] for p in pa.COMMON_PROVIDERS]
        self._provider_combo = ctk.CTkComboBox(
            self, values=provider_names, command=self._on_preset_pick,
            fg_color=C["input"], text_color=C["fg"],
            border_color=C["border"], dropdown_fg_color=C["panel"],
            dropdown_text_color=C["fg"],
            font=("Segoe UI", 10), corner_radius=8, state="readonly")
        self._provider_combo.set("-- auto-detect from my key --")
        self._provider_combo.pack(fill="x", padx=14, pady=(4, 0))
        # recognition label -- the dialog's voice, always honest about
        # what it figured out (or didn't)
        self._detect_lbl = ctk.CTkLabel(
            self, text="", text_color=C["fg_dim"],
            font=("Segoe UI", 10), anchor="w", justify="left",
            wraplength=390)
        self._detect_lbl.pack(anchor="w", padx=14, pady=(3, 0), fill="x")

        self._labeled_entry('Display name (e.g. "GLM @ Z.ai")',
                            self._name_var)

        # -- model picker + fetch --------------------------------------
        ctk.CTkLabel(
            self, text="Model", text_color=C["fg_dim"],
            font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=(10, 0))
        model_row = ctk.CTkFrame(self, fg_color="transparent",
                                 corner_radius=0)
        model_row.pack(fill="x", padx=14, pady=(4, 0))
        self._model_combo = ctk.CTkComboBox(
            model_row, values=[], variable=self._model_var,
            fg_color=C["input"], text_color=C["fg"],
            border_color=C["border"], dropdown_fg_color=C["panel"],
            dropdown_text_color=C["fg"],
            font=("Segoe UI", 10), corner_radius=8)
        self._model_combo.pack(side="left", fill="x", expand=True, ipady=4)
        self._fetch_btn = ctk.CTkButton(
            model_row, text="Fetch models", width=95,
            command=self._fetch_models)
        panel_ui.style_button(self._fetch_btn, C)
        self._fetch_btn.pack(side="left", padx=(8, 0))
        self._model_hint_lbl = ctk.CTkLabel(
            self, text="", text_color=C["fg_dim"],
            font=("Segoe UI", 10), anchor="w", justify="left",
            wraplength=390)
        self._model_hint_lbl.pack(anchor="w", padx=14, pady=(3, 0),
                                  fill="x")
        self._bulk_btn = None
        if on_add_many and faculty != "Image Generation":
            self._bulk_btn = ctk.CTkButton(
                self, text="Add all FREE + TRIAL models",
                state="disabled", command=self._add_free_trial_models)
            panel_ui.style_button(self._bulk_btn, C, "secondary")
            self._bulk_btn.pack(fill="x", padx=14, pady=(7, 0))
            ctk.CTkLabel(
                self,
                text=("Adds one unchecked row per provider-classified free "
                      "or trial model. No generation checks are run."),
                text_color=C["fg_faint"], font=("Segoe UI", 9),
                anchor="w", justify="left", wraplength=420,
            ).pack(anchor="w", padx=14, pady=(2, 0), fill="x")

        self._labeled_entry("API base URL (auto-filled for known "
                            "providers)", self._base_url_var)

        ctk.CTkLabel(
            self, text="API key (any format -- free or paid; leave empty "
            "for a local server)",
            text_color=C["fg_dim"], font=("Segoe UI", 10)).pack(
            anchor="w", padx=14, pady=(10, 0))
        key_row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        key_row.pack(fill="x", padx=14, pady=(2, 0))
        self._key_entry = ctk.CTkEntry(
            key_row, textvariable=self._key_var, show="\u2022",
            fg_color=C["input"], text_color=C["fg"],
            font=("Segoe UI", 10), corner_radius=8)
        self._key_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self._show_var = tk.BooleanVar(value=False)

        def _toggle_show():
            self._key_entry.configure(show="" if self._show_var.get() else "\u2022")

        ctk.CTkCheckBox(
            key_row, text="Show", variable=self._show_var, command=_toggle_show,
            text_color=C["fg_dim"], fg_color=C["accent"],
            hover_color=C["accent_hover"], border_color=C["border"],
            font=("Segoe UI", 10), checkbox_width=16, checkbox_height=16,
        ).pack(side="left", padx=(8, 0))
        self._key_var.trace_add("write", lambda *_: self._on_key_change())
        self._base_url_var.trace_add(
            "write", lambda *_: self._invalidate_bulk_models())

        btn_row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        btn_row.pack(fill="x", padx=14, pady=14)
        panel_ui.style_button(
            ctk.CTkButton(btn_row, text="Cancel", command=self.destroy), C
        ).pack(side="right", padx=(6, 0))
        panel_ui.style_button(
            ctk.CTkButton(btn_row, text="Save", command=self._submit), C, "primary"
        ).pack(side="right")

        # Seed recognition for an existing entry (edit mode).
        if existing.get("base_url") or existing.get("api_key"):
            hint = (pa.preset_for_base_url(existing.get("base_url", ""))
                    or pa.detect_provider(existing.get("api_key", "")))
            if hint:
                self._apply_hint(hint, fill_base=False, quiet=True)
                self._detect_lbl.configure(
                    text=f"Recognized: {hint['name']}")
        self._refresh_model_hint()

        self.update_idletasks()
        w, h = 460, self.winfo_reqheight()
        try:
            px = master.winfo_rootx() + (master.winfo_width() - w) // 2
            py = master.winfo_rooty() + (master.winfo_height() - h) // 2
        except tk.TclError:
            px, py = 200, 150
        self.geometry(f"{w}x{h}+{max(0, px)}+{max(0, py)}")

    def _labeled_entry(self, label, var):
        ctk.CTkLabel(
            self, text=label, text_color=C["fg_dim"],
            font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=(10, 0))
        ctk.CTkEntry(
            self, textvariable=var, fg_color=C["input"], text_color=C["fg"],
            border_width=1, border_color=C["border"],
            font=("Segoe UI", 10), corner_radius=8,
        ).pack(fill="x", padx=14, pady=(4, 0), ipady=6)

    # -- detection / presets --------------------------------------------
    def _apply_hint(self, hint, fill_base=True, quiet=False):
        self._api_style = hint["api_style"]
        if fill_base and hint["base_url"]:
            self._base_url_var.set(hint["base_url"])
        self._refresh_model_hint(hint)

    def _refresh_model_hint(self, hint=None):
        hint = hint or pa.preset_for_base_url(self._base_url_var.get())
        txt = ""
        if hint and hint.get("model_hint"):
            txt = f"e.g. {hint['model_hint']}  \u2014  or use Fetch models"
        self._model_hint_lbl.configure(text=txt)

    def _on_preset_pick(self, name):
        hint = pa.preset_by_name(name)
        if not hint:
            return
        self._api_style = hint["api_style"]
        if hint["slug"] == "custom":
            self._detect_lbl.configure(
                text="Fully custom -- fill the base URL below yourself.")
            self._refresh_model_hint()
            return
        self._base_url_var.set(hint["base_url"])
        note = f"Using {hint['name']}."
        if hint.get("no_key_ok"):
            note += " Local -- a key usually isn't needed."
        self._detect_lbl.configure(text=note)
        self._refresh_model_hint(hint)

    def _on_key_change(self):
        self._invalidate_bulk_models()
        hint = pa.detect_provider(self._key_var.get())
        if hint:
            # Don't fight a deliberate manual choice: refill the base URL
            # only when it's still empty or already equals another preset.
            current = self._base_url_var.get().strip()
            if not current or pa.preset_for_base_url(current):
                self._base_url_var.set(hint["base_url"])
            self._api_style = hint["api_style"]
            self._detect_lbl.configure(
                text=f"Detected: {hint['name']} -- its base URL is filled "
                     "in for you.")
            self._refresh_model_hint(hint)
        elif self._key_var.get().strip():
            self._detect_lbl.configure(
                text="This key's shape isn't recognized -- pick the "
                     "provider above, or fill the base URL yourself.")
        else:
            self._detect_lbl.configure(
                text="")

    # -- fetching the provider's real model catalog -----------------------
    def _fetch_models(self):
        if self._fetching:
            return
        base = self._base_url_var.get().strip()
        if not base:
            messagebox.showwarning(
                "No base URL", "Fill (or pick) the provider's base URL "
                "first, then fetch its model list.", parent=self)
            return
        fetcher = self._fetcher or settings_checks.fetch_model_list
        api_key = self._key_var.get().strip()
        api_style = self._api_style or None
        self._fetching = True
        self._invalidate_bulk_models()
        self._fetch_btn.configure(state="disabled", text="fetching...")
        request_snapshot = (base, api_key, api_style)

        import threading as _th

        def _work():
            try:
                result = fetcher({"base_url": base, "api_key": api_key,
                                  "api_style": api_style})
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": str(exc)}
            try:
                self.after(0, lambda r=result, s=request_snapshot:
                           self._apply_fetch(r, s))
            except (tk.TclError, RuntimeError):  # dialog closed mid-fetch
                pass

        _th.Thread(target=_work, daemon=True).start()

    def _invalidate_bulk_models(self):
        self._bulk_models = []
        if self._bulk_btn is not None:
            try:
                self._bulk_btn.configure(state="disabled")
            except tk.TclError:
                pass

    _TIER_LABELS = [("free", "[FREE]"), ("trial", "[TRIAL]"),
                    ("paid", "[PAID]"), ("unstated", "[open]")]

    def _apply_fetch(self, result, request_snapshot=None):
        self._fetching = False
        try:
            self._fetch_btn.configure(state="normal", text="Fetch models")
        except tk.TclError:
            return
        if request_snapshot is not None and request_snapshot != (
                self._base_url_var.get().strip(),
                self._key_var.get().strip(), self._api_style or None):
            self._invalidate_bulk_models()
            self._model_hint_lbl.configure(
                text="Connection settings changed while the catalog was "
                     "loading. Fetch models again before adding them.")
            return
        if not result.get("ok"):
            self._invalidate_bulk_models()
            self._model_hint_lbl.configure(
                text="Couldn't fetch the list -- type the model manually. "
                     f"({result.get('error') or 'unknown error'})")
            return
        ids = result["ids"]
        tiers = result.get("tiers")
        self._display_to_id = {}
        self._bulk_models = []
        if tiers:
            self._bulk_models = list(dict.fromkeys(
                list(tiers.get("free") or [])
                + list(tiers.get("trial") or [])))
            values = []
            counts = []
            for tier, label in self._TIER_LABELS:
                bucket = tiers.get(tier) or []
                if bucket:
                    if tier == "unstated":
                        counts.append(f"{len(bucket)} price-unstated")
                    else:
                        counts.append(f"{len(bucket)} {tier.upper()}")
                for model_id in bucket:
                    shown = f"{label} {model_id}"
                    values.append(shown)
                    self._display_to_id[shown] = model_id
            if values:
                ids = values
                summary = " \u00b7 ".join(counts)
                note = (f"{summary} -- best-effort classes read from the "
                        "provider's own pricing metadata (price-unstated "
                        "= the provider didn't publish a price for that "
                        "id either way).")
            else:
                note = (f"{len(ids)} model(s) -- the provider shared no "
                        "pricing info at all.")
        else:
            note = (f"{len(ids)} model(s) from the provider itself -- pick "
                    "from the dropdown or type your own.")
        self._model_combo.configure(values=ids)
        if not self._model_var.get().strip():
            self._model_var.set(ids[0])
        self._model_hint_lbl.configure(text=note)
        if self._bulk_btn is not None:
            self._bulk_btn.configure(
                state="normal" if self._bulk_models else "disabled")

    def _add_free_trial_models(self):
        models = list(self._bulk_models)
        if not models or not self._on_add_many:
            return
        name = self._name_var.get().strip()
        if not name:
            messagebox.showwarning(
                "Missing provider name",
                "Give this provider a display name before adding its models.",
                parent=self)
            return
        base_url = self._base_url_var.get().strip()
        base_hint = pa.preset_for_base_url(base_url)
        api_style = (self._api_style
                     or (base_hint["api_style"] if base_hint else "")
                     or pa.infer_api_style(base_url))
        provider_hint = base_hint["name"] if base_hint else ""
        try:
            added = self._on_add_many(
                name, models, self._key_var.get().strip(), base_url,
                api_style, provider_hint)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Could not add models", str(exc) or exc.__class__.__name__,
                parent=self)
            return
        count = len(added or [])
        if count:
            messagebox.showinfo(
                "Models added",
                f"Added {count} model row(s). They remain unchecked until "
                "you run their individual checks; no generation calls were "
                "made.", parent=self)
        else:
            messagebox.showinfo(
                "No new models",
                "These free/trial models are already in the connection list.",
                parent=self)
        self.destroy()

    # -- submit -------------------------------------------------------------
    def _submit(self):
        name = self._name_var.get().strip()
        model = self._model_var.get().strip()
        # A tier-prefixed pick ("[FREE] m-a") resolves back to the raw id
        # BEFORE anything saves it -- the prefix is display-only.
        model = self._display_to_id.get(model, model)
        if not name:
            messagebox.showwarning("Missing name", "Give this model a display name.",
                                    parent=self)
            return
        base_url = self._base_url_var.get().strip()
        base_hint = pa.preset_for_base_url(base_url)
        api_style = (self._api_style
                     or (base_hint["api_style"] if base_hint else "")
                     or pa.infer_api_style(base_url))
        provider_hint = base_hint["name"] if base_hint else ""
        self._on_submit(name, model, self._key_var.get().strip(),
                        base_url, api_style, provider_hint)
        self.destroy()


class SettingsPanel(tk.Toplevel):
    """Friday's own native Settings panel: a titlebar matching every
    other panel's chrome (drag to move, minimize, close), then a
    scrollable, flat list of connections with masked keys, status badges,
    Check/Edit/Remove actions, and add buttons. `initial_content` is accepted only for
    constructor-signature compatibility with the other custom panels
    (AppPanelManager._open_custom_panel calls every custom panel class
    the same way) -- Settings has no notion of "content" and ignores it.
    """

    DEFAULT_W = 760
    DEFAULT_H = 600
    TITLEBAR_H = panel_ui.TITLEBAR_H
    BORDER_PX = panel_ui.BORDER_PX

    def __init__(self, master, on_minimize, on_close, corner_index=0,
                 initial_content=None, bounds=None, initial_page=None):
        super().__init__(master)
        self.title("Settings")
        self.overrideredirect(True)
        self.configure(bg=C["border_hi"])
        self.is_maximized = False
        self._is_active_panel = False
        self._friday_glow_state = "idle"
        self._friday_glow_is_error = False
        self._closing = False
        self._destroyed_settings = False
        self._active_glow = _ActiveGlowAnimator(
            self, lambda c: self.configure(bg=c))

        if bounds is not None:
            x, y, w, h = bounds
        else:
            w, h = self.DEFAULT_W, self.DEFAULT_H
            x, y = panel_ui.corner_position(master, corner_index, w, h)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self._default_geometry = (x, y, w, h)

        self._data = {}
        self._row_widgets = {}  # (faculty, id) -> {"dot":..., "status_lbl":..., ...}
        self._connection_counts_lbl = None
        self._remove_disconnected_btn = None

        bp = self.BORDER_PX
        # Titlebar (with a real, working close button) is built first,
        # before anything below that depends on settings_store actually
        # loading cleanly -- so even if that ever fails for some reason
        # (a corrupted settings file, a missing dependency, ...), this
        # panel is never left on screen as an inert, un-closeable blank
        # window; see the try/except further down.
        panel_ui.build_titlebar(
            self, C, "Settings \u2014 AI Models", on_minimize, on_close,
            self.TITLEBAR_H, bp)

        # -- a one-line subheader + "Check all" --
        sub = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0)
        sub.pack(side="top", fill="x", padx=bp)
        ctk.CTkLabel(
            sub, text=("Keys are masked. Check performs a real API call. "
                       "IDE chat uses Coding; Friday chat uses Voice. "
                       "Act Script uses its named faculties without "
                       "cross-section fallback."),
            text_color=C["fg_dim"], font=("Segoe UI", 10), wraplength=560,
            justify="left").pack(side="left", padx=8, pady=6)
        # user-directed (F41): "Check all" belongs to the Keys page
        # ONLY -- the Profile page has nothing to check. Shown/hidden
        # on page flips in show_page().
        self._check_all_btn = panel_ui.style_button(
            ctk.CTkButton(sub, text="Check all", command=self._check_all),
            C, "primary")
        # Honest live diagnostics (one slot, registered while this panel
        # is open): every wheel/touchpad event that reaches Friday shows
        # up here with its route -- if it stays silent, the events are
        # not reaching Tk at all, and that fact is worth reporting.
        self._scroll_probe = ctk.CTkLabel(
            sub, text="scroll probe: idle", text_color=C["fg_dim"],
            font=("Segoe UI", 10))
        self._scroll_probe.pack(side="right", padx=8)

        def _probe(text,
                   _now=__import__("datetime").datetime.now):
            try:
                self._scroll_probe.configure(
                    text="scroll probe: " + _now().strftime("%H:%M:%S")
                    + " \u2014 " + text)
            except Exception:  # noqa: BLE001
                touchpad_scroll.set_probe(None, owner=self)

        self._probe_update = _probe
        touchpad_scroll.set_probe(_probe, owner=self)

        # -- two pages behind a TOP segmented switcher (the settings
        #    panel's nav, customtkinter-native): "Keys & Connect" (the
        #    list below) and "Profile" (who the user is; see
        #    user_profile.py). Centered horizontally, pill-styled in the
        #    IDE palette; the active segment uses the accent like the
        #    panel's primary buttons. --
        split = ctk.CTkFrame(self, fg_color=C["editor"], corner_radius=0)
        split.pack(side="top", fill="both", expand=True, padx=bp,
                   pady=(0, bp))
        self._page = None
        # multi-remove selection state: entry ids ticked on the keys page
        self._selected_ids = set()
        self._sel_vars = {}      # (faculty, entry_id) -> BooleanVar
        self._remove_selected_btn = None
        self._page_keys = {"Keys & Connect": "keys", "Profile": "profile"}
        self._page_labels = {v: k for k, v in self._page_keys.items()}
        self._seg = ctk.CTkSegmentedButton(
            split, values=list(self._page_keys), height=32,
            command=lambda label: self.show_page(self._page_keys[label]),
            fg_color=C["panel"], corner_radius=16, border_width=1,
            selected_color=C["accent"],
            selected_hover_color=C.get("accent_hover", C["accent"]),
            unselected_color=C["panel"],
            unselected_hover_color=C["hover"],
            text_color=C["fg"], font=("Segoe UI", 10, "bold"))
        self._seg.pack(side="top", padx=6, pady=(8, 6))
        self._content = ctk.CTkFrame(split, fg_color=C["editor"],
                                     corner_radius=0)
        self._content.pack(side="top", fill="both", expand=True)

        # -- scrollable body (the Keys & Connect page) --
        body_host = ctk.CTkFrame(
            self._content, fg_color=C["editor"], corner_radius=0)
        body_host.pack(side="top", fill="both", expand=True)
        self._canvas = tk.Canvas(body_host, bg=C["editor"], highlightthickness=0,
                                  bd=0)
        vsb = tk.Scrollbar(body_host, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        # No side scrollbar: two-finger touchpad / wheel scrolling comes
        # from touchpad_scroll's global pointer-routed handler (Windows
        # delivers wheel events to the FOCUSED widget, so per-widget
        # bindings on the rows could never fire reliably).
        self._body = ctk.CTkFrame(
            self._canvas, fg_color=C["editor"], corner_radius=0)
        self._body_window = self._canvas.create_window((0, 0), window=self._body,
                                                         anchor="nw")

        def _on_body_configure(_e):
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))

        def _on_canvas_configure(e):
            # Panel-fitting content column (F36): width follows the
            # window (plus a small even margin), capped at _CONTENT_CAP
            # and centered beyond it. itemconfigure(width=), NOT
            # body.configure(width=): row children demand ~840px of
            # natural width and pack would let them overflow the frame
            # anyway; the canvas-window width is the constraint pack
            # actually obeys.
            w = min(e.width - 2 * _CONTENT_PAD, _CONTENT_CAP)
            self._canvas.itemconfigure(self._body_window, width=w)
            self._canvas.coords(self._body_window,
                                max(0, (e.width - w) // 2), 0)

        self._body.bind("<Configure>", _on_body_configure)
        self._canvas.bind("<Configure>", _on_canvas_configure)

        # Cursor-edge auto-scroll (user-directed control): this Windows
        # box proved it never delivers wheel events to us at all, but
        # <Motion> arrives fine -- hovering near a panel edge glides the
        # list that way, deeper-in = faster.
        touchpad_scroll.attach_edge_autoscroll(self, self._canvas,
                                              tick_ms=20)
        self._keys_host = body_host
        self._add_jump_buttons(body_host, self._canvas, "keys")
        self._profile_host = self._build_profile_page(self._content)
        if initial_page == "profile":
            self.show_page("profile")
        else:
            self.show_page("keys")

        # Wheel/touchpad scrolling comes from touchpad_scroll's global
        # pointer-routed handler (Windows delivers wheel events to the
        # FOCUSED widget, so per-widget bindings out here can never
        # fire; the pointer route always does).

        try:
            self._data = settings_store.load()
            self._render_all()
        except Exception as exc:  # noqa: BLE001
            self._show_fatal_error(exc)

        # Overrideredirect windows like this one can, on real Windows,
        # become visible on screen before Tk has actually finished
        # painting every freshly-built child widget -- showing nothing
        # but this window's own plain background (a "blank white
        # panel") until some interaction (move/resize/focus) forces a
        # repaint. Re-applying this exact geometry once more right now,
        # with an update_idletasks() in between to force Tk to actually
        # process the pending layout/paint work, closes that window --
        # the same "set it, flush, set it again" trick
        # _set_geometry_firmly already uses elsewhere in this codebase
        # for the same class of problem (see its own docstring).
        self._set_geometry_firmly(x, y, w, h)
        try:
            self.lift()
            self.update()
        except tk.TclError:
            pass

    # ---------------------------------------------------------------
    # Fast travel: floating ---↑ Top / ---↓ Bottom pills (F38)
    # ---------------------------------------------------------------
    def _add_jump_buttons(self, host, canvas, tag):
        """Floating fast-scroll pills, bottom-right of the page
        (user-directed: long settings/profile pages needed one-tap
        travel). Auto-visible: 'Top' only once scrolled down a touch,
        'Bottom' only while content remains below. The click is a short
        12-tick ease-out glide (same 20 ms / 50 Hz heartbeat as the
        edge auto-scroll) instead of a disorienting jump."""
        holder = ctk.CTkFrame(host, fg_color="transparent",
                              corner_radius=0)
        holder.place(relx=1.0, rely=1.0, x=-14, y=-14, anchor="se")
        btn_top = ctk.CTkButton(
            holder, text="↑ Top", width=66, height=26,
            corner_radius=13, command=lambda: self._fly_scroll(
                canvas, 0.0))
        btn_bottom = ctk.CTkButton(
            holder, text="↓ Bottom", width=66, height=26,
            corner_radius=13,
            command=lambda: self._fly_scroll(canvas, None))
        panel_ui.style_button(btn_top, C, "ghost")
        panel_ui.style_button(btn_bottom, C, "ghost")
        self._jump_btns = getattr(self, "_jump_btns", {})
        self._jump_btns[tag] = (btn_top, btn_bottom)

        def _refresh():
            try:
                if not canvas.winfo_exists():
                    return
                top_f, bot_f = canvas.yview()
                if top_f > 0.04:      #(beyond a touch of scroll)
                    btn_top.pack(side="left", padx=(0, 6))
                else:
                    btn_top.pack_forget()
                if bot_f < 0.96:      #more content hiding below
                    btn_bottom.pack(side="left")
                else:
                    btn_bottom.pack_forget()
                holder.lift()
            except tk.TclError:
                return                #panel died between schedules
            self.after(350, _refresh)

        _refresh()

    def _fly_scroll(self, canvas, target):
        """12-tick ease-out glide to a fraction (0.0 = top; None = bottom,
        computed from the live scrollregion so long pages land exactly
        at their end)."""
        old_job = getattr(canvas, "_settings_fly_job", None)
        if old_job is not None:
            try:
                canvas.after_cancel(old_job)
            except tk.TclError:
                pass
            canvas._settings_fly_job = None
        try:
            if target is None:
                bbox = canvas.bbox("all")
                region_h = max(1.0, float(bbox[3] - bbox[1]))
                view_h = float(canvas.winfo_height())
                target = max(0.0, (region_h - view_h) / region_h)
            start = canvas.yview()[0]
        except tk.TclError:
            return
        steps = {"i": 0, "n": 12}

        def _step():
            try:
                steps["i"] += 1
                t = steps["i"] / steps["n"]
                eased = 1 - (1 - t) ** 3
                canvas.yview_moveto(start + (target - start) * eased)
            except tk.TclError:
                canvas._settings_fly_job = None
                return
            if steps["i"] < steps["n"]:
                canvas._settings_fly_job = canvas.after(20, _step)
            else:
                canvas._settings_fly_job = None

        _step()

    # ---------------------------------------------------------------
    # Pages: Keys & Connect / Profile
    # ---------------------------------------------------------------
    def show_page(self, name):
        """Flip the top switcher. Both pages are always built (cheap, keeps
        every row's state while you hop page to page); only the packing
        changes. `initial_page` lets the 'open my profile' voice route
        land here directly (see app_panels' profile redirect)."""
        self._page = name
        # keep the top switcher's active segment honest even when a page
        # flip came from outside (e.g. the 'open my profile' redirect)
        self._seg.set(self._page_labels[name])
        if name == "keys":
            self._check_all_btn.pack(side="right", padx=8, pady=6)
        else:
            self._check_all_btn.pack_forget()
        hosts = {"keys": self._keys_host, "profile": self._profile_host}
        for key, host in hosts.items():
            if key == name:
                host.pack(side="top", fill="both", expand=True)
            else:
                host.pack_forget()

    def _build_profile_page(self, parent):
        """Who the user is: identity, links, photo, bio, connected apps,
        and (encrypted + always-masked) web passwords -- everything the
        user chose to keep here in their own words. Saved blob lands in
        Friday's encrypted store; Friday reads it into every prompt."""
        host = ctk.CTkFrame(parent, fg_color=C["editor"], corner_radius=0)
        canvas = tk.Canvas(host, bg=C["editor"], highlightthickness=0,
                           bd=0)
        canvas.pack(side="left", fill="both", expand=True)
        body = ctk.CTkFrame(canvas, fg_color=C["editor"], corner_radius=0)
        win = canvas.create_window((0, 0), window=body, anchor="nw")

        def _body_conf(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _canvas_conf(e):
            # Same panel-fitting column as the keys page (F36): nothing
            # clips; the right-anchored actions guarantee that.
            w = min(e.width - 2 * _CONTENT_PAD, _CONTENT_CAP)
            # canvas-window width (not body.configure) -- pack doesn't
            # clamp childrens' natural widths.
            canvas.itemconfigure(win, width=w)
            canvas.coords(win, max(0, (e.width - w) // 2), 0)

        body.bind("<Configure>", _body_conf)
        canvas.bind("<Configure>", _canvas_conf)
        touchpad_scroll.attach_edge_autoscroll(self, canvas, tick_ms=20)
        self._profile_canvas = canvas
        self._add_jump_buttons(host, canvas, "profile")

        import user_profile
        profile = user_profile.load()
        self._profile_vars = {}

        def _heading(text):
            ctk.CTkLabel(body, text=text, text_color=C["fg"],
                         font=("Segoe UI", 12, "bold")).pack(
                anchor="w", padx=14, pady=(16, 2))

        def _entry(key, label, placeholder="", secret=False):
            ctk.CTkLabel(body, text=label, text_color=C["fg_dim"],
                         font=("Segoe UI", 10)).pack(
                anchor="w", padx=14, pady=(8, 0))
            ent = _style_entry(ctk.CTkEntry(body, placeholder_text=
                placeholder, show="•" if secret else ""))
            ent.pack(fill="x", padx=14, pady=(2, 0), ipady=4)
            if profile.get(key):
                ent.insert(0, profile[key])
            self._profile_vars[key] = ent
            return ent

        _heading("Who you are")
        _entry("name", "Real name", "e.g. Ada Lovelace")
        _entry("dev_names", "Dev names / handles (comma-separated)",
               "e.g. alovelace, ada-dev")
        ctk.CTkLabel(body, text="Bio -- describe yourself (this text is "
                     "what Friday reads to know you)",
                     text_color=C["fg_dim"], font=("Segoe UI", 10)).pack(
            anchor="w", padx=14, pady=(8, 0))
        self._bio_text = tk.Text(
            body, height=4, bg=C["input"], fg=C["fg"], bd=0,
            insertbackground=C["caret"], font=("Segoe UI", 10),
            wrap="word", highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C.get("border_hi", C["border"]))
        self._bio_text.pack(fill="x", padx=14, pady=(2, 0))
        if profile.get("bio"):
            self._bio_text.insert("1.0", profile["bio"])

        _entry("interests", "Interests \u2014 what you care about",
               placeholder="e.g. AI agents, music production, fintech")
        _entry("goals", "Goals \u2014 what you're working toward",
               placeholder="e.g. ship a SaaS, pass finals, learn Rust")

        _heading("Photo")
        photo_row = ctk.CTkFrame(body, fg_color="transparent")
        photo_row.pack(fill="x", padx=14, pady=(4, 0))
        self._photo_entry = _style_entry(ctk.CTkEntry(
            photo_row,
            placeholder_text="path to an image file, or an https:// URL"))
        self._photo_entry.pack(side="left", fill="x", expand=True, ipady=4)
        if profile.get("photo"):
            self._photo_entry.insert(0, profile["photo"])

        def _browse():
            from tkinter import filedialog
            picked = filedialog.askopenfilename(
                parent=self, title="Pick your profile photo",
                filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif")])
            if picked:
                self._photo_entry.delete(0, "end")
                self._photo_entry.insert(0, picked)
                _preview()

        def _preview():
            img = user_profile.avatar_photoimage(
                {"photo": self._photo_entry.get().strip()}, size=56,
                master=self)
            if img is not None:
                self._photo_img = img   # keep a reference: GC eats images
                self._avatar_holder.configure(image=img, text="")
            else:
                self._avatar_holder.configure(
                    image=None, text="(couldn't load that photo \u2014 "
                    "check the path/URL)")

        panel_ui.style_button(
            ctk.CTkButton(photo_row, text="Browse\u2026", width=80,
                          command=_browse), C, "ghost"
        ).pack(side="left", padx=(6, 0))
        panel_ui.style_button(
            ctk.CTkButton(photo_row, text="Preview", width=80,
                          command=_preview), C, "ghost"
        ).pack(side="left", padx=(6, 0))
        panel_ui.style_button(
            ctk.CTkButton(photo_row, text="✕", width=30,
                          command=self._clear_photo), C, "danger"
        ).pack(side="left", padx=(6, 0))
        self._photo_img = None
        self._avatar_holder = ctk.CTkLabel(
            body, text="(no photo yet)", font=("Segoe UI", 10),
            text_color=C["fg_dim"])
        self._avatar_holder.pack(anchor="w", padx=14, pady=(6, 0))
        img0 = user_profile.avatar_photoimage(profile, size=56,
                                              master=self)
        if img0 is not None:
            self._photo_img = img0
            self._avatar_holder.configure(image=img0, text="")

        _heading("Links")
        self._link_vars = {}
        for key, label in (("website", "Website"),
                           ("portfolio", "Portfolio"),
                           ("github", "GitHub"),
                           ("twitter", "Twitter / X"),
                           ("linkedin", "LinkedIn"),
                           ("telegram", "Telegram")):
            ctk.CTkLabel(body, text=label, text_color=C["fg_dim"],
                         font=("Segoe UI", 10)).pack(
                anchor="w", padx=14, pady=(8, 0))
            ent = _style_entry(
                ctk.CTkEntry(body, placeholder_text="https://"))
            ent.pack(fill="x", padx=14, pady=(2, 0), ipady=4)
            value = (profile.get("links") or {}).get(key, "")
            if value:
                ent.insert(0, value)
            self._link_vars[key] = ent

        _heading("Emails")
        ctk.CTkLabel(
            body,
            text="Mailboxes Friday can work for you (read / send / "
                 "sign-ups). Add as many as you have \u2014 when a task "
                 "needs mail and you've added 2 or more, Friday asks "
                 "which one to use. Passwords are encrypted, masked, "
                 "and never reach any prompt. Setup only for now: no "
                 "mail action happens yet.",
            text_color=C["fg_dim"], font=("Segoe UI", 10),
            justify="left", wraplength=560, anchor="w").pack(
            anchor="w", padx=14, pady=(2, 0), fill="x")
        self._emails_host = ctk.CTkFrame(body, fg_color="transparent")
        self._emails_host.pack(fill="x", padx=14, pady=(6, 0))
        self._email_rows = []
        for stored in (profile.get("emails") or []):
            self._email_row(stored=stored)
        add_e = ctk.CTkFrame(body, fg_color="transparent")
        add_e.pack(fill="x", padx=14, pady=(8, 0))
        self._new_addr = _style_entry(ctk.CTkEntry(
            add_e, placeholder_text="email address"))
        self._new_addr.pack(side="left", fill="x", expand=True, ipady=4)
        self._new_epass = _style_entry(ctk.CTkEntry(
            add_e, placeholder_text="password", show="•"))
        self._new_epass.pack(side="left", fill="x", expand=True,
                             ipady=4, padx=(6, 0))
        panel_ui.style_button(ctk.CTkButton(
            add_e, text="+ Add email", width=90,
            command=lambda: self._email_row(stored=None)), C
        ).pack(side="left", padx=(8, 0))

        _heading("Connect apps")
        ctk.CTkLabel(body, text="Apps you use (comma-separated) \u2014 "
                     "context so Friday knows your toolbox",
                     text_color=C["fg_dim"], font=("Segoe UI", 10),
                     wraplength=560, justify="left").pack(
            anchor="w", padx=14, pady=(4, 0))
        self._apps_entry = _style_entry(ctk.CTkEntry(
            body,
            placeholder_text="e.g. VS Code, GitHub, Figma, Slack"))
        self._apps_entry.pack(fill="x", padx=14, pady=(2, 0), ipady=4)
        if profile.get("connected_apps"):
            self._apps_entry.insert(0, ", ".join(profile["connected_apps"]))

        _heading("GitHub authors")
        ctk.CTkLabel(
            body,
            text="Commit identities for YOUR GitHub accounts (username "
                 "+ email). A clone carrying one of these usernames "
                 "commits as that author automatically -- no asking; "
                 "other repos let you pick. The third field is that "
                 "account's own ACCESS TOKEN for GitHub actions (masked, "
                 "never prompted). Friday uses only the selected account's "
                 "token from this section for commits/pushes/PRs; Web "
                 "passwords is not a fallback for those actions.",
            text_color=C["fg_dim"], font=("Segoe UI", 10),
            justify="left", wraplength=560, anchor="w").pack(
            anchor="w", padx=14, pady=(2, 0), fill="x")
        self._gh_host = ctk.CTkFrame(body, fg_color="transparent")
        self._gh_host.pack(fill="x", padx=14, pady=(6, 0))
        self._gh_rows = []
        for stored in (profile.get("github_authors") or []):
            self._gh_row(stored=stored)
        add_g = ctk.CTkFrame(body, fg_color="transparent")
        add_g.pack(fill="x", padx=14, pady=(8, 0))
        self._new_gh_user = _style_entry(ctk.CTkEntry(
            add_g, placeholder_text="github username"))
        self._new_gh_user.pack(side="left", fill="x", expand=True,
                               ipady=4)
        self._new_gh_mail = _style_entry(ctk.CTkEntry(
            add_g, placeholder_text="author email"))
        self._new_gh_mail.pack(side="left", fill="x", expand=True,
                               ipady=4, padx=(6, 0))
        self._new_gh_tok = _style_entry(ctk.CTkEntry(
            add_g, placeholder_text="token (optional)", show="•"))
        self._new_gh_tok.pack(side="left", fill="x", expand=True,
                              ipady=4, padx=(6, 0))
        self._token_eye(add_g, self._new_gh_tok).pack(side="left", padx=(4, 0))
        panel_ui.style_button(ctk.CTkButton(
            add_g, text="+ Add author", width=90,
            command=lambda: self._gh_row(stored=None)), C
        ).pack(side="left", padx=(8, 0))

        _heading("Web passwords")
        ctk.CTkLabel(body, text="Stored encrypted inside Friday's local "
                     "store and shown only as masks \u2014 Friday never "
                     "reads them into prompts or speaks them.",
                     text_color=C["fg_dim"], font=("Segoe UI", 10),
                     wraplength=560, justify="left").pack(
            anchor="w", padx=14, pady=(4, 0))
        self._creds_host = ctk.CTkFrame(body, fg_color="transparent")
        self._creds_host.pack(fill="x", padx=14, pady=(4, 0))
        self._cred_rows = []
        for row in profile.get("credentials", []):
            self._cred_row(stored=row)

        add = ctk.CTkFrame(body, fg_color="transparent")
        add.pack(fill="x", padx=14, pady=(6, 0))
        self._new_site = _style_entry(
            ctk.CTkEntry(add, placeholder_text="site url"))
        self._new_site.pack(side="left", fill="x", expand=True, ipady=4)
        self._new_mail = _style_entry(
            ctk.CTkEntry(add, placeholder_text="email (optional)"))
        self._new_mail.pack(side="left", fill="x", expand=True,
                            ipady=4, padx=(6, 0))
        self._new_user = _style_entry(
            ctk.CTkEntry(add, placeholder_text="username"))
        self._new_user.pack(side="left", fill="x", expand=True,
                            ipady=4, padx=(6, 0))
        self._new_pass = _style_entry(
            ctk.CTkEntry(add, placeholder_text="password",
                         show="•"))
        self._new_pass.pack(side="left", fill="x", expand=True,
                            ipady=4, padx=(6, 0))
        panel_ui.style_button(
            ctk.CTkButton(add, text="Add", width=70,
                          command=lambda: self._cred_row(stored=None)),
            C, "ghost").pack(side="left", padx=(6, 0))

        save_row = ctk.CTkFrame(body, fg_color="transparent")
        save_row.pack(fill="x", padx=14, pady=(16, 12))
        self._profile_status = ctk.CTkLabel(save_row, text="",
                                            text_color=C["fg_dim"],
                                            font=("Segoe UI", 10))
        self._profile_status.pack(side="left")
        panel_ui.style_button(
            ctk.CTkButton(save_row, text="Save profile",
                          command=self._save_profile),
            C, "primary").pack(side="right")
        return host

    def _clear_photo(self):
        """The X next to the photo row: drop the path AND the preview,
        back to the honest placeholder."""
        self._photo_entry.delete(0, "end")
        self._photo_img = None
        try:
            self._avatar_holder.configure(
                image=None, text="(no photo yet)")
        except tk.TclError:
            pass

    def _email_row(self, stored):
        """One mailbox row (F40 multi-email): {address, password}.
        Same vault contract as web passwords -- masked by default,
        user-only Show toggle, delete asks first."""
        import user_profile
        frame = ctk.CTkFrame(self._emails_host, fg_color="transparent")
        if stored is None:
            address = self._new_addr.get().strip()
            if not address:
                return
            row = {"address": address,
                   "password": self._new_epass.get()}
            self._new_addr.delete(0, "end")
            self._new_epass.delete(0, "end")
        else:
            row = dict(stored)
        ctk.CTkLabel(frame, text=row["address"], text_color=C["fg"],
                     font=("Segoe UI", 10, "bold")).pack(side="left")
        pass_lbl = ctk.CTkLabel(
            frame, text=user_profile.mask(row.get("password", "")),
            text_color=C["fg_dim"], font=("Segoe UI", 10))
        pass_lbl.pack(side="left", padx=(8, 0))
        reveal = {"on": False}

        def _toggle_reveal(btn=None):
            reveal["on"] = not reveal["on"]
            if reveal["on"]:
                pass_lbl.configure(text=row.get("password", ""),
                                   font=("Consolas", 9))
                btn.configure(text="Hide")
            else:
                pass_lbl.configure(
                    text=user_profile.mask(row.get("password", "")),
                    font=("Segoe UI", 10))
                btn.configure(text="Show")

        show_btn = ctk.CTkButton(frame, text="Show", width=44,
                                 height=22,
                                 command=lambda: _toggle_reveal(
                                     btn=show_btn))
        panel_ui.style_button(show_btn, C, "ghost")
        show_btn.pack(side="left", padx=(8, 0))
        panel_ui.style_button(
            ctk.CTkButton(frame, text="✕", width=26, height=22,
                          command=lambda: self._del_email(frame)),
            C, "danger").pack(side="right")
        frame.pack(fill="x", pady=1)
        self._email_rows.append({"frame": frame, "row": row})

    def _token_eye(self, parent, entry):
        """Drawn eye control; avoids platform-dependent emoji fonts."""
        state = {"visible": False}
        eye = tk.Canvas(parent, width=28, height=24, bg=C["sidebar"],
                        highlightthickness=1,
                        highlightbackground=C["border"], cursor="hand2")

        def draw():
            eye.delete("all")
            color = C["fg_dim"]
            eye.create_oval(4, 5, 24, 19, outline=color, width=1.5)
            eye.create_oval(11, 8, 17, 16, outline=color, width=1.2)
            eye.create_oval(13, 10, 15, 14, fill=color, outline=color)
            if not state["visible"]:
                eye.create_line(5, 20, 23, 4, fill=color, width=1.5)

        def toggle(_event=None):
            state["visible"] = not state["visible"]
            entry.configure(show="" if state["visible"] else "•")
            draw()

        draw()
        eye.bind("<Button-1>", toggle)
        return eye

    def _gh_row(self, stored):
        """One GitHub account identity row. Username + email are public
        info (shown plain, that's what a commit displays); the F47
        token is this account's OWN push key -- a masked entry,
        editable in place, its value only ever saved encrypted."""
        frame = ctk.CTkFrame(self._gh_host, fg_color="transparent")
        if stored is None:
            user = self._new_gh_user.get().strip()
            if not user:
                return
            row = {"username": user,
                   "email": self._new_gh_mail.get().strip(),
                   "token": self._new_gh_tok.get().strip()}
            self._new_gh_user.delete(0, "end")
            self._new_gh_mail.delete(0, "end")
            self._new_gh_tok.delete(0, "end")
        else:
            row = dict(stored)
            row.setdefault("token", "")
        ctk.CTkLabel(frame, text=row["username"], text_color=C["fg"],
                     font=("Segoe UI", 10, "bold")).pack(side="left")
        ctk.CTkLabel(frame, text=row.get("email", ""),
                     text_color=C["fg_dim"], font=("Segoe UI", 10)
                     ).pack(side="left", padx=(8, 0))
        tok = _style_entry(ctk.CTkEntry(
            frame, placeholder_text="token (optional)", show="•"))
        if row.get("token"):
            tok.insert(0, row["token"])
        tok.pack(side="left", fill="x", expand=True, padx=(10, 4))
        self._token_eye(frame, tok).pack(side="left", padx=(0, 6))
        panel_ui.style_button(
            ctk.CTkButton(frame, text="✕", width=26, height=22,
                          command=lambda: self._del_gh(frame)),
            C, "danger").pack(side="right")
        frame.pack(fill="x", pady=1)
        self._gh_rows.append({"frame": frame, "row": row, "tok": tok})

    def _del_gh(self, frame):
        rec = next((r for r in self._gh_rows if r["frame"] is frame),
                   None)
        user = (rec or {}).get("row", {}).get("username", "this author")
        if not messagebox.askyesno(
                "Remove GitHub author",
                f"Are you sure you want to remove the GitHub author "
                f"{user}?",
                parent=self):
            return
        self._gh_rows = [r for r in self._gh_rows
                         if r["frame"] is not frame]
        frame.destroy()

    def _del_email(self, frame):
        rec = next((r for r in self._email_rows if r["frame"] is frame),
                   None)
        address = (rec or {}).get("row", {}).get("address", "this email")
        if not messagebox.askyesno(
                "Remove email",
                f"Are you sure you want to remove {address}?",
                parent=self):
            return
        self._email_rows = [r for r in self._email_rows
                            if r["frame"] is not frame]
        frame.destroy()

    def _cred_row(self, stored):
        """One credentials row. `stored` rows come from the encrypted
        store: their password field is locked to a mask and the real
        value rides along in the row record (so a re-save doesn't
        overwrite it with asterisks). New rows' entry text IS the value."""
        import user_profile
        frame = ctk.CTkFrame(self._creds_host, fg_color="transparent")
        if stored is None:
            site = self._new_site.get().strip()
            if not site:
                return
            # F40 record format: site url, email, username, password
            row = {"site": site,
                   "email": self._new_mail.get(),
                   "username": self._new_user.get(),
                   "password": self._new_pass.get()}
            self._new_site.delete(0, "end")
            self._new_mail.delete(0, "end")
            self._new_user.delete(0, "end")
            self._new_pass.delete(0, "end")
        else:
            row = dict(stored)
        row_frame = frame
        ctk.CTkLabel(row_frame, text=row["site"], text_color=C["fg"],
                     font=("Segoe UI", 10, "bold")).pack(side="left")
        ctk.CTkLabel(row_frame, text=row.get("email", ""),
                     text_color=C["fg_dim"], font=("Segoe UI", 10)
                     ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row_frame, text=row.get("username", ""),
                     text_color=C["fg_dim"], font=("Segoe UI", 10)
                     ).pack(side="left", padx=(8, 0))
        # Masked by DEFAULT (fixed-length mask, leaks nothing); the
        # Show toggle reveals the plaintext locally -- to the USER ONLY
        # (their own screen, their own vault; spec F39: "viewed by only
        # the user"). Passwords still never reach prompts or logs.
        pass_lbl = ctk.CTkLabel(
            row_frame, text=user_profile.mask(row.get("password", "")),
            text_color=C["fg_dim"], font=("Segoe UI", 10))
        pass_lbl.pack(side="left", padx=(8, 0))
        reveal = {"on": False}

        def _toggle_reveal(btn=None):
            reveal["on"] = not reveal["on"]
            if reveal["on"]:
                pass_lbl.configure(text=row.get("password", ""),
                                   font=("Consolas", 9))
                btn.configure(text="Hide")
            else:
                pass_lbl.configure(
                    text=user_profile.mask(row.get("password", "")),
                    font=("Segoe UI", 10))
                btn.configure(text="Show")

        show_btn = ctk.CTkButton(row_frame, text="Show", width=44,
                                 height=22,
                                 command=lambda: _toggle_reveal(btn=show_btn))
        panel_ui.style_button(show_btn, C, "ghost")
        show_btn.pack(side="left", padx=(8, 0))
        panel_ui.style_button(
            ctk.CTkButton(row_frame, text="\u2715", width=26, height=22,
                          command=lambda: self._del_cred(frame)),
            C, "danger").pack(side="right")
        row_frame.pack(fill="x", pady=1)
        self._cred_rows.append({"frame": row_frame, "row": row})

    def _del_cred(self, frame):
        # user-specified (F40): "are you sure you want to remove the web
        # password" -- every single removal asks first, like Keys rows.
        rec = next((r for r in self._cred_rows if r["frame"] is frame),
                   None)
        site = (rec or {}).get("row", {}).get("site", "this site")
        if not messagebox.askyesno(
                "Remove web password",
                f"Are you sure you want to remove the web password "
                f"for {site}?",
                parent=self):
            return
        self._cred_rows = [r for r in self._cred_rows
                           if r["frame"] is not frame]
        frame.destroy()

    def _save_profile(self):
        import user_profile
        profile = user_profile.default_profile()
        profile["name"] = self._profile_vars["name"].get().strip()
        profile["dev_names"] = self._profile_vars["dev_names"].get().strip()
        profile["bio"] = self._bio_text.get("1.0", "end").strip()
        profile["interests"] = self._profile_vars["interests"].get().strip()
        profile["goals"] = self._profile_vars["goals"].get().strip()
        profile["photo"] = self._photo_entry.get().strip()
        for key, ent in self._link_vars.items():
            profile["links"][key] = ent.get().strip()
        apps = [a.strip() for a in self._apps_entry.get().split(",")
                if a.strip()]
        profile["connected_apps"] = apps
        # multi-mailbox: each row record carries the REAL secret (the
        # UI shows it masked; Show is user-only-eyeballs)
        profile["emails"] = [dict(r["row"]) for r in self._email_rows]
        profile["github_authors"] = [{**dict(r["row"]),
                                      "token": r["tok"].get().strip()}
                                     for r in self._gh_rows]
        profile["credentials"] = [r["row"] for r in self._cred_rows]
        try:
            user_profile.save(profile)
            who = profile["name"] or "you"
            self._profile_status.configure(
                text="Saved \u2014 Friday now knows " + who + ".")
            try:
                import friday_chat.view as _view
                _view.refresh_presence()   # chat badge updates live
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            self._profile_status.configure(text="Save FAILED: " + str(exc))

    def destroy(self):
        """Release singleton diagnostics and animation state before Tk teardown."""
        if getattr(self, "_destroyed_settings", False):
            return
        self._destroyed_settings = True
        self._closing = True
        touchpad_scroll.set_probe(None, owner=self)
        try:
            self._active_glow.stop()
        except Exception:  # noqa: BLE001
            pass
        super().destroy()

    def _show_fatal_error(self, exc):
        """Shown in place of the connection list if settings_store.load()
        or the initial render ever raises -- never leave the user
        staring at a silently blank panel with no explanation and (had
        this not been caught) possibly no way to close it either; the
        titlebar's close button above is already built and working by
        the time this can run."""
        for child in self._body.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self._body, text="Settings couldn't fully load.",
            text_color=C["danger"], font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", padx=14, pady=(16, 4))
        ctk.CTkLabel(
            self._body, text=str(exc), text_color=C["fg_dim"],
            font=("Segoe UI", 10), wraplength=640, justify="left",
        ).pack(anchor="w", padx=14)
        ctk.CTkLabel(
            self._body,
            text="Close this panel and try opening Settings again -- "
                 "if it keeps happening, check the terminal Friday "
                 "was started from for the full error.",
            text_color=C["fg_faint"], font=("Segoe UI", 10),
            wraplength=640, justify="left",
        ).pack(anchor="w", padx=14, pady=(8, 0))

    # ---------------------------------------------------------------
    # Rendering
    # ---------------------------------------------------------------
    def _render_all(self):
        old_sections = tuple(self._body.winfo_children())
        for section in old_sections:
            section.pack_forget()
        if old_sections:
            self.after_idle(
                lambda sections=old_sections: self._destroy_sections(sections))
        self._row_widgets.clear()
        self._sel_vars = {}
        section = ctk.CTkFrame(
            self._body, fg_color=C["panel"], border_width=1,
            border_color=C["border"], corner_radius=10)
        self._connection_section = section
        section.pack(side="top", fill="x", pady=(8, 4), padx=10)
        header = ctk.CTkFrame(
            section, fg_color="transparent", corner_radius=0)
        header.pack(side="top", fill="x", padx=12, pady=(10, 6))
        ctk.CTkLabel(
            header, text="Connected models & services",
            text_color=C["fg_bright"],
            font=("Segoe UI", 12, "bold")).pack(side="left")
        panel_ui.style_button(
            ctk.CTkButton(
                header, text="+ Add model",
                command=lambda: self._open_add_dialog("Thinking")),
            C, "secondary").pack(side="right", padx=(6, 0))
        panel_ui.style_button(
            ctk.CTkButton(
                header, text="+ Add image provider",
                command=lambda: self._open_add_dialog(
                    "Image Generation", "Add image provider")),
            C, "secondary").pack(side="right")

        ctk.CTkLabel(
            section,
            text=("Friday tries task-matched chat models first, then other "
                  "compatible chat models. Search, image, embedding, and "
                  "local services are used only for supported tasks; video "
                  "generation remains local."),
            text_color=C["fg_faint"], font=("Segoe UI", 10),
            justify="left", wraplength=640, anchor="w",
        ).pack(side="top", fill="x", padx=12, pady=(0, 8))

        connection_summary = ctk.CTkFrame(
            section, fg_color="transparent", corner_radius=0)
        connection_summary.pack(
            side="top", fill="x", padx=12, pady=(0, 8))
        self._connection_counts_lbl = ctk.CTkLabel(
            connection_summary, text="", text_color=C["fg_dim"],
            font=("Segoe UI", 10), anchor="w")
        self._connection_counts_lbl.pack(side="left", fill="x", expand=True)
        self._remove_disconnected_btn = panel_ui.style_button(
            ctk.CTkButton(
                connection_summary, text="Remove not connected (0)",
                state="disabled", command=self._remove_disconnected),
            C, "danger")
        self._remove_disconnected_btn.pack(side="right")
        ctk.CTkLabel(
            section,
            text=("Bulk removal deletes only connections whose latest check "
                  "failed; unchecked models are kept."),
            text_color=C["fg_faint"], font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="top", fill="x", padx=12, pady=(0, 6))

        rows_host = ctk.CTkFrame(
            section, fg_color="transparent", corner_radius=0)
        rows_host.pack(side="top", fill="x", padx=8, pady=(0, 8))
        entries = [
            (faculty, entry)
            for faculty in settings_store.FACULTIES
            for entry in self._data.get(faculty, [])
        ]
        if not entries:
            ctk.CTkLabel(
                rows_host, text="No connections added yet.",
                text_color=C["fg_faint"], font=("Segoe UI", 10, "italic"),
            ).pack(side="top", anchor="w", pady=4)
        for faculty, entry in entries:
            self._render_row(rows_host, faculty, entry)
        self._update_connection_summary()

        bar = ctk.CTkFrame(rows_host, fg_color="transparent",
                           corner_radius=0)
        bar.pack(side="top", fill="x", padx=2, pady=(6, 0))
        panel_ui.style_button(ctk.CTkButton(
            bar, text="All", width=54,
            command=lambda: self._set_all_selection(True)),
            C, "ghost").pack(side="left", padx=1)
        panel_ui.style_button(ctk.CTkButton(
            bar, text="None", width=54,
            command=lambda: self._set_all_selection(False)),
            C, "ghost").pack(side="left", padx=1)
        self._remove_selected_btn = panel_ui.style_button(ctk.CTkButton(
            bar, text="Remove selected (0)",
            command=self._remove_selected), C, "danger")
        self._remove_selected_btn.pack(side="right", padx=1)
        self._update_selected_count()
        ctk.CTkFrame(
            section, fg_color=C["border_soft"], height=1, corner_radius=0,
        ).pack(side="top", fill="x", padx=12, pady=(0, 1))

    @staticmethod
    def _destroy_sections(sections):
        for section in sections:
            if section.winfo_exists():
                section.destroy()

    def _render_row(self, parent, faculty, entry):
        row = ctk.CTkFrame(
            parent, fg_color=C["chat"], corner_radius=8)
        row.pack(side="top", fill="x", pady=3)

        # ACTIONS FIRST, packed RIGHT: pack always gives a side-packed
        # child its full requested width before the filling side gets
        # what's left -- so Check/Edit/Remove can NEVER be clipped off
        # the centered column again (the F34 660px cap clipped exactly
        # these at 760px windows; user-report, F35). The info side wraps
        # instead.
        actions = ctk.CTkFrame(row, fg_color="transparent",
                               corner_radius=0)
        actions.pack(side="right", padx=(6, 8), pady=5)
        check_btn = panel_ui.style_button(
            ctk.CTkButton(
                actions, text="Check", width=64,
                command=lambda f=faculty, e=entry: self._run_check(f, e)),
            C)
        check_btn.pack(side="left", padx=2)
        panel_ui.style_button(
            ctk.CTkButton(
                actions, text="Edit", width=64,
                command=lambda f=faculty, e=entry:
                    self._open_edit_dialog(f, e)),
            C).pack(side="left", padx=2)
        panel_ui.style_button(
            ctk.CTkButton(
                actions, text="Remove", width=64,
                command=lambda f=faculty, e=entry: self._remove(f, e)),
            C, "danger").pack(side="left", padx=2)

        selection_id = (faculty, entry["id"])
        sel_var = tk.BooleanVar(value=selection_id in self._selected_ids)
        self._sel_vars[selection_id] = sel_var
        ctk.CTkCheckBox(
            row, text="", variable=sel_var, width=20,
            checkbox_width=18, checkbox_height=18,
            font=("Segoe UI", 10),  # explicit: default CTkFont binds the
                                   # wrong Tk interpreter on Friday
            fg_color=C["accent"], hover_color=C["accent_hover"],
            border_color=C["border"],
            command=lambda f=faculty, e=entry, v=sel_var:
                self._toggle_sel(f, e, v)
        ).pack(side="left", padx=(8, 2), pady=5)

        info = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
        info.pack(side="left", fill="x", expand=True, pady=4)

        head = ctk.CTkFrame(info, fg_color="transparent", corner_radius=0)
        head.pack(side="top", fill="x")
        dot = ctk.CTkLabel(
            head, text="\u25cf", text_color=C["fg_faint"],
            font=("Segoe UI", 10))
        dot.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            head, text=entry["name"], text_color=C["fg"],
            font=("Segoe UI", 10, "bold")).pack(side="left")
        status_lbl = ctk.CTkLabel(head, text="",
                                  font=("Segoe UI", 10, "bold"))
        status_lbl.pack(side="right", padx=(0, 4))

        sub = ctk.CTkFrame(info, fg_color="transparent", corner_radius=0)
        sub.pack(side="top", fill="x")
        model_txt = entry.get("model") or "\u2014"
        ctk.CTkLabel(
            sub, text=model_txt, text_color=C["fg_dim"],
            font=("Segoe UI", 10)).pack(side="left")
        key_lbl = ctk.CTkLabel(
            sub, text=settings_store.mask_key(entry.get("api_key", "")),
            text_color=C["fg_dim"], font=("Consolas", 9))
        key_lbl.pack(side="right")

        # The latest check's full reason, VISIBLE at a glance (previously
        # tooltip-only). Wraps to whatever width the info side actually
        # got after the actions claimed theirs -- never clipped.
        detail_lbl = ctk.CTkLabel(
            info, text="", text_color=C["fg_faint"],
            font=("Segoe UI", 10), justify="left", anchor="w",
            wraplength=320)
        detail_lbl.pack(side="top", fill="x")

        def _rewrap(_e, lbl=detail_lbl, host=info):
            try:
                lbl.configure(wraplength=max(120,
                                             host.winfo_width() - 6))
            except tk.TclError:
                pass

        info.bind("<Configure>", _rewrap, add="+")

        self._row_widgets[(faculty, entry["id"])] = {
            "dot": dot, "status_lbl": status_lbl, "key_lbl": key_lbl,
            "detail_lbl": detail_lbl, "check_btn": check_btn,
        }
        self._apply_status(faculty, entry["id"], entry.get("status", "unknown"),
                            entry.get("detail", ""))

    # ---------------------------------------------------------------\
    # Multi-select removal (F35)
    # ---------------------------------------------------------------\
    def _toggle_sel(self, faculty, entry, var):
        selection_id = (faculty, entry["id"])
        if var.get():
            self._selected_ids.add(selection_id)
        else:
            self._selected_ids.discard(selection_id)
        self._update_selected_count()

    def _update_selected_count(self):
        if self._remove_selected_btn is None:
            return
        n = len(self._selected_ids & set(self._sel_vars))
        try:
            self._remove_selected_btn.configure(
                text=f"Remove selected ({n})",
                state="normal" if n else "disabled")
        except tk.TclError:
            pass

    def _update_connection_summary(self):
        counts = settings_store.connection_counts(self._data)
        if self._connection_counts_lbl is None:
            return
        self._connection_counts_lbl.configure(
            text=(f"Connected: {counts['connected']}  |  "
                  f"Not connected: {counts['not_connected']}  |  "
                  f"Not checked: {counts['not_checked']}"))
        self._remove_disconnected_btn.configure(
            text=f"Remove not connected ({counts['not_connected']})",
            state="normal" if counts["not_connected"] else "disabled")

    def _set_all_selection(self, on):
        for var in self._sel_vars.values():
            var.set(on)
        if on:
            self._selected_ids.update(self._sel_vars)
        else:
            self._selected_ids.difference_update(self._sel_vars)
        self._update_selected_count()

    def _remove_selected(self):
        selected = [selection_id for selection_id in self._sel_vars
                    if selection_id in self._selected_ids]
        if not selected:
            return
        if not messagebox.askyesno(
                "Remove connections",
                f"Remove {len(selected)} selected connection"
                + ("s" if len(selected) != 1 else "") + "?",
                parent=self):
            return
        for faculty, entry_id in selected:
            settings_store.remove_model(self._data, faculty, entry_id)
            self._selected_ids.discard((faculty, entry_id))
        self._render_all()

    def _remove_disconnected(self):
        count = settings_store.connection_counts(
            self._data)["not_connected"]
        if not count:
            return
        if not messagebox.askyesno(
                "Remove disconnected models",
                f"Remove {count} model connection"
                + ("s" if count != 1 else "")
                + " whose latest check failed? Models that haven't been "
                "checked are kept.",
                parent=self):
            return
        settings_store.remove_disconnected_models(self._data)
        self._render_all()

    def _apply_status(self, faculty, model_id, status, detail):
        widgets = self._row_widgets.get((faculty, model_id))
        if not widgets:
            return
        glyph, color, label = _STATUS_STYLE.get(status, _STATUS_STYLE["unknown"])
        widgets["dot"].configure(text_color=color)
        widgets["status_lbl"].configure(text=f"{glyph} {label}", text_color=color)
        tip = detail or label
        for key in ("status_lbl", "dot"):
            try:
                widgets[key].configure(text=widgets[key].cget("text"))
            except tk.TclError:
                pass
        _Tooltip.attach(widgets["status_lbl"], tip)
        # The full reason, plainly on screen: red really explains itself
        # (the actual API error), green states what was proven (the model
        # really generated). Only an empty detail hides the line.
        detail_lbl = widgets.get("detail_lbl")
        if detail_lbl is not None:
            shown = (detail or "")
            if len(shown) > 150:
                shown = shown[:147] + "..."
            try:
                detail_lbl.configure(text=shown,
                                     text_color=color if shown else
                                     C["fg_faint"])
            except tk.TclError:
                pass

    # ---------------------------------------------------------------
    # Add / Edit / Remove
    # ---------------------------------------------------------------
    def _open_add_dialog(self, faculty, title="Add model"):
        def _submit(name, model, api_key, base_url, api_style="",
                    provider_hint=""):
            entry = settings_store.add_model(self._data, faculty, name, model,
                                              api_key, base_url)
            if api_style:
                entry["api_style"] = api_style
            if provider_hint:
                entry["provider_hint"] = provider_hint
            if api_style or provider_hint:
                settings_store.save(self._data)
            self._render_all()
            if api_key or base_url:
                self._run_check(faculty, entry)

        def _submit_many(name, models, api_key, base_url, api_style="",
                         provider_hint=""):
            entries = settings_store.add_models(
                self._data, faculty, name, models, api_key, base_url,
                api_style=api_style, provider_hint=provider_hint)
            self._render_all()
            return entries

        _FieldDialog(
            self, title, faculty, _submit,
            on_add_many=(_submit_many if faculty != "Image Generation" else None))

    def _open_edit_dialog(self, faculty, entry):
        def _submit(name, model, api_key, base_url, api_style="",
                    provider_hint=""):
            entry["name"] = name or entry["name"]
            entry["model"] = model
            entry["api_key"] = api_key
            entry["base_url"] = base_url
            if api_style:
                entry["api_style"] = api_style
            if provider_hint:
                entry["provider_hint"] = provider_hint
            settings_store.save(self._data)
            self._render_all()
        _FieldDialog(self, "Edit model", faculty, _submit, existing=entry)

    def _remove(self, faculty, entry):
        if not messagebox.askyesno(
                "Remove model", f"Remove \"{entry['name']}\"?",
                parent=self):
            return
        settings_store.remove_model(self._data, faculty, entry["id"])
        self._render_all()

    # ---------------------------------------------------------------
    # Connectivity checks (threaded -- see settings_checks.py's own
    # docstring for why these can block on real network I/O)
    # ---------------------------------------------------------------
    def _run_check(self, faculty, entry):
        if self._closing:
            return
        self._apply_status(faculty, entry["id"], "checking", "")
        widgets = self._row_widgets.get((faculty, entry["id"]))
        if widgets:
            try:
                widgets["check_btn"].configure(state="disabled")
            except tk.TclError:
                pass

        def _worker():
            try:
                with _SETTINGS_CHECK_SLOTS:
                    if self._closing:
                        return
                    ok, detail = settings_checks.run_check(dict(entry))
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, str(exc) or "The check failed unexpectedly."
            if self._closing:
                return
            try:
                self.after(0, lambda: self._finish_check(
                    faculty, entry["id"], ok, detail))
            except (tk.TclError, RuntimeError):
                # Settings may close while a network call is in flight.
                pass

        threading.Thread(target=_worker, name="FridaySettingsCheck",
                         daemon=True).start()

    def _finish_check(self, faculty, model_id, ok, detail):
        if self._closing:
            return
        settings_store.update_check_result(self._data, faculty, model_id, ok, detail,
                                            time.time())
        self._apply_status(faculty, model_id, "connected" if ok else "error", detail)
        self._update_connection_summary()
        widgets = self._row_widgets.get((faculty, model_id))
        if widgets:
            try:
                widgets["check_btn"].configure(state="normal")
            except tk.TclError:
                pass

    def _check_all(self):
        for faculty, entries in self._data.items():
            for entry in entries:
                if entry.get("api_key") or entry.get("base_url") or \
                        entry.get("check_kind", "").startswith("local"):
                    self._run_check(faculty, entry)

    # ---------------------------------------------------------------
    # Panel-manager protocol: same methods/semantics as
    # CustomNotepadPanel's own (see its docstring) -- withdraw/deiconify/
    # destroy come from this native Tk Toplevel.
    # ---------------------------------------------------------------
    def restore_default(self):
        x, y, w, h = self._default_geometry
        self._set_geometry_firmly(x, y, w, h)
        try:
            self.lower()
        except tk.TclError:
            pass
        self.is_maximized = False
        self.set_active_panel(False)

    def set_active_panel(self, active):
        active = bool(active)
        if active == getattr(self, "_is_active_panel", False):
            return
        self._is_active_panel = active
        _update_active_panel_glow(self)

    def set_friday_status(self, state, is_error=False):
        self._friday_glow_state = state
        self._friday_glow_is_error = bool(is_error)
        _update_active_panel_glow(self)

    def _set_geometry_firmly(self, x, y, w, h):
        geom = f"{int(w)}x{int(h)}+{int(x)}+{int(y)}"
        try:
            self.geometry(geom)
            self.update_idletasks()
            self.geometry(geom)
        except tk.TclError:
            pass


class _Tooltip:
    """A minimal, dependency-free hover tooltip -- used only to surface
    a status row's full check detail (e.g. the real error message from
    a failed connectivity check) without permanently taking up layout
    space for it."""

    @staticmethod
    def attach(widget, text):
        # Replaces any previously-attached tooltip bindings for this
        # widget (status can be re-checked repeatedly) rather than
        # stacking duplicate handlers.
        widget._tooltip_text = text
        if getattr(widget, "_tooltip_bound", False):
            return
        widget._tooltip_bound = True
        tip = {"win": None}

        def _show(_e):
            if tip["win"] is not None:
                return
            try:
                x = widget.winfo_rootx() + 10
                y = widget.winfo_rooty() + widget.winfo_height() + 4
            except tk.TclError:
                return
            win = tk.Toplevel(widget)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            tk.Label(win, text=getattr(widget, "_tooltip_text", ""), bg="#222222",
                      fg="#ffffff", font=("Segoe UI", 10), padx=6, pady=3,
                      wraplength=320, justify="left").pack()
            win.geometry(f"+{x}+{y}")
            tip["win"] = win

        def _hide(_e):
            if tip["win"] is not None:
                try:
                    tip["win"].destroy()
                except tk.TclError:
                    pass
                tip["win"] = None

        widget.bind("<Enter>", _show, add="+")
        widget.bind("<Leave>", _hide, add="+")

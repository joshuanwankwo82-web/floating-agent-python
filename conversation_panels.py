"""Friday's native CustomTkinter work panels.

Chat hosts the earlier Quiet Chat interface (sidebar/history, transcript,
multiline composer and labelled local sample replies) via friday_chat.
Image Generation hosts the approved Quiet Image interface via
friday_image in cloud-only form: a user's own Cloudflare Workers AI or
Stability AI key (Settings -> Image Generation, chosen per render in the
panel's ENGINE picker) drives real AI generation through
image_backend_resolver.py; with no usable key the panel shows honest
setup guidance and renders nothing.
Video Generation likewise hosts Quiet Video and its deterministic local
GIF/MP4 motion renderer. Both histories use Friday's encrypted kv store,
not semantic AI memory. Profile retains an unconnected draft surface; no
profile backend is added. The video media panel claims no AI provider call.

_TextBoxPanel provides the shared native Toplevel chrome, titlebar actions,
geometry and Friday state border. AppPanelManager continues to own the
window lifecycle and minimized pills for all four panels.
"""

import os
import sys
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk

_IDE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ide")
if _IDE_DIR not in sys.path:
    sys.path.insert(0, _IDE_DIR)
import ide as _ide_module  # noqa: E402
C = _ide_module.C
_ActiveGlowAnimator = _ide_module._ActiveGlowAnimator
_update_active_panel_glow = _ide_module._update_active_panel_glow
import panel_ui


# ---------------------------------------------------------------------------
# Hand-drawn icons -- small Canvas widgets, not font glyphs (see module
# docstring). Each one is a thin clickable button: hover highlight, a
# bound <Button-1>, and a `set_active(bool)` toggle used by the mic/
# speaker buttons' own pressed/active cosmetic state.
# ---------------------------------------------------------------------------

class _IconButton(tk.Canvas):
    SIZE = 28

    def __init__(self, parent, draw_fn, on_click=None, tooltip="", colors=None):
        self._colors = colors or C
        super().__init__(parent, width=self.SIZE, height=self.SIZE,
                          bg=self._colors["input"], highlightthickness=0, cursor="hand2")
        self._draw_fn = draw_fn
        self._on_click = on_click
        self._active = False
        self._tooltip = tooltip
        self._redraw()
        self.bind("<Enter>", lambda _e: self._set_bg(self._colors["hover"]))
        self.bind(
            "<Leave>",
            lambda _e: self._set_bg(
                self._colors["list_sel"] if self._active else self._colors["input"]))
        self.bind("<Button-1>", self._handle_click)

    def _set_bg(self, color):
        self.configure(bg=color)
        self._redraw(bg=color)

    def _redraw(self, bg=None):
        self.delete("all")
        self._draw_fn(self, self.SIZE, self._colors["fg"], bg or self.cget("bg"))

    def _handle_click(self, _event):
        if self._on_click:
            self._on_click()

    def set_active(self, active):
        """Cosmetic pressed/active toggle -- used by mic (listening) and
        speaker (unmuted) so clicking them gives real visual feedback
        even with no backend wired yet."""
        self._active = active
        self._set_bg(
            self._colors["list_sel"] if active else self._colors["input"])


def _draw_attach(cv, size, fg, bg):
    """A simple paperclip silhouette: one smoothed zig-zag line, thick
    enough to read clearly at icon size."""
    pts = [size * 0.64, size * 0.18,
           size * 0.32, size * 0.18,
           size * 0.22, size * 0.32,
           size * 0.30, size * 0.46,
           size * 0.62, size * 0.46,
           size * 0.70, size * 0.60,
           size * 0.60, size * 0.76,
           size * 0.36, size * 0.76]
    cv.create_line(*pts, smooth=True, width=max(2, size // 10), fill=fg,
                    capstyle="round", joinstyle="round")


def _draw_mic(cv, size, fg, bg):
    """A microphone glyph built from solid filled shapes (a capsule
    "stadium" head, a cradle arc, a stem and base) rather than thin
    multi-arc outlines -- at icon sizes this small (28px), a handful of
    sub-3px-radius arcs blur into an unrecognizable blob (confirmed by
    screenshot during development: the original outline version read
    as a plain "0"), while a solid filled silhouette stays legible."""
    w = max(2, size // 9)
    cx = size * 0.5
    top_y, bot_y, r = size * 0.16, size * 0.52, size * 0.13
    cv.create_oval(cx - r, top_y - r, cx + r, top_y + r, fill=fg, outline=fg)
    cv.create_oval(cx - r, bot_y - r, cx + r, bot_y + r, fill=fg, outline=fg)
    cv.create_rectangle(cx - r, top_y, cx + r, bot_y, fill=fg, outline=fg)
    cv.create_arc(cx - r * 2.3, bot_y - r * 0.5, cx + r * 2.3, bot_y + r * 2.3,
                  start=200, extent=140, style="arc", outline=fg, width=w)
    stem_top = bot_y + r * 1.8
    cv.create_line(cx, stem_top, cx, size * 0.86, fill=fg, width=w)
    cv.create_line(cx - size * 0.15, size * 0.86, cx + size * 0.15, size * 0.86,
                    fill=fg, width=w)


def _draw_speaker(cv, size, fg, bg):
    """A speaker cone plus two volume arcs."""
    w = max(2, size // 11)
    cv.create_rectangle(size * 0.16, size * 0.38, size * 0.32, size * 0.62,
                         outline=fg, fill=fg)
    cv.create_polygon(size * 0.32, size * 0.38, size * 0.50, size * 0.22,
                       size * 0.50, size * 0.78, size * 0.32, size * 0.62,
                       outline=fg, fill=fg)
    cv.create_arc(size * 0.54, size * 0.32, size * 0.72, size * 0.68,
                  start=-55, extent=110, style="arc", outline=fg, width=w)
    cv.create_arc(size * 0.60, size * 0.20, size * 0.84, size * 0.80,
                  start=-55, extent=110, style="arc", outline=fg, width=w)


class _TextBoxPanel(tk.Toplevel):
    """Shared base for Chat/Profile/Image Generation/Video Generation:
    identical titlebar/drag/minimize/close chrome as CustomNotepadPanel/
    SettingsPanel, an empty body area with a short placeholder message,
    and the bottom input bar the user asked for -- attach icon (left),
    a text entry (middle, expands), mic + speaker icons (right).

    The draft-surface subclasses set PANEL_TITLE/EMPTY_STATE_TEXT/ENTRY_PLACEHOLDER
    and, if it wants one, overrides `_build_body_extra` for a little
    panel-specific content above the empty-state message (see
    ProfilePanel below). ChatPanel hosts a complete child ChatView instead. The constructor signature matches every other
    _CUSTOM_PANEL_CLASSES entry exactly, so app_panels.py's
    _open_custom_panel dispatches to all of them identically.
    """

    PANEL_TITLE = "Panel"
    EMPTY_STATE_TITLE = "Nothing here yet"
    EMPTY_STATE_TEXT = ""
    EMPTY_STATE_HINT = ""
    ENTRY_PLACEHOLDER = "Type a message..."
    SUBMIT_NOTICE = "This panel is not connected yet. Your draft has not been sent."

    DEFAULT_W = panel_ui.DEFAULT_PANEL_W
    DEFAULT_H = panel_ui.DEFAULT_PANEL_H
    TITLEBAR_H = panel_ui.TITLEBAR_H
    BORDER_PX = panel_ui.BORDER_PX
    INPUT_BAR_H = 56

    def __init__(self, master, on_minimize, on_close, corner_index=0,
                 initial_content=None, bounds=None):
        super().__init__(master)
        self._colors = getattr(self, "COLORS", C)
        self.title(self.PANEL_TITLE)
        self.overrideredirect(True)
        self.configure(bg=self._colors["border_hi"])
        self.is_maximized = False
        self._on_close_cb = on_close
        self._is_active_panel = False
        self._friday_glow_state = "idle"
        self._friday_glow_is_error = False
        self._active_glow = _ActiveGlowAnimator(
            self, lambda c: self.configure(bg=c))

        if bounds is not None:
            x, y, w, h = bounds
        else:
            w, h = self.DEFAULT_W, self.DEFAULT_H
            x, y = panel_ui.corner_position(master, corner_index, w, h)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self._default_geometry = (x, y, w, h)

        bp = self.BORDER_PX
        self._build_titlebar(bp, on_minimize, on_close)
        self._build_input_bar(bp)
        self._build_body(bp)

    # -- titlebar: identical chrome/behavior to every other custom panel --
    def _build_titlebar(self, bp, on_minimize, on_close):
        panel_ui.build_titlebar(
            self, self._colors, self.PANEL_TITLE, on_minimize, on_close,
            self.TITLEBAR_H, bp)

    # -- empty body area, with room for a subclass to add its own extra
    #    content (e.g. Profile's placeholder avatar) above the message --
    def _build_body(self, bp):
        body = ctk.CTkFrame(self, fg_color=self._colors["panel"], corner_radius=0)
        body.pack(side="top", fill="both", expand=True, padx=bp)
        self._body = body
        self._build_body_extra(body)
        empty_state = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0)
        empty_state.pack(expand=True, padx=28, pady=10)
        ctk.CTkLabel(
            empty_state, text="PREVIEW  ·  NOT CONNECTED",
            text_color=self._colors["warn"], font=("Segoe UI", 8, "bold"),
        ).pack(pady=(0, 8))
        ctk.CTkLabel(
            empty_state, text=self.EMPTY_STATE_TITLE,
            text_color=self._colors["fg"], font=("Segoe UI", 12, "bold"),
            wraplength=420, justify="center",
        ).pack(pady=(0, 6))
        ctk.CTkLabel(
            empty_state, text=self.EMPTY_STATE_TEXT,
            text_color=self._colors["fg_dim"], font=("Segoe UI", 9),
            wraplength=420, justify="center",
        ).pack()
        if self.EMPTY_STATE_HINT:
            ctk.CTkLabel(
                empty_state, text=self.EMPTY_STATE_HINT,
                text_color=self._colors["fg_faint"], font=("Segoe UI", 8),
                wraplength=420, justify="center",
            ).pack(pady=(8, 0))
        self._notice_label = ctk.CTkLabel(
            body, text="", text_color=self._colors["warn"],
            font=("Segoe UI", 8), wraplength=520, justify="center")

    def _build_body_extra(self, body):
        """Hook for a subclass to add panel-specific content above the
        empty-state message. No-op by default."""
        pass

    # -- the bottom input bar: attach (left) / text entry (middle,
    #    expands) / mic + speaker (right) -- exactly as requested --
    def _build_input_bar(self, bp):
        bar = ctk.CTkFrame(
            self, fg_color=self._colors.get("bg_main", self._colors["panel"]),
            height=self.INPUT_BAR_H, corner_radius=0)
        bar.pack(side="bottom", fill="x", padx=bp + 10, pady=(0, 10))
        bar.pack_propagate(False)

        inner = ctk.CTkFrame(
            bar, fg_color=self._colors["input"], border_width=1,
            border_color=self._colors["border"], corner_radius=10)
        inner.pack(fill="x", expand=True, pady=10)

        # Packing order matters here, not just visual side= -- Tk's
        # packer hands each widget whatever's left of the cavity *at
        # the moment it's packed*, so every fixed-size widget (attach,
        # and the whole mic+speaker cluster) has to be packed before
        # the expanding entry, regardless of attach being visually
        # leftmost -- otherwise the entry's expand=True greedily claims
        # the entire remaining cavity immediately, leaving nothing for
        # whatever's packed after it (this is exactly what happened the
        # first time: mic/speaker were being packed last and silently
        # squeezed out to zero width).
        self._attach_btn = _IconButton(
            inner, _draw_attach, on_click=self._on_attach_click,
            tooltip="Attach a file", colors=self._colors)
        self._attach_btn.pack(side="left", padx=(8, 4), pady=8)

        right = ctk.CTkFrame(
            inner, fg_color=self._colors["input"], corner_radius=0)
        right.pack(side="right", padx=(4, 8), pady=8)
        self._mic_btn = _IconButton(right, _draw_mic, on_click=self._on_mic_click,
                                     tooltip="Voice input", colors=self._colors)
        self._mic_btn.pack(side="left", padx=(0, 6))
        self._speaker_btn = _IconButton(right, _draw_speaker, on_click=self._on_speaker_click,
                                         tooltip="Read replies aloud",
                                         colors=self._colors)
        self._speaker_btn.pack(side="left")

        self.entry = ctk.CTkEntry(
            inner, fg_color=self._colors["input"],
            text_color=self._colors["fg"],
            placeholder_text=None, border_width=0,
            font=("Segoe UI", 11), corner_radius=6)
        self.entry.pack(side="left", fill="x", expand=True, padx=6, ipady=6)
        self._placeholder = self.ENTRY_PLACEHOLDER
        self._show_placeholder()
        self.entry.bind("<FocusIn>", self._clear_placeholder)
        self.entry.bind("<FocusOut>", self._maybe_restore_placeholder)
        self.entry.bind("<Return>", self._on_entry_submit)

        self._attached_path = None
        self._attach_chip = None  # small "filename.png \u2715" label, shown
        # above the input bar once a file is actually picked -- built
        # lazily in _on_attach_click so a panel that never uses Attach
        # never pays for it.

    # -- entry placeholder handling (same pattern as ide.py's ChatSidePanel) --
    def _show_placeholder(self):
        self.entry.configure(text_color=self._colors["fg_faint"])
        self.entry.delete(0, "end")
        self.entry.insert(0, self._placeholder)
        self._showing_placeholder = True

    def _clear_placeholder(self, _event=None):
        if getattr(self, "_showing_placeholder", False):
            self.entry.delete(0, "end")
            self.entry.configure(text_color=self._colors["fg"])
            self._showing_placeholder = False

    def _maybe_restore_placeholder(self, _event=None):
        if not self.entry.get().strip():
            self._show_placeholder()

    def _on_entry_submit(self, _event=None):
        """Keep the user's draft and state plainly that no backend ran."""
        if getattr(self, "_showing_placeholder", False):
            return "break"
        self._show_notice(self.SUBMIT_NOTICE)
        return "break"

    def _show_notice(self, message):
        self._notice_label.configure(text=message)
        if not self._notice_label.winfo_manager():
            self._notice_label.pack(side="bottom", fill="x", padx=18, pady=(0, 10))

    # -- attach: a real OS file picker, shown as a small chip once chosen --
    def _on_attach_click(self):
        path = filedialog.askopenfilename(parent=self, title="Attach a file")
        if not path:
            return
        self._attached_path = path
        self._show_attach_chip(os.path.basename(path))

    def _show_attach_chip(self, filename):
        self._hide_attach_chip()
        chip = ctk.CTkFrame(
            self._body, fg_color=self._colors["list_sel"], corner_radius=8)
        ctk.CTkLabel(
            chip, text=filename, text_color=self._colors["fg"],
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(8, 4), pady=4)
        remove = ctk.CTkButton(
            chip, text="\u2715", command=self._clear_attachment,
            fg_color="transparent", hover_color=self._colors["hover"],
            text_color=self._colors["fg_dim"], font=("Segoe UI", 8),
            width=24, height=24, corner_radius=6)
        remove.pack(side="left", padx=(0, 3), pady=2)
        chip.place(relx=0.0, rely=1.0, anchor="sw", x=4, y=-4)
        self._attach_chip = chip

    def _hide_attach_chip(self):
        if self._attach_chip is not None:
            self._attach_chip.destroy()
            self._attach_chip = None

    def _clear_attachment(self):
        self._attached_path = None
        self._hide_attach_chip()

    # -- keep controls honest until this panel has voice backends --
    def _on_mic_click(self):
        self._show_notice("Voice input is not connected to this panel.")

    def _on_speaker_click(self):
        self._show_notice("Spoken replies are not connected to this panel.")

    # -- active-panel glow + maximize/restore, same contract every other
    #    custom panel already implements (AppPanelManager calls these) --
    def set_active_panel(self, is_active):
        self._is_active_panel = bool(is_active)
        _update_active_panel_glow(self)

    def set_friday_status(self, state, is_error=False):
        self._friday_glow_state = state
        self._friday_glow_is_error = bool(is_error)
        _update_active_panel_glow(self)

    def restore_default_geometry(self):
        x, y, w, h = self._default_geometry
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.is_maximized = False


class ChatPanel(_TextBoxPanel):
    """Host the original Quiet Chat UI inside Friday's native panel chrome.

    Window bounds, the active-state border, titlebar callbacks and
    minimize-to-pill are still owned by _TextBoxPanel/AppPanelManager.
    The child interface uses Friday's live AIWorker through Settings → Voice
    and can hand the selected transcript to the IDE's Coding-scoped chat.
    Window and panel lifecycle remain owned by Friday's native panel system.
    """
    PANEL_TITLE = "Chat"
    DEFAULT_W = 1100
    DEFAULT_H = 740
    COLORS = dict(
        C, panel="#151517", bg_main="#101012", tabbar="#1B1B1E",
        input="#1E1E21", border="#35353B", border_hi="#29292E",
        hover="#303035", fg="#E7E6E3", fg_dim="#B2B1B8",
        fg_faint="#909098", accent="#C7C3BC", accent_hover="#DAD6D0",
        danger="#68535A",
    )

    def __init__(self, master, on_minimize, on_close, corner_index=0,
                 initial_content=None, bounds=None):
        super().__init__(master, on_minimize, on_close, corner_index,
                         initial_content=initial_content, bounds=bounds)
        if initial_content:
            self.chat_view.use_prompt(str(initial_content))

    def after(self, ms, func=None, *args):
        # CTk's process-wide DPI checker is normally scheduled on the
        # first Toplevel. That window is transient; keep this SDK timer
        # on Friday's persistent owner-thread Tk host instead.
        if func == ctk.ScalingTracker.check_dpi_scaling:
            return self.master.after(ms, func, *args)
        return super().after(ms, func, *args)

    def _build_titlebar(self, bp, on_minimize, on_close):
        from friday_chat.widgets import PanelButton

        panel_ui.build_titlebar(
            self, self._colors, self.PANEL_TITLE, on_minimize, on_close,
            self.TITLEBAR_H, bp, button_class=PanelButton)

    def _build_input_bar(self, bp):
        # The ported view owns its own multiline composer. Do not create
        # the other panels' older single-line draft bar as well.
        pass

    def _build_body(self, bp):
        from friday_chat.live import handoff_to_ide, settings_reply_handler
        from friday_chat.storage import EncryptedChatStore
        from friday_chat.view import ChatView

        self.chat_view = ChatView(
            self, reply_handler=settings_reply_handler,
            storage=EncryptedChatStore(), handoff_handler=handoff_to_ide)
        self.chat_view.pack(fill="both", expand=True, padx=bp, pady=(0, bp))
        self._body = self.chat_view
        # Keep the panel's established public control references for
        # manager integrations, while the view owns their behaviour.
        self.entry = self.chat_view.entry
        self._attach_btn = self.chat_view.attach_button
        self._mic_btn = self.chat_view.mic_button
        self._speaker_btn = self.chat_view.speaker_button
        self._send_btn = self.chat_view.send_button

    def maximize_to(self, x, y, width, height):
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.is_maximized = True

    def move_to_slot(self, x, y, width, height):
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.is_maximized = False

    def restore_default(self):
        self.restore_default_geometry()

    def _on_entry_submit(self, event=None):
        if event is not None:
            return self.chat_view._on_return(event)
        self.chat_view.send_message()
        return "break"

    def _on_attach_click(self):
        self.chat_view.attach_files()

    def _clear_attachment(self):
        self.chat_view.clear_files()

    def _on_mic_click(self):
        self.chat_view._voice_unavailable()

    def _on_speaker_click(self):
        self.chat_view._speech_unavailable()

    def destroy(self):
        # Destroy children/images/timers on this interpreter's owner
        # thread before Friday releases the native window. Idempotent.
        if getattr(self, "_chat_destroyed", False):
            return
        self._chat_destroyed = True
        self._active_glow.stop()
        view = getattr(self, "chat_view", None)
        if view is not None and not view._closed:
            view.destroy()
        super().destroy()
        # Unlike CTkToplevel, this deliberately native Tk Toplevel is
        # not itself a CTk scaling subscriber. Its child widgets have
        # unsubscribed; release the dead SDK window entry as well.
        ctk.ScalingTracker.window_widgets_dict.pop(self, None)
        ctk.ScalingTracker.window_dpi_scaling_dict.pop(self, None)



class ProfilePanel(_TextBoxPanel):
    """A profile panel with a placeholder avatar and the same bottom
    input bar as the others (e.g. for asking Friday to update profile
    info by typing/voice later) -- no real profile data wired up yet."""
    PANEL_TITLE = "Profile"
    EMPTY_STATE_TITLE = "Your profile"
    EMPTY_STATE_TEXT = (
        "Profile editing is not connected to Friday's saved memory yet.")
    EMPTY_STATE_HINT = "Nothing entered here will be stored."
    ENTRY_PLACEHOLDER = "Profile editing is not available yet..."
    SUBMIT_NOTICE = (
        "Profile storage is not connected. Your text remains here and was "
        "not saved.")

    def _build_body_extra(self, body):
        avatar = tk.Canvas(body, width=48, height=48, bg=C["panel"],
                            highlightthickness=0)
        avatar.pack(pady=(8, 0))
        avatar.create_oval(2, 2, 46, 46, fill=C["list_sel"], outline="")
        avatar.create_oval(17, 10, 31, 24, fill=C["fg_bright"], outline="")
        avatar.create_arc(9, 27, 39, 57, start=0, extent=180,
                           fill=C["fg_bright"], outline="", style="chord")


class ImageGenPanel(_TextBoxPanel):
    """Host the complete Quiet Image child view in Friday's native chrome.

    Titlebar callbacks, minimize-to-pill, active glow, bounds and manager
    lifecycle remain Friday-owned. The child owns prompt/art controls and the
    honest deterministic local PNG/JPEG/WebP renderer; no AI provider claim.
    """
    PANEL_TITLE = "Image Generation"
    DEFAULT_W = 1200
    DEFAULT_H = 780
    COLORS = dict(
        C, panel="#151517", bg_main="#101012", tabbar="#1B1B1E",
        input="#1E1E21", border="#35353B", border_hi="#29292E",
        hover="#303035", fg="#E7E6E3", fg_dim="#B2B1B8",
        fg_faint="#909098", accent="#C7C3BC", accent_hover="#DAD6D0",
        danger="#68535A",
    )

    def __init__(self, master, on_minimize, on_close, corner_index=0,
                 initial_content=None, bounds=None):
        super().__init__(master, on_minimize, on_close, corner_index,
                         initial_content=initial_content, bounds=bounds)
        if initial_content:
            self.image_view.use_prompt(str(initial_content), persist=False)

    def after(self, ms, func=None, *args):
        # Keep CTk's transient-window DPI polling on Friday's persistent
        # owner-thread host rather than on this native panel window.
        if func == ctk.ScalingTracker.check_dpi_scaling:
            return self.master.after(ms, func, *args)
        return super().after(ms, func, *args)

    def _build_titlebar(self, bp, on_minimize, on_close):
        from friday_chat.widgets import PanelButton

        panel_ui.build_titlebar(
            self, self._colors, self.PANEL_TITLE, on_minimize, on_close,
            self.TITLEBAR_H, bp, button_class=PanelButton)

    def _build_input_bar(self, bp):
        # The newer panel application owns its own composer/actions. Do not
        # stack the legacy single-line "unavailable" draft bar below it.
        pass

    def _build_body(self, bp):
        from friday_image.view import QuietImageView
        from image_backend_resolver import (image_engine_catalog,
                                            resolve_image_backend)

        # resolve_image_backend is consulted (with the picker's choice)
        # before every render; the catalog fills the panel's engine
        # dropdown from live Settings entries. No key -> the honest local
        # renderer keeps running.
        self.image_view = QuietImageView(self, backend_factory=resolve_image_backend,
                                         engine_lister=image_engine_catalog)
        self.image_view.pack(fill="both", expand=True, padx=bp, pady=(0, bp))
        self._body = self.image_view
        # Established public control references remain available to manager
        # integrations; the child view owns all of their behavior.
        self.entry = self.image_view.prompt_box
        self._send_btn = self.image_view.generate_button
        self._attach_btn = self.image_view.attach_button
        self._mic_btn = None
        self._speaker_btn = None

    @property
    def chat_view(self):
        # Keep migration-era generic references valid without reintroducing
        # the old placeholder surface.
        return self.image_view

    def maximize_to(self, x, y, width, height):
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.is_maximized = True

    def move_to_slot(self, x, y, width, height):
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.is_maximized = False

    def restore_default(self):
        self.restore_default_geometry()

    def _on_entry_submit(self, event=None):
        self.image_view.start_render()
        return "break"

    def _on_attach_click(self):
        self.image_view.choose_reference_image()

    def _clear_attachment(self):
        self.image_view.clear_reference_image()

    def destroy(self):
        if getattr(self, "_image_destroyed", False):
            return
        self._image_destroyed = True
        self._active_glow.stop()
        view = getattr(self, "image_view", None)
        if view is not None and not view._closed:
            view.destroy()
        super().destroy()
        ctk.ScalingTracker.window_widgets_dict.pop(self, None)
        ctk.ScalingTracker.window_dpi_scaling_dict.pop(self, None)


class VideoGenPanel(_TextBoxPanel):
    """Host the complete Quiet Video child view in Friday's native chrome.

    Titlebar callbacks, minimize-to-pill, active glow, bounds and manager
    lifecycle remain Friday-owned. The child owns the prompt/card controls
    and the honest local GIF/MP4 renderer; no AI provider is claimed.
    """
    PANEL_TITLE = "Video Generation"
    DEFAULT_W = 1200
    DEFAULT_H = 780
    COLORS = dict(
        C, panel="#151517", bg_main="#101012", tabbar="#1B1B1E",
        input="#1E1E21", border="#35353B", border_hi="#29292E",
        hover="#303035", fg="#E7E6E3", fg_dim="#B2B1B8",
        fg_faint="#909098", accent="#C7C3BC", accent_hover="#DAD6D0",
        danger="#68535A",
    )

    def __init__(self, master, on_minimize, on_close, corner_index=0,
                 initial_content=None, bounds=None):
        super().__init__(master, on_minimize, on_close, corner_index,
                         initial_content=initial_content, bounds=bounds)
        if initial_content:
            self.video_view.use_prompt(str(initial_content), persist=False)

    def after(self, ms, func=None, *args):
        # Keep CTk's transient-window DPI polling on Friday's persistent
        # owner-thread host rather than on this native panel window.
        if func == ctk.ScalingTracker.check_dpi_scaling:
            return self.master.after(ms, func, *args)
        return super().after(ms, func, *args)

    def _build_titlebar(self, bp, on_minimize, on_close):
        from friday_chat.widgets import PanelButton

        panel_ui.build_titlebar(
            self, self._colors, self.PANEL_TITLE, on_minimize, on_close,
            self.TITLEBAR_H, bp, button_class=PanelButton)

    def _build_input_bar(self, bp):
        # The newer panel application owns its own composer/actions. Do
        # not stack the legacy single-line "unavailable" draft bar below it.
        pass

    def _build_body(self, bp):
        from friday_video.view import QuietVideoView

        self.video_view = QuietVideoView(self)
        self.video_view.pack(fill="both", expand=True, padx=bp, pady=(0, bp))
        self._body = self.video_view
        # Established public control references remain available to manager
        # integrations; the child view owns all of their behavior.
        self.entry = self.video_view.prompt_box
        self._send_btn = self.video_view.generate_button
        self._attach_btn = self.video_view.attach_button
        self._mic_btn = None
        self._speaker_btn = None

    @property
    def chat_view(self):
        # Some migration-era code touched a generic `.chat_view` reference.
        # Keep it valid instead of leaking a stale placeholder object.
        return self.video_view

    def maximize_to(self, x, y, width, height):
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.is_maximized = True

    def move_to_slot(self, x, y, width, height):
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.is_maximized = False

    def restore_default(self):
        self.restore_default_geometry()

    def _on_entry_submit(self, event=None):
        self.video_view.start_render()
        return "break"

    def _on_attach_click(self):
        self.video_view.choose_reference_image()

    def _clear_attachment(self):
        self.video_view.clear_reference_image()

    def destroy(self):
        if getattr(self, "_video_destroyed", False):
            return
        self._video_destroyed = True
        self._active_glow.stop()
        view = getattr(self, "video_view", None)
        if view is not None and not view._closed:
            view.destroy()
        super().destroy()
        ctk.ScalingTracker.window_widgets_dict.pop(self, None)
        ctk.ScalingTracker.window_dpi_scaling_dict.pop(self, None)

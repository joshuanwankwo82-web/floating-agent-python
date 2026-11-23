"""The delivered Quiet Chat interface, adapted as a Friday child view.

No root window, Qt dependency, model SDK, microphone or network call is
started here. A supplied reply_handler can provide responses; the default
remains an explicitly labelled local UI demo.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Thread
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont

import customtkinter as ctk

from .icons import IconBank
from .widgets import PanelButton, PanelScrollFrame, PanelTextbox, PanelToplevel
from .model import (Conversation, Message, ReplyError, ReplyHandler,
                    demo_conversations, demo_reply, export_text)


# One restrained palette; change these values to recolor the entire interface.
C = {
    "outside": "#101012",
    "background": "#151517",
    "sidebar": "#1B1B1E",
    "surface": "#222225",
    "input": "#1E1E21",
    "bubble": "#28282D",
    "selected": "#2A2A2F",
    "hover": "#303035",
    "border": "#35353B",
    "border_soft": "#29292E",
    "focus": "#626169",
    "text": "#E7E6E3",
    "body": "#CCCBCF",
    "secondary": "#B2B1B8",
    "muted": "#909098",
    "accent": "#C7C3BC",
    "accent_hover": "#DAD6D0",
    "accent_text": "#29282B",
}


def shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def fit_text(text: str, font, width: int, master) -> str:
    """Measure in this view's interpreter, never tkinter's default root."""
    def measure(value):
        return int(master.tk.call("font", "measure", font, value))
    if measure(text) <= width:
        return text
    while text and measure(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


class Tooltip:
    """A quiet tooltip for icon-only controls, using the same visual language."""

    def __init__(self, widget, text: str):
        self.widget, self.text = widget, text
        self.job = None
        self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def schedule(self, _event=None):
        self.hide()
        self.job = self.widget.after(650, self.show)

    def show(self):
        self.job = None
        if not self.widget.winfo_exists():
            return
        self.window = PanelToplevel(self.widget)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(fg_color=C["surface"])
        ctk.CTkLabel(self.window, text=self.text, font=("Arial", 11),
                     text_color=C["body"], fg_color=C["surface"],
                     corner_radius=7).pack(padx=9, pady=4)
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
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


_PRESENCE_VIEWS = []   # live ChatView instances with a presence badge


def refresh_presence():
    """Called after the Profile page saves: every open chat header
    re-reads the user's name/photo immediately (no restart)."""
    for view in list(_PRESENCE_VIEWS):
        try:
            view.refresh_presence()
        except Exception:  # noqa: BLE001
            pass


class ChatView(ctk.CTkFrame):
    def __init__(self, master, reply_handler: ReplyHandler | None = None,
                 storage=None, seed_demo: bool = True, handoff_handler=None):
        super().__init__(master, fg_color=C["outside"], corner_radius=0)
        self.icon = IconBank(self)
        self.storage = storage
        self._bindings = []
        self._tooltips = []
        self._dialogs = []
        self._compact = False
        self._sidebar_open = False
        self._last_notice = ""
        self._notice_callback = None

        available_fonts = set(tkfont.families(self))
        self.family = next((f for f in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "DejaVu Sans")
                            if f in available_fonts), "Arial")
        self.is_demo = reply_handler is None
        self.is_settings_backed = bool(getattr(reply_handler, "_friday_settings_backed", False))
        self.reply_handler = reply_handler or demo_reply
        self.handoff_handler = handoff_handler
        self._handoff_pending = False
        self.handoff_results: Queue = Queue()
        self.pending: set[str] = set()
        self.results: Queue = Queue()
        self._closed = False
        self._draft_attachments: list[str] = []
        self._drafts: dict[str, tuple[str, list[str]]] = {}
        self._wrapped_labels = []
        self._resize_job = self._scroll_job = self._toast_job = None
        self._poll_job = self._warning_job = None
        self._toast = None
        self._typing_label = None
        self._load_warning = False
        self._can_save = self.storage is not None
        self._menu_open = False

        self.conversations, active_id = self._load(seed_demo)
        self.active_id = active_id or self.conversations[0].id
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_layout()
        self._refresh()
        # Bind only to this native Chat Toplevel, not bind_all or the
        # IDE/hidden app-panel root. Remove exactly these bindings on close.
        top = self.winfo_toplevel()
        for sequence, callback in (
            ("<Escape>", self._escape),
            ("<Button-1>", self._dismiss_menu),
            ("<Control-n>", lambda _event: self.new_conversation()),
        ):
            self._bindings.append((top, sequence, top.bind(sequence, callback, add="+")))
        self._poll_job = self.after(80, self._poll_replies)
        if self._load_warning:
            self._warning_job = self.after(500, lambda: self._notify("History couldn’t be read. Started a temporary session."))

    def font(self, size: int, weight="normal"):
        # CTkFont implicitly uses the global default Tk root. Font tuples
        # are resolved by each widget in its own interpreter instead.
        return (self.family, size, weight)

    @property
    def current(self) -> Conversation:
        return next(c for c in self.conversations if c.id == self.active_id)

    def _load(self, seed_demo):
        if self.storage is not None:
            try:
                conversations, active = self.storage.load()
                if conversations:
                    return conversations, active
            except Exception:
                # Keep unreadable history untouched and keep the UI usable.
                self.storage = None
                self._can_save = False
                self._load_warning = True
                conversations = [Conversation()]
                return conversations, conversations[0].id
        conversations = demo_conversations() if seed_demo else [Conversation()]
        return conversations, conversations[0].id

    def _build_layout(self):
        self.shell = ctk.CTkFrame(self, fg_color=C["background"], corner_radius=22,
                                  border_width=1, border_color=C["border_soft"])
        self.shell.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.shell.grid_columnconfigure(1, weight=1)
        self.shell.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self.shell, width=238, fg_color=C["sidebar"], corner_radius=15)
        self.sidebar.grid(row=0, column=0, sticky="ns", padx=(10, 0), pady=10)
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(3, weight=1)
        self._build_sidebar()

        self.main = ctk.CTkFrame(self.shell, fg_color="transparent", corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew", padx=(24, 24), pady=(6, 12))
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(2, weight=1)
        self._build_header()
        ctk.CTkFrame(self.main, height=1, fg_color=C["border_soft"], corner_radius=0).grid(
            row=1, column=0, sticky="ew")
        self.chat = PanelScrollFrame(self.main, label_font=self.font(11), fg_color="transparent", corner_radius=0,
                                          scrollbar_fg_color=C["background"],
                                          scrollbar_button_color=C["border_soft"],
                                          scrollbar_button_hover_color=C["border"])
        self.chat.grid(row=2, column=0, sticky="nsew", pady=(3, 0))
        self.chat.grid_columnconfigure(0, weight=1)
        # Reserve the scrollbar's space, but show its thumb only when scrolling is needed.
        self.chat._parent_canvas.configure(yscrollcommand=self._chat_scroll_position)
        self._build_suggestions()
        self._build_composer()
        self._build_footer()
        self._build_menu()
        self.main.bind("<Configure>", self._schedule_wrap, add="+")
        self.bind("<Configure>", self._layout_for_width, add="+")

    def _build_sidebar(self):
        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=20, pady=(27, 27))
        mark = ctk.CTkFrame(brand, width=39, height=39, corner_radius=13,
                            fg_color=C["surface"], border_width=1, border_color=C["border"])
        mark.grid(row=0, column=0, rowspan=2, padx=(0, 11))
        mark.grid_propagate(False)
        ctk.CTkLabel(mark, text="", font=self.font(11), image=self.icon("spark", C["accent"], 23)).place(
            relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(brand, text="friday", font=self.font(25, "bold"), height=29,
                     text_color=C["text"], anchor="w").grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(brand, text="A little room to think.", font=self.font(10), height=16,
                     text_color=C["muted"]).grid(row=1, column=1, sticky="w", pady=(2, 0))
        PanelButton(self.sidebar, text="New conversation", image=self.icon("plus", C["body"], 16),
                      compound="left", font=self.font(12), height=43, corner_radius=11,
                      fg_color=C["surface"], hover_color=C["hover"], text_color=C["text"],
                      border_width=1, border_color=C["border"], command=self.new_conversation).grid(
            row=1, column=0, sticky="ew", padx=18, pady=(0, 28))
        ctk.CTkLabel(self.sidebar, text="CONVERSATIONS", font=self.font(10), height=15,
                     text_color=C["muted"], anchor="w").grid(
            row=2, column=0, sticky="w", padx=23, pady=(0, 12))
        self.history = PanelScrollFrame(self.sidebar, label_font=self.font(11), fg_color="transparent", corner_radius=0,
                                             scrollbar_fg_color=C["sidebar"],
                                             scrollbar_button_color=C["sidebar"],
                                             scrollbar_button_hover_color=C["border"])
        self.history.grid(row=3, column=0, sticky="nsew", padx=(12, 4))
        self.history.grid_columnconfigure(0, weight=1)
        ctk.CTkFrame(self.sidebar, height=1, fg_color=C["border_soft"], corner_radius=0).grid(
            row=4, column=0, sticky="ew", padx=20, pady=(12, 16))
        identity = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        identity.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 23))
        identity.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(identity, text="Y", font=self.font(13), width=34, height=34,
                     corner_radius=11, fg_color=C["selected"], text_color=C["accent"]).grid(
            row=0, column=0, rowspan=2, padx=(0, 10))
        ctk.CTkLabel(identity, text="Your workspace", font=self.font(11), height=18,
                     text_color=C["body"]).grid(row=0, column=1, sticky="w")
        self.storage_label = ctk.CTkLabel(identity, text="Encrypted locally", font=self.font(10),
                                         height=14, text_color=C["muted"])
        self.storage_label.grid(row=1, column=1, sticky="w", pady=(2, 0))
        info = self._icon_button(identity, "info", self.show_about, "About this interface", size=17)
        info.grid(row=0, column=2, rowspan=2, padx=(6, 0))

    def _build_header(self):
        header = ctk.CTkFrame(self.main, fg_color="transparent", height=81)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)
        self.sidebar_button = self._icon_button(
            header, "sidebar", self._toggle_sidebar, "Show conversation history")
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=1, sticky="w", pady=(23, 20))
        self.title_label = ctk.CTkLabel(titles, text="", font=self.font(18, "bold"),
                                       height=23, text_color=C["text"], anchor="w")
        self.title_label.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(titles, text="Your space, without the noise.", font=self.font(11),
                     height=17, text_color=C["muted"]).grid(row=1, column=0, sticky="w", pady=(4, 0))
        # presence badge (identity from the Settings -> Profile page):
        # small round avatar + display name; hidden when no profile yet
        badge = ctk.CTkFrame(header, fg_color="transparent")
        badge.grid(row=0, column=2, sticky="e", pady=(22, 20), padx=(0, 6))
        self._presence_badge = badge
        # font= is MANDATORY here: a default CTkFont binds to the
        # process DEFAULT root, and Friday's panels live in a DIFFERENT
        # Tk interpreter on the GUI thread (the multi-interpreter chat
        # battery exists precisely to shoot this down).
        self._presence_avatar = ctk.CTkLabel(badge, text="", width=26,
                                             height=26,
                                             font=self.font(12))
        self._presence_name = ctk.CTkLabel(badge, text="",
                                           font=self.font(12, "bold"),
                                           text_color=C["text"])
        _PRESENCE_VIEWS.append(self)
        badge.after(60, self.refresh_presence)

        def _drop_badge(_e):
            if self in _PRESENCE_VIEWS:
                _PRESENCE_VIEWS.remove(self)

        header.bind("<Destroy>", _drop_badge, add="+")
        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.grid(row=0, column=3, sticky="e", pady=(22, 20))
        responder_label = ("•  Local UI demo" if self.is_demo else
                           "•  Settings → Voice" if self.is_settings_backed else
                           "•  Connected responder")
        self.status_badge = ctk.CTkLabel(controls, text=responder_label,
                                         font=self.font(10), text_color=C["secondary"], fg_color=C["surface"],
                                         corner_radius=11, height=26)
        self.status_badge.grid(row=0, column=0, padx=(0, 10), ipadx=10)
        export = self._icon_button(controls, "download", self.export_conversation,
                                   "Export conversation")
        export.grid(row=0, column=1, padx=(0, 3))
        self.more_button = self._icon_button(controls, "more", self._toggle_menu, "Conversation options")
        self.more_button.grid(row=0, column=2)

    def refresh_presence(self):
        """Re-read the user's profile (name + photo) and render the
        badge; fully hidden when the profile is empty, so the chat
        owner identity appears only when the user has actually told
        Friday who they are."""
        try:
            import user_profile
            profile = user_profile.load()
            name = user_profile.display_name(profile)
            img = user_profile.avatar_photoimage(
                profile, size=26, master=self)
        except Exception:  # noqa: BLE001
            name, img = "", None
        try:
            if not name and img is None:
                self._presence_badge.grid_remove()
                return
            self._presence_badge.grid()
            if img is not None:
                self._presence_img = img   # keep reference: GC eats it
                self._presence_avatar.configure(image=img)
                self._presence_avatar.pack(side="left")
            else:
                self._presence_avatar.pack_forget()
            self._presence_name.configure(text=name)
            self._presence_name.pack(side="left",
                                     padx=(6 if img is not None else 0,
                                           4))
        except Exception:  # noqa: BLE001
            pass

    def _build_suggestions(self):
        self.suggestions = ctk.CTkFrame(self.main, fg_color="transparent")
        self.suggestions.grid(row=3, column=0, sticky="w", padx=(44, 0), pady=(14, 15))
        self.prompt_buttons = []
        for column, (name, title, prompt, width) in enumerate([
            ("sun", "Plan my day", "Help me plan a calmer day.", 126),
            ("pen", "Draft something", "Help me write something clear and simple.", 146),
            ("spark", "Explore an idea", "Help me explore an idea I’d like to make.", 145),
        ]):
            button = PanelButton(self.suggestions, text=title, image=self.icon(name, C["secondary"], 15),
                                   font=self.font(11), height=32, width=width, corner_radius=16,
                                   fg_color="transparent", hover_color=C["surface"],
                                   border_width=1, border_color=C["border"], text_color=C["secondary"],
                                   command=lambda p=prompt: self.use_prompt(p))
            button.grid(row=0, column=column, padx=(0, 8 if column < 2 else 0))
            self.prompt_buttons.append(button)

    def _build_composer(self):
        self.composer = ctk.CTkFrame(self.main, fg_color=C["input"], corner_radius=17,
                                    border_width=1, border_color=C["border"])
        self.composer.grid(row=4, column=0, sticky="ew")
        self.composer.grid_columnconfigure(0, weight=1)
        self.entry = PanelTextbox(self.composer, height=46, fg_color="transparent", corner_radius=0,
                                    border_width=0, border_spacing=0, activate_scrollbars=False,
                                    font=self.font(14), text_color=C["text"], wrap="word")
        self.entry.grid(row=0, column=0, sticky="ew", padx=16, pady=(13, 0))
        self.entry._textbox.configure(insertbackground=C["text"], insertwidth=1,
                                       selectbackground=C["border"], selectforeground=C["text"])
        self.placeholder = ctk.CTkLabel(self.composer, text="Write a message…", font=self.font(14),
                                        text_color=C["muted"], fg_color=C["input"], height=23)
        self.placeholder.place(x=17, y=13)
        self.placeholder.bind("<Button-1>", lambda _event: self.entry.focus_set())
        self.entry.bind("<Return>", self._on_return, add="+")
        self.entry.bind("<KP_Enter>", self._on_return, add="+")
        self.entry.bind("<<Modified>>", self._on_modified, add="+")
        self.entry.bind("<FocusIn>", lambda _event: self.composer.configure(border_color=C["focus"]), add="+")
        self.entry.bind("<FocusOut>", lambda _event: self.composer.configure(border_color=C["border"]), add="+")

        toolbar = ctk.CTkFrame(self.composer, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", padx=(11, 12), pady=(0, 10))
        toolbar.grid_columnconfigure(2, weight=1)
        attach = self._icon_button(toolbar, "clip", self.attach_files, "Add file-name references", size=17)
        self.attach_button = attach
        attach.grid(row=0, column=0)
        self.attach_hint = ctk.CTkLabel(toolbar, text="Attach file", font=self.font(11),
                                       text_color=C["muted"])
        self.attach_hint.grid(row=0, column=1, padx=(4, 0))
        self.attach_hint.bind("<Button-1>", lambda _event: self.attach_files())
        self.file_chip = PanelButton(toolbar, text="", image=self.icon("file", C["secondary"], 13),
                                       font=self.font(10), fg_color=C["surface"], hover_color=C["hover"],
                                       text_color=C["secondary"], corner_radius=8, height=25,
                                       border_width=1, border_color=C["border"], command=self.clear_files)
        self.send_button = PanelButton(toolbar, text="", font=self.font(11), width=35, height=35, corner_radius=11,
                                         image=self.icon("arrow_up", C["accent_text"], 18),
                                         fg_color=C["accent"], hover_color=C["accent_hover"],
                                         command=self.send_message)
        self.voice_controls = ctk.CTkFrame(toolbar, fg_color="transparent")
        self.voice_controls.grid(row=0, column=3, padx=(0, 9))
        self.mic_button = self._icon_button(
            self.voice_controls, "mic", self._voice_unavailable,
            "Voice input · not connected to this panel", size=17)
        self.mic_button.grid(row=0, column=0, padx=(0, 2))
        self.speaker_button = self._icon_button(
            self.voice_controls, "speaker", self._speech_unavailable,
            "Spoken replies · not connected to this panel", size=17)
        self.speaker_button.grid(row=0, column=1)
        self.send_button.grid(row=0, column=4)
        self._tooltips.append(Tooltip(self.send_button, "Send message · Enter"))

    def _build_footer(self):
        footer = ctk.CTkFrame(self.main, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", pady=(11, 2))
        footer.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(footer, text="", font=self.font(10), image=self.icon("lock", C["muted"], 11), width=14,
                     height=16).grid(row=0, column=0, padx=(1, 5))
        self.privacy_label = ctk.CTkLabel(footer, text="", text_color=C["muted"],
                                         height=16, font=self.font(10))
        self.privacy_label.grid(row=0, column=1, sticky="w")
        self.keyboard_hint = ctk.CTkLabel(footer, text="", text_color=C["muted"],
                                         height=16, font=self.font(10))
        self.keyboard_hint.grid(row=0, column=2, sticky="e")

    def _build_menu(self):
        self.menu = ctk.CTkFrame(self.main, fg_color=C["surface"], corner_radius=12,
                                 border_width=1, border_color=C["border"])
        actions = []
        if self.handoff_handler is not None:
            actions.append(("spark", "Continue in IDE", self.handoff_conversation))
        actions.extend([
            ("download", "Export conversation", self.export_conversation),
            ("trash", "Delete conversation", self.confirm_delete),
        ])
        for row, (name, text, command) in enumerate(actions):
            PanelButton(self.menu, text=text, image=self.icon(name, C["secondary"], 15),
                          font=self.font(12), anchor="w", width=183, height=35,
                          fg_color="transparent", hover_color=C["hover"], text_color=C["body"],
                          corner_radius=8, command=lambda cmd=command: self._menu_action(cmd)).grid(
                row=row, column=0, padx=6, pady=(6 if row == 0 else 0, 6))

    def _layout_for_width(self, event=None):
        if self._closed or (event is not None and event.widget not in (self, self._canvas)):
            return
        width = self.winfo_width() / self._get_widget_scaling()
        compact = width < 850
        if compact == self._compact:
            return
        self._compact = compact
        self._sidebar_open = False
        self.sidebar.place_forget()
        if compact:
            self.sidebar.grid_remove()
            self.shell.grid_columnconfigure(0, weight=1)
            self.shell.grid_columnconfigure(1, weight=0)
            self.main.grid_configure(column=0, columnspan=2, padx=(14, 14))
            self.sidebar_button.grid(row=0, column=0, padx=(0, 10))
        else:
            self.shell.grid_columnconfigure(0, weight=0)
            self.shell.grid_columnconfigure(1, weight=1)
            self.main.grid_configure(column=1, columnspan=1, padx=(24, 24))
            self.sidebar.grid(row=0, column=0, sticky="ns", padx=(10, 0), pady=10)
            self.sidebar_button.grid_remove()
        self._schedule_wrap()

    def _toggle_sidebar(self):
        if not self._compact:
            return
        if self._sidebar_open:
            self._hide_sidebar()
        else:
            self.sidebar.place(x=10, y=10, relheight=0.96)
            self.sidebar.lift()
            self._sidebar_open = True

    def _hide_sidebar(self):
        if self._compact and self._sidebar_open:
            self.sidebar.place_forget()
            self._sidebar_open = False

    def _escape(self, _event=None):
        self._hide_menu()
        self._hide_sidebar()

    def _voice_unavailable(self):
        self._notify("Voice input is not connected to this panel.")

    def _speech_unavailable(self):
        self._notify("Spoken replies are not connected to this panel.")

    def _icon_button(self, parent, name, command, tooltip, size=18):
        button = PanelButton(parent, text="", font=self.font(11), image=self.icon(name, C["secondary"], size),
                               width=32, height=32, corner_radius=10, fg_color="transparent",
                               hover_color=C["surface"], command=command)
        self._tooltips.append(Tooltip(button, tooltip))
        return button

    def _refresh(self):
        self._render_sidebar()
        self.title_label.configure(text=shorten(self.current.title, 38))
        self._render_chat()
        self._update_composer()
        self._update_wrap()

    def _render_sidebar(self):
        for child in self.history.winfo_children():
            child.destroy()
        for row, conversation in enumerate(self.conversations):
            selected = conversation.id == self.active_id
            button = PanelButton(self.history, text=shorten(conversation.title, 25),
                                   image=self.icon("chat", C["body"] if selected else C["muted"], 15),
                                   compound="left", anchor="w", border_spacing=10,
                                   height=43, width=192, corner_radius=10, font=self.font(11),
                                   text_color=C["text"] if selected else C["secondary"],
                                   fg_color=C["selected"] if selected else "transparent",
                                   hover_color=C["hover"], border_width=1 if selected else 0,
                                   border_color=C["border"],
                                   command=lambda cid=conversation.id: self.switch_conversation(cid))
            button.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        self.storage_label.configure(text="Encrypted locally" if self._can_save else "Session only")

    def _render_chat(self):
        for child in self.chat.winfo_children():
            child.destroy()
        self._wrapped_labels = []
        self._typing_label = None
        if not self.current.messages:
            self._render_empty()
        else:
            today = self.current.messages[-1].created_at.startswith(datetime.now().date().isoformat())
            ctk.CTkLabel(self.chat, text="Today" if today else "Your conversation", font=self.font(10),
                         height=23, width=64, corner_radius=9, fg_color=C["surface"],
                         text_color=C["muted"]).grid(row=0, column=0, pady=(19, 22))
            for index, message in enumerate(self.current.messages):
                self._render_message(index + 1, message, index == len(self.current.messages) - 1)
        if self.active_id in self.pending:
            self._render_typing(len(self.current.messages) + 1)
        self._update_wrap()
        if self._scroll_job is not None:
            self.after_cancel(self._scroll_job)
        self._scroll_job = self.after(60, self._scroll_bottom)

    def _render_empty(self):
        welcome = ctk.CTkFrame(self.chat, fg_color="transparent")
        welcome.grid(row=0, column=0, sticky="ew", pady=(90, 55))
        welcome.grid_columnconfigure(0, weight=1)
        mark = ctk.CTkFrame(welcome, width=61, height=61, corner_radius=20,
                            fg_color=C["surface"], border_width=1, border_color=C["border"])
        mark.grid(row=0, column=0, pady=(0, 23))
        mark.grid_propagate(False)
        ctk.CTkLabel(mark, text="", font=self.font(11), image=self.icon("spark", C["accent"], 31)).place(
            relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(welcome, text="A little room to think.", font=self.font(26, "bold"),
                     text_color=C["text"]).grid(row=1, column=0, pady=(0, 11))
        ctk.CTkLabel(welcome, text="A quiet place for questions, ideas,\nand everything in between.",
                     font=self.font(13), text_color=C["muted"], justify="center").grid(row=2, column=0)

    def _render_message(self, row, message, is_last):
        outer = ctk.CTkFrame(self.chat, fg_color="transparent")
        outer.grid(row=row, column=0, sticky="ew", padx=(2, 12), pady=(0, 22))
        if message.role == "user":
            outer.grid_columnconfigure(0, weight=1)
            body = ctk.CTkFrame(outer, fg_color="transparent")
            body.grid(row=0, column=1, sticky="e", padx=(42, 0))
            ctk.CTkLabel(body, text="You", font=self.font(10), text_color=C["muted"],
                         height=16).grid(row=0, column=0, sticky="e", pady=(0, 7))
            bubble = ctk.CTkFrame(body, corner_radius=15, fg_color=C["bubble"],
                                  border_width=1, border_color=C["border"])
            bubble.grid(row=1, column=0, sticky="e")
            line = 0
            if message.content:
                label = ctk.CTkLabel(bubble, text=message.content, font=self.font(14), height=1,
                                     text_color=C["text"], justify="left", anchor="w", wraplength=530)
                label.grid(row=line, column=0, sticky="w", padx=16, pady=(12, 12))
                self._wrapped_labels.append((label, "user"))
                line += 1
            for name in message.attachments:
                label = ctk.CTkLabel(bubble, text=shorten(name, 44), image=self.icon("file", C["secondary"], 14),
                                     compound="left", font=self.font(11), text_color=C["secondary"])
                label.grid(row=line, column=0, sticky="w", padx=14, pady=(4, 9))
                line += 1
        else:
            outer.grid_columnconfigure(1, weight=1)
            self._avatar(outer)
            body = ctk.CTkFrame(outer, fg_color="transparent")
            body.grid(row=0, column=1, sticky="new", padx=(12, 0))
            body.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(body, text="Friday", font=self.font(11, "bold"), text_color=C["secondary"],
                         height=18).grid(row=0, column=0, sticky="w")
            paragraphs = message.content.split("\n\n")
            for number, paragraph in enumerate(paragraphs, start=1):
                heading = paragraph.startswith("## ")
                label = ctk.CTkLabel(body, text=paragraph[3:] if heading else paragraph,
                                     font=self.font(16, "bold") if heading else self.font(14),
                                     text_color=C["text"] if heading else C["body"], height=1,
                                     justify="left", anchor="w", wraplength=620)
                label.grid(row=number, column=0, sticky="w", pady=(6 if number == 1 else 11, 0))
                self._wrapped_labels.append((label, "assistant"))
            if is_last:
                PanelButton(body, text="Copy", image=self.icon("copy", C["muted"], 12),
                              font=self.font(10), text_color=C["muted"], height=23, width=57,
                              fg_color="transparent", hover_color=C["surface"], corner_radius=6,
                              command=lambda value=message.content: self.copy_message(value)).grid(
                    row=len(paragraphs) + 1, column=0, sticky="w", pady=(10, 0))

    def _avatar(self, parent):
        avatar = ctk.CTkFrame(parent, width=30, height=30, corner_radius=10,
                              fg_color=C["surface"], border_width=1, border_color=C["border_soft"])
        avatar.grid(row=0, column=0, sticky="nw", pady=(1, 0))
        avatar.grid_propagate(False)
        ctk.CTkLabel(avatar, text="", font=self.font(11), image=self.icon("spark", C["accent"], 17)).place(
            relx=0.5, rely=0.5, anchor="center")

    def _render_typing(self, row):
        outer = ctk.CTkFrame(self.chat, fg_color="transparent")
        outer.grid(row=row, column=0, sticky="ew", padx=(2, 12), pady=(0, 15))
        self._avatar(outer)
        self._typing_label = ctk.CTkLabel(outer, text="Preparing a sample reply…" if self.is_demo else "Waiting for a reply…",
                                         font=self.font(13), text_color=C["muted"], height=30)
        self._typing_label.grid(row=0, column=1, padx=(12, 0), sticky="w")

    def _schedule_wrap(self, _event=None):
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(60, self._update_wrap)

    def _update_wrap(self):
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
            self._resize_job = None
        if self._closed:
            return
        scale = self._get_widget_scaling()
        width = self.main.winfo_width() / scale
        if width < 100:
            width = 780
        for label, role in self._wrapped_labels:
            wrap = min(550, int(width * 0.76)) if role == "user" else width - 90
            label.configure(wraplength=max(90, wrap))
        available = width
        small = available < 620
        narrow = available < 460
        for index, button in enumerate(self.prompt_buttons):
            if (index == 2 and narrow) or (index == 1 and available < 340):
                button.grid_remove()
            else:
                button.grid()
        if narrow:
            self.keyboard_hint.grid_remove()
        else:
            self.keyboard_hint.grid()
        if small:
            self.status_badge.grid_remove()
        else:
            self.status_badge.grid()
        controls_width = 80 + (0 if small else self.status_badge.winfo_reqwidth() / scale + 30) + (42 if self._compact else 0)
        self.title_label.configure(text=fit_text(self.current.title, self.title_label._label.cget("font"),
                                                 max(70, (width - controls_width - 16) * scale), self))
        self.suggestions.grid_configure(padx=(0 if small else 44, 0))
        self.keyboard_hint.configure(text="Enter ↵  ·  Shift + Enter" if small else "Enter to send  ·  Shift + Enter for a new line")
        if self.is_demo:
            privacy = "Local UI demo · Encrypted history" if small else "Local UI demo · History encrypted on this device"
            if not self._can_save:
                privacy = "Local UI demo · Session only"
        elif self.is_settings_backed:
            privacy = ("Voice models · Encrypted history" if small else
                       "Settings → Voice models · Conversation history encrypted on this device")
        else:
            privacy = "Connected responder · Check its data policy"
        self.privacy_label.configure(text=privacy)
        self._chat_scroll_position(*self.chat._parent_canvas.yview())

    def _chat_scroll_position(self, first, last):
        self.chat._scrollbar.set(first, last)
        needed = float(last) - float(first) < 0.999
        self.chat._scrollbar.configure(
            button_color=C["border"] if needed else C["background"],
            button_hover_color=C["focus"] if needed else C["background"])

    def _scroll_bottom(self):
        self._scroll_job = None
        if not self._closed:
            self.chat._parent_canvas.yview_moveto(1.0)
            self._chat_scroll_position(*self.chat._parent_canvas.yview())

    def _on_return(self, event):
        if event.state & 0x0001:  # Shift: keep Tk's native newline behavior.
            return None
        self.send_message()
        return "break"

    def _on_modified(self, _event=None):
        if self.entry._textbox.edit_modified():
            self.entry._textbox.edit_modified(False)
            self._update_composer()

    def _update_composer(self):
        value = self.entry.get("1.0", "end-1c")
        if value:
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=17, y=13)
            self.placeholder.lift()
        enabled = bool(value.strip() or self._draft_attachments) and self.active_id not in self.pending
        self.send_button.configure(state="normal" if enabled else "disabled",
                                   fg_color=C["accent"] if enabled else C["border"],
                                   image=self.icon("arrow_up", C["accent_text"] if enabled else C["muted"], 18))
        if self._draft_attachments:
            self.attach_hint.grid_remove()
            title = shorten(self._draft_attachments[0], 24)
            if len(self._draft_attachments) > 1:
                title += f" +{len(self._draft_attachments) - 1}"
            self.file_chip.configure(text=f"{title}  ×")
            self.file_chip.grid(row=0, column=1, padx=(5, 0))
        else:
            self.file_chip.grid_remove()
            self.attach_hint.grid()

    def use_prompt(self, prompt):
        self.entry.delete("1.0", "end")
        self.entry.insert("1.0", prompt)
        self._update_composer()
        self.entry.focus_set()

    def attach_files(self):
        paths = filedialog.askopenfilenames(parent=self, title="Add file-name references")
        if paths:
            self._draft_attachments = list(dict.fromkeys(
                self._draft_attachments + [Path(path).name for path in paths]))
            self._update_composer()
            self._notify("File names added. No file contents are read or uploaded.")

    def clear_files(self):
        self._draft_attachments = []
        self._update_composer()

    def send_message(self):
        text = self.entry.get("1.0", "end-1c").strip()
        if (not text and not self._draft_attachments) or self.active_id in self.pending:
            return
        conversation = self.current
        conversation.add(Message("user", text, attachments=list(self._draft_attachments)))
        self._draft_attachments = []
        self.entry.delete("1.0", "end")
        self.pending.add(conversation.id)
        history = [message.wire() for message in conversation.messages]
        self._persist()
        self._refresh()
        self.entry.focus_set()

        # A worker retains plain data/queue only, never a Tk widget or
        # this view. Closing Friday's Tk host mid-reply must not cause
        # the last GUI/image references to be collected on this thread.
        handler, is_demo = self.reply_handler, self.is_demo
        result_queue, identifier = self.results, conversation.id

        def worker():
            try:
                if is_demo:
                    time.sleep(0.65)
                result = handler(text, history)
                if not isinstance(result, str) or not result.strip():
                    raise ValueError("A reply handler must return a nonempty string")
                result_queue.put((identifier, result, False))
            except ReplyError as error:
                result_queue.put((identifier, str(error), True))
            except Exception as error:
                print(f"Friday Chat responder failed ({type(error).__name__}).", file=sys.stderr)
                result_queue.put((identifier,
                                  "The reply couldn’t be loaded.\n\n"
                                  "Check Settings → Voice and your model connection, then try again.", True))

        Thread(target=worker, name="Friday-Chat-Reply", daemon=True).start()

    def handoff_conversation(self):
        """Move a snapshot of this conversation into the IDE's coding chat."""
        if self.handoff_handler is None or self._handoff_pending:
            return
        conversation = self.current
        history = [message.wire() for message in conversation.messages]
        draft = self.entry.get("1.0", "end-1c").strip()
        if not history and not draft:
            self._notify("Start a conversation before moving it to the IDE.")
            return
        self._handoff_pending = True
        self._notify("Moving this conversation to the IDE…")
        handler = self.handoff_handler
        title = conversation.title
        results = self.handoff_results

        def worker():
            try:
                message = handler(title, history, draft)
                results.put((True, str(message)))
            except Exception:  # noqa: BLE001
                results.put((False, "Couldn't open the IDE or transfer this conversation. Try again."))

        Thread(target=worker, name="Friday-Chat-IDE-Handoff", daemon=True).start()

    def _poll_replies(self):
        if self._closed:
            return
        while True:
            try:
                ok, message = self.handoff_results.get_nowait()
            except Empty:
                break
            self._handoff_pending = False
            self._notify(message)
        changed = False
        while True:
            try:
                identifier, content, failed = self.results.get_nowait()
            except Empty:
                break
            self.pending.discard(identifier)
            conversation = next((c for c in self.conversations if c.id == identifier), None)
            if conversation is None:
                continue  # A deleted conversation must not be recreated by a late reply.
            conversation.add(Message("assistant", content))
            self._persist()
            changed = changed or identifier == self.active_id
            if failed and identifier == self.active_id:
                self._notify("Couldn’t load a reply. Your message is still here.")
        if changed:
            self._refresh()
        self._poll_job = self.after(80, self._poll_replies)

    def _remember_draft(self):
        self._drafts[self.active_id] = (self.entry.get("1.0", "end-1c"), list(self._draft_attachments))

    def switch_conversation(self, identifier):
        if identifier == self.active_id:
            return
        self._remember_draft()
        self.active_id = identifier
        text, attachments = self._drafts.get(identifier, ("", []))
        self.entry.delete("1.0", "end")
        self.entry.insert("1.0", text)
        self._draft_attachments = list(attachments)
        self._hide_menu()
        self._hide_sidebar()
        self._refresh()
        self._persist()

    def new_conversation(self):
        self._remember_draft()
        conversation = Conversation()
        self.conversations.insert(0, conversation)
        self.switch_conversation(conversation.id)
        self.entry.focus_set()

    def copy_message(self, content):
        self.clipboard_clear()
        self.clipboard_append(content)
        self._notify("Reply copied.")

    def export_conversation(self):
        safe_name = "".join(c for c in self.current.title if c.isalnum() or c in " -_").strip() or "conversation"
        path = filedialog.asksaveasfilename(parent=self, title="Export conversation",
                                          initialfile=f"{safe_name}.txt", defaultextension=".txt",
                                          filetypes=[("Text file", "*.txt")])
        if path:
            try:
                Path(path).write_text(export_text(self.current), encoding="utf-8")
                self._notify("Conversation exported.")
            except OSError:
                self._notify("Couldn’t write that file. Try a different location.")

    def _toggle_menu(self):
        if self._menu_open:
            self._hide_menu()
        else:
            self.menu.place(relx=1.0, x=0, y=70, anchor="ne")
            self.menu.lift()
            self._menu_open = True

    def _hide_menu(self):
        self.menu.place_forget()
        self._menu_open = False

    def _menu_action(self, command):
        self._hide_menu()
        command()

    def _dismiss_menu(self, event):
        if not self._menu_open:
            return
        widget = event.widget
        while widget is not None:
            if widget in (self.menu, self.more_button):
                return
            widget = getattr(widget, "master", None)
        self._hide_menu()

    def _dialog(self, title, width=420, height=242):
        dialog = PanelToplevel(self)
        dialog.title(title)
        dialog.configure(fg_color=C["sidebar"])
        dialog.resizable(False, False)
        dialog.transient(self.winfo_toplevel())
        x = self.winfo_rootx() + (self.winfo_width() - width) // 2
        y = self.winfo_rooty() + (self.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        dialog.grid_columnconfigure(0, weight=1)
        grab_job = dialog.after(30, lambda: dialog.grab_set() if dialog.winfo_exists() else None)

        def on_destroy(event):
            if event.widget is dialog:
                try:
                    dialog.after_cancel(grab_job)
                except tk.TclError:
                    pass
                if dialog in self._dialogs:
                    self._dialogs.remove(dialog)

        dialog.bind("<Destroy>", on_destroy, add="+")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        self._dialogs.append(dialog)
        return dialog

    def confirm_delete(self):
        identifier = self.active_id
        dialog = self._dialog("Delete conversation")
        ctk.CTkLabel(dialog, text="Delete this conversation?", font=self.font(19, "bold"),
                     text_color=C["text"], anchor="w").grid(row=0, column=0, sticky="w", padx=26, pady=(27, 8))
        ctk.CTkLabel(dialog, text="This removes the conversation saved on this\ndevice. It can’t be undone.",
                     justify="left", font=self.font(13), text_color=C["secondary"]).grid(
            row=1, column=0, sticky="w", padx=26, pady=(0, 27))
        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="e", padx=26)
        PanelButton(buttons, text="Cancel", font=self.font(12), width=100, height=36, corner_radius=10,
                      fg_color=C["surface"], hover_color=C["hover"], text_color=C["text"],
                      command=dialog.destroy).grid(row=0, column=0, padx=(0, 9))

        def delete():
            dialog.destroy()
            self._delete_conversation(identifier)

        PanelButton(buttons, text="Delete", font=self.font(12), width=100, height=36, corner_radius=10,
                      fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["accent_text"],
                      command=delete).grid(row=0, column=1)

    def _delete_conversation(self, identifier):
        self.conversations = [c for c in self.conversations if c.id != identifier]
        self._drafts.pop(identifier, None)
        self.pending.discard(identifier)
        if not self.conversations:
            self.conversations = [Conversation()]
        if self.active_id == identifier:
            self.active_id = self.conversations[0].id
            text, attachments = self._drafts.get(self.active_id, ("", []))
            self.entry.delete("1.0", "end")
            self.entry.insert("1.0", text)
            self._draft_attachments = list(attachments)
        self._persist()
        self._refresh()

    def show_about(self):
        dialog = self._dialog("About Friday Chat", height=305)
        ctk.CTkLabel(dialog, text="A quiet interface. Your own engine.", font=self.font(18, "bold"),
                     text_color=C["text"]).grid(row=0, column=0, sticky="w", padx=25, pady=(28, 13))
        if self.is_demo:
            text = ("This version uses simulated sample replies.\n"
                    "No AI service is connected, and files are\nreferenced by name only.\n\n"
                    "This is Quiet Chat’s interface inside Friday.\n"
                    "The live agent connection is a separate step.")
        else:
            text = ("This version uses your custom reply handler.\n"
                    "Your integration determines which services\nreceive messages.\n\n"
                    "File references contain filenames only.\nSee README.md for integration details.")
        ctk.CTkLabel(dialog, text=text, font=self.font(13), text_color=C["secondary"],
                     justify="left").grid(row=1, column=0, sticky="w", padx=25, pady=(0, 23))
        PanelButton(dialog, text="Got it", font=self.font(12), width=95, height=35, corner_radius=10,
                      fg_color=C["surface"], hover_color=C["hover"], text_color=C["text"],
                      command=dialog.destroy).grid(row=2, column=0, sticky="e", padx=25)

    def _persist(self):
        if self.storage is None:
            return
        try:
            self.storage.save(self.conversations, self.active_id)
            self._can_save = True
        except Exception:
            self._can_save = False
            if not self._closed:
                self._notify("Couldn’t save encrypted history. This session is still available.")
        if hasattr(self, "storage_label"):
            self.storage_label.configure(text="Encrypted locally" if self._can_save else "Session only")
        if hasattr(self, "privacy_label") and not self._closed:
            self._update_wrap()

    def _notify(self, text):
        if self._closed:
            return
        if self._toast_job is not None:
            self.after_cancel(self._toast_job)
        if self._toast is not None:
            self._toast.destroy()
        self._toast = ctk.CTkFrame(self.main, fg_color=C["surface"], corner_radius=11,
                                   border_width=1, border_color=C["border"])
        ctk.CTkLabel(self._toast, text=text, font=self.font(11), text_color=C["body"],
                     wraplength=max(180, self.main.winfo_width() / self._get_widget_scaling() - 70)).pack(padx=15, pady=8)
        self._toast.place(relx=0.5, rely=1.0, y=-155, anchor="s")
        self._toast.lift()
        self._last_notice = text
        if self._notice_callback is not None:
            self._notice_callback(text)
        self._toast_job = self.after(3000, self._dismiss_toast)

    def _dismiss_toast(self):
        if self._toast is not None:
            self._toast.destroy()
            self._toast = None
        self._toast_job = None

    def close(self):
        """Close the child view only; Friday owns its containing window."""
        self.destroy()

    def destroy(self):
        if self._closed:
            return
        self._closed = True
        self._persist()
        for job in (self._poll_job, self._resize_job, self._scroll_job, self._toast_job, self._warning_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        for widget, sequence, identifier in self._bindings:
            try:
                widget.unbind(sequence, identifier)
            except tk.TclError:
                pass
        self._bindings.clear()
        for tooltip in self._tooltips:
            tooltip.hide()
        for dialog in list(self._dialogs):
            try:
                if dialog.winfo_exists():
                    dialog.destroy()
            except tk.TclError:
                pass
        super().destroy()
        self.icon.clear()

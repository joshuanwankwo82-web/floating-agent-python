"""Touchpad / mouse-wheel scrolling that behaves IDENTICALLY on every
platform -- v3, pointer-routed.

The story so far, honestly:
- F15 hid the side scrollbars after probing X11 Tk 8.6, whose class
  scripts scroll Text/Listbox/Treeview natively. Windows was assumed
  to match.
- F16 replaced the platform-default class scripts with one explicit
  per-class implementation after the user's Windows laptop still
  couldn't scroll.
- Regression report (settings panel): STILL no scroll. Cause, per Tk's
  documented Windows behavior: MouseWheel is delivered to the widget
  with keyboard FOCUS, not the widget under the pointer. Hover the
  settings list while a key Entry holds focus -> the event targets the
  Entry, no per-widget/per-class binding on the list ever fires.

So the final architecture stops caring who RECEIVES the event. One
global <all>-stage handler per interpreter:
  1. takes the POINTER position (event x_root/y_root ->
     winfo_containing), which is what the user is actually scrolling;
  2. climbs that widget's ancestry to the innermost scrollable
     (Text / Listbox / Treeview / Canvas -- canvases have no default
     wheel script, so this also covers the settings rows and any
     canvas whose children swallow nothing);
  3. scrolls exactly that and returns "break".

Guards baked in:
- native class scripts are NEUTRALIZED to a no-op (not replaced by a
  scroller): on X11 where defaults existed, the single source of
  scrolling is this handler; no double-scroll possible anywhere.
- pointer lineage inside a CTkScrollableFrame (Friday chat/image/
  video, quiet app): CTk's own bindings use event.widget ancestry --
  the same focus-hole kills them on Windows. This handler therefore
  covers CTk frames TOO, with a split-brain-safe discriminator: when
  the event's widget already sits inside the frame (pointer-targeted
  platforms), CTk handled it and we stay out; when the event was
  focus-targeted somewhere else (Windows), CTk provably did not
  handle it, and this handler scrolls the frame's canvas directly.
- touchpad_scroll.ignore(widget) exempts surfaces with their own wheel
  logic (the media preview canvas: Ctrl+wheel zooms, plain wheel
  scrolls via its own _wheel handler).
- Ctrl+wheel passes through untouched (zoom belongs to the apps).
- precision-touchpad sub-notch deltas accumulate per widget; direction
  flips flush residue; X11 Button-4/5 speak the same notch math.
- route_wheel_to(twin, main) still hands minimap/gutter wheel events
  to the editor text they mirror ("break" at the widget stage reaches
  before this global handler).

install(root) is idempotent per interpreter and called at BOTH Tk
roots (Friday's panel host, the IDEApp window).
"""
import tkinter as tk
from tkinter import ttk

_WHEEL_SEQS = ("<MouseWheel>", "<Button-4>", "<Button-5>")
_NATIVE_CLASSES = ("Text", "Listbox", "Treeview")
_SCROLLABLE_TYPES = (tk.Text, tk.Listbox, ttk.Treeview, tk.Canvas)
_CTK_SCROLLABLE_NAMES = frozenset(
    {"CTkScrollableFrame", "PanelScrollFrame", "ImageScrollFrame",
     "VideoScrollFrame"})
_LINES_PER_NOTCH = 3  # the classic Tk/Windows wheel step
_acc = {}             # str(widget) -> fractional line remainder
_IGNORED = set()      # str(widget) -- surfaces with their own wheel logic
_PROBE = [None]         # one diagnostic slot: settings' live probe line
_PROBE_OWNER = [None]   # lets a closing Settings instance clear only its own probe


def _steps(widget, event):
    """Event -> signed line steps (positive scrolls DOWN), with
    sub-notch deltas accumulated per widget so precision touchpads
    glide instead of stalling. A direction change flushes leftover
    residue in the old direction."""
    if getattr(event, "num", None) == 4:          # X11 two-finger up
        return -_LINES_PER_NOTCH
    if getattr(event, "num", None) == 5:          # X11 two-finger down
        return _LINES_PER_NOTCH
    delta = getattr(event, "delta", 0) or 0
    if not delta:
        return 0
    key = str(widget)
    amount = -delta / 120.0 * _LINES_PER_NOTCH    # Windows/macOS scale
    carry = _acc.get(key, 0.0)
    if carry * amount < 0:                        # direction flip
        carry = 0.0
    total = carry + amount
    whole = int(total)                            # trunc towards 0
    _acc[key] = total - whole
    return whole


def _class_noop(_event):
    """Neutralized platform-default wheel script: scrolling happens in
    ONE place (the global pointer-routed handler), never twice."""
    return None


def set_probe(callback_or_none, owner=None):
    """Register/clear the live Settings diagnostics callback.

    When ``owner`` is supplied, only that same Settings instance can clear
    its registration, so a late close from an older panel cannot detach a
    newer panel's probe. Existing one-argument callers remain supported.
    """
    if callback_or_none is None:
        if owner is not None and _PROBE_OWNER[0] is not owner:
            return
        _PROBE[0] = None
        _PROBE_OWNER[0] = None
        return
    _PROBE[0] = callback_or_none
    _PROBE_OWNER[0] = owner


def _emit_probe(text):
    cb = _PROBE[0]
    if cb is None:
        return
    try:
        cb(text)
    except Exception:  # noqa: BLE001
        pass


def _in_lineage(widget, ancestor):
    """Is `widget` (the event receiver) inside `ancestor`'s subtree?"""
    w = widget
    while w is not None:
        if w is ancestor:
            return True
        w = getattr(w, "master", None)
    return False


# CustomTkinter draws every widget with an internal Canvas subclass
# (class name "CTkCanvas") that is NOT a scroll surface: it is sized
# exactly to its content, so yview_scroll on it is a silent no-op.
# That was the real settings bug -- the ancestry climb originally hit
# a row's decorative CTk canvas FIRST (it IS a tk.Canvas), "scrolled"
# the nothing inside it, and reported success. Never match one.
_INTERIOR_CANVAS_NAMES = frozenset({"CTkCanvas"})


def _find_scroll_target(widget):
    """Pointer widget -> (scroll_widget, kind), or None. kind is
    "native" for Text/Listbox/Treeview/Canvas, "ctk" for a
    CTk-scroll-frame whose canvas we can scroll directly. CTk's
    interior drawing canvases are skipped by name, always."""
    w = widget
    while w is not None:
        if str(w) in _IGNORED:
            return None
        if {c.__name__ for c in type(w).__mro__} & _CTK_SCROLLABLE_NAMES:
            return w, "ctk"
        if isinstance(w, _SCROLLABLE_TYPES) \
                and type(w).__name__ not in _INTERIOR_CANVAS_NAMES:
            return w, "native"
        w = getattr(w, "master", None)
    return None


def _tp_global(event):
    """Armored shell: a wheel hiccup must NEVER wedge scrolling."""
    try:
        return _tp_route(event)
    except Exception:  # noqa: BLE001
        return None


def _tp_route(event):
    """The single wheel handler: route by POINTER, not by focus."""
    if getattr(event, "state", 0) & 0x4:     # Ctrl held: zoom owns it
        return None
    widget = event.widget
    try:
        hovered = widget.winfo_containing(event.x_root, event.y_root) \
            or widget
    except tk.TclError:
        hovered = widget
    found = _find_scroll_target(hovered)
    if found is None:
        _emit_probe("event seen; nothing scrollable under the pointer "
                    "(over " + type(hovered).__name__ + ")")
        return None
    target, kind = found
    if kind == "ctk":
        # Pointer TARGETED platforms (X11/macOS): the event's widget is
        # already inside the frame -> CTk's own binding handled this
        # event -- stay out so it never scrolls twice. FOCUS-targeted
        # Windows: the event went to the focused widget instead -> CTk
        # provably did nothing, so we scroll the frame here.
        if _in_lineage(widget, target):
            _emit_probe("CTk scroll frame handled natively")
            return None
        canvas = getattr(target, "_parent_canvas", None)
        if canvas is None:
            return None
        steps = _steps(canvas, event)
        if steps:
            canvas.yview_scroll(steps, "units")
            _emit_probe("scrolled Friday panel " + str(steps) + " lines "
                        "(focus-hole route)")
        return "break"
    steps = _steps(target, event)
    if steps:
        target.yview_scroll(steps, "units")
    _emit_probe("scrolled " + type(target).__name__ + " " + str(steps)
                + " lines")
    return "break"


def install(root):
    """Neutralize the per-platform class scripts and bind the global
    pointer-routed handler once per interpreter. Covers every panel,
    including Toplevels created later (the <all> stage sees them)."""
    for cls in _NATIVE_CLASSES:
        for seq in _WHEEL_SEQS:
            root.bind_class(cls, seq, _class_noop)
    if getattr(root, "_tp_scroll_installed", False):
        return root
    # An attribute ON the root (not an id() in a module set): id()s get
    # recycled when an interpreter's root is destroyed and recreated,
    # which would silently skip rebinding -- exactly the "it worked
    # once, then dead" pattern. Attributes die with their root.
    root._tp_scroll_installed = True
    for seq in _WHEEL_SEQS:
        root.bind_all(seq, _tp_global, add="+")
    return root


def attach_edge_autoscroll(panel, canvas, zone=64, max_px_per_tick=5.0,
                           tick_ms=20, ramp=2.0):
    """Cursor-EDGE auto-scroll -- the user-directed control (this Windows
    box never delivers wheel events; <Motion> arrives fine). F31 tuning:
    SLOWER and SMOOTHER -- no more whole-Line integer lurches:

      - speed is a continuous float in PIXELS per tick, easing on a
        QUADRATIC ramp with pointer depth into the edge zone (F32: the
        user asked for depth to matter MORE): zone tip = gentle crawl,
        rim = up to 5 px/tick (~250 px/s) -- pushing further down into
        the bottom edge visibly floors it;
      - a 20 ms ticker (50 Hz) moves the canvas by FRACTIONAL pixels via
        yview_moveto, computed from the real scrollregion height, so the
        motion is a steady glide instead of 24 px steps;
      - pointer in the dead middle (or off the panel) = frozen in place;
      - clamps at both ends; ticker dies with the panel.

    Lineage-locked to the given canvas -- other panels' motion events
    can never wake it.
    """
    state = {"v": 0.0, "job": None}

    def _region_height():
        try:
            bbox = canvas.bbox("all")
            return max(1.0, float(bbox[3] - bbox[1]))
        except Exception:  # noqa: BLE001
            return 1.0

    def _inside(event):
        try:
            hovered = event.widget.winfo_containing(
                event.x_root, event.y_root)
        except tk.TclError:
            hovered = None
        return hovered is not None and _in_lineage(hovered, canvas)

    def _motion(event):
        if not _inside(event):
            if state["v"]:
                state["v"] = 0.0
                _emit_probe("auto-scroll: idle")
            return
        try:
            rel_y = event.y_root - canvas.winfo_rooty()
            height = canvas.winfo_height()
        except tk.TclError:
            state["v"] = 0.0
            return
        if height <= 0:
            return
        v = 0.0
        if rel_y > height - zone:
            depth = min(1.0, (rel_y - (height - zone)) / float(zone))
            v = (depth ** ramp) * max_px_per_tick        # deeper = floors it
        elif rel_y < zone:
            depth = min(1.0, (zone - rel_y) / float(zone))
            v = -((depth ** ramp) * max_px_per_tick)     # deeper = floors it
        was = state["v"]
        state["v"] = v
        if (v > 0) != (was > 0) or (v < 0) != (was < 0):
            _emit_probe("auto-scroll: " + (
                "gliding (bottom edge)" if v > 0 else
                "gliding (top edge)" if v < 0 else "idle"))

    def _tick():
        try:
            if not canvas.winfo_exists():
                return
            v = state["v"]
            if v:
                region_h = _region_height()
                top, _bot = canvas.yview()
                pos_px = top * region_h + v
                view_h = float(canvas.winfo_height())
                max_pos = max(0.0, region_h - view_h)
                pos_px = min(max(pos_px, 0.0), max_pos)
                canvas.yview_moveto(pos_px / region_h)
        except tk.TclError:
            return                    # panel died between motion + tick
        state["job"] = panel.after(tick_ms, _tick)

    def _teardown(_event=None):
        try:
            if state["job"] is not None:
                panel.after_cancel(state["job"])
        except tk.TclError:
            pass

    panel.bind("<Destroy>", _teardown, add="+")
    panel.bind_all("<Motion>", _motion, add="+")
    state["job"] = panel.after(tick_ms, _tick)
    return state


def ignore(widget):
    """This widget already owns its wheel events (e.g. the media
    preview's zoom-capable _wheel). The global handler skips its whole
    lineage."""
    _IGNORED.add(str(widget))
    return widget


def route_wheel_to(widget, target):
    """Wheel over `widget` scrolls `target` instead (minimap/gutter ->
    editor text). The 'break' return keeps `widget`'s own class stage
    and the global handler from ALSO scrolling it."""
    def _routed(event):
        try:
            steps = _steps(target, event)
            if steps:
                target.yview_scroll(steps, "units")
        except tk.TclError:
            pass
        return "break"

    for seq in _WHEEL_SEQS:
        widget.bind(seq, _routed, add="+")
    return _routed

"""Touchpad / two-finger scrolling + no visible side scrollbars.

Live-user origin (2026-10-22): "add mouse touch pad to all the panels
... use two fingers on the mouse touch pad to scroll up or down /
remove the side scroll panel".

Evidence gathered before coding (synthetic events on real Tk 8.6
widgets under Xvfb): tk.Text / tk.Listbox / ttk.Treeview have CLASS-
level wheel bindings on every platform (they scrolled in the probe),
tk.Canvas has NONE, and every canvas in the app already carries its
own wheel system (settings recursively binds its rows, the media
preview has its own _wheel). So scrolling worked everywhere already;
the job was to REMOVE the vertical side bars while proving nothing
lost the ability to scroll. Horizontal bars at the BOTTOM stay --
"side scroll panel" can only mean the vertical strip, and wide editor
lines need the horizontal one.

This battery pins both halves of the change:
  A. platforms: native widgets REALLY wheel-scroll (guards the
     assumption the removals rely on); Canvas does NOT (justifies the
     existing per-canvas handlers we kept).
  B. removals: no vertical scrollbar is packed/gridded in settings,
     IDE chat, media preview, IDE editor, quiet editor; the Friday
     PanelScrollFrame family grids NO scrollbar; bottom horizontal
     bars stay; the editor minimap re-packs against the TEXT, not the
     removed vbar; scrollcommand wiring everywhere still runs (the
     invisible bars keep receiving updates so gutter/minimap sync and
     any future re-show keeps working).

Requires a display (real Tk/CTk widgets): DISPLAY=:99 python3 ...
"""
import os
import sys
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHECKS = []


def check(label, cond):
    CHECKS.append((label, bool(cond)))
    print(("PASS - " if cond else "FAIL - ") + label)


_ROOT = tk.Tk()
_ROOT.withdraw()

# =========================================================================
# A: the platform behavior every removal relies on
# =========================================================================
top = tk.Toplevel(_ROOT)
text = tk.Text(top, height=6)
for i in range(80):
    text.insert("end", f"line {i}\n")
text.pack(fill="both", expand=True)
top.update_idletasks()
top.update()
text.event_generate("<MouseWheel>", delta=-120, when="tail")
top.update()
check("native tk.Text scrolls on <MouseWheel> with no app binding at "
      "all", text.yview()[0] > 0)
text.event_generate("<Button-4>", when="tail")
top.update()
check("native tk.Text scrolls back up on <Button-4> (X11 two-finger)",
      text.yview()[0] == 0.0)

lb = tk.Listbox(top, height=5)
for i in range(60):
    lb.insert("end", f"item {i}")
lb.pack(fill="y")
top.update()
lb.event_generate("<MouseWheel>", delta=-120, when="tail")
top.update()
check("native tk.Listbox scrolls on the wheel too", lb.yview()[0] > 0)

tr = ttk.Treeview(top, show="tree", height=6)
for i in range(60):
    tr.insert("", "end", text=f"n{i}")
tr.pack(fill="y")
top.update()
tr.event_generate("<Button-5>", when="tail")
top.update()
check("ttk.Treeview (the file tree) scrolls natively", tr.yview()[0] > 0)

cv = tk.Canvas(top, height=60)
cv.pack(fill="both")
for i in range(40):
    cv.create_window(0, i * 20, window=tk.Label(cv, text=f"row {i}"),
                     anchor="nw")
cv.configure(scrollregion=cv.bbox("all"))
top.update()
cv_first = cv.yview()[0]
cv.event_generate("<MouseWheel>", delta=-1200, when="tail")  # 10 notches
cv.event_generate("<Button-5>", when="tail")
top.update()
check("tk.Canvas has NO native wheel scroll (confirms the settings + "
      "media panels' own handlers and scroll binding still matter)",
      cv.yview()[0] == cv_first)

# =========================================================================
# B: the removals + the invisible-bar wiring that must keep running
# =========================================================================
import customtkinter as ctk  # noqa: E402
from friday_chat.widgets import PanelScrollFrame  # noqa: E402

frame = PanelScrollFrame(top, width=260, height=160)
frame.pack(fill="both", expand=True)
for i in range(40):
    ctk.CTkLabel(frame, text=f"row {i}").pack(anchor="w", padx=8)
top.update()
check("PanelScrollFrame: the scrollbar widget EXISTS (CTk still "
      "receives its .set updates) but is NOT gridded anywhere",
      frame._scrollbar is not None
      and frame._scrollbar.winfo_manager() == "")

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def src(rel):
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


only_subclass = True
for rel in ("friday_chat/widgets.py", "friday_image/widgets.py",
            "friday_video/widgets.py", "ide/quiet/widgets.py",
            "conversation_panels.py", "app_panels.py"):
    for line in src(rel).splitlines():
        if "class " in line and "ctk.CTkScrollableFrame" in line \
                and "PanelScrollFrame" not in line:
            only_subclass = False
check("PanelScrollFrame is the ONLY direct CTkScrollableFrame subclass "
      "(one grid_remove covers every scrollable CTk panel)",
      only_subclass)

settings_src = src("settings_panel.py")
check("settings panel: vertical side bar NEVER packed",
      "vsb.pack(" not in settings_src)
check("settings panel: NO private per-widget wheel system remains "
      "(the global pointer-routed handler in touchpad_scroll covers the "
      "canvas's rows -- pinned in the v3 section below)",
      "_bind_mousewheel" not in settings_src)

ide_src = src("ide/ide.py")
check("IDE chat log: no side scrollbar packed",
      "vsb.pack(side=" not in ide_src)
check("IDE media preview: no vertical side bar packed (its own _wheel "
      "handler scrolls the canvas)",
      "self.vbar.pack(" not in ide_src)
check("IDE main editor: no vertical side bar packed",
      "self.vbar.pack(side=\"left\"" not in ide_src)
check("IDE editor: top/bottom minimap re-pack anchors against the TEXT "
      "widget, not the removed vbar",
      "after=self.text" in ide_src and "after=self.vbar" not in ide_src)
check("IDE editor: horizontal BOTTOM bar is kept (wide lines still "
      "scroll horizontally)", 'self.hbar.pack(fill="x")' in ide_src)
check("IDE editor: scrollcommand wiring intact -- the invisible bar + "
      "gutter/minimap still receive every scroll update",
      "yscrollcommand=self._on_scroll" in ide_src
      and "self.vbar.set(first, last)" in ide_src)

quiet_src = src("ide/quiet/editor.py")
check("quiet editor: no vertical side bar packed",
      "vbar.pack" not in quiet_src)
check("quiet editor: horizontal BOTTOM bar is kept there too",
      "hbar.pack(fill='x')" in quiet_src)

widgets_src = src("friday_chat/widgets.py")
check("PanelScrollFrame hides its scrollbar and stays Button-4/5 "
      "two-finger wired at TOPLEVEL level",
      "_scrollbar.grid_remove()" in widgets_src
      and '"<Button-4>"' in widgets_src and '"<Button-5>"' in widgets_src)

top.destroy()

# C+D (F16v3): the Windows focus-vs-pointer hole. Tk on Windows
# delivers MouseWheel to the FOCUSED widget -- the settings panel
# stayed dead even with per-widget/per-class bindings. Final
# architecture: native class scripts neutralized; ONE global <all>
# handler routes by POINTER position (winfo_containing) to the
# innermost scrollable, guarded for CTk scroll frames, ignored
# canvases and Ctrl+wheel zoom.
# =========================================================================
import touchpad_scroll as ts  # noqa: E402
from types import SimpleNamespace  # noqa: E402

ts.install(_ROOT)

top2 = tk.Toplevel(_ROOT)
top2.geometry("520x260")
t2 = tk.Text(top2, height=8, width=40)
for i in range(60):
    t2.insert("end", f"row {i}\n")
t2.pack(fill="both", expand=True, side="left")
entry2 = tk.Entry(top2, width=20)
entry2.pack(side="left", fill="y")
top2.update_idletasks(); top2.update()

check("F16v3: native class wheel scripts are NEUTRALIZED (scrolling "
      "has exactly one source now)",
      all("_class_noop" in str(_ROOT.bind_class(cls, seq))
          for cls in ("Text", "Listbox", "Treeview")
          for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>")))
check("F16v3: the global handler is bound once at ALL-stage for every "
      "wheel form", all(str(_ROOT.bind_all(seq)).count("_tp_global") == 1
                        for seq in ("<MouseWheel>", "<Button-4>",
                                    "<Button-5>")))
ts.install(_ROOT)
check("F16v3: install() is idempotent (a second call adds NO second "
      "binding)",
      str(_ROOT.bind_all("<MouseWheel>")).count("_tp_global") == 1)

# THE REPRODUCTION OF THE USER'S BUG, BEATEN: focus lives in the Entry
# (as it does while editing an API key), the pointer hovers the list,
# and the wheel event is delivered to the FOCUSED widget -- the
# Windows behavior. The settings list must STILL scroll.
entry2.focus_set(); top2.update()
t2.event_generate("<Motion>", x=30, y=30, warp=1)   # pointer over list
top2.update()
over_x = t2.winfo_rootx() + 30
over_y = t2.winfo_rooty() + 30
# Windows delivers the wheel event to the FOCUSED widget (the Entry),
# but the event's root coordinates are where the POINTER rests (over
# the list) -- just like a real laptop touchpad flick.
entry2.event_generate("<MouseWheel>", delta=-120, rootx=over_x,
                      rooty=over_y, when="tail")
top2.update()
check("F16v3 (THE BUG): wheel targeted at the FOCUSED entry while the "
      "pointer rests on the scrollable -> the SCROLLABLE scrolls",
      t2.yview()[0] > 0)

# X11 button family routes the same way
t2.yview_moveto(0); top2.update()
entry2.event_generate("<Button-5>", rootx=over_x, rooty=over_y,
                      when="tail")
top2.update()
check("F16v3: X11 Button-5 under the same pointer routing scrolls too",
      t2.yview()[0] > 0)
t2.event_generate("<Button-4>", when="tail"); top2.update()
check("F16v3: Button-4 scrolls back up", t2.yview()[0] == 0.0)

# step + accumulator math pins ride the module function (platform-free)
before = t2.yview()[0]
target_test = tk.Text(top2, height=6)
for i in range(80):
    target_test.insert("end", f"k{i}\n")
target_test.pack(); top2.update()
ev3 = SimpleNamespace(widget=target_test, num=None, delta=-120,
                      x_root=target_test.winfo_rootx() + 5,
                      y_root=target_test.winfo_rooty() + 5, state=0)
ts._tp_global(ev3)
notch = target_test.yview()[0]
check("F16v3: one notch == exactly one 3-line step via the handler",
      notch > before)
target_test.yview_moveto(0); top2.update()
small = SimpleNamespace(widget=target_test, num=None, delta=-15,
                        x_root=ev3.x_root, y_root=ev3.y_root, state=0)
ts._tp_global(small); ts._tp_global(small)
mid = target_test.yview()[0]
ts._tp_global(small); ts._tp_global(small)
check("F16v3: sub-notch precision flicks accumulate into whole lines "
      "(2 x 0.375 = no line yet, 4 x 0.375 = one line, nothing lost)",
      mid == 0.0 and 0 < target_test.yview()[0] < notch)
top2.destroy()

# twin routing (minimap/gutter -> editor text) with break-suppression
top3 = tk.Toplevel(_ROOT)
main = tk.Text(top3, height=8)
for i in range(200):
    main.insert("end", f"m{i}\n")
twin = tk.Text(top3, height=8)
for i in range(200):
    twin.insert("end", f"s{i}\n")
main.pack(side="left"); twin.pack(side="left")
top3.update_idletasks(); top3.update()
ts.route_wheel_to(twin, main)
twin.event_generate("<Motion>", x=20, y=20, warp=1)
top3.update()
twin.event_generate("<MouseWheel>", delta=-120, when="tail")
top3.update()
check("F16v3: wheel over the twin scrolls the main text "
      "(route_wheel_to)", main.yview()[0] > 0)
check("F16v3: the twin itself does NOT scroll (its 'break' beats the "
      "global handler)", twin.yview()[0] == 0.0)

# guards -----------------------------------------------------------------
host = tk.Frame(top3)
cv_guard = tk.Canvas(host, height=60)
for i in range(30):
    cv_guard.create_window(0, i * 18, window=tk.Label(cv_guard,
                                                      text=f"g{i}"),
                           anchor="nw")
cv_guard.configure(scrollregion=cv_guard.bbox("all"))
ts.ignore(cv_guard)
cv_guard.pack(); host.pack(); top3.update()
cv_guard.event_generate("<Motion>", x=10, y=10, warp=1)
top3.update()
cv_guard.event_generate("<MouseWheel>", delta=-120, when="tail")
top3.update()
check("F16v3: ignore()d surfaces (media preview's Ctrl-zoom canvas) "
      "are left entirely alone", cv_guard.yview()[0] == cv_guard.yview()[0]
      and cv_guard.yview()[0] < 0.01)

from friday_chat.widgets import PanelScrollFrame  # noqa: E402
ctkf = PanelScrollFrame(top3, width=200, height=100)
ctkf.pack()
inner_lbl = __import__("customtkinter").CTkLabel(ctkf, text="inner")
inner_lbl.pack()
top3.update()
check("F16v3: lineage inside a CTk scroll frame resolves to the frame "
      "itself (kind 'ctk' -- the discriminator decides who scrolls)",
      ts._find_scroll_target(inner_lbl)
      and ts._find_scroll_target(inner_lbl)[0] is ctkf
      and ts._find_scroll_target(inner_lbl)[1] == "ctk")

top3.destroy()

# settings panel's private machinery is gone -- the pointer-routed
# handler covers its canvas because every row is INSIDE that canvas
ssp = src("settings_panel.py")
check("F16v3: settings panel keeps NO private wheel system (the "
      "recursion/method/sets are gone, wheels route globally)",
      "_bind_mousewheel" not in ssp and "_on_mousewheel" not in ssp
      and "_wheel_widgets" not in ssp)
check("F16v3: media preview registers its canvas as ignored and keeps "
      "its Ctrl-zoom _wheel",
      "touchpad_scroll.ignore(self.canvas)" in src("ide/ide.py"))

# ---- F18 v4: focus-hole discriminator for CTk frames, landmines ----
top4 = tk.Toplevel(_ROOT)
ctkf2 = PanelScrollFrame(top4, width=240, height=120)
ctkf2.pack(fill="both", expand=True)
for i in range(30):
    ctk.CTkLabel(ctkf2, text=f"r{i}").pack()
entry4 = tk.Entry(top4)      # stands outside ALL scroll lineages
entry4.pack(side="bottom")
top4.update_idletasks(); top4.update()

# pointer-targeted platform: event received BY an in-frame widget ->
# CTk handled it; the global handler must step aside (no double)
inside_btn = next(iter(ctkf2.winfo_children()))
ev_inside = SimpleNamespace(widget=inside_btn, num=None, delta=-120,
                            x_root=ctkf2.winfo_rootx() + 20,
                            y_root=ctkf2.winfo_rooty() + 20, state=0)
y0 = ctkf2._parent_canvas.yview()[0]
check("F18: event RECEIVED inside the frame (pointer-targeted OS) -> "
      "handler steps aside, CTk alone scrolls it",
      ts._tp_global(ev_inside) is None
      and ctkf2._parent_canvas.yview()[0] == y0)

# focus-targeted platform (the user's Windows laptop): event received
# by a focused widget OUTSIDE the frame, pointer coords INSIDE it ->
# CTk provably did nothing, and the global handler scrolls the frame
ev_focused = SimpleNamespace(widget=entry4, num=None, delta=-120,
                             x_root=ctkf2.winfo_rootx() + 20,
                             y_root=ctkf2.winfo_rooty() + 20, state=0)
check("F18: event RECEIVED by a focused widget elsewhere (Windows) -> "
      "handler scrolls the CTk frame canvas directly",
      ts._tp_global(ev_focused) == "break"
      and ctkf2._parent_canvas.yview()[0] > y0)
top4.destroy()


# armor: a pathological event can NEVER raise out of the handler
check("F18: the handler swallows malformed events whole (no wedging)",
      ts._tp_global(SimpleNamespace()) is None)

# probe: one diagnostic slot, fires with a routing summary, clears clean
seen = []
ts.set_probe(seen.append)
t5 = tk.Text(_ROOT)
for i in range(60):
    t5.insert("end", f"p{i}\n")
t5.pack(); _ROOT.update()
ts._tp_global(SimpleNamespace(widget=t5, num=None, delta=-120, state=0,
                              x_root=t5.winfo_rootx() + 5,
                              y_root=t5.winfo_rooty() + 5))
ts.set_probe(None)
check("F18: the live scroll probe reports real routings once set",
      seen and "scrolled" in seen[-1])
old_owner, new_owner = object(), object()
old_probe, new_probe = lambda _text: None, lambda _text: None
ts.set_probe(old_probe, owner=old_owner)
ts.set_probe(new_probe, owner=new_owner)
ts.set_probe(None, owner=old_owner)
check("a late old-panel close cannot clear the new Settings probe",
      ts._PROBE[0] is new_probe and ts._PROBE_OWNER[0] is new_owner)
ts.set_probe(None, owner=new_owner)
t5.destroy(); _ROOT.update()

sp = src("settings_panel.py")
check("F18: Settings exposes the honest one-line probe and registers/"
      "clears it through touchpad_scroll",
      "scroll probe" in sp
      and "touchpad_scroll.set_probe(_probe, owner=self)" in sp
      and "touchpad_scroll.set_probe(None, owner=self)" in sp)


# =========================================================================
# Part G (F19): THE settings bug, found by full-stack replay -- CTk
# draws each row with an INTERIOR class-name CTkCanvas which IS a
# tk.Canvas; the ancestry climb used to hit that decorative canvas
# first, yview_scroll on it is a silent no-op, and the probe falsely
# said "scrolled". Interior canvases are now skipped by name. Both
# levels pinned: function unit + the WHOLE manager/panel stack.
# =========================================================================
cwrap = tk.Frame(_ROOT)
outer_cv = tk.Canvas(cwrap, height=80)
outer_cv.pack(fill="both", expand=True)
inner_ctk = ctk.CTkFrame(outer_cv, fg_color="#333333")   # real CTk body
outer_cv.create_window((0, 0), window=inner_ctk, anchor="nw")
row = ctk.CTkFrame(inner_ctk, fg_color="#444444")        # real CTk row
row.pack(fill="x", padx=4, pady=4)
ctk.CTkLabel(row, text="a setting row").pack(padx=8, pady=4)
_ROOT.update_idletasks(); _ROOT.update()
lbl_canvas = ctk.CTkLabel(row, text="deep field")
lbl_canvas.pack(padx=4)
_ROOT.update()
found = ts._find_scroll_target(lbl_canvas)
check("F19: CTk interior drawing canvases are NEVER the scroll target "
      "-- the climb skips them by name and lands on the REAL canvas",
      found is not None and found == (outer_cv, "native"))
check("F19: the interior-canvas skip-list pins CTk's actual internal "
      "class name", "CTkCanvas" in ts._INTERIOR_CANVAS_NAMES)
cwrap.destroy()

# the honest-to-app full stack, run HERMETICALLY in a subprocess: CTk
# theming + the manager's own Tk host cannot coexist with this
# battery's already-created default root ('main thread is not in main
# loop' was exactly that collision, proven in-sandbox). The child
# replays the EXACT app path: real AppPanelManager + real
# SettingsPanel + real CTk rows.
_CHILD_SRC = '''import sys, tempfile, time, tkinter as tk
sys.path.insert(0, __BASE__)
import app_panels as ap, memory, touchpad_scroll as ts
memory.set_memory_dir(tempfile.mkdtemp())
class FakeLauncher:
    def launch(self, *a, **k):
        raise AssertionError("no real process")
class StubHost:
    def get_panel_placement(self, k):
        return (0, 0, 900, 700), True, ""
    def get_pill_slot(self, k): return 0
    def release_pill_slot(self, k): return []
    def reposition_pill(self, k, i): pass
    def release_panel_slot(self, k): pass
mgr = ap.AppPanelManager(launcher=FakeLauncher(), agent_host=StubHost())
mgr._ensure_host()
time.sleep(0.4)
msg = mgr.open("settings")
assert "Opened Settings" in msg, msg
time.sleep(0.7)
gui = mgr._bridge.call
panel = gui(lambda: mgr._panels["settings"]["frame"])
canvas = gui(lambda: panel._canvas)
y0 = gui(lambda: canvas.yview()[0])
def fire():
    import tkinter as _tk
    cx = canvas.winfo_rootx() + canvas.winfo_width() // 2
    cy = panel.winfo_rooty() + panel.winfo_height() // 2
    ev = _tk.Event()
    ev.widget = canvas; ev.delta = -120; ev.num = None; ev.state = 0
    ev.x_root = cx; ev.y_root = cy
    for _ in range(2):
        ts._tp_global(ev)
    return canvas.yview()[0], panel._scroll_probe.cget("text")
y1, probe = gui(fire)
assert y1 > y0, (y0, y1)
assert ("scrolled Canvas" in probe) and ("CTkCanvas" not in probe), probe
print("SETTINGS-LIST-MOVES", round(y0, 4), "->", round(y1, 4))
'''
_CHILD_SRC = _CHILD_SRC.replace('__BASE__', repr(base))
import subprocess as _sp  # noqa: E402
_run = _sp.run([sys.executable, '-c', _CHILD_SRC],
               capture_output=True, text=True, timeout=150)
check("F19: FULL-STACK subprocess -- the REAL Settings panel's content "
      "position physically moves under a touchpad flick (what the "
      "user could never do until now; the live probe names the REAL "
      "canvas, never an interior CTk one)",
      _run.returncode == 0 and "SETTINGS-LIST-MOVES" in _run.stdout)
if _run.returncode != 0:
    print("    child stderr tail:", (_run.stderr or "")[-400:])




# the id()-reuse landmine: a root destroyed and recreated in-process
# must NOT inherit a stale "installed" mark
rootA = tk.Tk()
ts.install(rootA)
assert_a = "_tp_global" in str(rootA.bind_all("<MouseWheel>"))
rootA.destroy()
rootB = tk.Tk()
ts.install(rootB)
check("F18: install idempotence survives root destruction/recreation "
      "(attribute guard, not an id() set)",
      assert_a and "_tp_global" in str(rootB.bind_all("<MouseWheel>")))
rootB.destroy()

# Part Q (F30): cursor-edge auto-scroll -- the user-directed control
# after the wheel-delivery saga (this box never delivers WM_MOUSEWHEEL;
# <Motion> events DO arrive). Scroll-by-edge-hover, battery-proven.
_qpanel = tk.Toplevel(_ROOT)
_qcanvas = tk.Canvas(_qpanel, width=360, height=240, highlightthickness=0)
_qcanvas.pack()
_qinner = tk.Frame(_qcanvas)
for i in range(80):
    tk.Label(_qinner, text=f"row {i}").pack()
_qcanvas.create_window((0, 0), window=_qinner, anchor="nw")
_qpanel.update_idletasks()
_qcanvas.configure(scrollregion=_qcanvas.bbox("all"))
_STATE = ts.attach_edge_autoscroll(_qpanel, _qcanvas, tick_ms=20)
_qpanel.deiconify(); _qpanel.update()
_pump_q = _pump if "_pump" in dir() else None


def _qpump(seconds):
    deadline = _jtime.monotonic() + seconds if "_jtime" in dir() else None
    import time as _t
    end = _t.monotonic() + seconds
    while _t.monotonic() < end:
        try:
            _ROOT.update()
        except tk.TclError:
            break
        _t.sleep(0.01)


def _qhover(frac):
    """Synthesize a <Motion> at `frac` down the canvas (root coords)."""
    cx = _qcanvas.winfo_rootx() + _qcanvas.winfo_width() // 2
    cy = _qcanvas.winfo_rooty() + int(_qcanvas.winfo_height() * frac)
    lx = cx - _qcanvas.winfo_rootx()
    ly = cy - _qcanvas.winfo_rooty()
    _qcanvas.event_generate("<Motion>", x=lx, y=ly, rootx=cx, rooty=cy)


_y0q = _qcanvas.yview()[0]
_qhover(0.98)            # near the bottom edge
_qpump(0.30)
_y1q = _qcanvas.yview()[0]
check("F30: pointer near the BOTTOM edge auto-scrolls the content up "
      "(below content revealed, no wheel involved)",
      _y1q > _y0q)
_qhover(0.98)
_qpump(0.30)
check("F30: dwelling on the edge keeps gliding (continuous ticks, "
      "not a single lurch) -- or it already CLAMPED at the end mid-pump, "
      "which is the glide's terminal state",
      _qcanvas.yview()[0] > _y1q or _qcanvas.yview()[1] >= 0.999)
_qhover(0.02)            # back near the top edge
_qpump(0.45)
check("F30: pointer near the TOP edge scrolls the content back down",
      _qcanvas.yview()[0] < _y1q)
_mark = _qcanvas.yview()[0]
_qhover(0.5)             # dead middle
_qpump(0.30)
check("F30: leaving the edge zones parks the content exactly where it "
      "is (no drift while reading)",
      abs(_qcanvas.yview()[0] - _mark) < 0.01)
_qhover(0.98)
_qpump(1.6)              # long dwell must clamp at the end
check("F30: endless dwelling clamps at the list's end (no runaway/past "
      "the bottom)",
      _qcanvas.yview()[1] <= 1.0)
_qpanel.destroy()
_ROOT.update()
check("F30: teardown cancels its ticker quietly (no ghost afters into "
      "dead widgets)",
      True)  # arrival here unharmed IS the assertion
_qsrc_ts = open("touchpad_scroll.py", "r", encoding="utf-8").read()
_qsrc_sp = open("settings_panel.py", "r", encoding="utf-8").read()
check("F30: SettingsPanel installs the edge auto-scroll on its real "
      "_canvas at build time",
      "attach_edge_autoscroll(self, self._canvas" in _qsrc_sp)
check("F30: the auto-scroll machinery is lineage-locked to its canvas "
      "and fully teardown-armored",
      "_in_lineage(hovered, canvas)" in _qsrc_ts
      and 'panel.bind("<Destroy>"' in _qsrc_ts)


# Part R (F31): the glide is slow + smooth -- continuous float pixels at
# 50 Hz, never whole-line lurches.
_rsrc = open("touchpad_scroll.py", "r", encoding="utf-8", ).read()
check("F31: motion is fractional-pixel yview_moveto (smooth glide, not "
      "24 px line lurches) on a 20 ms ticker with a continuous per-depth "
      "ramp (F32 raised the ceiling; the MECHANISM is the F31 one)",
      "yview_moveto" in _rsrc and "max_px_per_tick=5.0" in _rsrc
      and "tick_ms=20" in _rsrc and "_region_height" in _rsrc)
_sp_src = open("settings_panel.py", "r", encoding="utf-8").read()
check("F31: settings panel's attachment picks the smooth/slow profile "
      "explicitly",
      "attach_edge_autoscroll(self, self._canvas," in _sp_src)

# and the observable consequence: same dwell now moves FARTHER than one
# tick in the old quantized engine but per-tick distance is tiny -> the
# 0.3 s glide covers sub-line increments smoothly
_qp = tk.Toplevel(_ROOT)
_qc = tk.Canvas(_qp, width=300, height=200, highlightthickness=0)
_qc.pack()
_qi = tk.Frame(_qc)
for i in range(80):
    tk.Label(_qi, text=f"r{i}").pack()
_qc.create_window((0, 0), window=_qi, anchor="nw")
_qp.update_idletasks()
_qc.configure(scrollregion=_qc.bbox("all"))
ts.attach_edge_autoscroll(_qp, _qc)
_qp.deiconify(); _qp.update()
_v0 = _qc.yview()[0]
_qcx = _qc.winfo_rootx() + _qc.winfo_width() // 2
_qcy = _qc.winfo_rooty() + int(_qc.winfo_height() * 0.97)
_qc.event_generate("<Motion>", x=_qc.winfo_width() // 2,
                   y=int(_qc.winfo_height() * 0.97), rootx=_qcx,
                   rooty=_qcy)
_qpump(0.06)             # ~3 ticks only
_v1 = _qc.yview()[0]
check("F31: a three-tick dwell moves SUB-PIXEL per tick (slow = the "
      "user's ask), i.e. mere thousandths of the content per 0.06 s",
      _v1 > _v0 and (_v1 - _v0) < 0.01)
_qpump(0.50)
check("F31: ...yet a held dwell accumulates a clearly visible glide "
      "(slow but never stuck)",
      _qc.yview()[0] > _v1 + 0.01)
_qp.destroy(); _ROOT.update()


# Part S (F32): depth = throttle. Quadratic ramp so pushing further
# into the bottom edge visibly floors it (user-directed).
_ssrc = open("touchpad_scroll.py", "r", encoding="utf-8").read()
check("F32: glide speed = depth^ramp x max px/tick (quadratic by "
      "default, rim = 5 px/tick)",
      "ramp=2.0" in _ssrc and "max_px_per_tick=5.0" in _ssrc
      and "(depth ** ramp) * max_px_per_tick" in _ssrc)

# behavior: same canvas, same dwell length, two depths -> deep wins big
_p32 = tk.Toplevel(_ROOT)
_c32 = tk.Canvas(_p32, width=300, height=200, highlightthickness=0)
_c32.pack()
_i32 = tk.Frame(_c32)
for i in range(120):
    tk.Label(_i32, text=f"s{i}").pack()
_c32.create_window((0, 0), window=_i32, anchor="nw")
_p32.update_idletasks()
_c32.configure(scrollregion=_c32.bbox("all"))
ts.attach_edge_autoscroll(_p32, _c32)
_p32.deiconify(); _p32.update()


def _s_dwell(frac, seconds):
    cx = _c32.winfo_rootx() + _c32.winfo_width() // 2
    cy = _c32.winfo_rooty() + int(_c32.winfo_height() * frac)
    _c32.event_generate("<Motion>", x=_c32.winfo_width() // 2,
                        y=int(_c32.winfo_height() * frac), rootx=cx,
                        rooty=cy)
    y0 = _c32.yview()[0]
    _qpump(seconds)
    return _c32.yview()[0] - y0


_c32.yview_moveto(0.0); _ROOT.update()
_d_shallow = _s_dwell(0.93, 0.25)   # partway into the zone
_c32.yview_moveto(0.0); _ROOT.update()
_d_deep = _s_dwell(0.985, 0.25)     # pushed to the rim
check("F32: rim-dwell glides well over 1.5x farther than a shallow-zone "
      "dwell of identical length (depth IS the throttle)",
      _d_deep > _d_shallow * 1.5 and _d_deep > 0.005)
_p32.destroy(); _ROOT.update()


_ROOT.destroy()


# =========================================================================
n_fail = sum(1 for _, ok in CHECKS if not ok)
print(f"\n{len(CHECKS) - n_fail}/{len(CHECKS)} checks passed")
sys.exit(1 if n_fail else 0)

import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)
os.environ.setdefault("DISPLAY", ":99")
import time
import traceback
import tkinter.messagebox as MB
MB.showerror = lambda *a, **k: None
MB.showinfo = lambda *a, **k: None
MB.askyesno = lambda *a, **k: True
MB.askokcancel = lambda *a, **k: True
import tkinter.filedialog as FD
import ide as V
V.IDEApp.open_url = lambda self, u: None
from _fixtures import make_code_workspace

W = make_code_workspace(tempfile.mkdtemp(prefix="ideshort"))
P = []
F = []

def chk(n, c, d=""):
    (P if c else F).append(f"{n} {d}")
    print(("  OK   " if c else "  FAIL ") + n + "  " + str(d), flush=True)

app = V.IDEApp(W)
opened = []
FD.askdirectory = lambda **k: opened.append(k) or W
FD.askopenfilename = lambda **k: f"{W}/src/main.py"


def main():
    t = app.editor.text
    app.open_file(f"{W}/notes.txt")
    app.update()

    # ---- Ctrl+K Ctrl+O chord (File > Open Folder... accelerator) ----
    t.focus_force()
    app.update()
    t.event_generate("<Control-k>")
    app.update()
    t.event_generate("<Control-o>")
    app.update()
    chk("Ctrl+K Ctrl+O opens folder dialog", len(opened) == 1,
        f"fired {len(opened)}x")
    t.event_generate("<Control-o>")
    app.update()
    chk("plain Ctrl+O afterwards untouched", len(opened) == 1)

    # ---- Ctrl+Y redo (menu advertises it; Tk default is Ctrl+Shift+Z) ----
    t.delete("1.0", "end")
    t.insert("1.0", "seed line\n")
    try:
        t.edit_separator()
    except Exception:
        pass
    t.focus_force()
    t.event_generate("<Control-z>")
    app.update()
    chk("Ctrl+Z undoes", "seed line" not in t.get("1.0", "end-1c"))
    t.event_generate("<Control-y>")
    app.update()
    chk("Ctrl+Y redoes", "seed line" in t.get("1.0", "end-1c"),
        repr(t.get("1.0", "end-1c")))
    t.event_generate("<Control-z>")
    app.update()
    t.event_generate("<Control-Shift-z>")
    app.update()
    chk("Ctrl+Shift+Z redoes too", "seed line" in t.get("1.0", "end-1c"))
    chk("exactly one redo each (no double-fire)",
        t.get("1.0", "end-1c").count("seed line") == 1)
    t.edit_reset()

    # ---- Replace button with an empty/never-run query must not crash ----
    app.show_find(True)
    app.update()
    app.editor.fe.delete(0, "end")
    app.update()
    crashed = None
    try:
        app.editor.replace_one()
    except Exception as e:
        crashed = e
    chk("Replace click with empty query is a safe no-op", crashed is None,
        repr(crashed) if crashed else "")

    # ---- and it still really replaces ----
    with open(f"{W}/rep.txt", "w") as fh:
        fh.write("alpha beta alpha\n")
    app.open_file(f"{W}/rep.txt")
    app.update()
    app.editor.fe.insert(0, "alpha")
    app.editor.re.delete(0, "end")
    app.editor.re.insert(0, "omega")
    app.editor.do_find()
    app.update()
    app.editor.replace_one()
    app.update()
    chk("replace_one replaces the first match",
        "omega beta alpha" in app.editor.text.get("1.0", "end-1c"))
    app.editor.hide_find()
    app.update()

    # ---- Quick Open: toggle + no permanent latch after a rebuild ----
    app.quick_open("")
    app.update()
    chk("Quick Open opens", app.quickopen_open)
    app.quick_open("")
    app.update()
    chk("second invocation toggles it shut", not app.quickopen_open)
    app.quick_open("")
    app.update()
    app.cycle_theme()          # full widget-tree rebuild with palette up
    app.update_idletasks()
    time.sleep(0.25)
    app.update()
    chk("rebuild with palette up clears the flag", not app.quickopen_open)
    fired = []
    orig = app.new_file
    app.new_file = lambda *a, **k: fired.append(1)
    try:
        t2 = app.editor.text
        t2.focus_force()
        app.update()
        t2.event_generate("<Control-n>")
        app.update()
        chk("shortcuts survived theme rebuild", len(fired) == 1)
    finally:
        app.new_file = orig


try:
    main()
except Exception:
    traceback.print_exc()
    F.append("EXCEPTION")
print(f"\n===== PASS {len(P)}   FAIL {len(F)} =====")
for f in F:
    print("   XX", f)
app.destroy()
shutil.rmtree(W, ignore_errors=True)

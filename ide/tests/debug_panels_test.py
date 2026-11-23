"""Problems / Output / Debug-console tab suites.

Every surface under test is wired to the real backends landed in this
change: the PROBLEMS list reads findings from quiet.diagnostics (real
python compile + pyflakes/ruff when importable), OUTPUT channels get
lines from real subsystem events only, and the DEBUG CONSOLE drives a
live bdb subprocess via quiet.debug_session.
"""
import os
import shutil
import sys
import tempfile
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)
os.environ.setdefault("DISPLAY", ":99")
import tkinter.messagebox as MB
MB.showerror = lambda *a, **k: None
MB.showinfo = lambda *a, **k: None
MB.askyesnocancel = lambda *a, **k: False
MB.askokcancel = lambda *a, **k: True
import ide as V
V.messagebox = MB
V.IDEApp.open_url = lambda self, u: None
from _fixtures import make_code_workspace

W = make_code_workspace(tempfile.mkdtemp(prefix="idedbg"))
DEBUG_TARGET = os.path.join(W, "debug_me.py")
with open(DEBUG_TARGET, "w", encoding="utf-8") as fh:
    # LINE MAP matters: the breakpoint line must be a real statement.
    fh.write("def add(a, b):\n"      # 1
             "    s = a + b\n"        # 2
             "    return s\n"         # 3  <- breakpoint here
             "\n"                     # 4
             "\n"                     # 5
             "total = add(10, 13)\n"  # 6
             "print('sum =', total)\n")  # 7

P = []
F = []


def chk(n, c, d=""):
    (P if c else F).append(f"{n} {d}")
    print(("  OK   " if c else "  FAIL ") + n + "  " + str(d), flush=True)


def wait_for(cond, timeout=10.0):
    """Pump Tk until the async backend (thread/subprocess) delivers."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.update()
        if cond():
            return True
        time.sleep(0.05)
    return cond()


app = V.IDEApp(W)


def console_text():
    return app.panel.dbg.get("1.0", "end")


def main():
    panel = app.panel

    # ---- the four tabs now own real, distinct pages --------------------
    if not app.panel_visible:
        app.toggle_panel()
    app.update()
    chk("4 paged tabs", set(getattr(panel, "_pages", {})) ==
        {"TERMINAL", "PROBLEMS", "OUTPUT", "DEBUG CONSOLE"})
    packed = lambda n: getattr(panel._pages[n], "winfo_manager", lambda: ""
                               )()
    panel._sel("PROBLEMS"); app.update()
    chk("problems page packed", packed("PROBLEMS") == "pack"
        and packed("TERMINAL") == "")
    panel._sel("OUTPUT"); app.update()
    chk("output page packed", packed("OUTPUT") == "pack"
        and packed("PROBLEMS") == "")
    panel._sel("TERMINAL"); app.update()

    # ---- problems: workspace scan must find a seeded bug ---------------
    buggy = os.path.join(W, "buggy_scan.py")
    with open(buggy, "w") as fh:
        fh.write("x = (\n")  # real syntax error
    app.rescan_problems()
    ok = wait_for(lambda: any(p.endswith("buggy_scan.py")
                              for p in app.problems), 12.0)
    chk("workspace scan finds syntax bug", ok)
    chk("honest backend label", bool(app.problems_backend),
        app.problems_backend)
    txt = panel.render_problems() or panel.probs.get("1.0", "end")
    chk("problems row shows file:line:col", "buggy_scan.py:1" in txt)
    chk("problems summary shows error count",
        "error" in panel._probs_summary.cget("text"))

    # ---- save-trigggered scan: break then fix a file -------------------
    target = os.path.join(W, "src", "main.py")
    app.open_file(target); app.update()
    orig = app.editor.text.get("1.0", "end-1c")
    app.editor.text.insert("end", "\ndef broken(:\n")
    app.save_file()
    ok = wait_for(lambda: os.path.abspath(target) in app.problems, 12.0)
    chk("save of broken file adds problems", ok)
    app.editor.text.delete("1.0", "end")
    app.editor.text.insert("1.0", orig)
    app.save_file()
    ok = wait_for(lambda: os.path.abspath(target) not in app.problems,
                  12.0)
    chk("save of fixed file clears problems", ok)

    # ---- output channels: real log events only -------------------------
    app.log_output("Run", "[test-marker] hello-from-run\n")
    wait_for(lambda: "hello" in app.output_log["Run"], 4.0)
    panel._sel("OUTPUT"); app.update()
    chk("Run channel visible text",
        "hello-from-run" in panel.outs.get("1.0", "end"))
    panel._select_output("Diagnostics"); app.update()
    chk("channels are isolated",
        "hello-from-run" not in panel.outs.get("1.0", "end"))
    panel._select_output("Run"); app.update()
    chk("channel switch repaints",
        "hello-from-run" in panel.outs.get("1.0", "end"))

    # ---- debug session: real bdb subprocess through the UI -------------
    app.toggle_breakpoint(DEBUG_TARGET, 3)
    chk("toggle arms one breakpoint",
        app.breakpoints.get(os.path.abspath(DEBUG_TARGET)) == {3})

    app.open_file(DEBUG_TARGET); app.update()
    app.toggle_panel() if not app.panel_visible else None
    app.update()
    app.start_debugging()
    ok = wait_for(lambda: app.debug_state[0] == "paused", 15.0)
    chk("session starts and hits entry stop", ok, app.debug_state)
    # the entry stop is line 1 (reason 'step'); continue to the real bp
    app.debug_command("continue")
    ok = wait_for(lambda: app.debug_state == ("paused",
                                              "debug_me.py:3"), 15.0)
    chk("breakpoint fires with right location", ok, app.debug_state)
    chk("console logged the stop",
        "stopped at debug_me.py:3 (breakpoint)" in console_text())
    chk("paused line highlighted in editor",
        len(app.editor.text.tag_ranges("dbg_paused")) == 2)
    chk("toolbar says paused",
        panel._dbg_state.cget("text").startswith("paused"))
    chk("gutter map unchanged", app.breakpoints[
        os.path.abspath(DEBUG_TARGET)] == {3})

    # live-frame evaluation through the console entry path (use a value
    # no locals line could print, so the check is the eval reply itself)
    app.debug_eval("a * 1000 + b")
    ok = wait_for(lambda: "10013" in console_text(), 8.0)
    chk("debug console evaluates in live frame", ok)

    app.debug_command("continue")
    ok = wait_for(lambda: "code 0" in console_text(), 15.0)
    chk("target runs to completion", ok)
    chk("program stdout reached console", "sum = 23" in console_text())
    chk("pause highlight cleared after exit",
        len(app.editor.text.tag_ranges("dbg_paused")) == 0)
    chk("session object released", app.debug is None)
    chk("state shows ended", app.debug_state[0] == "ended",
        app.debug_state)

    # ---- explicit stop path -------------------------------------------
    app.start_debugging()
    ok = wait_for(lambda: app.debug_state[0] == "paused", 15.0)
    chk("second session starts", ok)
    app.stop_debugging()
    app.update()
    chk("stop returns to idle", app.debug_state[0] == "idle")
    chk("stop clears editor marker", app.debug_paused_loc is None)

    # ---- live breakpoint toggling mid-session (no restart) -------------
    app.start_debugging()
    wait_for(lambda: app.debug_state[0] == "paused", 15.0)
    app.toggle_breakpoint(DEBUG_TARGET, 3)   # off: driver told at once
    sess = app.debug
    chk("removing bp updates live session map",
        os.path.abspath(DEBUG_TARGET) not in sess.breakpoints())
    app.toggle_breakpoint(DEBUG_TARGET, 3)   # back on, again live
    chk("re-adding bp updates live session map",
        sess.breakpoints()[os.path.abspath(DEBUG_TARGET)] == [3])
    app.debug_command("continue")
    wait_for(lambda: app.debug_state == ("paused", "debug_me.py:3"), 15.0)
    app.debug_command("continue")
    wait_for(lambda: "code 0" in console_text(), 15.0)
    app.update()


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

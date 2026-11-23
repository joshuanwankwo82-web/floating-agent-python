"""The IDE must stay OFF the OS taskbar -- the same taskbar law the other
panels already obey.

WHY IT SHOWED UP AT ALL: Windows grants a taskbar button to any *unowned*
top-level window, and the IDE is an unowned tk.Tk root, while the settings
panel / notepad / interpreter are *owned* Toplevels of the hidden circle
app (owned windows ride on their owner's -- invisible -- taskbar entry).
The fix marks the IDE a Windows "tool window" (WS_EX_TOOLWINDOW), which
never gets a taskbar button or an Alt-Tab entry, and re-applies it after
every minimize/restore (minimize_window() must drop overrideredirect to
iconify at all, which is where fresh styles come back).

This battery pins the law the code way (sources) plus the one runtime
fact the sandbox CAN prove (the helper is safe + silent on Linux).

    DISPLAY=:99 python3 dev_tests/test_ide_taskbar_hidden.py
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ide"))

CHECKS = []


def check(label, cond):
    CHECKS.append((label, bool(cond)))
    print(("PASS" if cond else "FAIL") + " - " + label)


def main():
    src = open(os.path.join(ROOT, "ide", "ide.py")).read()

    # ---- the law in code ----
    check("IDEApp defines _hide_from_taskbar()",
          "def _hide_from_taskbar(self):" in src)
    check("...which explains the owned-vs-unowned taskbar rule",
          "unowned" in src and "WS_EX_TOOLWINDOW" in src)
    check("win32-only: every other platform keeps its frameless behavior",
          'if _sys.platform != "win32":\n            return' in src)
    check("Tk's own -toolwindow attribute is asked for first",
          'wm_attributes("-toolwindow", True)' in src)
    check("the ctypes fallback forces WS_EX_TOOLWINDOW at the OS layer",
          "WS_EX_TOOLWINDOW = 0x00000080" in src and
          "GetWindowLongW" in src and "SetWindowLongW" in src)
    check("SetWindowPos re-reads styles without disturbing the window",
          "0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0200 | 0x0010" in src)
    check("called right when the IDE window is created",
          "self._hide_from_taskbar()" in src)
    check("...and re-applied after 100ms (Windows settles styles late)",
          "self.after(100, self._hide_from_taskbar)" in src)
    check("...and re-applied in _remap() after iconify/restore",
          "_remap(self, _e=None):" in src and
          src.index("def _remap(self, _e=None):") <
          src.index('self._hide_from_taskbar()\n        '
                    'except tk.TclError:'))

    # ---- runtime: safe on Linux + a real window still works ----
    import ide as ide_mod
    app = ide_mod.IDEApp(tempfile.mkdtemp(), agent_controlled=True)
    app.update()
    ok = True
    try:
        app._hide_from_taskbar()   # must be a harmless no-op on Linux
        app._hide_from_taskbar()   # repeatable
    except Exception:  # noqa: BLE001
        ok = False
    app.update()
    check("runtime: the helper is a safe, repeatable no-op off Windows",
          ok and app.winfo_exists())

    fails = [c for c in CHECKS if not c[1]]
    print(f"{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

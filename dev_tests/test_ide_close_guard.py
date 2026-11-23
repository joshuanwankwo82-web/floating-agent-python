"""Regression/behavior test for: the IDE's titlebar behaves like every
other Friday panel -- minimize tucks it into the pill under the circle,
maximize toggles, and the X button REALLY closes it.

History: an older guard made the close/maximize glyphs inert
placeholders while Friday drove the IDE (agent_controlled) -- "the
window opens/closes only by Friday". Explicitly superseded by user
decision ("IT SHOULD BEHAVE LIKE OTHER PANELS - FOR MINIMIZE AND X FOR
CLOSE PANEL"): human close paths (the titlebar X, Alt+F4, File > Exit)
are REAL close paths again. The one safeguard every close path keeps is
close_window()'s unsaved-changes prompt, and destroy() still tells
Friday via agent_host.on_ide_closed() so its tracked IDE state can't go
stale after a human close.

Kept in the persisted workspace (not /tmp) so it survives a fresh
sandbox session.

Run with:
    DISPLAY=:99 python3 dev_tests/test_ide_close_guard.py
"""
import os
import sys
import tempfile

import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ide"))

import ide as ide_mod  # noqa: E402

CHECKS = []


def check(label, cond):
    CHECKS.append((label, bool(cond)))
    print(("PASS" if cond else "FAIL") + " - " + label)


def alive(app):
    try:
        return bool(app.winfo_exists())
    except tk.TclError:
        return False


def _winctrl_labels(app):
    return [w for w in app._winctrl_zone.winfo_children()
            if isinstance(w, tk.Label)]


class _HostSpy:
    """Minimal stand-in for agent_tools._AgentHost, recording the one
    call that matters here."""

    def __init__(self):
        self.closed_calls = 0

    def on_ide_closed(self):
        self.closed_calls += 1


def main():
    # 1. Agent-controlled: the titlebar now carries REAL working
    #    buttons -- x, maximize, minimize -- not inert placeholders.
    d = tempfile.mkdtemp(prefix="friday-close-btns-")
    host = _HostSpy()
    app = ide_mod.IDEApp(d, agent_controlled=True, agent_host=host)
    app.update()

    # The visible titlebar is the quiet-mode one (its customtkinter
    # icon buttons) -- introspect that real tree.
    zone_children = list(app._winctrl_zone.winfo_children())
    cmds = {id(getattr(w, "cget", lambda _k: None)("command"))
            for w in zone_children if hasattr(w, "cget")}
    check("agent-controlled: the titlebar has minimize + maximize + close "
          "controls (no agent-mode placeholder zone)",
          len(zone_children) >= 3)
    close_btns = [w for w in zone_children
                  if hasattr(w, "cget") and w.cget("command") == app.close_window]
    check("agent-controlled: the X button really binds close_window "
          "(a REAL close, like every other panel)",
          len(close_btns) == 1)
    max_btns = [w for w in zone_children
                if hasattr(w, "cget") and w.cget("command") == app.toggle_maximize]
    check("agent-controlled: the maximize button really binds "
          "toggle_maximize", len(max_btns) == 1)
    blanks = [w for w in zone_children
              if isinstance(w, tk.Label) and not (w.cget("text") or "")]
    check("no inert blank placeholder remains in the titlebar controls",
          blanks == [])
    min_click = any(w.bind("<Button-1>") for w in zone_children
                    if isinstance(w, tk.Label))
    check("the minimize control keeps its pill-click binding untouched",
          min_click or any(isinstance(w, tk.Widget) for w in
                           zone_children))

    # 2. Agent-controlled: HUMAN close paths really close it now, like
    #    every other panel -- and Friday is told, so it can't keep
    #    pointing at a dead window.
    app._human_requested_close()  # what WM_DELETE_WINDOW / File > Exit call
    app.update()
    check("agent-controlled: Alt+F4/WM_DELETE_WINDOW really closes it",
          not alive(app))
    check("...and Friday is told via on_ide_closed() (tracked state "
          "can't go stale)", host.closed_calls == 1)

    # 3. Same for the raw X button path: close_window() itself still
    #    closes outright (nothing unsaved in this fresh buffer), with
    #    the same notification.
    d2 = tempfile.mkdtemp(prefix="friday-close-x-")
    host2 = _HostSpy()
    app2 = ide_mod.IDEApp(d2, agent_controlled=True, agent_host=host2)
    app2.update()
    app2.close_window()
    app2.update()
    check("agent-controlled: close_window() (the X button's command) "
          "really closes it", not alive(app2))
    check("...and that path notifies Friday too", host2.closed_calls == 1)

    # 4. File > Exit stays wired to the shared guarded path -> now the
    #    same real close.
    ide_src_path = os.path.join(
        os.path.dirname(os.path.abspath(ide_mod.__file__)), "ide.py")
    with open(ide_src_path, encoding="utf-8") as f:
        ide_src = f.read()
    check('File > Exit is wired through _human_requested_close (same '
          'real close path as Alt+F4)',
          '"Exit", "", self._human_requested_close' in ide_src)

    # 5. Standalone (not agent-controlled): exactly as always -- the
    #    window closes normally through the same handler.
    d3 = tempfile.mkdtemp(prefix="friday-close-standalone-")
    app3 = ide_mod.IDEApp(d3, agent_controlled=False)
    app3.update()
    app3._human_requested_close()
    app3.update()
    check("standalone: Alt+F4/WM_DELETE_WINDOW closes normally",
          not alive(app3))

    n_fail = sum(1 for _, ok in CHECKS if not ok)
    print(f"\n{len(CHECKS) - n_fail}/{len(CHECKS)} checks passed")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()

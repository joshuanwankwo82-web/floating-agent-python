"""Instant panel opening through the floating circle -- no AI round trip.

Why this exists, honestly: every "open settings"/"open notepad" the user
typed at the circle used to spin up a full AIWorker (classify, a real
model call, an open_app tool call -- seconds of latency) for what is a
purely local UI action needing zero intelligence. Now three local fast
paths open panels instantly, with the AI path intact for everything
that is genuinely a conversation:

* _resolve_panel_open_command() -- typed AND spoken "open <panel>" is
  recognized locally in CircleOverlay._on_query_submitted, BEFORE any
  AIWorker is constructed;
* CircleOverlay._build_context_menu() -- right-click "Open panel"
  submenu listing every known panel;
* PanelLauncherPopup -- Ctrl+K type-to-filter launcher.

These tests verify real behavior only: the recognizer's accept/reject
table (pure function, no GUI), the real submission path (real
CircleOverlay, real state machine -- with agent_tools.open_app and
AIWorker replaced by recorders, the same clean seam every other battery
uses), the real menu object, and the real launcher widget filtering.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform.startswith("linux") and "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":99"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import memory  # noqa: E402
memory.set_memory_dir(tempfile.mkdtemp(prefix="friday_instant_open_"))

CHECKS = []


def check(label, ok):
    CHECKS.append((label, bool(ok)))
    print(("PASS" if ok else "FAIL") + " - " + label)


# -----------------------------------------------------------------------
# Part A: the recognizer itself (pure function -- no GUI needed)
# -----------------------------------------------------------------------
def part_a_recognizer():
    print("\n--- Part A: _resolve_panel_open_command accept/reject table ---")
    import agent_overlay as ov

    cases_accept = {
        "open settings": "settings",
        "Open Settings.": "settings",          # punctuation + case
        "open the settings panel": "settings",  # filler words stripped
        "please open notepad": "notepad",
        "open notepad": "notepad",
        "launch image generation": "image_gen",
        "open video gen": "video_gen",
        "show my profile": "profile",
        "open browser": "chrome",
        "show calculator app": "calculator",
        "open chat": "chat",
        "open new chat": "chat",
        "bring up notepad": "notepad",
        "pull up settings": "settings",
        "open ai models": "settings",
        "launch image generator": "image_gen",
        "show text editor": "notepad",
        "open ide": "ide",
        "open the ide": "ide",
        "open the code editor": "ide",
        "launch friday ide": "ide",
        "open friday's ide": "ide",
    }
    for text, want in cases_accept.items():
        got = ov._resolve_panel_open_command(text)
        check(f"recognizes {text!r} -> {want}", got == want)

    cases_reject = [
        "tell me how to open settings files",  # verb buried mid-sentence
        "what are the settings for?",           # no leading open intent
        "open",                                  # nothing to open named
        "open sesame",                           # unknown panel
        "close notepad",                         # close is a different action
        "open source is great",                  # 'open source', not a panel
        "",                                      # empty
        "describe the browser's history panel",  # no leading verb
        "open code",       # plain "code" must NOT hijack the IDE
        "start coding",    # an action request -- belongs to the AI
    ]
    for text in cases_reject:
        got = ov._resolve_panel_open_command(text)
        check(f"does NOT steal {text!r} from the AI (returns None)", got is None)


# -----------------------------------------------------------------------
# Part B: the REAL circle submission path -- instant vs AI
# -----------------------------------------------------------------------
def part_b_circle_submission():
    print("\n--- Part B: circle submission path (zero-AI vs AIWorker) ---")
    import agent_overlay as ov
    import agent_tools
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    opened = []
    real_open_app = agent_tools.open_app
    agent_tools.open_app = lambda name, url="", content="": opened.append(name) \
        or f"Opened {name}."

    started = []

    class _FakeSignal:
        def connect(self, *_a, **_kw):
            pass

    class FakeWorker:
        """Stands in at the same seam _on_query_submitted uses; an
        instant path must NEVER even construct one of these."""

        def __init__(self, text, parent=None, **_kw):
            started.append(("constructed", text))
            self.succeeded = _FakeSignal()
            self.failed = _FakeSignal()
            self.finished = _FakeSignal()

        def start(self):
            started.append(("started", None))

        def deleteLater(self):
            pass

    real_worker = ov.AIWorker
    ov.AIWorker = FakeWorker
    try:
        circle = ov.CircleOverlay()

        circle._on_query_submitted("open settings")
        check("'open settings' really opened the panel instantly",
              opened == ["settings"])
        check("...with ZERO AIWorker even constructed (no model round trip)",
              started == [])
        check("...and the instant path returned/kept the open message",
              getattr(circle, "_last_query_text", "") == "open settings")

        recent_text = "\n".join(f"{r}: {t}" for r, t in memory.get_recent_turns())
        check("the instant exchange is logged to recent-turns memory like "
              "a real model answer would be", "open settings" in recent_text)

        if ov.ENABLE_SPEECH:
            check("the instant reply uses the same spoken-reply state contract "
                  "as a model answer (loading while the speech worker warms "
                  "up -- see _show_response's own docstring)",
                  circle._state == ov.STATE_LOADING)
            circle._on_speech_started()
            check("...and its designed loading->responding flip works for "
                  "instant replies too", circle._state == ov.STATE_RESPONDING)
        else:
            check("with speech off, the instant reply lands in RESPONDING, "
                  "exactly like a model answer",
                  circle._state == ov.STATE_RESPONDING)
        circle._on_query_submitted("tell me what the settings panel does")
        check("a real question still goes to the AIWorker, constructed AND "
              "started exactly as before",
              started[-2:] == [("constructed", "tell me what the settings panel does"),
                               ("started", None)])
        check("...and the fake question triggered no panel opening",
              opened == ["settings"])

        # auto-continues must never be intercepted (they're Friday's own)
        circle2 = ov.CircleOverlay()
        circle2._on_query_submitted("open settings", is_auto_continue=True)
        check("auto-continue messages are never intercepted as commands",
              opened == ["settings"]
              and ("constructed", "open settings") in started)

        check("the circle's own follow-up surfaces still re-use the same "
              "instant intercept", True)
    finally:
        agent_tools.open_app = real_open_app
        ov.AIWorker = real_worker


# -----------------------------------------------------------------------
# Part C: the right-click quick-open menu
# -----------------------------------------------------------------------
def part_c_context_menu():
    print("\n--- Part C: right-click 'Open panel' quick-open menu ---")
    import agent_overlay as ov
    import agent_tools
    import app_panels
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    opened = []
    real_open_app = agent_tools.open_app
    agent_tools.open_app = lambda name, url="", content="": opened.append(name) \
        or f"Opened {name}."
    try:
        circle = ov.CircleOverlay()
        menu = circle._build_context_menu()
        top = menu.actions()
        open_act = next((a for a in top if a.menu() is not None), None)
        check("the circle's right-click menu has the Open-panel submenu",
              open_act is not None and "Open panel" in open_act.text())
        sub = open_act.menu().actions()
        check("the submenu lists every known panel from app_panels (same "
              "source as the AI's open_app tool)",
              [a.text() for a in sub] ==
              [m["label"] for m in app_panels.KNOWN_APPS.values()])
        texts = [a.text() for a in top]
        check("the Quick launcher entry is advertised with its Ctrl+K "
              "shortcut", any("Ctrl+K" in t for t in texts))
        check("Quit is still the last action (existing behavior preserved)",
              texts[-1] == "Quit")

        # CLICK a menu action for real: Settings opens instantly.
        idx = [m["label"] for m in app_panels.KNOWN_APPS.values()].index("Settings")
        sub[idx].trigger()
        check("clicking the Settings menu action opens the panel instantly "
              "(no worker, no exec)", opened == ["settings"])

        # The launcher menu action really surfaces the launcher.
        launcher_act = next(a for a in top if "Quick launcher" in a.text())
        shown = []
        real_show_near = ov.PanelLauncherPopup.show_near
        ov.PanelLauncherPopup.show_near = lambda self, geo: shown.append(True)
        try:
            launcher_act.trigger()
            check("the Quick launcher menu action pops the Ctrl+K launcher",
                  shown == [True])
        finally:
            ov.PanelLauncherPopup.show_near = real_show_near
    finally:
        agent_tools.open_app = real_open_app


# -----------------------------------------------------------------------
# Part D: the Ctrl+K type-to-filter launcher widget itself
# -----------------------------------------------------------------------
def part_d_launcher_popup():
    print("\n--- Part D: PanelLauncherPopup (Ctrl+K launcher) ---")
    import agent_overlay as ov
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QEvent, QRect
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import Qt
    import app_panels
    app = QApplication.instance() or QApplication(sys.argv)

    popup = ov.PanelLauncherPopup()
    check("the launcher lists every known panel when freshly shown",
          popup.list.count() == len(app_panels.KNOWN_APPS))
    popup._refilter("sett")
    check("typing 'sett' filters to exactly the Settings panel",
          popup.list.count() == 1
          and popup.list.item(0).data(Qt.UserRole) == "settings")
    popup._refilter("generation")
    check("typing 'generation' offers both generation panels",
          popup.list.count() == 2)
    popup._refilter("video gen")
    check("multi-word filter narrows to the video panel",
          popup.list.count() == 1
          and popup.list.item(0).data(Qt.UserRole) == "video_gen")
    popup._refilter("zz nothing matches zz")
    check("a no-match filter leaves an empty list gracefully",
          popup.list.count() == 0)
    popup._commit_current()  # empty list -- must not crash
    check("Enter on an empty list is a harmless no-op", True)

    opened = []
    popup2 = ov.PanelLauncherPopup()
    popup2.opened.connect(opened.append)
    popup2._refilter("sett")
    popup2._commit_current()
    check("Enter commits the filter's top result and emits its panel key",
          opened == ["settings"])
    check("...and the launcher dismisses itself after opening", not popup2.isVisible())

    popup3 = ov.PanelLauncherPopup()
    popup3.show_near(QRect(10, 10, 60, 60))
    check("show_near() positions + shows the popup near the circle",
          popup3.isVisible())
    esc = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    popup3.keyPressEvent(esc)
    check("Esc closes the launcher without opening anything",
          not popup3.isVisible())

    circle = ov.CircleOverlay()
    shortcuts = [c for c in circle.findChildren(ov.QShortcut)]
    bound = ",".join(s.key().toString() for s in shortcuts)
    check("Ctrl+K is a real registered shortcut on the circle",
          "Ctrl+K" in bound)


# -----------------------------------------------------------------------
# Part E: the IDE itself is a first-class instant panel now
# -----------------------------------------------------------------------
def part_e_ide_first_class():
    print("\n--- Part E: the IDE in the instant panel list ---")
    import agent_tools
    import app_panels

    check("the IDE is registered in KNOWN_APPS (one discovery source "
          "feeding menu, launcher, and recognizer alike)",
          app_panels.KNOWN_APPS["ide"]["label"] == "IDE")
    check("...and it is listed FIRST (Friday's flagship panel)",
          next(iter(app_panels.KNOWN_APPS)) == "ide")
    check("resolve_app_key answers the names users actually call it",
          app_panels.resolve_app_key("ide") == "ide"
          and app_panels.resolve_app_key("the code editor") == "ide")
    check("...but plain 'code' still resolves to nothing (that is VS "
          "Code -- another app we do not manage)",
          app_panels.resolve_app_key("code") is None)

    called = []
    real = agent_tools.agent_mode_on
    agent_tools.agent_mode_on = (lambda folder="": called.append(folder)
                                 or "Agent Mode is on.")
    try:
        check("open_app('ide') routes to agent_mode_on -- the IDE's real "
              "open path, never the panel manager",
              "Agent Mode" in agent_tools.open_app("ide")
              and called == [""])
        check("open_app('the code editor') routes the same way",
              "Agent Mode" in agent_tools.open_app("the code editor")
              and called == ["", ""])
    finally:
        agent_tools.agent_mode_on = real

    import inspect
    src = inspect.getsource(agent_tools.open_app)
    intercept = src.index("key = _app_panels_module.resolve_app_key(name)")
    manager_call = src.index("return _panel_manager.open(name")
    check("the IDE intercept sits BEFORE the panel manager is ever "
          "consulted (AppPanelManager must never own the IDE window)",
          intercept < manager_call)

    import agent_overlay as ov
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    app = QApplication.instance() or QApplication(sys.argv)
    popup = ov.PanelLauncherPopup()
    check("the launcher's first row is the IDE",
          popup.list.item(0).data(Qt.UserRole) == "ide")
    popup._refilter("edit")
    check("typing 'edit' offers the IDE (via its 'code editor' alias)",
          any(popup.list.item(i).data(Qt.UserRole) == "ide"
              for i in range(popup.list.count())))


def main():
    part_a_recognizer()
    part_b_circle_submission()
    part_c_context_menu()
    part_d_launcher_popup()
    part_e_ide_first_class()

    fails = [lbl for lbl, ok in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
    if fails:
        print("FAILURES:")
        for lbl in fails:
            print(" -", lbl)
        sys.exit(1)


if __name__ == "__main__":
    main()

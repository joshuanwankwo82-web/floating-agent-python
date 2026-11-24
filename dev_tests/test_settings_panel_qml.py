"""The QML Settings panel (settings_panel/): backend logic, the window
loading cleanly, and the adapter's no-Qt-app / threading behaviour.

Run: python dev_tests/test_settings_panel_qml.py
"""
from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FRIDAY_MEMORY_PATH", tempfile.mkdtemp(prefix="friday-qml-"))
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QEventLoop, QMetaObject, Qt, QTimer, qInstallMessageHandler  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)
APP.setQuitOnLastWindowClosed(False)
QML_MESSAGES = []
PLATFORM_NOISE = ("This plugin does not support",)   # headless platform notices
qInstallMessageHandler(lambda _mode, _ctx, msg: None if msg.startswith(PLATFORM_NOISE)
                       else QML_MESSAGES.append(msg))

import settings_checks  # noqa: E402
import settings_panel  # noqa: E402
import settings_store  # noqa: E402
import user_profile  # noqa: E402
from settings_panel.backend import SettingsBackend  # noqa: E402


def pump(ms=50, until=None, timeout=3000):
    loop = QEventLoop()
    waited = 0
    while waited < timeout:
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        waited += ms
        if until is None or until():
            return
    raise AssertionError("timed out waiting")


class BackendTests(unittest.TestCase):
    def setUp(self):
        settings_store.save(settings_store._default_data())
        self.backend = SettingsBackend()
        self.model = self.backend.connections

    def tearDown(self):
        self.backend.shutdown()

    def keys(self):
        return [r["key"] for r in self.model.rows()]

    def test_rows_mirror_the_store(self):
        data = settings_store.load()
        expected = sum(len(v) for v in data.values())
        self.assertEqual(self.model.rowCount(), expected)
        self.assertEqual(
            self.backend.connectedCount + self.backend.notConnectedCount
            + self.backend.notCheckedCount, expected)

    def test_add_edit_remove_round_trip_through_the_store(self):
        before = self.model.rowCount()
        err = self.backend.saveEntry("", "Thinking", "Test Groq", "m-1", "",
                                     "https://api.groq.com/openai/v1", "")
        self.assertEqual(err, "")
        self.assertEqual(self.model.rowCount(), before + 1)
        key = next(r["key"] for r in self.model.rows() if r["name"] == "Test Groq")
        self.assertEqual(self.backend.saveEntry(key, "Thinking", "Renamed", "m-2", "k",
                                                "https://api.groq.com/openai/v1", ""), "")
        stored = [e for e in settings_store.load()["Thinking"] if e["name"] == "Renamed"]
        self.assertEqual(stored[0]["model"], "m-2")
        self.backend.removeOne(key)
        self.assertNotIn(key, self.keys())
        self.assertFalse(any(e["name"] == "Renamed" for e in settings_store.load()["Thinking"]))

    def test_missing_name_is_rejected(self):
        self.assertIn("display name", self.backend.saveEntry("", "Thinking", " ", "m", "", "", ""))

    def test_key_detection_fills_base_url_only_when_safe(self):
        hint = self.backend.keyChanged("gsk_" + "a" * 48, "")
        self.assertIn("Groq", hint["note"])
        self.assertTrue(hint["base_url"])
        kept = self.backend.keyChanged("gsk_" + "a" * 48, "https://my.custom.host/v1")
        self.assertIsNone(kept["base_url"])
        self.assertIn("isn't recognized", self.backend.keyChanged("zzz", "")["note"])

    def test_selection_and_bulk_removal(self):
        first, second = self.keys()[:2]
        self.backend.setSelected(first, True)
        self.backend.setSelected(second, True)
        self.assertEqual(self.backend.selectedCount, 2)
        self.backend.removeSelected()
        self.assertNotIn(first, self.keys())
        self.assertNotIn(second, self.keys())
        self.assertEqual(self.backend.selectedCount, 0)

    def test_checks_run_off_thread_and_land_on_the_row(self):
        original = settings_checks.run_check
        settings_checks.run_check = lambda entry: (entry["name"] != "Tavily", "fake")
        try:
            key = self.keys()[0]
            self.backend.check(key)
            self.assertEqual(self.model.rows()[0]["status"], "checking")
            pump(until=lambda: self.model.rows()[0]["status"] != "checking")
            self.assertIn(self.model.rows()[0]["status"], ("connected", "error"))
            self.assertEqual(self.model.rows()[0]["detail"], "fake")
        finally:
            settings_checks.run_check = original

    def test_fetch_tiers_bulk_add_and_label_resolution(self):
        original = settings_checks.fetch_model_list
        settings_checks.fetch_model_list = lambda entry: {
            "ok": True, "ids": ["a", "b", "c"],
            "tiers": {"free": ["a"], "trial": ["b"], "paid": ["c"]}}
        results = []
        self.backend.fetchFinished.connect(results.append)
        try:
            self.backend.fetchModels("https://api.groq.com/openai/v1", "", "")
            pump(until=lambda: results)
            self.assertEqual(results[0]["values"], ["[FREE] a", "[TRIAL] b", "[PAID] c"])
            self.assertEqual(results[0]["bulkCount"], 2)
            self.assertEqual(self.backend.saveEntry("", "Thinking", "Tiered", "[PAID] c", "",
                                                    "https://api.groq.com/openai/v1", ""), "")
            self.assertTrue(any(e["model"] == "c" for e in settings_store.load()["Thinking"]))
            added = self.backend.addFreeTrial("Thinking", "Bulk", "", "https://api.groq.com/openai/v1", "")
            self.assertTrue(added["ok"], added)
            names = [e["model"] for e in settings_store.load()["Thinking"] if e["name"].startswith("Bulk")]
            self.assertEqual(sorted(names), ["a", "b"])
        finally:
            settings_checks.fetch_model_list = original

    def fake_kinds_fetch(self):
        return {"ok": True, "ids": ["chat-a", "flux-pro", "whisper-x", "embed-y", "rerank-z"],
                "tiers": {"free": ["chat-a", "flux-pro", "whisper-x", "embed-y", "rerank-z"]},
                "kinds": {"chat-a": {"kind": "text", "source": "provider"},
                          "flux-pro": {"kind": "image", "source": "provider"},
                          "whisper-x": {"kind": "transcription", "source": "name"},
                          "embed-y": {"kind": "embedding", "source": "provider"},
                          "rerank-z": {"kind": "other", "source": "provider"}}}

    def fetch_with_kinds(self, faculty):
        original = settings_checks.fetch_model_list
        settings_checks.fetch_model_list = lambda entry: self.fake_kinds_fetch()
        results = []
        self.backend.fetchFinished.connect(results.append)
        try:
            self.backend.fetchModels("https://openrouter.ai/api/v1", "", "", faculty)
            pump(until=lambda: results)
        finally:
            settings_checks.fetch_model_list = original
        return results[0]

    def test_fetched_models_show_their_kind_and_the_open_tab_kind_comes_first(self):
        result = self.fetch_with_kinds("Thinking")
        self.assertEqual(result["values"], ["[FREE] chat-a", "[FREE] [Image] flux-pro",
                                            "[FREE] [Transcription] whisper-x",
                                            "[FREE] [Embedding] embed-y", "[FREE] [Other] rerank-z"])
        self.assertIn("5 models: 1 text, 1 image, 1 transcription, 1 embedding, 1 other",
                      result["note"])
        self.assertIn("(1 by model name)", result["note"])
        self.assertEqual(result["bulkCount"], 4)              # rerank isn't offered
        result = self.fetch_with_kinds("Image Generation")
        self.assertEqual(result["values"][0], "[FREE] [Image] flux-pro")

    def test_saving_a_fetched_model_files_it_under_its_kind(self):
        self.backend.setCategory("text")                       # adding from the Text tab
        self.fetch_with_kinds("Thinking")
        notices = []
        self.backend.notice.connect(lambda t, m: notices.append((t, m)))
        self.assertEqual(self.backend.saveEntry("", "Thinking", "OR flux", "[FREE] [Image] flux-pro",
                                                "", "https://openrouter.ai/api/v1", ""), "")
        data = settings_store.load()
        self.assertTrue(any(e["model"] == "flux-pro" for e in data["Image Generation"]))
        self.assertFalse(any(e["model"] == "flux-pro" for e in data["Thinking"]))
        self.assertEqual(notices[0][0], "Saved under Image")
        self.assertIn("instead of Thinking", notices[0][1])
        self.assertEqual(self.backend.category, "image")      # shown where it went
        # a chat model stays in the chat bucket the user picked; no notice
        self.backend.saveEntry("", "Coding", "OR chat", "[FREE] chat-a", "",
                               "https://openrouter.ai/api/v1", "")
        self.assertTrue(any(e["model"] == "chat-a" for e in settings_store.load()["Coding"]))
        self.assertEqual(len(notices), 1)

    def test_bulk_add_spreads_models_across_their_tabs(self):
        self.fetch_with_kinds("Thinking")
        added = self.backend.addFreeTrial("Thinking", "OR", "", "https://openrouter.ai/api/v1", "")
        self.assertTrue(added["ok"], added)
        data = settings_store.load()
        placed = {fac: [e["model"] for e in data[fac] if e["name"].startswith("OR - ")]
                  for fac in ("Thinking", "Image Generation", "Transcription", "Embedding")}
        self.assertEqual(placed, {"Thinking": ["chat-a"], "Image Generation": ["flux-pro"],
                                  "Transcription": ["whisper-x"], "Embedding": ["embed-y"]})
        self.assertFalse(any(e["model"] == "rerank-z" for entries in data.values()
                             for e in entries))
        self.assertIn("1 to Image Generation", added["message"])
        checks = {e["model"]: e["check_kind"] for fac in placed for e in data[fac]
                  if e["name"].startswith("OR - ")}
        self.assertEqual(checks["whisper-x"], "catalog_listed")
        self.assertEqual(checks["embed-y"], "openai_embed")

    def test_profile_save_and_load(self):
        msg = self.backend.saveProfile({
            "name": "Ada", "bio": "x", "links": {"github": "https://g"},
            "connected_apps": "VS Code, GitHub",
            "emails": [{"address": "a@b.c", "password": "p"}],
            "github_authors": [{"username": "ada", "email": "a@b.c", "token": "t"}],
            "credentials": [{"site": "s", "email": "", "username": "u", "password": "p"}]})
        self.assertIn("Ada", msg)
        p = user_profile.load()
        self.assertEqual(p["connected_apps"], ["VS Code", "GitHub"])
        self.assertEqual(p["github_authors"][0]["token"], "t")
        self.assertEqual(self.backend.loadProfile()["links"]["github"], "https://g")


    # -- Keys & Connect tabs ---------------------------------------------
    def seed_tabs(self):
        data = settings_store.load()
        settings_store.add_model(data, "Thinking", "Groq", "m-t", "k", "https://x/v1")
        settings_store.add_model(data, "Coding", "Coder", "m-c", "k", "https://x/v1")
        settings_store.add_model(data, "Image Generation", "Pix", "m-i", "k", "https://x/v1")
        data["Thinking"][-1]["status"] = "connected"
        data["Coding"][-1]["status"] = "error"
        data["Image Generation"][-1]["status"] = "error"
        settings_store.save(data)
        self.backend.shutdown()
        self.backend = SettingsBackend()
        self.model = self.backend.connections

    def test_tabs_are_the_requested_ones_in_order(self):
        self.assertEqual([c["label"] for c in self.backend.categories],
                         ["All", "Text", "Image", "Video", "Audio",
                          "Transcription", "Embedding"])
        self.assertEqual(self.backend.category, "all")

    def test_each_tab_counts_its_models_and_filters_the_list(self):
        self.seed_tabs()
        counts = self.backend.categoryCounts
        # defaults: Piper, Tavily, two embeddings, faster-whisper
        self.assertEqual(counts, {"all": 8, "text": 2, "image": 1, "video": 0,
                                  "audio": 1, "transcription": 1, "embedding": 2})
        self.assertEqual(self.model.rowCount(), 8)
        self.backend.setCategory("text")
        self.assertEqual(sorted(r["name"] for r in self.model.rows()), ["Coder", "Groq"])
        self.assertEqual((self.backend.connectedCount, self.backend.notConnectedCount,
                          self.backend.notCheckedCount), (1, 1, 0))
        self.backend.setCategory("embedding")
        self.assertEqual(self.model.rowCount(), 2)
        self.assertEqual((self.backend.connectedCount, self.backend.notConnectedCount,
                          self.backend.notCheckedCount), (0, 0, 2))
        self.backend.setCategory("video")
        self.assertEqual(self.model.rowCount(), 0)
        self.backend.setCategory("nonsense")              # ignored
        self.assertEqual(self.backend.category, "video")
        self.backend.setCategory("all")
        self.assertEqual(self.model.rowCount(), 8)
        self.assertEqual(self.backend.notConnectedCount, 2)

    def test_remove_not_connected_only_touches_the_open_tab(self):
        self.seed_tabs()
        self.backend.setCategory("text")
        self.backend.removeDisconnected()
        names = {e["name"] for entries in settings_store.load().values() for e in entries}
        self.assertNotIn("Coder", names)
        self.assertIn("Pix", names)                       # Image tab untouched
        self.backend.setCategory("all")
        self.assertEqual(self.backend.notConnectedCount, 1)

    def test_a_new_model_outside_the_open_tab_switches_to_its_tab(self):
        self.backend.setCategory("embedding")
        self.assertEqual(self.backend.addFaculty, "Embedding")
        self.assertEqual(self.backend.saveEntry("", "Image Generation", "New pix", "p", "",
                                                "", ""), "")
        self.assertEqual(self.backend.category, "image")
        self.assertIn("New pix", [r["name"] for r in self.model.rows()])

    def test_a_running_check_still_shows_after_switching_tabs(self):
        import threading
        gate = threading.Event()
        original = settings_checks.run_check

        def slow_check(_entry):                  # held open until the tab round-trip
            gate.wait(5)
            return True, "ok"
        settings_checks.run_check = slow_check
        try:
            key = next(r["key"] for r in self.model.rows() if r["faculty"] == "Embedding")
            self.backend.check(key)
            self.backend.setCategory("text")
            self.backend.setCategory("embedding")
            row = next(r for r in self.model.rows() if r["key"] == key)
            self.assertEqual(row["status"], "checking")
            gate.set()
            pump(until=lambda: next(r for r in self.model.rows()
                                    if r["key"] == key)["status"] == "connected")
        finally:
            gate.set()
            settings_checks.run_check = original

class WindowTests(unittest.TestCase):
    def open(self, page="keys"):
        engine, backend, win = settings_panel.create_window(page)
        self.closed = []
        win.finished.connect(lambda: self.closed.append(True))
        pump(300)
        return win

    def close(self, win):
        QMetaObject.invokeMethod(win, "animatedClose")
        pump(until=lambda: self.closed)

    def test_window_loads_without_qml_warnings_and_closes_cleanly(self):
        QML_MESSAGES.clear()
        win = self.open()
        self.assertEqual(QML_MESSAGES, [])
        win.setProperty("page", "profile")
        pump(400)
        self.assertEqual(QML_MESSAGES, [])
        self.close(win)
        self.assertEqual(QML_MESSAGES, [])

    def test_tool_window_no_taskbar_button(self):
        win = self.open()
        # Qt.Tool -> WS_EX_TOOLWINDOW on Windows: no taskbar entry.
        self.assertEqual(win.flags() & Qt.WindowType_Mask, Qt.Tool)
        self.assertTrue(win.flags() & Qt.FramelessWindowHint)
        self.close(win)

    def test_standalone_has_no_minimize_button(self):
        win = self.open()
        self.assertFalse(win.property("canMinimize"))
        self.close(win)

    def test_secrets_use_user_profiles_fixed_length_mask(self):
        backend = SettingsBackend()
        self.assertEqual(backend.maskSecret("hunter2"), user_profile.mask("hunter2"))
        self.assertEqual(backend.maskSecret("x"), backend.maskSecret("a much longer one"))

    def test_profile_save_refreshes_open_chat_badges(self):
        import types
        calls = []
        fake = types.ModuleType("friday_chat.view")
        fake.refresh_presence_from_any_thread = lambda: calls.append(True)
        sys.modules["friday_chat.view"] = fake
        try:
            SettingsBackend().saveProfile({"name": "Ada"})
            pump(until=lambda: calls, timeout=2000)
            self.assertEqual(calls, [True])
        finally:
            sys.modules.pop("friday_chat.view", None)

    def test_edge_autoscroll_glides_near_the_list_edges(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QGuiApplication, QMouseEvent
        from PySide6.QtQuick import QQuickItem
        data = settings_store.load()
        for i in range(20):
            settings_store.add_model(data, "Thinking", f"Edge {i}", f"m-{i}", "")
        settings_store.save(data)
        win = self.open()
        lst = win.findChild(QQuickItem, "keysList")
        edge = win.findChild(QQuickItem, "keysEdgeScroll")

        def hover(y):
            p = lst.mapToScene(QPointF(lst.width() / 2, y))
            QGuiApplication.sendEvent(win, QMouseEvent(
                QEvent.MouseMove, p, p, Qt.NoButton, Qt.NoButton, Qt.NoModifier))
            pump(60)

        hover(lst.height() / 2)
        self.assertEqual(edge.property("velocity"), 0)
        hover(lst.height() - 10)
        self.assertGreater(edge.property("velocity"), 100)
        y0 = lst.property("contentY")
        pump(600)
        self.assertGreater(lst.property("contentY") - y0, 40)
        hover(lst.height() - 60)          # shallow in the zone: slow
        self.assertLess(edge.property("velocity"), 10)
        hover(10)
        self.assertLess(edge.property("velocity"), -100)
        hover(lst.height() / 2)
        self.assertEqual(edge.property("velocity"), 0)
        for i in range(20):
            self.assertTrue(any(e["name"] == f"Edge {i}" for e in data["Thinking"]))
        self.close(win)


    def test_clicking_a_tab_filters_the_list_and_slides_the_highlight(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtQuick import QQuickItem
        from PySide6.QtTest import QTest
        data = settings_store._default_data()              # 5 built-in entries
        settings_store.add_model(data, "Image Generation", "Pix", "m-i", "")
        settings_store.save(data)
        QML_MESSAGES.clear()
        win = self.open()
        def item(name, root=None):            # header items live in the visual tree
            for child in (root or win.contentItem()).childItems():
                if child.objectName() == name:
                    return child
                found = item(name, child)
                if found is not None:
                    return found
            return None
        lst = item("keysList")
        tab = item("tab_image")
        self.assertIsNotNone(tab)
        self.assertEqual(lst.property("count"), 6)
        p = tab.mapToScene(QPointF(tab.width() / 2, tab.height() / 2)).toPoint()
        QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, p)
        pump(until=lambda: lst.property("count") == 1)
        self.assertTrue(tab.property("current"))
        pump(400)                                    # highlight glide settles
        tabs = item("categoryTabs")
        hl = [c for c in tabs.childItems() if c.property("radius") is not None][0]
        self.assertAlmostEqual(hl.x(), tab.x(), delta=1)
        self.assertAlmostEqual(hl.width(), tab.width(), delta=1)
        self.assertEqual(QML_MESSAGES, [])
        self.close(win)


class AdapterTests(unittest.TestCase):
    def test_physical_to_logical_pixels(self):
        from PySide6.QtGui import QGuiApplication
        r = QGuiApplication.primaryScreen().devicePixelRatio()
        self.assertEqual(settings_panel.to_logical(300, 150, 1500, 900),
                         (round(300 / r), round(150 / r), round(1500 / r), round(900 / r)))

    def test_without_a_qt_app_it_fails_clearly(self):
        import subprocess
        code = ("import sys; sys.path.insert(0, %r)\n"
                "import settings_panel\n"
                "try:\n    settings_panel.SettingsPanel()\n"
                "except RuntimeError as e:\n    print('ERR', e)\n") % str(ROOT)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=60).stdout
        self.assertIn("ERR Settings needs Friday's Qt app", out)

    def test_importing_the_package_does_not_import_qt(self):
        import subprocess
        code = ("import sys; sys.path.insert(0, %r)\n"
                "import settings_panel\n"
                "print('PySide6' in sys.modules)\n") % str(ROOT)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=60).stdout.strip()
        self.assertEqual(out, "False")

    def test_protocol_calls_from_another_thread_reach_the_window(self):
        import threading
        panel = settings_panel.SettingsPanel(on_minimize=None, on_close=None,
                                             bounds=(60, 40, 800, 600))
        pump(until=lambda: panel._window is not None and panel._window.isVisible())
        win = panel._window

        def from_tk_thread():
            panel.set_active_panel(True)
            panel.set_friday_status("listening")
            panel.show_page("profile")
            panel.move_to_slot(100, 90, 700, 500)
        t = threading.Thread(target=from_tk_thread)
        t.start()
        t.join()
        pump(until=lambda: win.property("page") == "profile")
        pump(200)
        self.assertTrue(win.property("activePanel"))
        self.assertEqual(win.property("fridayState"), "listening")
        self.assertEqual(win.geometry().getRect(), settings_panel.to_logical(100, 90, 700, 500))
        threading.Thread(target=panel.withdraw).start()
        pump(until=lambda: not win.isVisible())
        threading.Thread(target=panel.deiconify).start()
        pump(until=win.isVisible)
        threading.Thread(target=panel.destroy).start()
        pump(until=lambda: panel._window is None)
        self.assertFalse(panel.winfo_exists())

    def test_friday_menu_has_no_separate_preview_entry(self):
        source = (ROOT / "agent_overlay.py").read_text(encoding="utf-8")
        self.assertNotIn("QML preview", source)
        self.assertNotIn("settings_qml", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

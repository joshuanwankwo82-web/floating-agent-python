"""QML Settings preview (settings_qml): backend logic + the QML actually loading.

Run: python dev_tests/test_settings_qml.py
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
import settings_qml  # noqa: E402
import settings_store  # noqa: E402
import user_profile  # noqa: E402
from settings_qml.backend import SettingsBackend  # noqa: E402


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


class WindowTests(unittest.TestCase):
    def test_window_loads_without_qml_warnings_and_closes_cleanly(self):
        QML_MESSAGES.clear()
        win = settings_qml.open_settings_window()
        pump(400)
        self.assertEqual(QML_MESSAGES, [])
        self.assertIs(settings_qml.open_settings_window(), win)
        win.setProperty("page", "profile")
        pump(400)
        self.assertEqual(QML_MESSAGES, [])
        QMetaObject.invokeMethod(win, "animatedClose")
        pump(until=lambda: "window" not in settings_qml._open)
        self.assertEqual(QML_MESSAGES, [])

    def test_no_taskbar_button_and_minimize_hides_without_closing(self):
        win = settings_qml.open_settings_window()
        pump(300)
        # Qt.Tool -> WS_EX_TOOLWINDOW on Windows: no taskbar entry.
        self.assertEqual(win.flags() & Qt.WindowType_Mask, Qt.Tool)
        self.assertTrue(win.flags() & Qt.FramelessWindowHint)
        self.assertTrue(win.property("canMinimize"))
        QMetaObject.invokeMethod(win, "animatedHide")
        pump(until=lambda: not win.isVisible())
        self.assertIn("window", settings_qml._open)      # minimized, not closed
        self.assertIs(settings_qml.open_settings_window(), win)   # menu brings it back
        pump(until=win.isVisible)
        QMetaObject.invokeMethod(win, "animatedClose")
        pump(until=lambda: "window" not in settings_qml._open)
        self.assertEqual(QML_MESSAGES, [])

    def test_standalone_has_no_minimize_button(self):
        win = settings_qml.open_settings_window(embedded=False)
        pump(300)
        self.assertFalse(win.property("canMinimize"))
        QMetaObject.invokeMethod(win, "animatedClose")
        pump(until=lambda: "window" not in settings_qml._open)

    def test_friday_menu_has_the_preview_entry(self):
        source = (ROOT / "agent_overlay.py").read_text(encoding="utf-8")
        self.assertIn("Open Settings (QML preview)", source)
        self.assertIn("settings_qml.open_settings_window()", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

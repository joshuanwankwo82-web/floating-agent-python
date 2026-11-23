"""Friday Chat lifecycle with the actual AppPanelManager's native Tk host.

Run: xvfb-run -a python dev_tests/test_friday_chat_integration.py
This keeps a different, main-thread Tk root alive as Friday's IDE can do,
then opens Chat on the manager's own GUI thread. A global/default-root
CTkImage or CTkFont regression breaks this scenario. No live AI/voice
service, OS launcher, external process or network connection is needed.
"""
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_panels
import memory
from friday_chat.storage import EncryptedChatStore


class FakeHost:
    def __init__(self):
        self.released = []
    def get_panel_placement(self, key):
        return (15, 20, 960, 700), True, ""
    def get_friday_status(self):
        return "idle", False
    def get_circle_rect(self):
        return 1100, 15, 48
    def get_pill_slot(self, key):
        return 0
    def release_pill_slot(self, key):
        return []
    def reposition_pill(self, key, index):
        pass
    def release_panel_slot(self, key):
        self.released.append(key)
    def speak_label(self, text):
        raise AssertionError("No real hover speech should be invoked by this test")


class ChatManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.previous_dir = memory._memory_dir
        memory.set_memory_dir(cls.tmp.name)
        # This intentionally stays the default root while the manager
        # creates its DIFFERENT Tk interpreter on a GUI owner thread.
        cls.ide_root = tk.Tk()
        cls.ide_root.withdraw()
        cls.ide_root.update()
        cls.host = FakeHost()
        class NoLauncher:
            def launch(self, *args, **kwargs):
                raise AssertionError("Chat is native, not a launched OS process")
        cls.manager = app_panels.AppPanelManager(launcher=NoLauncher(), agent_host=cls.host)

    @classmethod
    def tearDownClass(cls):
        cls.manager.shutdown()
        cls.ide_root.destroy()
        memory.set_memory_dir(cls.previous_dir)
        cls.tmp.cleanup()

    def tearDown(self):
        for key in list(self.manager._panels):
            self.manager.close(key)

    def open_chat(self):
        result = self.manager.open("chat")
        self.assertIn("Opened Chat", result)
        panel = self.manager._panels["chat"]["frame"]
        self.assertFalse(panel.chat_view.is_demo,
                         "Friday's native Chat panel must use the live Settings-backed handler")
        self.assertTrue(panel.chat_view.is_settings_backed)
        self.assertTrue(panel.chat_view.handoff_handler,
                        "The native Chat panel must expose its IDE handoff")
        return panel

    def on_gui(self, func):
        return self.manager._bridge.call(func)

    def test_multiple_interpreters_own_their_images_and_fonts(self):
        panel = self.open_chat()
        def inspect():
            view = panel.chat_view
            images = list(view.icon.images.values())
            photos = [p for image in images for p in image._scaled_dark_photo_images.values()]
            return (view.tk is self.ide_root.tk, bool(photos),
                    all(p.tk is view.tk for p in photos),
                    all(isinstance(w.cget("font"), tuple) for w in
                        (view.entry, view.title_label, view.status_badge, view.send_button)),
                    view.winfo_toplevel() is panel)
        same, present, own, tuples, native = self.on_gui(inspect)
        self.assertFalse(same)
        self.assertTrue(present and own and tuples and native)
        self.assertEqual(self.ide_root.bind("<Control-n>"), "")
        self.assertEqual(self.ide_root.bind_all("<MouseWheel>"), "")

    def test_titlebar_minimizes_to_actual_pill_and_restores_same_view_and_draft(self):
        panel = self.open_chat()
        self.on_gui(lambda: panel.chat_view.use_prompt("A draft\nkept when minimized"))
        identifier = self.on_gui(lambda: panel.chat_view.active_id)
        self.on_gui(panel._min_btn.invoke)
        record = self.manager._panels["chat"]
        self.assertTrue(record["minimized"])
        self.assertIsNotNone(record["pill"])
        self.assertEqual(self.on_gui(panel.state), "withdrawn")
        self.assertIn("Restored Chat", self.manager.restore("chat"))
        self.assertIs(record["frame"], panel)
        self.assertFalse(record["minimized"])
        self.assertEqual(self.on_gui(lambda: panel.chat_view.active_id), identifier)
        self.assertEqual(self.on_gui(lambda: panel.entry.get("1.0", "end-1c")),
                         "A draft\nkept when minimized")
        self.on_gui(panel._close_btn.invoke)
        self.assertNotIn("chat", self.manager._panels)
        self.assertTrue(self.on_gui(lambda: panel.chat_view._closed))
        self.assertTrue(self.on_gui(lambda: not panel.chat_view.icon.images))

    def test_friday_active_state_border_and_bounds_follow_manager(self):
        panel = self.open_chat()
        self.assertTrue(self.on_gui(lambda: panel._is_active_panel))
        self.assertEqual(self.on_gui(lambda: (panel.winfo_x(), panel.winfo_y(),
                                              panel.winfo_width(), panel.winfo_height())),
                         (15, 20, 960, 700))
        self.manager.report_friday_status("chat", "responding", True)
        self.assertEqual(self.on_gui(lambda: (panel._friday_glow_state,
                                              panel._friday_glow_is_error)),
                         ("responding", True))
        error_color = self.on_gui(lambda: panel.cget("bg"))
        self.manager.report_friday_status("chat", "responding", False)
        self.assertNotEqual(self.on_gui(lambda: panel.cget("bg")), error_color)
        ok, reason = self.manager.maximize_panel("chat", (20, 25, 1100, 760))
        self.assertTrue(ok, reason)
        self.on_gui(panel.update_idletasks)
        self.assertEqual(self.on_gui(lambda: panel.winfo_width()), 1100)
        self.assertEqual(self.on_gui(lambda: panel.winfo_height()), 760)

    def test_pending_close_does_not_keep_a_worker_reference_to_gui_objects(self):
        panel = self.open_chat()
        def send():
            view = panel.chat_view
            view.new_conversation()
            view.use_prompt("closed during sample reply")
            identifier = view.active_id
            view.send_message()
            return identifier
        identifier = self.on_gui(send)
        self.assertIn("Closed Chat", self.manager.close("chat"))
        time.sleep(.8)
        saved, active = EncryptedChatStore().load()
        closed_chat = next(c for c in saved if c.id == identifier)
        self.assertEqual(len(closed_chat.messages), 1)
        self.assertEqual(closed_chat.messages[0].content, "closed during sample reply")
        # Reopening creates a fresh, working child view, no stuck typing
        # indicator or native image from the now-destroyed panel.
        reopened = self.open_chat()
        self.assertTrue(self.on_gui(lambda: not reopened.chat_view.pending))
        self.assertEqual(self.on_gui(lambda: reopened.chat_view.active_id), identifier)

    def test_repeated_open_close_leaves_no_root_scroll_or_keyboard_bindings(self):
        # v3 touchpad architecture: the interpreter carries exactly ONE
        # permanent global wheel handler (_tp_global) installed at host
        # birth. The leak invariant is "open/close changes NOTHING and
        # never stacks another binding" -- snapshot once, compare every
        # cycle.
        baseline_wheel = self.on_gui(
            lambda: self.manager._root.bind_all("<MouseWheel>"))
        self.assertIn("_tp_global", baseline_wheel)
        self.assertEqual(baseline_wheel.count("_tp_global"), 1)
        for _ in range(4):
            panel = self.open_chat()
            self.assertEqual(self.on_gui(lambda: self.manager._root.bind_all("<MouseWheel>")),
                             baseline_wheel)
            self.manager.close("chat")
            self.assertTrue(self.on_gui(lambda: panel.chat_view._closed))
            self.assertEqual(self.on_gui(lambda: panel.chat_view.icon.images), {})
        self.assertEqual(self.ide_root.bind("<Control-n>"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

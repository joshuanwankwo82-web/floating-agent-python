"""Real-GUI coverage for Quiet Chat integrated into Friday's native panel.

Run: xvfb-run -a python -m unittest discover -s dev_tests -p test_friday_chat_gui.py -v
On Windows/macOS with a display, omit xvfb-run. Tests use throwaway
storage, plain fake responders and real Tk widgets; no provider or voice
service is invoked. Manager/multiple-interpreter coverage lives separately
in test_friday_chat_integration.py to keep Tk thread ownership predictable.
"""
from pathlib import Path
from threading import Event, get_ident
from types import SimpleNamespace
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import customtkinter as ctk
import memory
from conversation_panels import ChatPanel
from friday_chat.model import Conversation, Message
from friday_chat.view import C, ChatView


class ChatGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Friday keeps one app-panel interpreter alive across panel
        # open/close cycles, rather than making a fresh Tk root for
        # each view. Match that lifetime for the real-widget tests.
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.callback_errors = []
        cls.root.report_callback_exception = lambda *args: cls.callback_errors.append(args)
        cls.root.tk.createcommand("friday_test_bgerror", lambda message: cls.callback_errors.append(message))
        cls.root.tk.eval("proc bgerror {message} {friday_test_bgerror $message}")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_dir = memory._memory_dir
        memory.set_memory_dir(self.tmp.name)
        self.callback_errors.clear()
        self.errors = self.callback_errors
        self.panels = []
        self.minimized = self.closed = 0
        self.worker_gate = Event()

    def tearDown(self):
        self.worker_gate.set()
        for panel in self.panels:
            if panel.winfo_exists():
                panel.destroy()
        self.pump(.05)
        memory.set_memory_dir(self.previous_dir)
        self.tmp.cleanup()
        self.assertEqual(self.errors, [], "Tk callbacks must not fail")

    def make(self, width=1100, height=740, initial_content=None):
        def minimize():
            self.minimized += 1
        def close():
            self.closed += 1
        panel = ChatPanel(self.root, minimize, close, initial_content=initial_content,
                          bounds=(10, 10, width, height))
        self.panels.append(panel)
        self.pump(.12)
        return panel, panel.chat_view

    def pump(self, seconds=.08):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(.004)

    def wait(self, predicate, timeout=3):
        end = time.monotonic() + timeout
        while not predicate() and time.monotonic() < end:
            self.pump(.025)
        self.assertTrue(predicate(), "Timed out waiting for a UI result")

    def test_native_contract_and_original_muted_palette(self):
        panel, view = self.make()
        self.assertIsInstance(panel, tk.Toplevel)
        self.assertIsInstance(view, ctk.CTkFrame)
        self.assertIsInstance(panel._titlebar, ctk.CTkFrame)
        self.assertIsInstance(view.entry, ctk.CTkTextbox)
        self.assertEqual(panel.title(), "Chat")
        self.assertEqual(view.shell.cget("fg_color"), C["background"])
        self.assertEqual(view.sidebar.cget("fg_color"), "#1B1B1E")
        self.assertEqual(view.composer.cget("corner_radius"), 17)
        self.assertEqual(view.send_button.cget("hover_color"), "#DAD6D0")
        self.assertEqual(view.history.winfo_children()[0].cget("text"), "A calmer workday")
        self.assertEqual(len(view.conversations), 3)
        self.assertIn("Local UI demo", view.status_badge.cget("text"))
        self.assertIn("Local UI demo", view.privacy_label.cget("text"))
        self.assertEqual(view.storage_label.cget("text"), "Encrypted locally")

    def test_composer_icons_are_real_ctk_controls_inside_bounds(self):
        panel, view = self.make()
        for button in (view.attach_button, view.mic_button, view.speaker_button, view.send_button):
            self.assertIsInstance(button, ctk.CTkButton)
            self.assertGreater(button.winfo_width(), 0)
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                 panel.winfo_rootx() + panel.winfo_width())
        self.assertLess(view.attach_button.winfo_rootx(), view.mic_button.winfo_rootx())
        self.assertLess(view.mic_button.winfo_rootx(), view.speaker_button.winfo_rootx())
        self.assertLess(view.speaker_button.winfo_rootx(), view.send_button.winfo_rootx())
        self.assertIs(panel.entry, view.entry)
        self.assertIs(panel._attach_btn, view.attach_button)

    def test_titlebar_callbacks_and_geometry_restore(self):
        panel, view = self.make()
        panel._min_btn.invoke()
        panel._close_btn.invoke()
        self.assertEqual((self.minimized, self.closed), (1, 1))
        panel.geometry("700x520+25+30")
        self.pump()
        panel.restore_default_geometry()
        self.pump()
        self.assertEqual((panel.winfo_x(), panel.winfo_y(), panel.winfo_width(), panel.winfo_height()),
                         (10, 10, 1100, 740))

    def test_initial_content_is_an_unsent_draft(self):
        panel, view = self.make(initial_content="Draft this\ncarefully")
        self.assertEqual(view.entry.get("1.0", "end-1c"), "Draft this\ncarefully")
        self.assertEqual(view.current.messages[-1].role, "assistant")
        self.assertEqual(view.pending, set())

    def test_placeholder_is_not_user_text_and_blank_send_is_disabled(self):
        panel, view = self.make()
        self.assertEqual(view.entry.get("1.0", "end-1c"), "")
        self.assertEqual(view.placeholder.winfo_manager(), "place")
        self.assertEqual(view.send_button.cget("state"), "disabled")
        count = len(view.current.messages)
        view.send_message()
        self.assertEqual(len(view.current.messages), count)
        view.use_prompt("   ")
        self.assertEqual(view.send_button.cget("state"), "disabled")

    def test_suggestion_only_fills_draft_and_modified_state_updates(self):
        panel, view = self.make()
        count = len(view.current.messages)
        view.prompt_buttons[0].invoke()
        self.assertEqual(view.entry.get("1.0", "end-1c"), "Help me plan a calmer day.")
        self.assertEqual(len(view.current.messages), count)
        self.assertEqual(view.placeholder.winfo_manager(), "")
        self.assertEqual(view.send_button.cget("state"), "normal")
        view.entry.delete("1.0", "end")
        self.pump()
        self.assertEqual(view.send_button.cget("state"), "disabled")

    def test_shift_enter_keeps_newline_and_enter_sends_local_sample(self):
        panel, view = self.make()
        view.new_conversation()
        view.use_prompt("First line")
        view.entry._textbox.focus_force()
        self.pump()
        view.entry._textbox.event_generate("<Shift-Return>")
        self.pump()
        self.assertIn("\n", view.entry.get("1.0", "end-1c"))
        self.assertEqual(view.current.messages, [])
        view.entry._textbox.event_generate("<Return>")
        self.pump(.05)
        self.assertEqual(view.current.messages[0].content, "First line")
        self.assertEqual(view.current.messages[0].role, "user")
        self.assertEqual(view.entry.get("1.0", "end-1c"), "")
        self.wait(lambda: not view.pending)
        self.assertEqual(view.current.messages[-1].role, "assistant")
        self.assertIn("sample replies", view.current.messages[-1].content)

    def test_new_switch_and_drafts_preserve_each_conversations_files(self):
        panel, view = self.make()
        a = view.active_id
        view.use_prompt("draft A\nline 2")
        view._draft_attachments = ["A.txt"]
        view.new_conversation()
        b = view.active_id
        self.assertNotEqual(a, b)
        self.assertEqual(view.entry.get("1.0", "end-1c"), "")
        view.use_prompt("draft B")
        view._draft_attachments = ["B.txt"]
        view.switch_conversation(a)
        self.assertEqual(view.entry.get("1.0", "end-1c"), "draft A\nline 2")
        self.assertEqual(view._draft_attachments, ["A.txt"])
        view.switch_conversation(b)
        self.assertEqual(view.entry.get("1.0", "end-1c"), "draft B")
        self.assertEqual(view._draft_attachments, ["B.txt"])

    def test_keyboard_new_is_scoped_to_native_chat_toplevel(self):
        panel, view = self.make()
        before = len(view.conversations)
        view.entry._textbox.focus_force()
        self.pump()
        view.entry._textbox.event_generate("<Control-n>")
        self.pump()
        self.assertEqual(len(view.conversations), before + 1)
        self.assertEqual(self.root.bind("<Control-n>"), "")

    def test_attach_adds_unique_names_only_and_cancel_or_remove_is_safe(self):
        panel, view = self.make()
        paths = [str(Path(self.tmp.name)/"one.txt"), str(Path(self.tmp.name)/"two.txt")]
        with patch("friday_chat.view.filedialog.askopenfilenames", return_value=paths), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("must not read files")):
            panel._on_attach_click()
            panel._on_attach_click()
        self.assertEqual(view._draft_attachments, ["one.txt", "two.txt"])
        self.assertEqual(view.file_chip.winfo_manager(), "grid")
        self.assertIn("No file contents are read or uploaded", view._last_notice)
        with patch("friday_chat.view.filedialog.askopenfilenames", return_value=()):
            view.attach_files()
        self.assertEqual(view._draft_attachments, ["one.txt", "two.txt"])
        view.file_chip.invoke()
        self.assertEqual(view._draft_attachments, [])
        self.assertEqual(view.file_chip.winfo_manager(), "")

    def test_file_only_message_uses_reference_and_explains_no_upload(self):
        panel, view = self.make()
        view.new_conversation()
        view._draft_attachments = ["not-opened.txt"]
        view._update_composer()
        view.send_button.invoke()
        self.assertEqual(view.current.messages[0].content, "")
        self.assertEqual(view.current.messages[0].attachments, ["not-opened.txt"])
        self.assertEqual(view._draft_attachments, [])
        self.wait(lambda: not view.pending)
        self.assertIn("doesn’t read or upload files", view.current.messages[-1].content)

    def test_mic_and_speaker_are_honest_and_do_not_fake_active_states(self):
        panel, view = self.make()
        view.mic_button.invoke()
        self.assertIn("Voice input is not connected", view._last_notice)
        view.speaker_button.invoke()
        self.assertIn("Spoken replies are not connected", view._last_notice)
        self.assertNotEqual(view.mic_button.cget("fg_color"), C["accent"])
        self.assertNotEqual(view.speaker_button.cget("fg_color"), C["accent"])

    def test_pending_reply_targets_original_conversation_not_current_view(self):
        panel, view = self.make()
        threads = []
        view.is_demo = False
        def responder(text, history):
            threads.append(get_ident())
            self.worker_gate.wait(2)
            return "Result for " + text
        view.reply_handler = responder
        view.new_conversation()
        a = view.current
        view.use_prompt("conversation A")
        view.send_message()
        self.assertEqual(view.send_button.cget("state"), "disabled")
        view.use_prompt("duplicate")
        view.send_message()
        self.assertEqual(len(a.messages), 1)
        view.new_conversation()
        b = view.current
        self.worker_gate.set()
        self.wait(lambda: not view.pending)
        self.assertEqual(a.messages[-1].content, "Result for conversation A")
        self.assertEqual(b.messages, [])
        self.assertNotEqual(threads[0], get_ident())

    def test_deleted_conversation_is_not_resurrected_by_late_reply(self):
        panel, view = self.make()
        view.is_demo = False
        view.reply_handler = lambda *_: (self.worker_gate.wait(2) and "late reply")
        view.new_conversation()
        identifier = view.active_id
        view.use_prompt("a deleted request")
        view.send_message()
        view._delete_conversation(identifier)
        self.worker_gate.set()
        self.pump(.2)
        self.assertNotIn(identifier, [c.id for c in view.conversations])
        self.assertEqual(view.pending, set())
        self.assertNotIn("late reply", [m.content for c in view.conversations for m in c.messages])

    def test_responder_failure_stays_in_ui_without_leaking_exception_detail(self):
        panel, view = self.make()
        view.is_demo = False
        def fail(*_):
            raise RuntimeError("SECRET_TOKEN")
        view.reply_handler = fail
        view.new_conversation()
        view.use_prompt("kept request")
        with patch("sys.stderr") as errors:
            view.send_message()
            self.wait(lambda: not view.pending)
            self.assertNotIn("SECRET_TOKEN", str(errors.write.call_args_list))
        self.assertEqual(view.current.messages[0].content, "kept request")
        self.assertIn("couldn’t be loaded", view.current.messages[-1].content)
        self.assertIn("Couldn’t load a reply", view._last_notice)

    def test_copy_export_and_cancelled_export(self):
        panel, view = self.make()
        view.copy_message("Copy this reply")
        self.assertEqual(self.root.clipboard_get(), "Copy this reply")
        output = Path(self.tmp.name)/"conversation.txt"
        with patch("friday_chat.view.filedialog.asksaveasfilename", return_value=str(output)):
            view.export_conversation()
        text = output.read_text()
        self.assertIn("Friday", text)
        self.assertIn(view.current.title, text)
        with patch("friday_chat.view.filedialog.asksaveasfilename", return_value=""):
            view.export_conversation()
        self.assertEqual(output.read_text(), text)

    def test_delete_confirmation_cancel_then_delete_and_last_conversation_fallback(self):
        panel, view = self.make()
        identifier = view.active_id
        view.confirm_delete()
        dialog = view._dialogs[-1]
        controls = [w for frame in dialog.winfo_children() for w in frame.winfo_children()
                    if isinstance(w, ctk.CTkButton)]
        next(w for w in controls if w.cget("text") == "Cancel").invoke()
        self.assertIn(identifier, [c.id for c in view.conversations])
        view.confirm_delete()
        dialog = view._dialogs[-1]
        controls = [w for frame in dialog.winfo_children() for w in frame.winfo_children()
                    if isinstance(w, ctk.CTkButton)]
        next(w for w in controls if w.cget("text") == "Delete").invoke()
        self.assertNotIn(identifier, [c.id for c in view.conversations])
        for c in list(view.conversations):
            view._delete_conversation(c.id)
        self.assertEqual(len(view.conversations), 1)
        self.assertEqual(view.current.messages, [])
        self.assertEqual(view._dialogs, [])
        self.pump(.1)

    def test_history_survives_native_panel_close_and_reopen(self):
        panel, view = self.make()
        view.new_conversation()
        identifier = view.active_id
        view.current.add(Message("user", "saved across Friday panels"))
        panel.destroy()
        new_panel, new_view = self.make()
        self.assertEqual(new_view.active_id, identifier)
        self.assertEqual(new_view.current.messages[-1].content, "saved across Friday panels")
        self.assertEqual(len(new_view.conversations), 4)

    def test_compact_history_overlay_and_short_panel_keep_composer_visible(self):
        panel, view = self.make(width=620, height=530)
        self.assertTrue(view._compact)
        self.assertEqual(view.sidebar.winfo_manager(), "")
        self.assertEqual(view.sidebar_button.winfo_manager(), "grid")
        view.sidebar_button.invoke()
        self.assertTrue(view._sidebar_open)
        self.assertEqual(view.sidebar.winfo_manager(), "place")
        view.new_conversation()
        self.assertFalse(view._sidebar_open)
        panel.geometry("420x350+10+10")
        self.pump(.15)
        self.assertGreater(view.entry.winfo_width(), 80)
        self.assertTrue(view.entry.winfo_ismapped())
        self.assertTrue(view.send_button.winfo_ismapped())
        self.assertLessEqual(view.send_button.winfo_rootx()+view.send_button.winfo_width(),
                             panel.winfo_rootx()+panel.winfo_width())
        self.assertLessEqual(view.composer.winfo_rooty()+view.composer.winfo_height(),
                             panel.winfo_rooty()+panel.winfo_height())
        self.assertEqual(view.prompt_buttons[-1].winfo_manager(), "")
        panel.geometry("1100x740+10+10")
        self.pump(.15)
        self.assertFalse(view._compact)
        self.assertEqual(view.sidebar.winfo_manager(), "grid")
        self.assertEqual(view.sidebar_button.winfo_manager(), "")

    def test_scrolling_bindings_are_panel_scoped_and_removed_on_view_close(self):
        self.root.bind_all("<MouseWheel>", lambda e: None, add="+")
        before = self.root.bind_all("<MouseWheel>")
        panel, view = self.make()
        self.assertEqual(self.root.bind_all("<MouseWheel>"), before)
        self.assertTrue(panel.bind("<MouseWheel>"))
        view.destroy()
        self.assertEqual(panel.bind("<MouseWheel>"), "")
        self.assertEqual(panel.bind("<Control-n>"), "")
        self.assertEqual(self.root.bind_all("<MouseWheel>"), before)

    def test_images_have_explicit_interpreter_and_tuple_fonts(self):
        panel, view = self.make()
        for image in view.icon.images.values():
            self.assertIs(image._master, view)
            for photo in (*image._scaled_dark_photo_images.values(),
                          *image._scaled_light_photo_images.values()):
                self.assertIs(photo.tk, view.tk)
        for widget in (view.entry, view.title_label, view.send_button, view.status_badge):
            self.assertIsInstance(widget.cget("font"), tuple)

    def test_destroy_cancels_jobs_tooltips_dialogs_and_native_image_caches(self):
        panel, view = self.make()
        view._notify("A temporary notice")
        view.show_about()
        view._tooltips[0].schedule()
        jobs = [view._poll_job, view._resize_job, view._scroll_job, view._toast_job]
        images = list(view.icon.images.values())
        panel.set_active_panel(True)
        panel.set_friday_status("thinking")
        panel.destroy()
        panel.destroy()
        self.pump(.3)
        remaining = self.root.tk.call("after", "info")
        for job in jobs:
            if job is not None:
                self.assertNotIn(job, remaining)
        self.assertTrue(view._closed)
        self.assertEqual(view.icon.images, {})
        self.assertTrue(all(not image._scaled_dark_photo_images for image in images))
        self.assertTrue(all(tooltip.job is None and tooltip.window is None for tooltip in view._tooltips))
        self.assertEqual(view._dialogs, [])

    def test_mouse_click_animation_is_cancelled_with_native_titlebar_controls(self):
        from friday_chat.widgets import PanelButton
        panel, view = self.make()
        self.assertIsInstance(panel._min_btn, PanelButton)
        self.assertIsInstance(panel._close_btn, PanelButton)
        panel._min_btn._canvas.event_generate("<Button-1>", x=5, y=5)
        self.assertEqual(self.minimized, 1)
        self.assertTrue(panel._min_btn._owned_after_jobs)
        panel._close_btn.configure(command=panel.destroy)
        panel._close_btn._canvas.event_generate("<Button-1>", x=5, y=5)
        self.pump(.3)
        self.assertEqual(panel._min_btn._owned_after_jobs, set())
        self.assertEqual(panel._close_btn._owned_after_jobs, set())
        self.assertTrue(view._closed)

    def test_widget_scaling_keeps_icons_and_long_messages_inside_transcript(self):
        panel, view = self.make(width=1180, height=800)
        view.current.messages = [Message("user", "A fairly long user message. " * 20),
                                 Message("assistant", "A long unboxed Friday answer. " * 30)]
        try:
            ctk.set_widget_scaling(1.25)
            view._refresh()
            self.pump(.2)
            right = view.chat._parent_canvas.winfo_rootx() + view.chat._parent_canvas.winfo_width()
            for label, role in view._wrapped_labels:
                self.assertLessEqual(label.winfo_rootx() + label.winfo_width(), right,
                                     "Scaled " + role + " text must not be clipped")
            photos = [photo for image in view.icon.images.values()
                      for photo in image._scaled_dark_photo_images.values()]
            self.assertTrue(any(photo.width() == 22 for photo in photos))
        finally:
            ctk.set_widget_scaling(1.0)
            self.pump(.05)

    def test_corrupt_history_is_session_only_and_never_overwritten(self):
        from friday_chat.storage import EncryptedChatStore
        path = Path(memory._kv_path(EncryptedChatStore.KEY))
        path.write_bytes(b"corrupt-private-history")
        panel, view = self.make()
        self.assertIsNone(view.storage)
        self.assertTrue(view._load_warning)
        self.assertEqual(view.storage_label.cget("text"), "Session only")
        view.new_conversation()
        panel.destroy()
        self.assertEqual(path.read_bytes(), b"corrupt-private-history")

    def test_save_failure_reports_session_state_but_keeps_user_message(self):
        panel, view = self.make()
        with patch.object(view.storage, "save", side_effect=OSError("read only")):
            view.new_conversation()
            view.current.add(Message("user", "kept locally"))
            view._persist()
        self.assertEqual(view.storage_label.cget("text"), "Session only")
        self.assertIn("Couldn’t save encrypted history", view._last_notice)
        self.assertEqual(view.current.messages[0].content, "kept locally")



if __name__ == "__main__":
    unittest.main()

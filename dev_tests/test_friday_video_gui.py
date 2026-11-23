"""Real-GUI coverage for Quiet Video integrated into Friday's native panel.

Run: xvfb-run -a python -m unittest discover -s dev_tests -p test_friday_video_gui.py -v
Tests use throwaway Friday memory folders, real Tk widgets and only the local
deterministic renderer. No provider or network service is invoked.
"""
from pathlib import Path
from PIL import Image
from threading import Event
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
from conversation_panels import VideoGenPanel
from friday_video.model import VideoRequest
from friday_video.storage import EncryptedVideoStore
from friday_video.view import C, QuietVideoView


class VideoGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.callback_errors = []
        cls.root.report_callback_exception = lambda *args: cls.callback_errors.append(args)
        cls.root.tk.createcommand("friday_video_test_bgerror",
                                   lambda message: cls.callback_errors.append(message))
        cls.root.tk.eval("proc bgerror {message} {friday_video_test_bgerror $message}")

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
        self.render_gate = Event()

    def tearDown(self):
        self.render_gate.set()
        for panel in self.panels:
            try:
                if panel.winfo_exists():
                    panel.destroy()
            except tk.TclError:
                pass
        self.pump(.05)
        memory.set_memory_dir(self.previous_dir)
        self.tmp.cleanup()
        self.assertEqual(self.errors, [], "Tk callbacks must not fail")

    def make(self, width=1100, height=740, initial_content=""):
        def minimize():
            self.minimized += 1
        def close():
            self.closed += 1
        panel = VideoGenPanel(self.root, minimize, close,
                              initial_content=initial_content,
                              bounds=(10, 10, width, height))
        self.panels.append(panel)
        self.pump(.16)
        return panel, panel.video_view

    def pump(self, seconds=.08):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                self.root.update()
            except tk.TclError:
                pass
            time.sleep(.004)

    def wait(self, predicate, timeout=5):
        end = time.monotonic() + timeout
        while not predicate() and time.monotonic() < end:
            self.pump(.03)
        self.assertTrue(predicate(), "Timed out waiting for a UI result")

    def configure_fast_gif(self, view, seconds=1):
        view.duration_slider.set(seconds)
        view._choice_select(view.quality_group, "Draft")
        view._choice_select(view.format_group, "gif")
        view._choice_select(view.fps_group, "12")
        view.update_preview(force=True)
        self.pump(.03)

    def test_native_security_contract_and_quiet_palette(self):
        panel, view = self.make()
        self.assertIsInstance(panel, tk.Toplevel)
        self.assertIsInstance(view, ctk.CTkFrame)
        self.assertIs(panel._body, view)
        self.assertEqual(panel.title(), "Video Generation")
        self.assertIs(panel.entry, view.prompt_box)
        self.assertIs(panel._send_btn, view.generate_button)
        self.assertIs(panel._attach_btn, view.attach_button)
        self.assertEqual(view.shell.cget("fg_color"), C["background"])
        self.assertEqual(view.rail.cget("fg_color"), C["sidebar"])
        self.assertIn("local renderer", view.provider_badge.cget("text"))
        self.assertIn("Friday encrypted memory", view.storage_line.cget("text"))
        self.assertTrue(view.provider_badge.cget("image"))
        self.assertEqual(view.generate_button.cget("text"), "Generate video locally")

    def test_titlebar_callbacks_geometry_active_state_and_manager_methods(self):
        panel, view = self.make()
        panel._min_btn.invoke()
        panel._close_btn.invoke()
        self.assertEqual((self.minimized, self.closed), (1, 1))
        panel.geometry("700x500+25+30")
        self.pump()
        panel.restore_default_geometry()
        self.pump()
        self.assertEqual((panel.winfo_x(), panel.winfo_y(), panel.winfo_width(),
                          panel.winfo_height()), (10, 10, 1100, 740))
        panel.set_active_panel(True)
        active_color = panel.cget("bg")
        panel.set_friday_status("thinking", True)
        error_color = panel.cget("bg")
        self.assertNotEqual(active_color, error_color)
        panel.maximize_to(30, 40, 900, 620)
        self.assertTrue(panel.is_maximized)
        panel.move_to_slot(11, 12, 800, 600)
        self.pump()
        self.assertFalse(panel.is_maximized)
        self.assertEqual((panel.winfo_x(), panel.winfo_y(), panel.winfo_width(),
                          panel.winfo_height()), (11, 12, 800, 600))

    def test_initial_content_is_an_editable_unsubmitted_draft(self):
        panel, view = self.make(initial_content="Draft this film idea\ncarefully")
        self.assertEqual(view.prompt_box.get("1.0", "end-1c"),
                         "Draft this film idea\ncarefully")
        self.assertEqual(len(view.project.jobs), 0)
        self.assertEqual(view.generate_button.cget("state"), "normal")

    def test_controls_update_real_preview_and_incremental_value_display(self):
        panel, view = self.make()
        view.use_prompt("A lantern drifting over wet stone")
        view._choice_select(view.style_group, "Ember")
        view._choice_select(view.aspect_group, "1:1")
        view._choice_select(view.motion_group, "Pan right")
        view._choice_select(view.fps_group, "12")
        view.duration_slider.set(1.0)
        view._widgets_changed()
        self.wait(lambda: view._preview_job is None and view._preview_photo is not None)
        self.assertIn("1:1", view.preview_meta.cget("text"))
        self.assertIn("12 fps", view.preview_meta.cget("text"))
        self.assertIn("12 frames", view.preview_meta.cget("text"))
        old_seed = view.seed_entry.get()
        view.new_seed()
        self.assertNotEqual(view.seed_entry.get(), old_seed)
        view.duration_slider.set(2.5)
        self.assertEqual(view.duration_slider.display.cget("text"), "2.5 s")
        request = view._request_from_controls()
        self.assertIsInstance(request, VideoRequest)
        self.assertEqual((request.style, request.aspect, request.motion), ("Ember", "1:1", "Pan right"))

    def test_reference_image_picker_chip_and_clear_are_real(self):
        panel, view = self.make()
        source = Path(self.tmp.name) / "reference image.png"
        Image.new("RGB", (80, 60), (90, 100, 110)).save(source)
        with patch("friday_video.view.filedialog.askopenfilename", return_value=str(source)):
            panel._on_attach_click()
        self.assertEqual(view.reference_image, str(source))
        self.assertEqual(view.image_chip.cget("text"), "reference image.png")
        self.assertEqual(view.clear_image_button.winfo_manager(), "grid")
        request = view._request_from_controls()
        self.assertEqual(request.source_image, str(source))
        panel._clear_attachment()
        self.assertIsNone(view.reference_image)
        self.assertEqual(view.clear_image_button.winfo_manager(), "")

    def test_real_local_gif_render_updates_queue_preview_and_encrypted_history(self):
        panel, view = self.make()
        self.configure_fast_gif(view)
        view._send_click() if hasattr(view, "_send_click") else view.generate_button.invoke()
        self.wait(lambda: view.running_job_id is not None)
        self.wait(lambda: view.running_job_id is None, timeout=20)
        job = view.project.latest_job
        self.assertEqual(job.status, "succeeded", job.error)
        self.assertEqual(job.frames_done, 12)
        self.assertTrue(Path(job.output_path).is_file())
        self.assertTrue(Path(job.poster_path).is_file())
        self.assertTrue(job.output_path.startswith(self.tmp.name))
        with Image.open(job.output_path) as video:
            self.assertEqual(video.n_frames, 12)
        self.assertIn("Video rendered locally", view.status.cget("text"))
        self.assertIn("Latest result", view.preview_meta.cget("text"))
        path = Path(memory._kv_path(EncryptedVideoStore.KEY))
        self.assertTrue(path.is_file())
        self.assertNotIn(b"A river at dusk", path.read_bytes())

    def test_cancelled_render_updates_status_and_leaves_no_partial_output(self):
        panel, view = self.make()
        view.duration_slider.set(8)
        view._choice_select(view.quality_group, "Draft")
        view._choice_select(view.format_group, "gif")
        view._choice_select(view.fps_group, "30")
        view.generate_button.invoke()
        self.wait(lambda: view.running_job_id is not None)
        view.cancel_render()
        self.wait(lambda: view.running_job_id is None, timeout=12)
        job = view.project.latest_job
        self.assertEqual(job.status, "cancelled")
        self.assertIn("Cancel", view.status.cget("text").title())
        outputs = list(Path(view.settings.output_dir).glob("*.gif"))
        temp_outputs = list(Path(view.settings.output_dir).glob("*.tmp.*"))
        self.assertEqual(outputs, [])
        self.assertEqual(temp_outputs, [])
        self.assertEqual(view.generate_button.cget("state"), "normal")

    def test_history_survives_native_panel_destroy_and_reopen(self):
        panel, view = self.make()
        view.use_prompt("A quiet street under violet rain", persist=True)
        view._choice_select(view.aspect_group, "9:16")
        view._save_now()
        identifier = view.active_id
        output_dir = view.settings.output_dir
        panel.destroy()
        self.pump(.05)
        new_panel, new_view = self.make()
        self.assertEqual(new_view.active_id, identifier)
        self.assertEqual(new_view.prompt_box.get("1.0", "end-1c"),
                         "A quiet street under violet rain")
        self.assertEqual(new_view.settings.output_dir, output_dir)
        self.assertEqual(new_view.current_project_brief if hasattr(new_view, "current_project_brief") else new_view.project.brief,
                         "A quiet street under violet rain")
        self.assertEqual(new_panel.title(), "Video Generation")

    def test_corrupt_encrypted_record_is_reported_and_not_overwritten(self):
        record = Path(memory._kv_path(EncryptedVideoStore.KEY))
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_bytes(b"still private, still corrupt")
        panel, view = self.make()
        self.assertFalse(view._persistent)
        self.assertIn("Temporary session", view.storage_line.cget("text"))
        view.use_prompt("Do not overwrite a corrupt record", persist=True)
        view._save_now()
        self.assertEqual(record.read_bytes(), b"still private, still corrupt")

    def test_compact_panel_bounds_keep_primary_controls_usable(self):
        panel, view = self.make(width=850, height=560)
        self.wait(lambda: not view.timing.winfo_manager())
        view.use_prompt("A candle floating through a narrow hall")
        self.pump(.08)
        self.assertTrue(view.prompt_box.winfo_ismapped())
        self.assertTrue(view.generate_button.winfo_ismapped())
        self.assertGreater(view.prompt_box.winfo_width(), 180)
        self.assertLessEqual(view.generate_button.winfo_rootx() + view.generate_button.winfo_width(),
                             panel.winfo_rootx() + panel.winfo_width())
        panel.maximize_to(10, 10, 1250, 760)
        self.wait(lambda: bool(view.timing.winfo_manager()), timeout=4)

    def test_images_have_explicit_interpreter_and_tuple_fonts(self):
        panel, view = self.make()
        images = list(view.icon_bank.images.values())
        self.assertTrue(images)
        for image in images:
            self.assertIs(image._master, view)
            for photo in (*image._scaled_dark_photo_images.values(),
                          *image._scaled_light_photo_images.values()):
                self.assertIs(photo.tk, view.tk)
        self.assertIs(view._preview_photo._master, view)
        for widget in (view.prompt_box, view.generate_button, view.provider_badge,
                       view.preview_label, view.status):
            self.assertIsInstance(widget.cget("font"), tuple)

    def test_destroy_cancels_jobs_tooltips_and_native_image_caches(self):
        panel, view = self.make()
        self.wait(lambda: view._preview_photo is not None)
        image = view._preview_photo
        icons = list(view.icon_bank.images.values())
        view._tooltips[0].schedule()
        jobs = list(view._after_jobs)
        panel.set_active_panel(True)
        panel.set_friday_status("thinking")
        panel.destroy()
        panel.destroy()
        self.pump(.1)
        self.assertTrue(view._closed)
        self.assertEqual(view.icon_bank.images, {})
        self.assertTrue(all(not icon._scaled_dark_photo_images and
                            not icon._scaled_light_photo_images for icon in icons))
        self.assertEqual(image._scaled_dark_photo_images, {})
        self.assertEqual(image._scaled_light_photo_images, {})
        self.assertTrue(all(tooltip.job is None and tooltip.window is None
                            for tooltip in view._tooltips))
        remaining = self.root.tk.call("after", "info")
        for job in jobs:
            self.assertNotIn(job, remaining)

    def test_closing_during_render_marks_job_cancelled_and_reopens_safely(self):
        panel, view = self.make()
        view.duration_slider.set(8)
        view._choice_select(view.quality_group, "Draft")
        view._choice_select(view.format_group, "gif")
        view._choice_select(view.fps_group, "30")
        view.generate_button.invoke()
        self.wait(lambda: view.running_job_id is not None)
        identifier = view.running_job_id
        panel.destroy()
        self.wait(lambda: view._closed)
        raw, active, settings = EncryptedVideoStore().load()
        closed_job = next(job for project in raw for job in project.jobs if job.id == identifier)
        self.assertEqual(closed_job.status, "cancelled")
        reopened, new_view = self.make()
        self.assertIsNone(new_view.running_job_id)
        self.assertGreaterEqual(len(new_view.projects), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

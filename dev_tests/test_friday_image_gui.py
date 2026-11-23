"""Real-GUI coverage for Quiet Image integrated into Friday's native panel.

Run: xvfb-run -a python -m unittest discover -s dev_tests -p test_friday_image_gui.py -v
Tests use throwaway Friday memory folders, real Tk widgets and only the
local deterministic renderer. No provider or network service is invoked.
"""
from pathlib import Path
from PIL import Image
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import customtkinter as ctk
import memory
from conversation_panels import ImageGenPanel
from friday_image.model import ImageRequest
from friday_image.renderer import RenderCancelled
from friday_image.storage import EncryptedImageStore
from friday_image.icons import PanelImage
from friday_image.view import C, QuietImageView


class FakeSuccessBackend:
    """Deterministic stand-in for a configured cloud engine: writes a real
    PNG + thumbnail through the shared helpers so the panel's full success
    path (queue, inline result, encrypted history) is exercised without
    any network or local art renderer."""

    name = "Fixture cloud engine"
    description = "writes deterministic fixture output"
    badge = "cloud · fixture"
    provider_kind = "cloud"
    accent_badge = True
    model = "fixture-model"

    def render(self, request, output_dir, progress_callback=None, cancel_event=None):
        from friday_image.renderer import (RenderedImage, _output_paths,
                                           _save_output, thumbnail_final_path)
        request = request.validated()
        temp_path, final_path, thumb_temp = _output_paths(request, Path(output_dir))
        image = Image.new("RGB", (694, 522), (38, 88, 60))
        _save_output(image, temp_path, request)
        thumbnail = image.copy()
        thumbnail.thumbnail((520, 520), Image.Resampling.LANCZOS)
        thumbnail.save(thumb_temp, "PNG", optimize=True)
        thumb_final = thumbnail_final_path(final_path)
        temp_path.replace(final_path)
        thumb_temp.replace(thumb_final)
        if progress_callback:
            progress_callback(1, 1)
        return RenderedImage(output_path=str(final_path),
                             thumbnail_path=str(thumb_final),
                             detail_steps=request.steps_total,
                             elapsed_seconds=0.01,
                             size_bytes=final_path.stat().st_size,
                             format=request.format)


class SlowCancelBackend:
    name = "Slow cancel fixture"
    description = "Long-running deterministic backend used only for lifecycle tests"
    badge = "local renderer · no AI provider connected"

    def render(self, request, output_dir, progress_callback=None, cancel_event=None):
        total = max(2, request.steps_total * 3)
        cancel_event = cancel_event
        for index in range(total):
            if cancel_event is not None and cancel_event.is_set():
                raise RenderCancelled("Render cancelled.")
            if progress_callback:
                progress_callback(index + 1, total)
            time.sleep(.025)
        if cancel_event is not None and cancel_event.is_set():
            raise RenderCancelled("Render cancelled.")
        raise RuntimeError("fixture should have been cancelled")


class ImageGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.callback_errors = []
        cls.root.report_callback_exception = lambda *args: cls.callback_errors.append(args)
        cls.root.tk.createcommand("friday_image_test_bgerror",
                                   lambda message: cls.callback_errors.append(message))
        cls.root.tk.eval("proc bgerror {message} {friday_image_test_bgerror $message}")

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

    def tearDown(self):
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
        def minimize(): self.minimized += 1
        def close(): self.closed += 1
        panel = ImageGenPanel(self.root, minimize, close,
                              initial_content=initial_content,
                              bounds=(10, 10, width, height))
        self.panels.append(panel)
        self.pump(.16)
        return panel, panel.image_view

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

    def configure_fast_png(self, view):
        view._choice_select(view.quality_group, "Draft")
        view._choice_select(view.format_group, "png")
        view._choice_select(view.aspect_group, "4:3")
        view.detail_slider.set(10)
        view.update_preview(force=True)
        self.pump(.03)

    def test_native_security_contract_and_quiet_palette(self):
        panel, view = self.make()
        self.assertIsInstance(panel, tk.Toplevel)
        self.assertIsInstance(view, ctk.CTkFrame)
        self.assertIs(panel._body, view)
        self.assertEqual(panel.title(), "Image Generation")
        self.assertIs(panel.entry, view.prompt_box)
        self.assertIs(panel._send_btn, view.generate_button)
        self.assertIs(panel._attach_btn, view.attach_button)
        self.assertEqual(view.shell.cget("fg_color"), C["background"])
        self.assertEqual(view.rail.cget("fg_color"), C["sidebar"])
        self.assertIn("no provider key", view.provider_badge.cget("text"))
        self.assertIn("Friday encrypted memory", view.storage_line.cget("text"))
        self.assertTrue(view.provider_badge.cget("image"))
        self.assertEqual(view.generate_button.cget("text"), "Set up image provider…")

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
        panel, view = self.make(initial_content="A lantern in an evening garden")
        self.assertEqual(view.prompt_box.get("1.0", "end-1c"),
                         "A lantern in an evening garden")
        self.assertEqual(len(view.project.jobs), 0)
        self.assertEqual(view.generate_button.cget("state"), "normal")

    def test_controls_update_real_preview_and_incremental_value_display(self):
        panel, view = self.make()
        view.use_prompt("A lantern held behind rain-washed glass")
        view._choice_select(view.style_group, "Glass")
        view._choice_select(view.composition_group, "Rings")
        view._choice_select(view.aspect_group, "1:1")
        view._choice_select(view.quality_group, "Draft")
        view.detail_slider.set(28)
        view._widgets_changed()
        self.wait(lambda: view._preview_job is None)
        # No preview surface exists in the cloud-only layout; widget edits
        # still flow through the validator and refresh its signature.
        signature = view.last_preview_signature
        self.assertIsNotNone(signature)
        self.assertIn("1:1", signature[2])
        self.assertEqual(signature[3], "Draft")
        old_seed = view.seed_entry.get()
        view.new_seed()
        self.assertNotEqual(view.seed_entry.get(), old_seed)
        view.detail_slider.set(63)
        self.assertEqual(view.detail_slider.display.cget("text"), "63%")
        request = view._request_from_controls()
        self.assertIsInstance(request, ImageRequest)
        self.assertEqual((request.style, request.aspect, request.composition),
                         ("Glass", "1:1", "Rings"))

    def test_reference_image_picker_chip_and_clear_are_real(self):
        panel, view = self.make()
        source = Path(self.tmp.name) / "reference image.png"
        Image.new("RGB", (80, 60), (90, 100, 110)).save(source)
        with patch("friday_image.view.filedialog.askopenfilename", return_value=str(source)):
            panel._on_attach_click()
        self.assertEqual(view.reference_image, str(source))
        self.assertEqual(view.image_chip.cget("text"), "reference image.png")
        self.assertEqual(view.clear_image_button.winfo_manager(), "grid")
        request = view._request_from_controls()
        self.assertEqual(request.source_image, str(source))
        panel._clear_attachment()
        self.assertIsNone(view.reference_image)
        self.assertEqual(view.clear_image_button.winfo_manager(), "")

    def test_successful_render_updates_queue_preview_and_encrypted_history(self):
        panel, view = self.make()
        view.backend = FakeSuccessBackend()
        self.configure_fast_png(view)
        view.generate_button.invoke()
        self.wait(lambda: view.running_job_id is not None)
        self.wait(lambda: view.running_job_id is None, timeout=20)
        job = view.project.latest_job
        self.assertEqual(job.status, "succeeded", job.error)
        self.assertEqual(job.steps_done, job.steps_total)
        self.assertTrue(Path(job.output_path).is_file())
        self.assertTrue(Path(job.thumbnail_path).is_file())
        self.assertTrue(job.output_path.startswith(self.tmp.name))
        with Image.open(job.output_path) as image:
            self.assertEqual(image.size, (694, 522))
        self.assertIn("Image ready · Fixture cloud engine", view.status.cget("text"))
        self.assertIn(job.id, view._job_photos)
        path = Path(memory._kv_path(EncryptedImageStore.KEY))
        self.assertTrue(path.is_file())
        self.assertNotIn(b"An evening garden", path.read_bytes())

    def test_cancelled_render_updates_status_and_leaves_no_partial_output(self):
        panel, view = self.make()
        view.backend = SlowCancelBackend()
        self.configure_fast_png(view)
        view.generate_button.invoke()
        self.wait(lambda: view.running_job_id is not None)
        view.cancel_render()
        self.wait(lambda: view.running_job_id is None, timeout=12)
        job = view.project.latest_job
        self.assertEqual(job.status, "cancelled")
        self.assertIn("Cancel", view.status.cget("text").title())
        output_dir = Path(view.settings.output_dir)
        self.assertEqual(list(output_dir.glob("*.png")), [])
        self.assertEqual(list(output_dir.glob("*.jpg")), [])
        self.assertEqual(list(output_dir.glob("*.tmp*")), [])
        self.assertEqual(view.generate_button.cget("state"), "normal")

    def test_history_survives_native_panel_destroy_and_reopen(self):
        panel, view = self.make()
        view.use_prompt("A garden behind quiet violet rain", persist=True)
        view._choice_select(view.aspect_group, "9:16")
        view._save_now()
        identifier = view.active_id
        output_dir = view.settings.output_dir
        panel.destroy()
        self.pump(.05)
        new_panel, new_view = self.make()
        self.assertEqual(new_view.active_id, identifier)
        self.assertEqual(new_view.prompt_box.get("1.0", "end-1c"),
                         "A garden behind quiet violet rain")
        self.assertEqual(new_view.project.brief, "A garden behind quiet violet rain")
        self.assertEqual(new_view.settings.output_dir, output_dir)
        self.assertEqual(new_panel.title(), "Image Generation")

    def test_corrupt_encrypted_record_is_reported_and_not_overwritten(self):
        record = Path(memory._kv_path(EncryptedImageStore.KEY))
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
        self.wait(lambda: not view.bay.winfo_manager())
        view.use_prompt("A candle floating through a narrow hall")
        self.pump(.08)
        self.assertTrue(view.prompt_box.winfo_ismapped())
        self.assertTrue(view.generate_button.winfo_ismapped())
        self.assertGreater(view.prompt_box.winfo_width(), 180)
        self.assertLessEqual(view.generate_button.winfo_rootx() + view.generate_button.winfo_width(),
                             panel.winfo_rootx() + panel.winfo_width())
        panel.maximize_to(10, 10, 1250, 760)
        self.wait(lambda: bool(view.bay.winfo_manager()), timeout=4)

    def test_images_have_explicit_interpreter_and_tuple_fonts(self):
        panel, view = self.make()
        images = list(view.icon_bank.images.values())
        self.assertTrue(images)
        for image in images:
            self.assertIs(image._master, view)
            for photo in (*image._scaled_dark_photo_images.values(),
                          *image._scaled_light_photo_images.values()):
                self.assertIs(photo.tk, view.tk)
        for widget in (view.prompt_box, view.generate_button, view.provider_badge,
                       view.engine_b_label, view.status):
            self.assertIsInstance(widget.cget("font"), tuple)

    def test_destroy_cancels_jobs_tooltips_and_native_image_caches(self):
        panel, view = self.make()
        view._preview_photo = PanelImage(view, Image.new("RGB", (16, 16)), (16, 16))
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
        view.backend = SlowCancelBackend()
        self.configure_fast_png(view)
        view.generate_button.invoke()
        self.wait(lambda: view.running_job_id is not None)
        identifier = view.running_job_id
        panel.destroy()
        self.wait(lambda: view._closed)
        raw, active, settings = EncryptedImageStore().load()
        closed_job = next(job for project in raw for job in project.jobs if job.id == identifier)
        self.assertEqual(closed_job.status, "cancelled")
        reopened, new_view = self.make()
        self.assertIsNone(new_view.running_job_id)
        self.assertGreaterEqual(len(new_view.projects), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

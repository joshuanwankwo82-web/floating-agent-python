"""Friday Video Generation lifecycle with the real AppPanelManager.

Run: xvfb-run -a python dev_tests/test_friday_video_integration.py
A different Tk root stays alive as Friday's IDE can do while the manager
hosts Video Generation on its own GUI owner thread. Global/default-root
CTkImage/CTkFont or scroll-binding regressions break this scenario. No
network, OS launcher, provider, or external process is needed.
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
from friday_video.storage import EncryptedVideoStore


class FakeHost:
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
        pass
    def speak_label(self, text):
        raise AssertionError("No hover speech should run in this lifecycle test")


class VideoManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.previous_dir = memory._memory_dir
        memory.set_memory_dir(cls.tmp.name)
        # Deliberately the default root; the panel manager uses another
        # interpreter on its GUI thread, matching Friday's real host.
        cls.ide_root = tk.Tk()
        cls.ide_root.withdraw()
        cls.ide_root.update()
        class NoLauncher:
            def launch(self, *args, **kwargs):
                raise AssertionError("Video Generation is native, not a launched process")
        cls.manager = app_panels.AppPanelManager(launcher=NoLauncher(), agent_host=FakeHost())

    @classmethod
    def tearDownClass(cls):
        cls.manager.shutdown()
        cls.ide_root.destroy()
        memory.set_memory_dir(cls.previous_dir)
        cls.tmp.cleanup()

    def tearDown(self):
        for key in list(self.manager._panels):
            self.manager.close(key)

    def open_video(self):
        result = self.manager.open("video_gen")
        self.assertIn("Opened Video Generation", result)
        return self.manager._panels["video_gen"]["frame"]

    def on_gui(self, func):
        return self.manager._bridge.call(func)

    def configure_long_render(self, view):
        view.duration_slider.set(8)
        view._choice_select(view.quality_group, "Draft")
        view._choice_select(view.format_group, "gif")
        view._choice_select(view.fps_group, "30")

    def test_multiple_interpreters_own_their_images_and_fonts(self):
        panel = self.open_video()
        def inspect():
            view = panel.video_view
            images = list(view.icon_bank.images.values())
            photos = [photo for image in images
                      for photo in image._scaled_dark_photo_images.values()]
            preview = view._preview_photo
            return (view.tk is self.ide_root.tk, bool(photos),
                    all(photo.tk is view.tk for photo in photos),
                    preview is not None and preview._master is view,
                    all(isinstance(w.cget("font"), tuple) for w in
                        (view.prompt_box, view.generate_button, view.provider_badge,
                         view.status, view.preview_label)),
                    view.winfo_toplevel() is panel)
        same, present, own, preview, tuples, native = self.on_gui(inspect)
        self.assertFalse(same)
        self.assertTrue(present and own and preview and tuples and native)
        self.assertEqual(self.ide_root.bind_all("<MouseWheel>"), "")
        self.assertEqual(self.ide_root.bind_all("<Button-4>"), "")

    def test_titlebar_minimizes_to_real_pill_and_restores_same_view_and_draft(self):
        panel = self.open_video()
        self.on_gui(lambda: panel.video_view.use_prompt("A draft film\nkept when minimized"))
        identifier = self.on_gui(lambda: panel.video_view.active_id)
        self.on_gui(panel._min_btn.invoke)
        record = self.manager._panels["video_gen"]
        self.assertTrue(record["minimized"])
        self.assertIsNotNone(record["pill"])
        self.assertEqual(self.on_gui(panel.state), "withdrawn")
        self.assertIn("Restored Video Generation", self.manager.restore("video_gen"))
        self.assertIs(record["frame"], panel)
        self.assertFalse(record["minimized"])
        self.assertEqual(self.on_gui(lambda: panel.video_view.active_id), identifier)
        self.assertEqual(self.on_gui(lambda: panel.video_view.prompt_box.get("1.0", "end-1c")),
                         "A draft film\nkept when minimized")
        self.on_gui(panel._close_btn.invoke)
        self.assertNotIn("video_gen", self.manager._panels)
        self.assertTrue(self.on_gui(lambda: panel.video_view._closed))
        self.assertTrue(self.on_gui(lambda: not panel.video_view.icon_bank.images))

    def test_friday_active_state_border_and_manager_bounds_still_apply(self):
        panel = self.open_video()
        self.assertTrue(self.on_gui(lambda: panel._is_active_panel))
        self.assertEqual(self.on_gui(lambda: (panel.winfo_x(), panel.winfo_y(),
                                              panel.winfo_width(), panel.winfo_height())),
                         (15, 20, 960, 700))
        self.manager.report_friday_status("video_gen", "responding", True)
        self.assertEqual(self.on_gui(lambda: (panel._friday_glow_state,
                                              panel._friday_glow_is_error)),
                         ("responding", True))
        error_color = self.on_gui(lambda: panel.cget("bg"))
        self.manager.report_friday_status("video_gen", "responding", False)
        self.assertNotEqual(self.on_gui(lambda: panel.cget("bg")), error_color)
        ok, reason = self.manager.maximize_panel("video_gen", (20, 25, 1050, 750))
        self.assertTrue(ok, reason)
        self.on_gui(panel.update_idletasks)
        self.assertEqual(self.on_gui(lambda: panel.winfo_width()), 1050)
        self.assertEqual(self.on_gui(lambda: panel.winfo_height()), 750)

    def test_active_render_close_marks_cancelled_and_clean_reopen(self):
        panel = self.open_video()
        def start():
            view = panel.video_view
            view.use_prompt("A close-interrupted film")
            view.duration_slider.set(8)
            view._choice_select(view.quality_group, "Draft")
            view._choice_select(view.format_group, "gif")
            view._choice_select(view.fps_group, "30")
            view.generate_button.invoke()
            return view.running_job_id
        identifier = self.on_gui(start)
        self.assertIsNotNone(identifier)
        self.assertIn("Closed Video Generation", self.manager.close("video_gen"))
        projects, _active, _settings = EncryptedVideoStore().load()
        job = next(job for project in projects for job in project.jobs
                   if job.id == identifier)
        self.assertEqual(job.status, "cancelled")
        reopened = self.open_video()
        self.assertTrue(self.on_gui(lambda: reopened.video_view._closed is False))
        self.assertTrue(self.on_gui(lambda: reopened.video_view.running_job_id is None))
        self.assertTrue(self.on_gui(lambda: bool(reopened.video_view.icon_bank.images)))

    def test_repeated_open_close_leaves_no_root_scroll_bindings_or_images(self):
        # v3 touchpad architecture: the interpreter carries exactly ONE
        # permanent global wheel handler (_tp_global) installed at host
        # birth. The leak invariant is "open/close cycles change NOTHING"
        # -- snapshot once, assert byte-identical after every cycle.
        baseline_wheel = self.on_gui(lambda: self.manager._root.bind_all("<MouseWheel>"))
        baseline_b4 = self.on_gui(lambda: self.manager._root.bind_all("<Button-4>"))
        self.assertEqual(baseline_wheel.count("_tp_global"), 1)
        for _ in range(3):
            panel = self.open_video()
            self.assertEqual(self.on_gui(lambda: self.manager._root.bind_all("<MouseWheel>")), baseline_wheel)
            self.assertEqual(self.on_gui(lambda: self.manager._root.bind_all("<Button-4>")), baseline_b4)
            self.manager.close("video_gen")
            self.assertTrue(self.on_gui(lambda: panel.video_view._closed))
            self.assertEqual(self.on_gui(lambda: panel.video_view.icon_bank.images), {})
        ide_wh = self.ide_root.bind_all("<MouseWheel>")
        self.assertEqual(ide_wh.count("_tp_global"), 1 if "_tp_global" in ide_wh else 0)
        self.assertEqual(self.ide_root.bind_all("<Button-4>").count("_tp_global"), 1 if "_tp_global" in self.ide_root.bind_all("<Button-4>") else 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Unit tests for Friday Video Generation data, local render, and storage.

Run: python -m unittest discover -s dev_tests -p test_friday_video_model.py -v
"""
from pathlib import Path
import os
import sys
import tempfile
import threading
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import memory
from friday_video.model import (AppSettings, RenderJob, VideoProject, VideoRequest,
                                export_project_text, decode_state, encode_state,
                                seed_from_prompt)
from friday_video.renderer import (RenderCancelled, ffmpeg_available, render_still,
                                   render_video, target_size)
from friday_video.storage import EncryptedVideoStore


class TemporaryMemoryMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_dir = memory._memory_dir
        memory.set_memory_dir(self.tmp.name)

    def tearDown(self):
        memory.set_memory_dir(self.previous_dir)
        self.tmp.cleanup()


class VideoModelTests(TemporaryMemoryMixin, unittest.TestCase):
    def test_request_validates_prompt_controls_seed_and_format(self):
        request = VideoRequest(prompt="  Dusk over the river \n\nwarm windows ",
                               style="Ember", aspect="9:16", quality="Draft",
                               seconds=1.5, fps=18, motion="Push in", seed="492",
                               source_image=None, format="GIF").validated()
        self.assertEqual(request.prompt, "Dusk over the river warm windows")
        self.assertEqual(request.format, "gif")
        self.assertEqual(request.frame_count, 27)
        self.assertEqual(request.seed, 492)

    def test_invalid_controls_and_missing_reference_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "1–10 seconds"):
            VideoRequest(prompt="test", seconds=12).validated()
        with self.assertRaisesRegex(ValueError, "12, 18, 24, or 30"):
            VideoRequest(prompt="test", fps=25).validated()
        with self.assertRaisesRegex(ValueError, "Choose a supported"):
            VideoRequest(prompt="test", style="AI photorealistic").validated()
        missing = Path(self.tmp.name) / "gone.png"
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            VideoRequest(prompt="test", source_image=str(missing)).validated()

    def test_encrypted_state_roundtrip_and_interrupted_jobs_do_not_restart(self):
        project = VideoProject(title="Copper river", brief="Dusk moving over water")
        job = RenderJob(request=VideoRequest(prompt="Dusk moving over water",
                                             seconds=1, fps=12, format="gif").wire(),
                        status="running", progress=.42, frames_done=5, frames_total=12)
        project.add_job(job)
        settings = AppSettings(output_dir=str(Path(self.tmp.name) / "renders"))
        store = EncryptedVideoStore()
        store.save([project], project.id, settings)
        encrypted = Path(memory._kv_path(store.KEY))
        self.assertTrue(encrypted.is_file())
        self.assertNotIn(b"Copper river", encrypted.read_bytes())

        projects, active, loaded = store.load()
        self.assertEqual(active, project.id)
        self.assertEqual(projects[0].title, "Copper river")
        self.assertEqual(projects[0].jobs[0].status, "failed")
        self.assertIn("closed before this job finished", projects[0].jobs[0].error)
        self.assertEqual(loaded.output_dir, settings.output_dir)

    def test_corrupt_state_is_not_silently_overwritten(self):
        encrypted = Path(memory._kv_path(EncryptedVideoStore.KEY))
        encrypted.parent.mkdir(parents=True, exist_ok=True)
        encrypted.write_bytes(b"corrupted readable bytes")
        with self.assertRaisesRegex(ValueError, "decrypted or read"):
            EncryptedVideoStore().load()
        self.assertEqual(encrypted.read_bytes(), b"corrupted readable bytes")

    def test_fresh_state_defaults_outputs_inside_friday_memory(self):
        projects, active, settings = EncryptedVideoStore().load()
        self.assertEqual(len(projects), 1)
        self.assertIsNone(active)
        self.assertTrue(settings.output_dir.startswith(self.tmp.name))
        self.assertTrue(settings.output_dir.endswith(os.path.join("generated", "videos")))

    def test_prompt_seed_and_export_keep_real_paths(self):
        self.assertEqual(seed_from_prompt("Soft river motion"),
                         seed_from_prompt("Soft river motion"))
        project = VideoProject(title="River", brief="Soft river motion")
        project.add_job(RenderJob(request={"prompt": "Soft river motion"},
                                  status="succeeded", output_path="/tmp/river.mp4"))
        exported = export_project_text(project)
        self.assertIn("Creative brief", exported)
        self.assertIn("/tmp/river.mp4", exported)

    def test_state_shape_rejects_invalid_data(self):
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            decode_state({"version": 2})
        with self.assertRaisesRegex(ValueError, "Projects must be a list"):
            decode_state({"version": 1, "projects": {}})


class VideoRendererTests(TemporaryMemoryMixin, unittest.TestCase):
    def request(self, format="gif", source=None):
        return VideoRequest(prompt="A copper river in slow evening light",
                            style="Mist", aspect="16:9", quality="Draft",
                            seconds=1.0, fps=12, motion="Push in", seed=123456,
                            source_image=source, format=format).validated()

    def test_gif_local_render_progress_poster_and_real_frames(self):
        output_dir = Path(self.tmp.name) / "output"
        progress = []
        output = render_video(self.request(), output_dir,
                              progress_callback=lambda done, total: progress.append((done, total)))
        self.assertEqual(output.frames_written, 12)
        self.assertEqual(progress[0], (1, 12))
        self.assertEqual(progress[-1], (12, 12))
        self.assertGreater(output.size_bytes, 5_000)
        self.assertTrue(Path(output.output_path).is_file())
        with Image.open(output.output_path) as video:
            self.assertEqual(video.n_frames, 12)
            self.assertEqual(video.size, (644, 362))
        with Image.open(output.poster_path) as poster:
            self.assertEqual(poster.size, (644, 362))

    def test_mp4_encoder_writes_complete_file_when_available(self):
        if not ffmpeg_available():
            self.skipTest("imageio-ffmpeg is not installed")
        output = render_video(self.request(format="mp4"), Path(self.tmp.name) / "mp4")
        header = Path(output.output_path).read_bytes()
        self.assertEqual(output.frames_written, 12)
        self.assertGreater(output.size_bytes, 5_000)
        self.assertEqual(header[4:8], b"ftyp")

    def test_reference_image_renders_without_upload_or_state_copy(self):
        source = Path(self.tmp.name) / "reference.png"
        image = Image.new("RGB", (480, 300), (58, 66, 70))
        image.paste((128, 145, 134), (0, 160, 480, 300))
        image.paste((187, 164, 128), (70, 80, 210, 190))
        image.save(source)
        output = render_video(self.request(source=str(source), format="gif"),
                              Path(self.tmp.name) / "reference-output")
        self.assertEqual(output.frames_written, 12)
        self.assertTrue(Path(output.output_path).is_file())
        self.assertFalse((Path(self.tmp.name) / "reference-output" / source.name).exists())

    def test_cancel_before_start_leaves_no_partial_file(self):
        cancel = threading.Event()
        cancel.set()
        output_dir = Path(self.tmp.name) / "cancelled"
        with self.assertRaises(RenderCancelled):
            render_video(self.request(format="gif"), output_dir, cancel_event=cancel)
        self.assertFalse(list(output_dir.glob("*.gif")))
        self.assertFalse(list(output_dir.glob("*.tmp.*")))

    def test_live_preview_still_matches_supported_targets(self):
        still = render_still(self.request(), (320, 180))
        self.assertEqual((still.mode, still.size), ("RGB", (320, 180)))
        self.assertEqual(target_size("9:16", "Draft"), (430, 762))
        encoded = encode_state([VideoProject()], None)
        self.assertEqual(encoded["version"], 1)
        self.assertEqual(encoded["active_id"], None)
        self.assertEqual(len(encoded["projects"]), 1)


if __name__ == "__main__":
    unittest.main()

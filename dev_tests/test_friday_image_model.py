"""Unit tests for Friday Image Generation data, local render, and storage.

Run: python -m unittest dev_tests.test_friday_image_model -v
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
from friday_image.model import (AppSettings, ImageJob, ImageProject, ImageRequest,
                                decode_state, encode_state, export_project_text,
                                seed_from_prompt)
from friday_image.renderer import (RenderCancelled, render_image, render_still,
                                   target_size)
from friday_image.storage import EncryptedImageStore


class TemporaryMemoryMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_dir = memory._memory_dir
        memory.set_memory_dir(self.tmp.name)

    def tearDown(self):
        memory.set_memory_dir(self.previous_dir)
        self.tmp.cleanup()


class ImageModelTests(TemporaryMemoryMixin, unittest.TestCase):
    def test_request_validates_prompt_controls_seed_and_format(self):
        request = ImageRequest(prompt="  An evening garden \nheld in glass ",
                               style="Glass", aspect="4:3", quality="Draft",
                               composition="Center", detail="56", seed="492",
                               format="JPEG").validated()
        self.assertEqual(request.prompt, "An evening garden held in glass")
        self.assertEqual(request.format, "jpg")
        self.assertEqual(request.seed, 492)
        self.assertEqual(request.steps_total, 13)

    def test_invalid_controls_and_missing_reference_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "supported visual style"):
            ImageRequest(prompt="test", style="Photoreal AI").validated()
        with self.assertRaisesRegex(ValueError, "supported aspect"):
            ImageRequest(prompt="test", aspect="2:1").validated()
        with self.assertRaisesRegex(ValueError, "between 10 and 100"):
            ImageRequest(prompt="test", detail=101).validated()
        with self.assertRaisesRegex(ValueError, "PNG, JPEG, or WebP"):
            ImageRequest(prompt="test", format="bmp").validated()
        missing = Path(self.tmp.name) / "gone.png"
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            ImageRequest(prompt="test", source_image=str(missing)).validated()

    def test_encrypted_state_roundtrip_and_interrupted_jobs_do_not_restart(self):
        project = ImageProject(title="Evening garden", brief="A garden held in glass")
        job = ImageJob(request=ImageRequest(prompt="A garden held in glass",
                                            detail=10, format="png").wire(),
                       status="running", progress=.42, steps_done=4,
                       steps_total=8)
        project.add_job(job)
        settings = AppSettings(output_dir=str(Path(self.tmp.name) / "renders"))
        store = EncryptedImageStore()
        store.save([project], project.id, settings)
        encrypted = Path(memory._kv_path(store.KEY))
        self.assertTrue(encrypted.is_file())
        self.assertNotIn(b"Evening garden", encrypted.read_bytes())

        projects, active, loaded = store.load()
        self.assertEqual(active, project.id)
        self.assertEqual(projects[0].title, "Evening garden")
        self.assertEqual(projects[0].jobs[0].status, "failed")
        self.assertIn("closed before this job finished", projects[0].jobs[0].error)
        self.assertEqual(loaded.output_dir, settings.output_dir)

    def test_corrupt_state_is_not_silently_overwritten(self):
        encrypted = Path(memory._kv_path(EncryptedImageStore.KEY))
        encrypted.parent.mkdir(parents=True, exist_ok=True)
        encrypted.write_bytes(b"corrupted readable bytes")
        with self.assertRaisesRegex(ValueError, "decrypted or read"):
            EncryptedImageStore().load()
        self.assertEqual(encrypted.read_bytes(), b"corrupted readable bytes")

    def test_fresh_state_defaults_outputs_inside_friday_memory(self):
        projects, active, settings = EncryptedImageStore().load()
        self.assertEqual(len(projects), 1)
        self.assertIsNone(active)
        self.assertTrue(settings.output_dir.startswith(self.tmp.name))
        self.assertTrue(settings.output_dir.endswith(os.path.join("generated", "images")))

    def test_prompt_seed_state_shape_and_export_keep_real_paths(self):
        self.assertEqual(seed_from_prompt("Soft garden art"),
                         seed_from_prompt("Soft garden art"))
        project = ImageProject(title="Garden", brief="Soft garden art")
        project.add_job(ImageJob(request={"prompt": "Soft garden art"},
                                 status="succeeded", output_path="/tmp/garden.png"))
        exported = export_project_text(project)
        self.assertIn("Creative brief", exported)
        self.assertIn("/tmp/garden.png", exported)
        encoded = encode_state([ImageProject()], None)
        self.assertEqual(encoded["version"], 1)
        self.assertEqual(encoded["active_id"], None)
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            decode_state({"version": 2})
        with self.assertRaisesRegex(ValueError, "Projects must be a list"):
            decode_state({"version": 1, "projects": {}})


class ImageRendererTests(TemporaryMemoryMixin, unittest.TestCase):
    def request(self, format="png", source=None, **overrides):
        values = dict(prompt="An evening garden held in glass",
                      style="Glass", aspect="4:3", quality="Draft",
                      composition="Center", detail=20, seed=123456,
                      source_image=source, format=format)
        values.update(overrides)
        return ImageRequest(**values).validated()

    def test_png_render_progress_thumbnail_and_real_dimensions(self):
        output_dir = Path(self.tmp.name) / "output"
        progress = []
        request = self.request(format="png", detail=10)
        output = render_image(request, output_dir,
                              progress_callback=lambda done, total: progress.append((done, total)))
        self.assertEqual(output.detail_steps, request.steps_total)
        self.assertEqual(progress[0], (1, request.steps_total))
        self.assertEqual(progress[-1], (request.steps_total, request.steps_total))
        self.assertGreater(output.size_bytes, 1_000)
        self.assertTrue(Path(output.output_path).is_file())
        with Image.open(output.output_path) as image:
            self.assertEqual(image.size, (694, 522))
        self.assertTrue(Path(output.thumbnail_path).is_file())
        # The output folder keeps exactly one file per prompt; queue
        # thumbnails live in Friday's private cache instead.
        self.assertNotEqual(Path(output.thumbnail_path).parent.resolve(),
                            Path(output_dir).resolve())
        self.assertFalse(list(Path(output_dir).glob("*-preview.png")))

    def test_jpeg_and_webp_encoders_write_complete_files(self):
        for fmt in ("jpg", "webp"):
            output = render_image(self.request(format=fmt, detail=10),
                                  Path(self.tmp.name) / fmt)
            path = Path(output.output_path)
            self.assertTrue(path.is_file())
            self.assertGreater(output.size_bytes, 100)
            self.assertEqual(path.suffix, "." + fmt)
            self.assertTrue(Path(output.thumbnail_path).is_file())

    def test_reference_image_renders_without_upload_or_state_copy(self):
        source = Path(self.tmp.name) / "reference.png"
        image = Image.new("RGB", (420, 320), (58, 66, 70))
        image.paste((128, 145, 134), (0, 130, 420, 320))
        image.paste((187, 164, 128), (70, 80, 210, 190))
        image.save(source)
        output = render_image(self.request(source=str(source), format="png", detail=10),
                              Path(self.tmp.name) / "reference-output")
        self.assertTrue(Path(output.output_path).is_file())
        self.assertFalse((Path(output.output_path).parent / source.name).exists())

    def test_cancel_before_start_leaves_no_partial_file(self):
        cancel = threading.Event()
        cancel.set()
        output_dir = Path(self.tmp.name) / "cancelled"
        with self.assertRaises(RenderCancelled):
            render_image(self.request(format="png", detail=100), output_dir,
                         cancel_event=cancel)
        self.assertFalse(list(output_dir.glob("*.png")))
        self.assertFalse(list(output_dir.glob("*.tmp*")))

    def test_live_preview_and_all_supported_aspect_targets(self):
        still = render_still(self.request(detail=10), (320, 240))
        self.assertEqual((still.mode, still.size), ("RGB", (320, 240)))
        self.assertEqual(target_size("16:9", "Studio"), (1280, 720))
        for aspect in ("16:9", "4:3", "1:1", "3:4", "9:16"):
            width, height = target_size(aspect, "Draft")
            self.assertEqual(width % 2, 0)
            self.assertEqual(height % 2, 0)
            self.assertGreaterEqual(max(width, height), 440)

    def test_same_seed_live_preview_is_deterministic(self):
        first = render_still(self.request(detail=10, seed=80), (160, 120))
        second = render_still(self.request(detail=10, seed=80), (160, 120))
        changed = render_still(self.request(detail=10, seed=81), (160, 120))
        self.assertEqual(first.tobytes(), second.tobytes())
        self.assertNotEqual(first.tobytes(), changed.tobytes())


if __name__ == "__main__":
    unittest.main()

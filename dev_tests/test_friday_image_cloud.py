"""Tests for Friday Image's cloud provider adapter and Settings wiring.

Covers friday_image/providers.py (Cloudflare Workers AI backend) and
image_backend_resolver.py (Settings -> backend selection), including
URL normalization, request payloads, both provider response shapes,
error/cancellation honesty, and the local-renderer fallback contract.
All network I/O is faked through an injected urlopen; nothing real is
called and no credentials are used.
"""
import base64
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from friday_image.model import ImageRequest
from friday_image.providers import (CloudflareImageBackend, DEFAULT_CF_MODEL,
                                    DEFAULT_STABILITY_MODEL,
                                    IdeogramImageBackend,
                                    MisconfiguredProviderBackend,
                                    NoProviderBackend, OpenAIImageBackend,
                                    STABILITY_BASE_URL, StabilityImageBackend,
                                    build_endpoint, build_image_backend,
                                    cloud_num_steps, cloud_payload, cloud_size,
                                    detect_image_provider,
                                    normalize_cloudflare_base)
from friday_image.renderer import RenderCancelled, RenderError
from image_backend_resolver import (image_engine_catalog,
                                    resolve_image_backend)

ACCOUNT_URL = "https://api.cloudflare.com/client/v4/accounts/deadbeef123"


def _png_bytes(size=(32, 24), color=(120, 90, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, data: bytes, content_type: str):
        self._data = data
        self.headers = type("H", (), {"get_content_type": lambda _self: content_type})()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _urlopen_json(payload: dict, captured: dict):
    def urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["auth"] = request.headers.get("Authorization")
        return _FakeResponse(json.dumps(payload).encode("utf-8"), "application/json")
    return urlopen


def _urlopen_image(captured: dict):
    def urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(_png_bytes(), "image/png")
    return urlopen


def _request(**overrides):
    values = dict(prompt="a quiet garden at dusk", style="Mist", aspect="4:3",
                  quality="Draft", composition="Horizon", detail=42.0,
                  seed=77, format="png")
    values.update(overrides)
    return ImageRequest(**values)


def _entry(**overrides):
    values = dict(name="My Cloudflare", model=DEFAULT_CF_MODEL,
                  api_key="cf-token-abc", base_url=ACCOUNT_URL,
                  check_kind="cloudflare_image", builtin=False)
    values.update(overrides)
    return values


class EndpointTests(unittest.TestCase):
    def test_account_root_gets_run_suffix(self):
        self.assertEqual(
            build_endpoint(ACCOUNT_URL, DEFAULT_CF_MODEL),
            f"{ACCOUNT_URL}/ai/run/{DEFAULT_CF_MODEL}")

    def test_ai_run_base_gets_model_appended(self):
        self.assertEqual(
            build_endpoint(f"{ACCOUNT_URL}/ai/run/", "@cf/stabilityai/stable-diffusion-xl-base-1.0"),
            f"{ACCOUNT_URL}/ai/run/@cf/stabilityai/stable-diffusion-xl-base-1.0")

    def test_full_model_url_used_verbatim(self):
        full = f"{ACCOUNT_URL}/ai/run/@cf/lykon/dreamshaper-8-lcm"
        self.assertEqual(build_endpoint(full, "@cf/other/model"), full)

    def test_missing_base_url_explains_setup(self):
        with self.assertRaises(ValueError) as ctx:
            build_endpoint("", DEFAULT_CF_MODEL)
        self.assertIn("accounts/<ACCOUNT_ID>", str(ctx.exception))

    def test_unrelated_url_is_rejected_honestly(self):
        with self.assertRaises(ValueError) as ctx:
            build_endpoint("https://example.com/nope", DEFAULT_CF_MODEL)
        self.assertIn("does not look like", str(ctx.exception))

    def test_dashboard_url_is_auto_normalized(self):
        dash = "https://dash.cloudflare.com/09029e070a8897be5a1b8a4fb93a681f/api-tokens"
        self.assertEqual(
            normalize_cloudflare_base(dash),
            "https://api.cloudflare.com/client/v4/accounts/"
            "09029e070a8897be5a1b8a4fb93a681f")
        self.assertEqual(
            build_endpoint(dash, DEFAULT_CF_MODEL),
            "https://api.cloudflare.com/client/v4/accounts/"
            f"09029e070a8897be5a1b8a4fb93a681f/ai/run/{DEFAULT_CF_MODEL}")

    def test_dashboard_url_without_account_segment_explains(self):
        with self.assertRaises(ValueError) as ctx:
            build_endpoint("https://dash.cloudflare.com/", DEFAULT_CF_MODEL)
        self.assertIn("account ID segment", str(ctx.exception))

    def test_api_urls_pass_normalization_unchanged(self):
        self.assertEqual(normalize_cloudflare_base(ACCOUNT_URL), ACCOUNT_URL)
        self.assertEqual(normalize_cloudflare_base(""), "")


class PayloadTests(unittest.TestCase):
    def test_flux_payload_uses_steps_cap(self):
        payload = cloud_payload(_request(quality="Studio"), DEFAULT_CF_MODEL)
        self.assertEqual(payload["steps"], 8)
        self.assertNotIn("width", payload)
        payload = cloud_payload(_request(quality="Draft"), DEFAULT_CF_MODEL)
        self.assertEqual(payload["steps"], 4)

    def test_sdxl_payload_has_size_seed_steps(self):
        payload = cloud_payload(
            _request(quality="Studio", aspect="16:9"),
            "@cf/stabilityai/stable-diffusion-xl-base-1.0")
        self.assertEqual(payload["width"], 1280)
        self.assertEqual(payload["height"], 720)
        self.assertEqual(payload["num_steps"], 24)
        self.assertEqual(payload["seed"], 77)

    def test_cloud_size_is_provider_legal(self):
        for aspect in ("16:9", "4:3", "1:1", "3:4", "9:16"):
            width, height = cloud_size(aspect, "Draft")
            self.assertEqual(width % 8, 0, aspect)
            self.assertEqual(height % 8, 0, aspect)
            self.assertGreaterEqual(width, 256)
            self.assertGreaterEqual(height, 256)
            self.assertLessEqual(max(width, height), 2048)

    def test_step_mapping(self):
        self.assertEqual(cloud_num_steps("Studio", "flux-1-schnell"), 8)
        self.assertEqual(cloud_num_steps("Draft", "flux-1-schnell"), 4)
        self.assertEqual(cloud_num_steps("Studio", "sdxl"), 24)


class CloudBackendRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output_dir = Path(self.tmp.name)

    def _backend(self, urlopen, **kwargs):
        kwargs.setdefault("api_key", "cf-token-abc")
        kwargs.setdefault("base_url", ACCOUNT_URL)
        return CloudflareImageBackend(urlopen=urlopen, **kwargs)

    def test_json_base64_response_becomes_a_real_file(self):
        captured = {}
        payload = {"success": True, "errors": [], "result": {"image": base64.b64encode(_png_bytes()).decode("ascii")}}
        backend = self._backend(_urlopen_json(payload, captured))
        progress = []
        result = backend.render(_request(format="png"), self.output_dir,
                                progress_callback=lambda d, t: progress.append((d, t)))
        self.assertTrue(Path(result.output_path).is_file())
        self.assertTrue(Path(result.thumbnail_path).is_file())
        self.assertGreater(result.size_bytes, 0)
        self.assertEqual(result.format, "png")
        self.assertEqual(progress[-1], (6, 6))
        # One file per prompt in the user's folder; thumbnails are cached
        # privately under ~/.friday, never beside the image.
        self.assertNotEqual(Path(result.thumbnail_path).parent.resolve(),
                            self.output_dir.resolve())
        self.assertFalse(list(self.output_dir.glob("*-preview.png")))
        self.assertEqual(captured["auth"], "Bearer cf-token-abc")
        self.assertEqual(captured["url"], f"{ACCOUNT_URL}/ai/run/{DEFAULT_CF_MODEL}")
        self.assertEqual(captured["body"]["prompt"], "a quiet garden at dusk")

    def test_binary_image_response_is_accepted_and_reencoded(self):
        captured = {}
        backend = self._backend(_urlopen_image(captured))
        result = backend.render(_request(format="webp"), self.output_dir)
        self.assertTrue(result.output_path.endswith(".webp"))
        with Image.open(result.output_path) as saved:
            self.assertEqual(saved.format, "WEBP")

    def test_provider_json_error_surfaces_its_message(self):
        captured = {}
        payload = {"success": False, "errors": [{"message": "prompt blocked"}]}
        backend = self._backend(_urlopen_json(payload, captured))
        with self.assertRaises(RenderError) as ctx:
            backend.render(_request(), self.output_dir)
        self.assertIn("prompt blocked", str(ctx.exception))

    def test_response_without_image_is_honest_failure(self):
        captured = {}
        backend = self._backend(_urlopen_json({"success": True, "result": {}}, captured))
        with self.assertRaises(RenderError) as ctx:
            backend.render(_request(), self.output_dir)
        self.assertIn("without an image payload", str(ctx.exception))

    def test_http_401_explains_token_problem(self):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", hdrs=None,
                fp=io.BytesIO(json.dumps({"errors": [{"message": "bad token"}]}).encode()))
        backend = self._backend(urlopen)
        with self.assertRaises(RenderError) as ctx:
            backend.render(_request(), self.output_dir)
        self.assertIn("401", str(ctx.exception))
        self.assertIn("bad token", str(ctx.exception))

    def test_network_failure_is_a_clear_render_error(self):
        def urlopen(request, timeout=None):
            raise urllib.error.URLError("offline")
        backend = self._backend(urlopen)
        with self.assertRaises(RenderError) as ctx:
            backend.render(_request(), self.output_dir)
        self.assertIn("Couldn't reach", str(ctx.exception))

    def test_cancel_before_request_leaves_no_files(self):
        cancel = threading.Event()
        cancel.set()
        called = {"count": 0}

        def urlopen(request, timeout=None):
            called["count"] += 1
            return _FakeResponse(_png_bytes(), "image/png")

        backend = self._backend(urlopen)
        with self.assertRaises(RenderCancelled):
            backend.render(_request(), self.output_dir, cancel_event=cancel)
        self.assertEqual(called["count"], 0)
        self.assertFalse(list(self.output_dir.glob("*.png")))

    def test_empty_prompt_is_rejected_before_any_call(self):
        called = {"count": 0}

        def urlopen(request, timeout=None):
            called["count"] += 1
            return _FakeResponse(_png_bytes(), "image/png")

        backend = self._backend(urlopen)
        with self.assertRaises(RenderError) as ctx:
            backend.render(_request(prompt="   "), self.output_dir)
        self.assertIn("written brief", str(ctx.exception))
        self.assertEqual(called["count"], 0)

    def test_missing_credentials_raise_guidance(self):
        with self.assertRaises(ValueError) as ctx:
            CloudflareImageBackend(api_key="", base_url=ACCOUNT_URL)
        self.assertIn("API key field is empty", str(ctx.exception))
        with self.assertRaises(ValueError):
            CloudflareImageBackend.from_entry(_entry(api_key="token", base_url=""))

    def test_badge_and_description_are_honest(self):
        backend = self._backend(_urlopen_image({}))
        self.assertIn("cloud", backend.badge)
        self.assertEqual(backend.provider_kind, "cloud")
        self.assertIn("HTTPS", backend.description)


class MisconfiguredBackendTests(unittest.TestCase):
    def test_render_raises_the_real_problem(self):
        backend = MisconfiguredProviderBackend("My Cloudflare", "base URL missing")
        self.assertEqual(backend.provider_kind, "cloud")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RenderError) as ctx:
                backend.render(_request(), Path(tmp))
        self.assertIn("My Cloudflare", str(ctx.exception))
        self.assertIn("base URL missing", str(ctx.exception))


class ResolverTests(unittest.TestCase):
    def test_empty_settings_give_the_honest_no_provider_state(self):
        for data in ({}, {"Image Generation": []}):
            backend = resolve_image_backend(data)
            self.assertIsInstance(backend, NoProviderBackend)
            self.assertFalse(backend.accent_badge)
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(RenderError) as ctx:
                    backend.render(_request(), Path(tmp))
            self.assertIn("Settings", str(ctx.exception))

    def test_keyed_entry_becomes_the_cloud_backend(self):
        backend = resolve_image_backend({"Image Generation": [_entry()]})
        self.assertIsInstance(backend, CloudflareImageBackend)
        self.assertEqual(
            backend.endpoint,
            f"{ACCOUNT_URL}/ai/run/{DEFAULT_CF_MODEL}")

    def test_entry_without_key_is_skipped(self):
        backend = resolve_image_backend(
            {"Image Generation": [_entry(api_key="  ")]})
        self.assertIsInstance(backend, NoProviderBackend)

    def test_broken_keyed_entry_surfaces_as_setup_needed(self):
        backend = resolve_image_backend(
            {"Image Generation": [_entry(base_url="https://nope.example") ]})
        self.assertIsInstance(backend, MisconfiguredProviderBackend)
        self.assertIn("My Cloudflare", backend.entry_name)

    def test_first_usable_keyed_entry_wins(self):
        backend = resolve_image_backend({"Image Generation": [
            _entry(api_key=""),
            _entry(name="Second", api_key="tok-2"),
        ]})
        self.assertIsInstance(backend, CloudflareImageBackend)
        self.assertEqual(backend.api_key, "tok-2")


class AnyProviderTests(unittest.TestCase):
    """OpenAI-compatible and Ideogram entries (the user's real entries)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output_dir = Path(self.tmp.name)

    def test_detects_all_supported_hosts(self):
        cases = {
            "https://api.cloudflare.com/client/v4/accounts/x": "cloudflare",
            "https://api.stability.ai": "stability",
            "https://api.openai.com/v1": "openai",
            "https://openrouter.ai/api/v1": "openai",
            "https://api.ideogram.ai": "ideogram",
        }
        for base, expected in cases.items():
            self.assertEqual(detect_image_provider({"base_url": base}),
                             expected, base)

    def test_openai_backend_endpoint_and_payload(self):
        backend = OpenAIImageBackend(api_key="sk-x", base_url="https://api.openai.com/v1")
        self.assertEqual(backend.endpoint,
                         "https://api.openai.com/v1/images/generations")
        body = backend.payload(_request(aspect="16:9"))
        self.assertEqual(body["size"], "1536x1024")
        self.assertEqual(body["model"], "gpt-image-1")
        dall = OpenAIImageBackend(api_key="k", base_url="https://api.openai.com/v1",
                                  model="dall-e-3")
        self.assertEqual(dall.payload(_request(aspect="9:16"))["size"], "1024x1792")
        self.assertEqual(dall.payload(_request())["response_format"], "b64_json")
        with self.assertRaises(ValueError):
            OpenAIImageBackend(api_key="k", base_url="https://not-v1.example")

    def test_openai_b64_render_round_trip(self):
        captured = {}
        payload = {"data": [{"b64_json": base64.b64encode(_png_bytes()).decode("ascii")}]}
        backend = OpenAIImageBackend(api_key="sk-x",
                                     base_url="https://api.openai.com/v1",
                                     urlopen=_urlopen_json(payload, captured))
        result = backend.render(_request(), self.output_dir)
        self.assertTrue(Path(result.output_path).is_file())
        self.assertEqual(captured["auth"], "Bearer sk-x")

    def test_openai_error_envelope_surfaces_message(self):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", hdrs=None,
                fp=io.BytesIO(json.dumps({"error": {"message": "bad key"}}).encode()))
        backend = OpenAIImageBackend(api_key="sk-x",
                                     base_url="https://api.openai.com/v1",
                                     urlopen=urlopen)
        with self.assertRaises(RenderError) as ctx:
            backend.render(_request(), self.output_dir)
        self.assertIn("bad key", str(ctx.exception))

    def test_ideogram_master_flow_downloads_url_result(self):
        calls = []
        payload = {"data": [{"url": "https://cdn.ideogram.ai/x.png"}]}

        def urlopen(request, timeout=None):
            calls.append(request.full_url if hasattr(request, "full_url") else request)
            if len(calls) == 1:
                return _FakeResponse(json.dumps(payload).encode(), "application/json")
            return _FakeResponse(_png_bytes(), "image/png")

        captured_headers = {}

        def urlopen_observing(request, timeout=None):
            if hasattr(request, "headers"):
                captured_headers.update(request.headers)
            return urlopen(request, timeout)

        backend = IdeogramImageBackend(api_key="idg-x",
                                       base_url="https://api.ideogram.ai",
                                       urlopen=urlopen_observing)
        result = backend.render(_request(aspect="4:3"), self.output_dir)
        self.assertTrue(Path(result.output_path).is_file())
        body = backend.payload(_request(aspect="4:3"))
        self.assertEqual(body["aspect_ratio"], "4x3")
        self.assertEqual(body["model"], "V_3")
        self.assertEqual(backend.headers(), {"Api-Key": "idg-x"})
        self.assertIn("ideogram-v3/generate", backend.endpoint)
        self.assertEqual(calls[1], "https://cdn.ideogram.ai/x.png")

    def test_resolver_routes_each_provider_entry(self):
        openai = dict(_stability_entry(name="Openai", model="gpt-image-1",
                                       api_key="sk-x",
                                       base_url="https://api.openai.com/v1"))
        ideogram = dict(_stability_entry(name="Ideogram", model="V_3",
                                         api_key="idg-x",
                                         base_url="https://api.ideogram.ai"))
        openai["id"] = "e-openai"
        ideogram["id"] = "e-ideogram"
        cloudflare = _entry()
        backends = resolve_image_backend(
            {"Image Generation": [openai, ideogram, cloudflare]})
        self.assertIsInstance(backends, OpenAIImageBackend)
        picked = resolve_image_backend(
            {"Image Generation": [openai, ideogram]},
            entry_id=ideogram["id"])
        self.assertIsInstance(picked, IdeogramImageBackend)
        catalog = image_engine_catalog(
            {"Image Generation": [openai, ideogram, cloudflare]})
        self.assertTrue(all(item["usable"] for item in catalog))


class SettingsStoreKindTests(unittest.TestCase):
    def test_add_model_marks_image_entries_as_real_cloudflare_checks(self):
        """add_model must pick the real check kind for Image Generation
        (the genuinely callable region) and generic_http elsewhere."""
        import inspect
        try:
            import settings_store
        except Exception as exc:  # noqa: BLE001 -- e.g. cryptography absent
            self.skipTest(f"settings_store not importable here: {exc}")
        source = inspect.getsource(settings_store.add_model)
        self.assertIn("cloudflare_image", source)
        self.assertIn("generic_http", source)
        self.assertIn('"Image Generation"', source)


def _stability_entry(**overrides):
    values = dict(name="STABILITY", model=DEFAULT_STABILITY_MODEL,
                  api_key="sk-stability-demo", base_url=STABILITY_BASE_URL,
                  check_kind="stability_image", builtin=False)
    values.update(overrides)
    return values


class ProviderDetectionTests(unittest.TestCase):
    def test_hosts_detect_their_providers(self):
        self.assertEqual(detect_image_provider(
            {"base_url": "https://api.cloudflare.com/client/v4/accounts/x"}),
            "cloudflare")
        self.assertEqual(detect_image_provider(
            {"base_url": "https://api.stability.ai"}), "stability")
        self.assertIsNone(detect_image_provider(
            {"base_url": "https://example.com"}))
        self.assertIsNone(detect_image_provider({"base_url": ""}))

    def test_build_dispatches_per_provider(self):
        self.assertIsInstance(build_image_backend(_entry()),
                              CloudflareImageBackend)
        self.assertIsInstance(build_image_backend(_stability_entry()),
                              StabilityImageBackend)
        with self.assertRaises(ValueError) as ctx:
            build_image_backend(_stability_entry(base_url="https://nope.example"))
        self.assertIn("supported image provider", str(ctx.exception))


class StabilityBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output_dir = Path(self.tmp.name)

    def _backend(self, urlopen, **kwargs):
        kwargs.setdefault("api_key", "sk-stability-demo")
        kwargs.setdefault("base_url", STABILITY_BASE_URL)
        return StabilityImageBackend(urlopen=urlopen, **kwargs)

    def test_endpoint_mapping_per_model_family(self):
        base = dict(api_key="k", base_url=STABILITY_BASE_URL)
        self.assertTrue(StabilityImageBackend(model="sd3.5-flash", **base)
                        .endpoint.endswith("/generate/sd3"))
        self.assertTrue(StabilityImageBackend(model="ultra", **base)
                        .endpoint.endswith("/generate/ultra"))
        self.assertTrue(StabilityImageBackend(model="core", **base)
                        .endpoint.endswith("/generate/core"))
        self.assertTrue(StabilityImageBackend(model="weird-name", **base)
                        .endpoint.endswith("/generate/core"))

    def test_account_page_url_is_rejected_with_fix_guidance(self):
        with self.assertRaises(ValueError) as ctx:
            StabilityImageBackend(
                api_key="k", base_url="https://platform.stability.ai/account/keys")
        self.assertIn("api.stability.ai", str(ctx.exception))

    def test_multipart_render_success_saves_real_files(self):
        captured = {}

        def urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["content_type"] = request.headers.get("Content-type") or \
                request.headers.get("Content-Type")
            captured["body"] = request.data.decode("utf-8")
            captured["auth"] = request.headers.get("Authorization")
            return _FakeResponse(_png_bytes(), "image/png")

        backend = self._backend(urlopen)
        result = backend.render(_request(format="jpg", aspect="4:3"),
                                self.output_dir)
        self.assertTrue(Path(result.output_path).is_file())
        self.assertTrue(result.output_path.endswith(".jpg"))
        body = captured["body"]
        self.assertIn('name="prompt"', body)
        self.assertIn("a quiet garden at dusk", body)
        self.assertIn('name="aspect_ratio"', body)
        self.assertIn("3:2", body)  # 4:3 mapped onto Stability's set
        self.assertIn('name="output_format"', body)
        self.assertIn("jpeg", body)
        self.assertIn('name="seed"', body)
        self.assertIn('name="model"', body)  # sd3.5-flash endpoint needs it
        self.assertIn(DEFAULT_STABILITY_MODEL, body)
        self.assertIn("multipart/form-data; boundary=", captured["content_type"])
        self.assertEqual(captured["auth"], "Bearer sk-stability-demo")
        self.assertTrue(backend.endpoint.endswith("/generate/sd3"))

    def test_core_model_omits_model_field(self):
        captured = {}

        def urlopen(request, timeout=None):
            captured["body"] = request.data.decode("utf-8")
            return _FakeResponse(_png_bytes(), "image/webp")

        backend = self._backend(urlopen, model="core")
        backend.render(_request(), self.output_dir)
        self.assertNotIn('name="model"', captured["body"])
        self.assertTrue(backend.endpoint.endswith("/generate/core"))

    def test_json_error_envelope_surfaces_message(self):
        payload = {"id": "x", "name": "bad_request",
                   "errors": ["prompt is flagged"]}

        def urlopen(request, timeout=None):
            return _FakeResponse(json.dumps(payload).encode("utf-8"),
                                 "application/json")

        backend = self._backend(urlopen)
        with self.assertRaises(RenderError) as ctx:
            backend.render(_request(), self.output_dir)
        self.assertIn("prompt is flagged", str(ctx.exception))

    def test_http_402_explains_credits(self):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 402, "Payment Required", hdrs=None,
                fp=io.BytesIO(json.dumps({"errors": []}).encode()))
        backend = self._backend(urlopen)
        with self.assertRaises(RenderError) as ctx:
            backend.render(_request(), self.output_dir)
        self.assertIn("402", str(ctx.exception))
        self.assertIn("credits", str(ctx.exception))

    def test_cancel_before_request_leaves_no_files(self):
        cancel = threading.Event()
        cancel.set()
        called = {"count": 0}

        def urlopen(request, timeout=None):
            called["count"] += 1
            return _FakeResponse(_png_bytes(), "image/png")

        backend = self._backend(urlopen)
        with self.assertRaises(RenderCancelled):
            backend.render(_request(), self.output_dir, cancel_event=cancel)
        self.assertEqual(called["count"], 0)
        self.assertFalse(list(self.output_dir.glob("*")))


class ResolverPriorityTests(unittest.TestCase):
    def test_broken_first_entry_does_not_block_valid_second(self):
        backend = resolve_image_backend({"Image Generation": [
            _stability_entry(base_url="https://platform.stability.ai/account/keys"),
            _entry(),
        ]})
        self.assertIsInstance(backend, CloudflareImageBackend)

    def test_stability_entry_resolves_to_stability_backend(self):
        backend = resolve_image_backend({"Image Generation": [_stability_entry()]})
        self.assertIsInstance(backend, StabilityImageBackend)
        self.assertEqual(backend.model, DEFAULT_STABILITY_MODEL)

    def test_all_broken_keyed_entries_show_the_first_problem(self):
        backend = resolve_image_backend({"Image Generation": [
            _stability_entry(name="FIRST BAD",
                             base_url="https://platform.stability.ai/account/keys"),
            _entry(base_url="https://also-bad.example"),
        ]})
        self.assertIsInstance(backend, MisconfiguredProviderBackend)
        self.assertEqual(backend.entry_name, "FIRST BAD")
        self.assertIn("api.stability.ai", backend.problem)

    def test_unknown_host_keyed_entry_is_explained_not_guessed(self):
        resolved = resolve_image_backend({"Image Generation": [
            _stability_entry(base_url="https://mystery-provider.example"),
        ]})
        self.assertIsInstance(resolved, MisconfiguredProviderBackend)
        self.assertIn("supported image provider", resolved.problem)


class EnginePickerTests(unittest.TestCase):
    """The panel's engine dropdown: persistable per-entry selection."""

    def test_specific_entry_id_resolves_to_that_entry(self):
        stab = _stability_entry()
        stab["id"] = "e-stab"
        cf = _entry()
        cf["id"] = "e-cf"
        backend = resolve_image_backend(
            {"Image Generation": [stab, cf]}, entry_id="e-cf")
        self.assertIsInstance(backend, CloudflareImageBackend)
        backend = resolve_image_backend(
            {"Image Generation": [stab, cf]}, entry_id="e-stab")
        self.assertIsInstance(backend, StabilityImageBackend)

    def test_legacy_local_choice_falls_back_to_automatic(self):
        # Saved picker state from before cloud-only mode must not resurrect
        # the removed local renderer; it maps onto automatic selection.
        cf = _entry()
        cf["id"] = "e-cf"
        backend = resolve_image_backend(
            {"Image Generation": [cf]}, entry_id="local")
        self.assertIsInstance(backend, CloudflareImageBackend)

    def test_broken_chosen_entry_surfaces_instead_of_swapping(self):
        bad = _stability_entry(name="MY BROKEN",
                               base_url="https://platform.stability.ai/account/keys")
        bad["id"] = "e-bad"
        cf = _entry()
        cf["id"] = "e-cf"
        backend = resolve_image_backend(
            {"Image Generation": [bad, cf]}, entry_id="e-bad")
        self.assertIsInstance(backend, MisconfiguredProviderBackend)
        self.assertEqual(backend.entry_name, "MY BROKEN")

    def test_vanished_choice_falls_back_to_automatic(self):
        cf = _entry()
        cf["id"] = "e-cf"
        backend = resolve_image_backend(
            {"Image Generation": [cf]}, entry_id="e-deleted")
        self.assertIsInstance(backend, CloudflareImageBackend)

    def test_catalog_labels_and_verdicts(self):
        stab = _stability_entry(base_url="https://platform.stability.ai/account/keys")
        stab["id"] = "e-stab"
        cf = _entry()
        cf["id"] = "e-cf"
        no_key = _entry(api_key="")
        catalog = image_engine_catalog(
            {"Image Generation": [stab, cf, no_key]})
        by_id = {item["id"]: item for item in catalog}
        self.assertTrue(by_id["e-cf"]["usable"])
        self.assertIn("flux-1-schnell", by_id["e-cf"]["label"])
        self.assertFalse(by_id["e-stab"]["usable"])
        self.assertIn("needs attention", by_id["e-stab"]["label"])
        self.assertIn("api.stability.ai", by_id["e-stab"]["problem"])
        self.assertNotIn("e-no-key", by_id)

    def test_engine_setting_roundtrips_through_state(self):
        from friday_image.model import AppSettings, decode_state, encode_state
        state = encode_state([], None, AppSettings(engine="e-cf"))
        _projects, _active, settings = decode_state(state)
        self.assertEqual(settings.engine, "e-cf")
        # Records saved before the picker existed default to automatic.
        _projects, _active, settings = decode_state({"version": 1, "projects": []})
        self.assertEqual(settings.engine, "auto")


if __name__ == "__main__":
    unittest.main()

"""model_kinds: sorting provider models into Friday's tabs, plus the fetch
and check plumbing that feeds it. No network: the fixtures below are real
items, trimmed, copied from each provider's live catalog (Nov 2026).

Run: python dev_tests/test_model_kinds.py
"""
from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FRIDAY_MEMORY_PATH", tempfile.mkdtemp(prefix="friday-kinds-"))

import model_kinds as mk  # noqa: E402
import settings_checks  # noqa: E402
import settings_store  # noqa: E402


def kind(item):
    return mk.classify(item)[0]


class ProviderLabelTests(unittest.TestCase):
    """Each provider's own shape, as its catalog really sends it."""

    def test_openrouter_output_modalities(self):
        def item(i, o):
            return {"id": "x", "architecture": {"input_modalities": i, "output_modalities": o}}
        self.assertEqual(kind(item(["text", "image"], ["text"])), "text")
        self.assertEqual(kind(item(["text"], ["embeddings"])), "embedding")
        self.assertEqual(kind(item(["text"], ["image"])), "image")
        self.assertEqual(kind(item(["image", "text"], ["image", "text"])), "image")  # nano-banana
        self.assertEqual(kind(item(["text"], ["video"])), "video")
        self.assertEqual(kind(item(["text", "image"], ["text", "video"])), "video")  # omni
        self.assertEqual(kind(item(["text"], ["speech"])), "audio")                 # TTS
        self.assertEqual(kind(item(["audio"], ["transcription"])), "transcription")
        self.assertEqual(kind(item(["text"], ["rerank"])), "other")
        self.assertEqual(kind(item(["text"], ["decisions"])), "other")
        self.assertEqual(mk.classify(item(["text"], ["text"]))[1], "provider")
        # auto-routers advertise image output because they can route to an
        # image model; they're chat endpoints
        router = item(["text", "image", "audio", "file", "video"], ["text", "image"])
        for model_id in ("openrouter/auto", "openrouter/auto-code", "openrouter/auto-beta"):
            self.assertEqual(kind(dict(router, id=model_id)), "text", model_id)
        self.assertEqual(kind(dict(router, id="google/gemini-nano-banana-2.1")), "image")

    def test_vercel_type_beats_modalities(self):
        # Vercel's embedding models say output ["text"]; the type is right.
        emb = {"id": "alibaba/qwen3-embedding-0.6b", "type": "embedding",
               "modalities": {"input": ["text"], "output": ["text"]}}
        self.assertEqual(kind(emb), "embedding")
        for t, want in (("language", "text"), ("image", "image"), ("video", "video"),
                        ("speech", "audio"), ("transcription", "transcription"),
                        ("reranking", "other"), ("evaluation", "other"),
                        ("realtime", "audio")):
            self.assertEqual(kind({"id": "m", "type": t}), want, t)

    def test_together_opencode_llm7_lmstudio_types(self):
        for t, want in (("chat", "text"), ("code", "text"), ("transcribe", "transcription"),
                        ("audio", "audio"), ("moderation", "other"), ("rerank", "other")):
            self.assertEqual(kind({"id": "m", "type": t}), want, t)        # Together
        self.assertEqual(kind({"id": "m", "type": "decision"}), "other")    # OpenCode
        self.assertEqual(kind({"id": "Voxtral-Mini-3B-2507", "model_type": "audio_to_text"}),
                         "transcription")                                    # LLM7
        self.assertEqual(kind({"id": "m", "type": "vlm"}), "text")          # LM Studio
        self.assertEqual(kind({"id": "nomic", "type": "embeddings"}), "embedding")

    def test_mime_type_modalities_opencode(self):
        item = {"id": "gpt-live-1", "modalities": {
            "input": ["text", "audio/pcm"], "output": ["text", "audio/pcm"]}}
        self.assertEqual(kind(item), "audio")

    def test_nanogpt_audio_split(self):
        def item(i, o):
            return {"id": "m", "architecture": {"input_modalities": i, "output_modalities": o}}
        self.assertEqual(kind(item(["text"], ["audio"])), "audio")           # TTS
        self.assertEqual(kind(item(["text"], ["music"])), "audio")
        self.assertEqual(kind(item(["audio"], ["text"])), "transcription")   # STT
        self.assertEqual(kind(item(["audio"], ["voice"])), "audio")          # voice clone
        # a multimodal chat model that also hears audio stays text
        self.assertEqual(kind(item(["text", "image", "audio"], ["text"])), "text")

    def test_modality_string_aion_nebius(self):
        self.assertEqual(kind({"id": "m", "architecture": {"modality": "text->text"}}), "text")
        self.assertEqual(kind({"id": "m", "architecture": {"modality": "text->image"}}), "image")
        self.assertEqual(kind({"id": "m", "architecture": {"modality": "text->embedding"}}),
                         "embedding")

    def test_new_api_endpoint_types_unorouter(self):
        self.assertEqual(kind({"id": "text-embedding-3-small:free",
                               "supported_endpoint_types": ["openai", "embedding"]}), "embedding")
        self.assertEqual(kind({"id": "flux.2-pro",
                               "supported_endpoint_types": ["image-generation"]}), "image")
        self.assertEqual(kind({"id": "tunix-pony:free",
                               "supported_endpoint_types": ["aihorde"]}), "image")
        self.assertEqual(kind({"id": "jev-1.14",
                               "supported_endpoint_types": ["typesafe-decisions"]}), "other")
        # "openai" is an API format, not a kind -- the name decides
        self.assertEqual(mk.classify({"id": "whisper-large-v3:free",
                                      "supported_endpoint_types": ["openai"]}),
                         ("transcription", "name"))
        self.assertEqual(mk.classify({"id": "mistral-large-3",
                                      "supported_endpoint_types": ["openai"]}),
                         ("text", "default"))

    def test_capability_shapes(self):
        self.assertEqual(kind({"id": "c", "endpoints": ["chat", "embed"]}), "text")      # Cohere
        self.assertEqual(kind({"id": "c", "endpoints": ["embed"]}), "embedding")
        self.assertEqual(kind({"id": "c", "endpoints": ["rerank"]}), "other")
        self.assertEqual(kind({"id": "m", "capabilities": {"audio_transcription": True}}),
                         "transcription")                                             # Mistral
        self.assertEqual(kind({"id": "m", "capabilities": {"completion_chat": True,
                                                           "vision": True}}), "text")
        self.assertEqual(kind({"id": "m", "capabilities": ["embedding"]}), "embedding")  # Ollama
        self.assertEqual(kind({"id": "m", "capabilities": ["completion", "tools"]}), "text")
        g = lambda methods: kind({"id": "models/x", "supportedGenerationMethods": methods})  # noqa: E731
        self.assertEqual(g(["generateContent", "countTokens"]), "text")
        self.assertEqual(g(["embedContent"]), "embedding")
        self.assertEqual(g(["predict"]), "image")                                    # Imagen
        self.assertEqual(g(["predictLongRunning"]), "video")                         # Veo
        self.assertEqual(kind({"id": "m", "kind": "EMBEDDING_MODEL"}), "embedding")   # Fireworks
        self.assertEqual(kind({"id": "m", "kind": "FLUMINA_BASE_MODEL"}), "image")
        self.assertEqual(kind({"id": "m", "kind": "HF_BASE_MODEL", "supports_chat": True}), "text")
        self.assertEqual(kind({"id": "m", "supports_image_generation": True}), "image")  # Requesty
        self.assertEqual(kind({"id": "m", "task": {"name": "Text-to-Image"}}), "image")  # Cloudflare
        self.assertEqual(kind({"id": "m", "task": {"name": "Automatic Speech Recognition"}}),
                         "transcription")

    def test_caller_hint_wins(self):
        self.assertEqual(mk.classify({"id": "grok-imagine-image", "_kind": "image"}),
                         ("image", "provider"))


class NameTests(unittest.TestCase):
    """For providers that only send ids (OpenAI, Groq, NVIDIA, OVHcloud ...)."""

    def test_real_ids(self):
        cases = {
            "whisper-large-v3-turbo": "transcription", "openai/whisper-large-v3": "transcription",
            "nvidia/parakeet-tdt-0.6b-v3": "transcription", "x-ai/grok-stt-2.0": "transcription",
            "nvr-tts-en-us": "audio", "hexgrad/Kokoro-82M": "audio",
            "gemini-2.5-pro-preview-tts": "audio",
            "nvidia/nemotron-3.5-embed-1b": "embedding", "bge-m3": "embedding",
            "text-embedding-3-large": "embedding", "snowflake/arctic-embed-l": "embedding",
            "nvidia/nvclip": "embedding",
            "stable-diffusion-xl-base-v10": "image", "Qwen/Qwen-Image-Edit": "image",
            "gpt-image-2": "image", "dall-e-3": "image", "agnes-image-2.1-flash": "image",
            "gemini-3-pro-image-preview": "image", "black-forest-labs/FLUX.1-schnell": "image",
            "agnes-video-2.5": "video", "kling-v3.0-pro": "video", "google/veo-3.0": "video",
            "grok-video-3": "video", "alibaba/wan-v2.5-t2v-preview": "video",
            "Qwen3Guard-Gen-8B": "other", "nvidia/nemotron-3.5-content-safety": "other",
            "nvidia/ai-synthetic-video-detector": "other", "cohere/rerank-5-pro": "other",
        }
        for model_id, want in cases.items():
            self.assertEqual(mk.classify_name(model_id), want, model_id)

    def test_chat_models_are_not_misread(self):
        for model_id in ("thinkingmachines/inkling", "nebius/inkling-2-small",  # not "kling"
                         "meta-llama/Llama-3.2-90B-Vision-Instruct", "qwen3-vl-30b-a3b",
                         "gpt-oss-120b", "deepseek-ai/DeepSeek-V4-Pro", "glm-5.3",
                         "claude-opus-5[1m]", "mistral-large-3", "kimi-k3.1-pro"):
            self.assertIsNone(mk.classify_name(model_id), model_id)
            self.assertEqual(mk.classify(model_id), ("text", "default"))


class FacultyTests(unittest.TestCase):
    def test_kinds_map_to_buckets(self):
        self.assertEqual(mk.faculty_for("image", "Thinking"), "Image Generation")
        self.assertEqual(mk.faculty_for("embedding", "Thinking"), "Embedding")
        self.assertEqual(mk.faculty_for("text", "Coding"), "Coding")          # keeps a chat bucket
        self.assertEqual(mk.faculty_for("text", "Image Generation"), "Thinking")
        self.assertIsNone(mk.faculty_for("other", "Thinking"))
        self.assertEqual(mk.faculty_kind("Review"), "text")
        self.assertEqual(mk.faculty_kind("Transcription"), "transcription")
        self.assertIsNone(mk.faculty_kind("Research"))

    def test_buckets_match_the_settings_tabs(self):
        from settings_panel import backend
        tabs = {cid: facs for cid, _label, facs in backend.CATEGORIES if facs}
        self.assertEqual(set(tabs["text"]), set(mk.TEXT_FACULTIES))
        for kind_name, faculty in mk.KIND_FACULTY.items():
            self.assertEqual(tabs[kind_name], (faculty,))

    def test_new_entries_get_a_check_that_fits_their_kind(self):
        data = settings_store._default_data()
        url = "https://openrouter.ai/api/v1"
        kinds = {f: settings_store.add_model(data, f, "m", "m", "k", url)["check_kind"]
                 for f in ("Thinking", "Embedding", "Audio Generation",
                           "Video Generation", "Transcription", "Image Generation")}
        self.assertEqual(kinds, {"Thinking": "generic_http", "Embedding": "openai_embed",
                                 "Audio Generation": "catalog_listed",
                                 "Video Generation": "catalog_listed",
                                 "Transcription": "catalog_listed",
                                 "Image Generation": "openai_image"})


class _Resp:
    def __init__(self, code, data=None):
        self.status_code = code
        self._data = data
        self.text = ""

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class FetchPlumbingTests(unittest.TestCase):
    """Where each provider keeps its labels -- faked requests, no network."""

    def setUp(self):
        import requests
        self.requests = requests
        self.orig_get, self.orig_post = requests.get, requests.post
        self.calls = []

    def tearDown(self):
        self.requests.get, self.requests.post = self.orig_get, self.orig_post

    def serve(self, routes):
        def fake_get(url, headers=None, timeout=None):
            self.calls.append((url, headers))
            for prefix, resp in routes.items():
                if url.startswith(prefix):
                    return resp
            return _Resp(404, {})
        self.requests.get = fake_get

    def test_list_url_tweaks(self):
        self.assertEqual(settings_checks._catalog_list_url("https://openrouter.ai/api/v1/models"),
                         "https://openrouter.ai/api/v1/models?output_modalities=all")
        self.assertEqual(settings_checks._catalog_list_url("https://api.studio.nebius.com/v1/models"),
                         "https://api.studio.nebius.com/v1/models?verbose=true")
        self.assertEqual(settings_checks._catalog_list_url("https://api.groq.com/openai/v1/models"),
                         "https://api.groq.com/openai/v1/models")

    def test_xai_lists_tag_kinds(self):
        self.serve({
            "https://api.x.ai/v1/models": _Resp(200, {"data": [{"id": "grok-4"},
                                                               {"id": "grok-imagine-image"}]}),
            "https://api.x.ai/v1/image-generation-models": _Resp(200, {"models": [
                {"id": "grok-imagine-image"}]}),
            "https://api.x.ai/v1/video-generation-models": _Resp(200, {"models": [
                {"id": "grok-imagine-video"}]}),
        })
        r = settings_checks.fetch_model_list({"base_url": "https://api.x.ai/v1", "api_key": "k"})
        self.assertEqual(r["kinds"]["grok-4"]["kind"], "text")
        self.assertEqual(r["kinds"]["grok-imagine-image"], {"kind": "image", "source": "provider"})
        self.assertEqual(r["kinds"]["grok-imagine-video"]["kind"], "video")   # added from its list

    def test_google_native_methods_merge_onto_compat_ids(self):
        self.serve({
            "https://generativelanguage.googleapis.com/v1beta/openai/models": _Resp(200, {"data": [
                {"id": "models/gemini-3-flash"}, {"id": "models/imagen-4.0-generate"},
                {"id": "models/gemini-embedding-001"}]}),
            "https://generativelanguage.googleapis.com/v1beta/models": _Resp(200, {"models": [
                {"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/imagen-4.0-generate", "supportedGenerationMethods": ["predict"]},
                {"name": "models/gemini-embedding-001",
                 "supportedGenerationMethods": ["embedContent"]}]}),
        })
        r = settings_checks.fetch_model_list({
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "api_key": "g"})
        self.assertEqual({m: v["kind"] for m, v in r["kinds"].items()}, {
            "models/gemini-3-flash": "text", "models/imagen-4.0-generate": "image",
            "models/gemini-embedding-001": "embedding"})
        native = [h for u, h in self.calls if "/v1beta/models?" in u]
        self.assertEqual(native[0], {"x-goog-api-key": "g"})

    def test_siliconflow_sub_types(self):
        base = "https://api.siliconflow.cn/v1/models"
        self.serve({
            base + "?type=audio": _Resp(200, {"data": [{"id": "FunAudioLLM/CosyVoice2"},
                                                       {"id": "FunAudioLLM/SenseVoiceSmall"}]}),
            base + "?sub_type=speech-to-text": _Resp(200, {"data": [
                {"id": "FunAudioLLM/SenseVoiceSmall"}]}),
            base + "?sub_type=text-to-image": _Resp(200, {"data": [{"id": "Kwai-Kolors/Kolors"}]}),
            base + "?sub_type=embedding": _Resp(200, {"data": [{"id": "BAAI/bge-m3"}]}),
            base + "?sub_type=reranker": _Resp(200, {"data": []}),
            base + "?sub_type=image-to-image": _Resp(200, {"data": []}),
            base + "?sub_type=text-to-video": _Resp(200, {"data": []}),
            base: _Resp(200, {"data": [{"id": "Qwen/Qwen3-8B"}, {"id": "FunAudioLLM/CosyVoice2"},
                                       {"id": "FunAudioLLM/SenseVoiceSmall"},
                                       {"id": "Kwai-Kolors/Kolors"}, {"id": "BAAI/bge-m3"}]}),
        })
        r = settings_checks.fetch_model_list({"base_url": "https://api.siliconflow.cn/v1",
                                              "api_key": "k"})
        self.assertEqual({m: v["kind"] for m, v in r["kinds"].items()}, {
            "Qwen/Qwen3-8B": "text", "FunAudioLLM/CosyVoice2": "audio",
            "FunAudioLLM/SenseVoiceSmall": "transcription", "Kwai-Kolors/Kolors": "image",
            "BAAI/bge-m3": "embedding"})

    def test_nanogpt_side_lists_are_appended(self):
        root = "https://nano-gpt.com/api/v1"
        self.serve({
            root + "/models": _Resp(200, {"data": [{"id": "chat-1", "architecture": {
                "input_modalities": ["text"], "output_modalities": ["text"]}}]}),
            root + "/image-models": _Resp(200, {"data": [{"id": "kling-image-v4"}]}),
            root + "/embedding-models": _Resp(200, {"data": [{"id": "perplexity/pplx-embed"}]}),
            root + "/audio-models": _Resp(500, None),            # a failing side list is skipped
        })
        r = settings_checks.fetch_model_list({"base_url": root, "api_key": ""})
        self.assertTrue(r["ok"])
        self.assertEqual({m: v["kind"] for m, v in r["kinds"].items()}, {
            "chat-1": "text", "kling-image-v4": "image", "perplexity/pplx-embed": "embedding"})

    def test_providers_without_extra_lists_make_one_request(self):
        self.serve({"https://api.groq.com/openai/v1/models": _Resp(200, {"data": [
            {"id": "openai/gpt-oss-120b"}, {"id": "whisper-large-v3"}]})})
        r = settings_checks.fetch_model_list({"base_url": "https://api.groq.com/openai/v1",
                                              "api_key": "k"})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(r["kinds"]["whisper-large-v3"], {"kind": "transcription", "source": "name"})


class KindChecksTests(unittest.TestCase):
    def setUp(self):
        import requests
        self.requests = requests
        self.orig_get, self.orig_post = requests.get, requests.post

    def tearDown(self):
        self.requests.get, self.requests.post = self.orig_get, self.orig_post

    def test_embedding_check_makes_a_real_embedding(self):
        sent = []
        self.requests.post = lambda url, headers=None, json=None, timeout=None: (
            sent.append((url, json)) or _Resp(200, {"data": [{"embedding": [0.1]}]}))
        ok, detail = settings_checks.run_check({
            "check_kind": "openai_embed", "base_url": "https://api.unorouter.com/v1",
            "model": "text-embedding-3-small:free", "api_key": "k"})
        self.assertTrue(ok, detail)
        self.assertEqual(sent, [("https://api.unorouter.com/v1/embeddings",
                                 {"model": "text-embedding-3-small:free", "input": "ping"})])
        self.requests.post = lambda *a, **k: _Resp(401, {})
        ok, detail = settings_checks.run_check({
            "check_kind": "openai_embed", "base_url": "https://x/v1", "model": "e", "api_key": "k"})
        self.assertFalse(ok)
        self.assertIn("Key rejected", detail)

    def test_catalog_check_generates_nothing(self):
        self.requests.post = lambda *a, **k: self.fail("a catalog check must not POST")
        self.requests.get = lambda url, headers=None, timeout=None: _Resp(200, {"data": [
            {"id": "x-ai/grok-stt-2.0", "architecture": {
                "input_modalities": ["audio"], "output_modalities": ["transcription"]}}]})
        entry = {"check_kind": "catalog_listed", "base_url": "https://openrouter.ai/api/v1",
                 "model": "x-ai/grok-stt-2.0", "api_key": "k"}
        ok, detail = settings_checks.run_check(entry)
        self.assertTrue(ok, detail)
        self.assertIn("Nothing was generated", detail)
        ok, detail = settings_checks.run_check(dict(entry, model="not-a-model"))
        self.assertFalse(ok)
        self.assertIn("doesn't list", detail)
        self.requests.get = lambda url, headers=None, timeout=None: _Resp(401, {
            "error": {"message": "bad key"}})
        ok, detail = settings_checks.run_check(entry)
        self.assertFalse(ok)
        self.assertIn("Key rejected", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)

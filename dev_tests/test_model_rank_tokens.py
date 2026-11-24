"""Best-first model ranking (model_rank) and the token calculator
(token_meter), plus how the Settings panel shows both. No network: the
scores come from the bundled model_scores.json snapshot or tiny fixtures,
and every model "reply" here is a fake.

Run: python dev_tests/test_model_rank_tokens.py
"""
from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FRIDAY_MEMORY_PATH", tempfile.mkdtemp(prefix="friday-rank-"))
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QEventLoop, QMetaObject, QTimer, qInstallMessageHandler  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)
APP.setQuitOnLastWindowClosed(False)
QML_MESSAGES = []
qInstallMessageHandler(lambda _m, _c, msg: None if msg.startswith("This plugin does not support")
                       else QML_MESSAGES.append(msg))

import model_rank  # noqa: E402
import settings_store  # noqa: E402
import token_meter  # noqa: E402
from settings_panel.backend import SettingsBackend  # noqa: E402

# Never reach for the network from a test (scores refresh weekly).
model_rank.refresh_in_background = lambda on_done=None: None


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


def fresh_meter():
    token_meter.flush()
    token_meter.reset()
    token_meter.flush()


def entry(i, name, model, url, status="unknown"):
    return dict(id=i, name=name, model=model, api_key="sk-test", base_url=url,
                api_style="openai", check_kind="openai_compat", status=status, detail="")


def or_item(model_id, aa=None, arena=(), out=("text",)):
    bench = {}
    if aa is not None:
        bench["artificial_analysis"] = {"intelligence_index": aa}
    if arena:
        bench["design_arena"] = [{"arena": "models", "category": c, "elo": e} for c, e in arena]
    return {"id": model_id, "benchmarks": bench or None,
            "architecture": {"input_modalities": ["text"], "output_modalities": list(out)}}


# -- model_rank ----------------------------------------------------------------
class NameMatchingTests(unittest.TestCase):
    """One scored name, however each provider spells the id."""

    def test_provider_spellings_map_to_one_name(self):
        same = ["openai/gpt-oss-120b", "openai/gpt-oss-120b:free", "gpt-oss:120b",
                "GPT-OSS-120B", "accounts/fireworks/models/gpt-oss-120b",
                "openai/gpt-oss-120b@us-east"]
        self.assertEqual({model_rank.norm(m) for m in same}, {"gpt-oss-120b"})
        self.assertEqual(model_rank.norm("accounts/fireworks/models/glm-5p2"),
                         model_rank.norm("z-ai/glm-5.2"))
        self.assertEqual(model_rank.norm("claude-opus-5[1m]"), model_rank.norm("anthropic/claude-opus-5"))

    def test_bundled_snapshot_scores_the_big_names(self):
        table = model_rank.scores()
        self.assertGreaterEqual(len(table["text"]), 100)
        self.assertGreaterEqual(len(table["image"]), 10)
        hit = model_rank.score_for("anthropic/claude-opus-5.5", "text")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], "aa")
        self.assertIsNone(model_rank.score_for("someone/never-heard-of-it", "text"))
        self.assertIsNone(model_rank.score_for("anthropic/claude-opus-5.5", "video"))  # no table
        self.assertTrue(model_rank.score_label("sourceful/riverflow-v2.5-pro", "image").startswith("Elo "))

    def test_variant_suffixes_fall_back_to_the_base_model(self):
        base = model_rank.score_for("anthropic/claude-opus-5.5", "text")
        self.assertEqual(model_rank.score_for("anthropic/claude-opus-5.5-thinking", "text"), base)

    def test_percentile_orders_across_kinds(self):
        best = model_rank.percentile("anthropic/claude-fable-5.2", "text")
        low = model_rank.percentile("openai/gpt-oss-120b", "text")
        self.assertEqual(best, 100.0)
        self.assertLess(low, best)
        self.assertEqual(model_rank.percentile("sourceful/riverflow-v2.5-pro", "image"), 100.0)
        self.assertIsNone(model_rank.percentile("nope/nope", "text"))


class EstimateTests(unittest.TestCase):
    """Models with no published benchmark still rank, marked as estimates."""

    def test_unbenchmarked_models_get_a_labelled_estimate(self):
        hit = model_rank.score_for("openai/gpt-4o-mini", "text")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], "price")
        self.assertTrue(model_rank.is_estimate(hit[1]))
        self.assertTrue(model_rank.score_label("openai/gpt-4o-mini").startswith("~"))
        self.assertIsNone(model_rank.score_for("openai/gpt-4o-mini", "text", estimates=False))
        measured = model_rank.score_for("anthropic/claude-opus-5.5", "text")
        self.assertFalse(model_rank.is_estimate(measured[1]))
        self.assertFalse(model_rank.score_label("anthropic/claude-opus-5.5").startswith("~"))

    def test_family_siblings_cover_names_outside_openrouter(self):
        table = {"acme-3-70b": [40.0, "aa"], "acme-3-8b": [20.0, "aa"],
                 "other-1": [50.0, "aa"]}
        self.assertEqual(model_rank._sibling("acme-3-70b-special", table, 2.5), 40.0)
        est = model_rank._sibling("acme-3-140b", table, 2.5)     # one doubling above 70b
        self.assertAlmostEqual(est, 42.5)
        self.assertIsNone(model_rank._sibling("acme-9-1b", table, 2.5))  # family+version must match
        hit = model_rank.score_for("groq/llama-3.1-8b-instant", "text")
        self.assertIsNotNone(hit)

    def test_more_provider_spellings(self):
        self.assertEqual(model_rank.norm("llama3.3:70b"), model_rank.norm("meta-llama/Llama-3.3-70B-Instruct"))
        self.assertEqual(model_rank.norm("gemma3:4b"), "gemma-3-4b")
        self.assertEqual(model_rank.norm("nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8"),
                         "nemotron-3-super-120b-a12b")
        self.assertEqual(model_rank.norm("qwen/qwen-image"), "qwen-image")       # brand IS the name
        self.assertEqual(model_rank.norm("deepseek/deepseek-v3"), "deepseek-v3")

    def test_estimates_never_replace_measured_scores(self):
        items = [or_item(f"lab/m-{n}", aa=float(10 + n)) for n in range(20)]
        for n, item in enumerate(items):
            item["pricing"] = {"completion": str((n + 1) * 1e-6)}
            item["created"] = 1.75e9 + n * 1e6
            item["context_length"] = 128000
        arena_only = or_item("lab/arena-only", arena=[("website", 1300)])
        arena_only["pricing"] = {"completion": "0.00002"}
        arena_only["created"], arena_only["context_length"] = 1.76e9, 128000
        for n in range(5):                                      # arena<->AA overlap
            items[n]["benchmarks"]["design_arena"] = [
                {"arena": "models", "category": "website", "elo": 1000 + 50 * n}]
        priced = or_item("lab/priced-only")
        priced["pricing"] = {"completion": "0.000005"}
        priced["created"], priced["context_length"] = 1.76e9, 128000
        built = model_rank.build_scores(items + [arena_only, priced])
        self.assertEqual(built["text"]["m-3"][1], "aa")
        self.assertEqual(built["text"]["arena-only"][1], "arena")
        self.assertEqual(built["text"]["priced-only"][1], "price")
        lo = min(v[0] for v in built["text"].values() if v[1] == "aa")
        self.assertGreater(built["text"]["priced-only"][0], 0)
        self.assertGreaterEqual(min(v[0] for v in built["text"].values()), 0)
        self.assertLessEqual(lo, 10.0)

    def test_text_coverage_of_the_bundled_snapshot(self):
        sources = [v[1] for v in model_rank.scores()["text"].values()]
        self.assertGreater(sources.count("price"), 100)
        self.assertGreater(sources.count("aa"), 50)


class ScoreTableTests(unittest.TestCase):
    def setUp(self):
        self.saved = model_rank._scores

    def tearDown(self):
        model_rank._scores = self.saved
        try:
            os.remove(model_rank._cache_path())
        except OSError:
            pass

    def test_build_scores_reads_both_benchmarks(self):
        items = [or_item("a/alpha-1", aa=50.0), or_item("b/beta-2", aa=30.0),
                 or_item("c/pix-1", arena=[("image", 1300), ("logo", 1200)], out=("image",)),
                 or_item("d/voice-1", arena=[("text-to-speech", 1100)], out=("speech",))]
        built = model_rank.build_scores(items)
        self.assertEqual(built["text"]["alpha-1"][0], 50.0)
        self.assertEqual(built["image"]["pix-1"], [1250, "arena"])
        self.assertEqual(built["audio"]["voice-1"][0], 1100)
        self.assertGreater(built["built"], 0)

    def test_a_full_catalog_refreshes_and_caches_a_partial_one_is_ignored(self):
        self.assertFalse(model_rank.update_from_catalog([or_item("x/only-one", aa=99.0)]))
        self.assertFalse(model_rank.update_from_catalog([{"id": "no-bench"}] * 30))
        items = [or_item(f"lab/model-{n}", aa=float(n)) for n in range(25)]
        self.assertTrue(model_rank.update_from_catalog(items))
        self.assertEqual(model_rank.score_for("lab/model-24", "text")[0], 24.0)
        with open(model_rank._cache_path(), encoding="utf-8") as f:
            self.assertIn("model-24", json.load(f)["text"])
        model_rank._scores = None                      # a restart reads the cache back
        self.assertEqual(model_rank.score_for("other/model-24", "text")[0], 24.0)


# -- token_meter ---------------------------------------------------------------
class UsageShapeTests(unittest.TestCase):
    def test_every_provider_shape(self):
        gemini = types.SimpleNamespace(usage_metadata=types.SimpleNamespace(
            prompt_token_count=100, candidates_token_count=40, thoughts_token_count=10,
            total_token_count=150))
        self.assertEqual(token_meter.extract_usage(gemini), (100, 50))
        openai_obj = types.SimpleNamespace(usage=types.SimpleNamespace(
            prompt_tokens=12, completion_tokens=8, total_tokens=20))
        self.assertEqual(token_meter.extract_usage(openai_obj), (12, 8))
        self.assertEqual(token_meter.extract_usage(
            {"usage": {"prompt_tokens": 5, "completion_tokens": 7}}), (5, 7))
        self.assertEqual(token_meter.extract_usage(                       # Anthropic
            {"usage": {"input_tokens": 9, "output_tokens": 3}}), (9, 3))
        self.assertEqual(token_meter.extract_usage(                       # embeddings
            {"usage": {"prompt_tokens": 6, "total_tokens": 6}}), (6, 0))
        self.assertEqual(token_meter.extract_usage(
            {"usage_metadata": {"prompt_token_count": 4, "total_token_count": 10}}), (4, 6))
        self.assertEqual(token_meter.extract_usage(                       # Gemini REST
            {"usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4}}), (3, 4))
        self.assertEqual(token_meter.extract_usage(                       # Ollama native
            {"prompt_eval_count": 5, "eval_count": 6}), (5, 6))
        self.assertEqual(token_meter.extract_usage(                       # Cohere v1
            {"meta": {"billed_units": {"input_tokens": 7, "output_tokens": 8}}}), (7, 8))
        self.assertEqual(token_meter.extract_usage(                       # Cohere v2
            {"usage": {"billed_units": {"input_tokens": 1, "output_tokens": 2}}}), (1, 2))
        self.assertEqual(token_meter.extract_usage(                       # Workers AI
            {"result": {"usage": {"prompt_tokens": 2, "completion_tokens": 1}}}), (2, 1))
        self.assertEqual(token_meter.extract_usage(                       # Anthropic cache
            {"usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5}}),
            (100, 5))
        self.assertIsNone(token_meter.extract_usage({"choices": []}))
        self.assertIsNone(token_meter.extract_usage(None))

    def test_short_numbers(self):
        self.assertEqual(token_meter.short(999), "999")
        self.assertEqual(token_meter.short(1000), "1k")
        self.assertEqual(token_meter.short(12345), "12.3k")
        self.assertEqual(token_meter.short(4_560_000), "4.56M")
        self.assertEqual(token_meter.short(2_000_000_000), "2B")

    def test_hosts(self):
        self.assertEqual(token_meter.host_of({"base_url": "https://OpenRouter.ai/api/v1"}), "openrouter.ai")
        self.assertEqual(token_meter.host_of({"check_kind": "gemini", "base_url": ""}),
                         "generativelanguage.googleapis.com")
        self.assertEqual(token_meter.host_of("api.groq.com/openai/v1"), "api.groq.com")
        self.assertEqual(token_meter.host_of("http://localhost:11434/v1"), "localhost:11434")
        self.assertEqual(token_meter.host_of("http://localhost:1234/v1"), "localhost:1234")


class MeterTests(unittest.TestCase):
    def setUp(self):
        fresh_meter()

    def test_records_persist_and_reset(self):
        token_meter.record("api.groq.com", "llama-x", {"usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        token_meter.record("api.groq.com", "llama-x", {"usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        u = token_meter.usage_for()
        self.assertEqual((u["total"], u["in"], u["out"], u["calls"], u["today"]), (17, 11, 6, 2, 17))
        token_meter.flush()
        token_meter.reload()                        # as after a restart
        self.assertEqual(token_meter.model_total("api.groq.com|llama-x"), 17)
        self.assertEqual(token_meter.usage_for({"api.groq.com|other"})["total"], 0)
        token_meter.reset()
        self.assertEqual(token_meter.usage_for()["total"], 0)

    def test_totals_by_provider_add_up(self):
        token_meter.record("api.groq.com", "a", input_tokens=100, output_tokens=50)
        token_meter.record("api.groq.com", "b", input_tokens=10, output_tokens=0)
        token_meter.record("openrouter.ai", "c", input_tokens=500, output_tokens=500)
        hosts = token_meter.by_host()
        self.assertEqual([h for h, _ in hosts], ["openrouter.ai", "api.groq.com"])
        self.assertEqual(hosts[1][1]["total"], 160)
        self.assertEqual(hosts[1][1]["models"], 2)
        self.assertEqual(sum(h["total"] for _, h in hosts), token_meter.usage_for()["total"])

    def test_no_usage_report_is_estimated_and_flagged(self):
        token_meter.record("h", "m", {"choices": []}, prompt_text="x" * 400, reply_text="y" * 80)
        u = token_meter.usage_for()
        self.assertEqual((u["in"], u["out"], u["estimated"]), (100, 20, 120))
        token_meter.record("h", "m", None)          # nothing to go on: not counted
        self.assertEqual(token_meter.usage_for()["calls"], 1)

    def test_thread_safe_under_load(self):
        def work():
            for _ in range(200):
                token_meter.record("h", "m", input_tokens=1, output_tokens=1)
        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(token_meter.usage_for()["total"], 3200)

    def test_listeners_hear_every_record_and_bad_input_never_raises(self):
        heard = []
        fn = lambda: heard.append(1)  # noqa: E731
        token_meter.add_listener(fn)
        try:
            token_meter.record("h", "m", input_tokens=3, output_tokens=0)
            token_meter.record("h", "m", {"usage": {"prompt_tokens": "junk"}})
            token_meter.record(None, None, object())
        finally:
            token_meter.remove_listener(fn)
        self.assertEqual(len(heard), 1)


class CallSiteTests(unittest.TestCase):
    """The real call paths hand their replies to the meter."""

    def setUp(self):
        fresh_meter()

    def test_act_script_custom_http_entry(self):
        import act_script
        import requests

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"content": "done"}}],
                        "usage": {"prompt_tokens": 42, "completion_tokens": 8}}
        original = requests.post
        requests.post = lambda *a, **k: Resp()
        try:
            e = entry("z", "OR", "openai/gpt-oss-120b", "https://openrouter.ai/api/v1")
            reply = act_script._dispatch_generic_http(e, "sys", "hi", e["model"])
        finally:
            requests.post = original
        self.assertEqual(reply, "done")
        self.assertEqual(token_meter.model_total("openrouter.ai|openai/gpt-oss-120b"), 50)

    def test_memory_embeddings(self):
        import memory
        fake = types.ModuleType("mistralai")

        class Client:
            def __init__(self, api_key=None):
                self.embeddings = self

            def create(self, model, inputs):
                return types.SimpleNamespace(
                    data=[types.SimpleNamespace(embedding=[0.1, 0.2])],
                    usage=types.SimpleNamespace(prompt_tokens=7, total_tokens=7))
        fake.Mistral = Client
        saved = sys.modules.get("mistralai")
        sys.modules["mistralai"] = fake
        try:
            memory._embed_via_mistral("hello", "k")
        finally:
            if saved is not None:
                sys.modules["mistralai"] = saved
            else:
                del sys.modules["mistralai"]
        self.assertEqual(token_meter.model_total("api.mistral.ai|mistral-embed"), 7)

    def test_settings_checks_count_their_tokens(self):
        import requests
        import settings_checks

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"content": "p"}}],
                        "usage": {"prompt_tokens": 9, "completion_tokens": 1}}
        original = requests.post
        requests.post = lambda *a, **k: Resp()
        try:
            e = entry("q", "DeepSeek", "deepseek-chat", "https://api.deepseek.com/v1")
            ok, _detail = settings_checks.check_generic_http(e)
        finally:
            requests.post = original
        self.assertTrue(ok)
        self.assertEqual(token_meter.model_total("api.deepseek.com|deepseek-chat"), 10)

    def test_image_generation_is_counted(self):
        import base64
        import io
        from PIL import Image
        from friday_image.model import ImageRequest
        from friday_image.providers import OpenAIImageBackend
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buf, "PNG")
        body = json.dumps({"data": [{"b64_json": base64.b64encode(buf.getvalue()).decode()}],
                           "usage": {"input_tokens": 30, "output_tokens": 4160}}).encode()

        class Resp(io.BytesIO):
            headers = None

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        backend = OpenAIImageBackend(api_key="sk", base_url="https://api.openai.com/v1",
                                     model="gpt-image-1", urlopen=lambda r, timeout=None: Resp(body))
        req = ImageRequest(prompt="a red square", style="Mist", aspect="1:1", quality="Draft",
                           composition="Horizon", detail=42.0, seed=1, format="png")
        backend.render(req, Path(tempfile.mkdtemp()))
        self.assertEqual(token_meter.model_total("api.openai.com|gpt-image-1"), 4190)

    def test_overlay_helper_counts_sdk_replies(self):
        src = (ROOT / "agent_overlay.py").read_text(encoding="utf-8")
        # Gemini, Groq and Mistral replies in the chat worker all go through it.
        for host in ("generativelanguage.googleapis.com", "api.groq.com", "api.mistral.ai"):
            self.assertIn(f'_meter_reply("{host}"', src)
        self.assertIn("app.aboutToQuit.connect(token_meter.flush)", src)


# -- the Settings panel --------------------------------------------------------
class PanelTests(unittest.TestCase):
    def setUp(self):
        fresh_meter()
        data = settings_store._default_data()
        data["Thinking"] = [
            entry("a", "Groq OSS", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1", "connected"),
            entry("b", "Unknown bad", "acme/foo-7b", "https://acme.example/v1", "error"),
            entry("c", "Claude", "anthropic/claude-opus-5.5", "https://openrouter.ai/api/v1"),
            entry("d", "Unknown ok", "acme/bar-9b", "https://acme.example/v1", "connected"),
        ]
        settings_store.save(data)
        self.backend = SettingsBackend()
        self.backend.setCategory("text")

    def tearDown(self):
        self.backend.shutdown()

    def models(self):
        return [r["modelName"] for r in self.backend.connections.rows()]

    def test_connected_list_is_best_first_then_unscored_by_status(self):
        self.assertEqual(self.models(), ["anthropic/claude-opus-5.5", "openai/gpt-oss-120b",
                                         "acme/bar-9b", "acme/foo-7b"])
        rows = self.backend.connections.rows()
        self.assertTrue(rows[0]["score"])
        self.assertFalse(rows[0]["scoreEstimated"])
        self.assertIn("Artificial Analysis", rows[0]["scoreSource"])
        self.assertEqual(rows[2]["score"], "")

    def test_a_check_result_moves_an_unscored_row_without_duplicates(self):
        _fac, bad = self.backend._entry("Thinking::b")
        if bad is None:                          # key format differs: find it
            key = next(r["key"] for r in self.backend.connections.rows() if r["entryId"] == "b")
            _fac, bad = self.backend._entry(key)
        bad["status"] = "connected"
        self.backend._rebuild()
        self.assertEqual(self.models()[2:], ["acme/foo-7b", "acme/bar-9b"])   # as added
        self.assertEqual(len(self.models()), len(set(self.models())))
        self.assertEqual(self.backend.connections.rowCount(), 4)

    def test_fetch_dropdown_ranks_within_kind_and_tier(self):
        b = self.backend
        got = []
        b.fetchFinished.connect(got.append)
        b._fetch_serial, b._fetch_faculty = 7, "Thinking"
        b._finish_fetch({"serial": 7, "snapshot": {}, "result": {
            "ok": True, "tiers": None,
            "ids": ["x/unscored", "openai/gpt-oss-120b", "sourceful/riverflow-v2.5-pro",
                    "anthropic/claude-fable-5.2"],
            "kinds": {"sourceful/riverflow-v2.5-pro": {"kind": "image", "source": "provider"},
                      "x/unscored": {"kind": "text", "source": "provider"},
                      "openai/gpt-oss-120b": {"kind": "text", "source": "provider"},
                      "anthropic/claude-fable-5.2": {"kind": "text", "source": "provider"}}}})
        values = got[-1]["values"]
        ids = [b._display_to_id[v] for v in values]
        self.assertEqual(ids, ["anthropic/claude-fable-5.2", "openai/gpt-oss-120b",
                               "x/unscored", "sourceful/riverflow-v2.5-pro"])
        self.assertTrue(values[0].startswith("\u2605"))
        self.assertIn("Best first", got[-1]["note"])
        b._finish_fetch({"serial": 7, "snapshot": {}, "result": {
            "ok": True, "ids": [], "kinds": {},
            "tiers": {"free": ["x/unk:free", "openai/gpt-oss-120b:free"],
                      "paid": ["y/zz", "anthropic/claude-fable-5.2"]}}})
        ids = [b._display_to_id[v] for v in got[-1]["values"]]
        self.assertEqual(ids, ["openai/gpt-oss-120b:free", "x/unk:free",          # FREE stays on top
                               "anthropic/claude-fable-5.2", "y/zz"])

    def test_saving_a_starred_label_stores_the_bare_model_id(self):
        b = self.backend
        b._fetch_serial, b._fetch_faculty = 3, "Thinking"
        got = []
        b.fetchFinished.connect(got.append)
        b._finish_fetch({"serial": 3, "snapshot": {}, "result": {
            "ok": True, "tiers": None, "ids": ["anthropic/claude-fable-5.2"], "kinds": {}}})
        label = got[-1]["values"][0]
        self.assertNotEqual(label, "anthropic/claude-fable-5.2")
        self.assertEqual(b._display_to_id[label], "anthropic/claude-fable-5.2")

    def test_token_usage_updates_live_from_worker_threads(self):
        b = self.backend
        self.assertTrue(b.tokenUsage["empty"])
        changed = []
        b.tokensChanged.connect(lambda: changed.append(1))
        t = threading.Thread(target=token_meter.record, args=(
            "api.groq.com", "openai/gpt-oss-120b",
            {"usage": {"prompt_tokens": 1500, "completion_tokens": 300}}))
        t.start()
        t.join()
        pump(until=lambda: changed)
        u = b.tokenUsage
        self.assertEqual((u["total"], u["exact"], u["tab"]), ("1.8k", "1,800", "1.8k"))
        self.assertEqual(u["top"][0]["name"], "Groq OSS")
        row = next(r for r in b.connections.rows() if r["modelName"] == "openai/gpt-oss-120b")
        self.assertEqual(row["tokens"], "1.8k")
        token_meter.record("openrouter.ai", "x/y", input_tokens=200, output_tokens=0)
        pump(until=lambda: b.tokenUsage["providerCount"] == 2)
        provs = b.tokenUsage["providers"]
        self.assertEqual([p["name"] for p in provs], ["Groq", "OpenRouter"])
        self.assertAlmostEqual(sum(p["share"] for p in provs), 1.0)
        self.assertEqual(b.tokenUsage["exact"], "2,000")
        b.setCategory("image")                              # tab share follows the tab
        self.assertEqual(b.tokenUsage["tab"], "0")
        self.assertEqual(b.tokenUsage["total"], "2k")
        b.resetTokens()
        self.assertTrue(b.tokenUsage["empty"])


class WindowTests(unittest.TestCase):
    def setUp(self):
        fresh_meter()
        data = settings_store._default_data()
        data["Thinking"] = [entry("c", "Claude", "anthropic/claude-opus-5.5", "https://openrouter.ai/api/v1")]
        settings_store.save(data)

    def test_token_meter_sits_beside_check_all_and_ticks_up(self):
        import settings_panel
        QML_MESSAGES.clear()
        _engine, _backend, win = settings_panel.create_window("keys")
        closed = []
        win.finished.connect(lambda: closed.append(True))
        pump(300)

        def find(name, root=None):
            for child in (root or win.contentItem()).childItems():
                if child.objectName() == name:
                    return child
                hit = find(name, child)
                if hit is not None:
                    return hit
            return None
        meter, total = find("tokenMeter"), find("tokenTotal")
        self.assertIsNotNone(meter)
        self.assertEqual(total.property("text"), "0")
        check_all = [c for c in meter.parentItem().childItems() if c.property("text") == "Check all"]
        self.assertEqual(len(check_all), 1)                  # same header row ...
        self.assertLess(meter.x(), check_all[0].x())         # ... just left of it, top right
        self.assertGreater(meter.x(), win.width() / 2)
        token_meter.record("openrouter.ai", "anthropic/claude-opus-5.5", input_tokens=20000, output_tokens=4000)
        pump(until=lambda: total.property("text") == "24k")
        pump(until=lambda: find("rowTokens") is not None and find("rowTokens").property("visible"))
        badge = find("scoreBadge")
        self.assertTrue(badge.property("visible"))
        from PySide6.QtCore import QObject
        details = meter.findChild(QObject, "tokenDetails")   # the breakdown popup
        self.assertFalse(details.property("opened"))
        QMetaObject.invokeMethod(meter, "click")
        pump(until=lambda: details.property("opened"))
        self.assertEqual(QML_MESSAGES, [])
        QMetaObject.invokeMethod(win, "animatedClose")
        pump(until=lambda: closed)


if __name__ == "__main__":
    unittest.main(verbosity=2)

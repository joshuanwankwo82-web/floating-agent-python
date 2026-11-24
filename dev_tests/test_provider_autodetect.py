"""The any-key dream's engine tests: provider auto-detection and the
dialect machinery that lets ONE pasted key (free or paid, ANY format)
power the whole agent.

Grounded in a user goal: "any user adds his custom API keys either
free or paid IN ANY FORMAT all into the agent settings and the agent
uses those keys to work." The mechanics behind that line: recognize
the provider from the key's shape (or one quick-pick), speak its real
dialect (OpenAI-compatible AND Anthropic), fetch its real model
catalog, and -- because of the shape-parity rule that ended
green-check/red-build -- make the Settings check, the "Fetch models"
fetch, and every runtime call use THE SAME shared builders.

This file pins the pure surface (fingerprints, URLs, headers,
payloads). The runtime/verify call-sites pin their own sides in
test_act_script.py / test_settings_panel.py.
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import provider_autodetect as pa  # noqa: E402
import settings_checks  # noqa: E402

CHECKS = []


def check(label, cond):
    CHECKS.append((label, bool(cond)))
    print(("PASS" if cond else "FAIL") + " - " + label)


# =========================================================================
# A: key fingerprints -- any format recognized, ambiguous keys stay honest
# =========================================================================
check("OpenRouter key is recognized (sk-or-v1-...)",
      pa.detect_provider("sk-or-v1-" + "x" * 60)["slug"] == "openrouter")
check("Anthropic key is recognized (sk-ant-...)",
      pa.detect_provider("sk-ant-" + "x" * 40)["slug"] == "anthropic")
check("Groq key is recognized (gsk_...)",
      pa.detect_provider("gsk_" + "x" * 40)["slug"] == "groq")
check("OpenAI project key is recognized (sk-proj-...)",
      pa.detect_provider("sk-proj-" + "x" * 40)["slug"] == "openai")
check("xAI key is recognized (xai-...)",
      pa.detect_provider("xai-" + "x" * 40)["slug"] == "xai")
check("Cerebras key is recognized (csk-...)",
      pa.detect_provider("csk-" + "x" * 40)["slug"] == "cerebras")
check("Z.ai two-part key is recognized (32-hex.suffix)",
      pa.detect_provider("a1b2c3d4e5f60718a1b2c3d4e5f60718.AbCdXy1")["slug"]
      == "zai")
check("Google AI Studio key is recognized (AIza...)",
      pa.detect_provider("AIzaSy" + "x" * 35)["slug"] == "google")
blackpig = pa.preset_by_name("BlackPig AI")
check("BlackPig preset uses its documented Anthropic Messages endpoint",
      blackpig and blackpig["base_url"] == "https://api.vv666.ai"
      and blackpig["api_style"] == "anthropic"
      and blackpig["model_hint"] == "claude-opus-5-5"
      and pa.preset_for_base_url("https://api.vv666.ai")["slug"] == "blackpig")
check("bare legacy sk- keys are NOT guessed (DeepSeek vs OpenAI legacy "
      "is unguessable -- the quick-pick serves them)",
      pa.detect_provider("sk-" + "f" * 46) is None)
check("empty/None keys are not guessed",
      pa.detect_provider("") is None and pa.detect_provider(None) is None)

# -- Fingerprint trios from the live browser tabs: NVIDIA / UnoRouter /
#    OpenCode Console -----------------------------------------------------
check("NVIDIA NIM key is recognized (nvapi-...)",
      pa.detect_provider("nvapi-" + "x" * 55)["slug"] == "nvidia")
check("OpenCode Console key is recognized (oc_sk_...)",
      pa.detect_provider("oc_sk_9f" + "x" * 40)["slug"] == "opencode")
uno = pa.preset_by_name("UnoRouter")
check("UnoRouter preset: right host + right /v1 base (api host, not the "
      "web app)", uno and uno["base_url"] ==
      "https://api.unorouter.com/v1"
      and uno["api_style"] == "openai_compat")
oc = pa.preset_by_name("OpenCode Console")
check("OpenCode preset: chat-completions family base URL",
      oc and oc["base_url"] == "https://opencode.ai/inference/openai/v1")
check("OpenCode preset: catalog override points at the ALL-families "
      "models endpoint",
      oc["models_url_override"].endswith("/inference/v1/models"))
nv = pa.preset_by_name("NVIDIA (build.nvidia.com / NIM)")
check("NVIDIA preset: integrate.api.nvidia.com/v1 base",
      nv and nv["base_url"] == "https://integrate.api.nvidia.com/v1")
check("base-URL recognition works for the three newcomers",
      pa.preset_for_base_url("https://api.unorouter.com/v1")["slug"]
      == "unorouter"
      and pa.preset_for_base_url(
          "https://opencode.ai/inference/openai/v1")["slug"] == "opencode"
      and pa.preset_for_base_url(
          "https://integrate.api.nvidia.com/v1")["slug"] == "nvidia")

# =========================================================================
# B: presets, base-URL recognition, dialect inference
# =========================================================================
or_hint = pa.preset_by_name("OpenRouter")
check("the presets catalog exposes the common paths",
      or_hint and or_hint["base_url"] == "https://openrouter.ai/api/v1"
      and pa.preset_by_name("DeepSeek")["base_url"].startswith(
          "https://api.deepseek"))
check("a localhost 'no key needed' preset exists (Ollama/LM Studio)",
      any(p["no_key_ok"] for p in pa.COMMON_PROVIDERS))
check("'Something else' is always present (fully custom escape hatch)",
      pa.preset_by_name("Something else (set the base URL yourself)")
      is not None)
check("base URLs recognize their provider (full and trimmed)",
      pa.preset_for_base_url("https://api.anthropic.com")["slug"]
      == "anthropic"
      and pa.preset_for_base_url(
          "https://openrouter.ai/api/v1/")["slug"] == "openrouter")
check("unknown base URLs return None honestly",
      pa.preset_for_base_url("https://my.private.proxy.example/v1") is None)
check("explicit api_style on an entry wins over URL inference",
      pa.infer_api_style("https://openrouter.ai/api/v1",
                         explicit="anthropic") == "anthropic")
check("unrecognized anthropic-looking bases still infer the anthropic "
      "dialect", pa.infer_api_style("https://anthropic-mirror.example") ==
      "anthropic")
check("everything else defaults to the openai-compatible dialect",
      pa.infer_api_style("https://litellm.private.local") ==
      "openai_compat")

# --- F12: the freellm.net free-tier onboarding (README directory of
# 2026-10-22) -- every probe-viable provider is a preset, fingerprints
# only where the prefix is provably unambiguous ---------------------------
check("F12: hugging face + github PAT fingerprints resolve",
      pa.detect_provider("hf_" + "x" * 40)["slug"] == "huggingface"
      and pa.detect_provider("ghp_" + "x" * 40)["slug"] == "github-models"
      and pa.detect_provider("github_pat_" + "x" * 30)["slug"]
      == "github-models")
_F12_NEW_SLUGS = [
    "modelscope", "llm7", "kilo", "ovhcloud", "aionlabs", "huggingface",
    "agnes", "alibaba", "siliconflow", "sambanova", "chutes", "glhf",
    "ai21", "nscale", "nebius", "opencode-zen", "zhipu-cn",
    "ollama-cloud", "cloudflare-ai", "requesty", "vercel-ai-gateway",
    "github-models", "mancer", "featherless", "nanogpt",
]
check("F12: every probe-viable README provider has a preset",
      all(any(p["slug"] == s for p in pa.COMMON_PROVIDERS)
          for s in _F12_NEW_SLUGS))
check("F12: the quick-pick grew to the README's 46-entry catalog",
      len(pa.COMMON_PROVIDERS) == 47)
check("F12: zukijourney stays OUT (Discord-gated key + Cloudflare "
      "bot-wall blocks even a catalog probe -- no dead buttons)",
      not any("zuki" in p["slug"] for p in pa.COMMON_PROVIDERS))
check("F12: new bases are recognized backwards to their presets",
      pa.preset_for_base_url(
          "https://api-inference.modelscope.cn/v1")["slug"] == "modelscope"
      and pa.preset_for_base_url(
          "https://api.llm7.io/v1")["slug"] == "llm7"
      and pa.preset_for_base_url(
          "https://models.github.ai/inference")["slug"]
      == "github-models")
check("F12: off-convention catalogs carry explicit overrides",
      pa.preset_for_base_url("https://ollama.com/api")
      ["models_url_override"] == "https://ollama.com/api/tags"
      and pa.preset_for_base_url("https://api.chutes.ai/v1")
      ["models_url_override"] == "https://llm.chutes.ai/v1/models")
check("F12: cloudflare preset keeps the account-id placeholder visible",
      "{account_id}" in pa.preset_for_base_url(
          "https://api.cloudflare.com/client/v4/accounts/"
          "{account_id}/ai/v1")["base_url"])

# =========================================================================
# C: shared request builders -- check, fetch, runtime must agree byte-wise
# =========================================================================
check("chat_url: openai-style appends /chat/completions",
      pa.chat_url("https://api.z.ai/api/paas/v4/", "openai_compat")
      == "https://api.z.ai/api/paas/v4/chat/completions")
check("chat_url: anthropic appends /v1/messages",
      pa.chat_url("https://api.anthropic.com", "anthropic")
      == "https://api.anthropic.com/v1/messages")
check("models_url: both dialects expose /models",
      pa.models_url("https://openrouter.ai/api/v1", "openai_compat")
      .endswith("/models")
      and pa.models_url("https://api.anthropic.com", "anthropic")
      == "https://api.anthropic.com/v1/models")
check("headers_for: Bearer on openai, x-api-key+version on anthropic",
      pa.headers_for("openai_compat", "k")["Authorization"] == "Bearer k"
      and pa.headers_for("anthropic", "k")["x-api-key"] == "k"
      and "anthropic-version" in pa.headers_for("anthropic", "k"))
check("headers_for: an EMPTY key carries NO auth header at all "
      "(localhost providers answer that way)",
      "Authorization" not in pa.headers_for("openai_compat", "")
      and "x-api-key" not in pa.headers_for("anthropic", ""))

payload_openai = pa.chat_payload("openai_compat", "m", "sys", "usr")
check("openai payload: system+user messages, NO max_tokens unless asked "
      "(the credit-gate parity that ended green-check/red-build)",
      payload_openai == {"model": "m", "messages":
                         [{"role": "system", "content": "sys"},
                          {"role": "user", "content": "usr"}]})
payload_ant = pa.chat_payload("anthropic", "m", "sys", "usr")
check("anthropic payload: system hoisted to a top-level field, "
      "max_tokens MANDATORY from the shared constant",
      payload_ant["system"] == "sys"
      and payload_ant["max_tokens"] == pa.ANTHROPIC_MAX_TOKENS_DEFAULT
      and payload_ant["messages"] == [{"role": "user", "content": "usr"}])
check("anthropic max_tokens constant is a real build-sized budget",
      isinstance(pa.ANTHROPIC_MAX_TOKENS_DEFAULT, int)
      and pa.ANTHROPIC_MAX_TOKENS_DEFAULT >= 4096)
check("openai payload honors an explicit max_tokens only when given",
      pa.chat_payload("openai_compat", "m", "", "u", max_tokens=5)
      ["max_tokens"] == 5)

check("reply extraction: anthropic content blocks join",
      pa.extract_reply_text("anthropic",
                            {"content": [{"type": "text", "text": "hi"},
                                         {"type": "text", "text": " there"}]})
      == "hi there")
check("reply extraction: openai choices fall back gracefully",
      pa.extract_reply_text("openai_compat",
                            {"choices": [{"message": {"content": "hi"}}]})
      == "hi"
      and pa.extract_reply_text("anthropic", {"weird": True}) == "")

# =========================================================================
# D: fetch_model_list -- the dialog's "Fetch models" engine against fakes
# =========================================================================
import requests as _rq


class _FakeResp:
    def __init__(self, code, json_data=None, text=""):
        self.status_code = code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


calls = []


def _fake_get_ok(url, headers, timeout):
    calls.append((url, headers))
    return _FakeResp(200, json_data={
        "data": [{"id": "m-b"}, {"id": "m-a"}, {"id": "m-b"}]})


def _fake_get_err(url, headers, timeout):
    return _FakeResp(401, json_data={"error": {"message": "bad key"}})


def _fake_get_html(url, headers, timeout):
    return _FakeResp(200, json_data=None, text="<html>dashboard</html>")


def _fake_get_ollama(url, headers, timeout):
    calls.append((url, headers))
    return _FakeResp(200, json_data={
        "models": [{"name": "m1", "model": "m1"},
                   {"model": "m2"}]})


orig_get = _rq.get
try:
    _rq.get = _fake_get_ok
    r = settings_checks.fetch_model_list(
        {"base_url": "https://openrouter.ai/api/v1", "api_key": "sk-x"})
    check("fetch: ok path returns the provider's REAL ids (deduped-cap)",
          r["ok"] and set(r["ids"]) == {"m-a", "m-b"})
    check("fetch: openai headers + correct URL used",
          calls[0][0].endswith("/models")
          and calls[0][1].get("Authorization") == "Bearer sk-x")

    calls.clear()
    r = settings_checks.fetch_model_list(
        {"base_url": "https://api.anthropic.com", "api_key": "sk-ant-x"})
    check("fetch: anthropic dialect uses /v1/models + x-api-key",
          r["ok"] and calls[0][0].endswith("/v1/models")
          and calls[0][1].get("x-api-key") == "sk-ant-x")

    _rq.get = _fake_get_err
    r = settings_checks.fetch_model_list(
        {"base_url": "https://openrouter.ai/api/v1", "api_key": "sk-x"})
    check("fetch: 4xx surfaces the provider's own reason, honestly",
          r["ok"] is False and "401" in r["error"] and "bad key" in r["error"])

    _rq.get = _fake_get_html
    r = settings_checks.fetch_model_list(
        {"base_url": "https://openrouter.ai/api/v1", "api_key": "sk-x"})
    check("fetch: an HTML dashboard URL is named as such, never silently "
          "swallowed", r["ok"] is False and "JSON" in r["error"])
    calls.clear()
    _rq.get = _fake_get_ok
    # OpenCode's catalog lives at /inference/v1/models, NOT under the
    # chat families' base -- the override must route the fetch there.
    r = settings_checks.fetch_model_list(
        {"base_url": "https://opencode.ai/inference/openai/v1",
         "api_key": "oc_sk_" + "x" * 40})
    check("fetch: OpenCode catalog fetch goes to /inference/v1/models "
          "(the override), not the family path",
          r["ok"] and calls[0][0].endswith("/inference/v1/models"))
    calls.clear()
    # NVIDIA/UnoRouter follow the OpenAI convention <base>/models.
    settings_checks.fetch_model_list(
        {"base_url": "https://integrate.api.nvidia.com/v1",
         "api_key": "nvapi-x"})
    settings_checks.fetch_model_list(
        {"base_url": "https://api.unorouter.com/v1", "api_key": "ur-x"})
    check("fetch: NVIDIA and UnoRouter catalogs use <base>/models",
          calls[0][0] == "https://integrate.api.nvidia.com/v1/models"
          and calls[1][0] == "https://api.unorouter.com/v1/models")

    calls.clear()
    # F12: chutes' catalog probe-verified at llm.chutes.ai, NOT the
    # api. host -- the override must reroute the fetch there.
    settings_checks.fetch_model_list(
        {"base_url": "https://api.chutes.ai/v1", "api_key": "ch-x"})
    check("fetch (F12): chutes routes to the llm.chutes.ai catalog "
          "override", calls[0][0] == "https://llm.chutes.ai/v1/models")

    _rq.get = _fake_get_ollama
    calls.clear()
    r = settings_checks.fetch_model_list(
        {"base_url": "https://ollama.com/api", "api_key": ""})
    check("fetch (F12): ollama-style {models:[{name/model}]} catalogs "
          "parse into the same id list",
          r["ok"] and set(r["ids"]) == {"m1", "m2"})
    check("fetch (F12): ollama-cloud hits the /api/tags override",
          calls[0][0] == "https://ollama.com/api/tags")
    _rq.get = _fake_get_ok

finally:
    _rq.get = orig_get

r = settings_checks.fetch_model_list({"base_url": ""})
check("fetch: no base URL -> immediate honest 'set it first'",
      r["ok"] is False and "No base" in r["error"])

# =========================================================================
# E: cost-class bucketing -- free vs trial vs paid vs honestly unstated
# =========================================================================
c = pa.classify_model_tier
check("pricing metadata with a non-zero field -> PAID (hard evidence)",
      c({"id": "m", "pricing": {"prompt": "0.000001",
                                "completion": "0"}}) == "paid")
check("pricing metadata with every field zero -> FREE",
      c({"id": "m", "pricing": {"prompt": "0", "completion": "0.0",
                                "request": "0"}}) == "free")
check("':free' suffix ids -> FREE (OpenRouter convention)",
      c("poolside/laguna-s-2.1:free") == "free"
      and c({"id": "gpt-oss-120b:free"}) == "free")
check("'-free' suffix ids -> FREE too (UnoRouter/OpenCode conventions)",
      c("glm-5.3-flash-free") == "free" and c("jev-1.13-free") == "free")
check("explicit flags honor the provider's word (True, 1, 'true' all "
      "qualify; explicit-false never does)",
      c({"id": "m", "free": True}) == "free"
      and c({"id": "m", "is_free": 1}) == "free"
      and c({"id": "m", "free": "true"}) == "free"
      and c({"id": "m", "free": "false"}) == "unstated"
      and c({"id": "m", "free": False}) == "unstated")
check("trial/preview markers -> TRIAL",
      c("dots-3-note-preview") == "trial")
check("detached 'free' inside nested metadata also classifies "
      "(OpenCode-style records)",
      c({"id": "exo-free", "owned_by": "n/a"}) == "free")
check("NO data of any kind -> UNSTATED, never a fabricated 'paid'",
      c("mystery-model-x") == "unstated"
      and c({"id": "big-pickle"}) == "unstated")
check("embedded 'free' as part of a longer word is NOT falsely "
      "classed (carefree motors are not free models)",
      c("carefree-autos-9000") == "unstated")
tiers = pa.classify_model_tiers([
    {"id": "paid-1", "pricing": {"prompt": "0.00001", "completion": "0"}},
    {"id": "f-1", "pricing": {"prompt": "0", "completion": "0"}},
    "glm-free", "prev-preview", {"id": "silent"}, "f-1", "glm-free"])
check("bulk classification bills every id into exactly one bucket and "
      "dedupes repeats", tiers["paid"] == ["paid-1"]
      and tiers["free"] == ["f-1", "glm-free"]
      and tiers["trial"] == ["prev-preview"]
      and tiers["unstated"] == ["silent"])

# =========================================================================
n_fail = sum(1 for _, ok in CHECKS if not ok)
print(f"\n{len(CHECKS) - n_fail}/{len(CHECKS)} checks passed")
sys.exit(1 if n_fail else 0)

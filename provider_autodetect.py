"""Provider auto-detection -- the "paste any key, we figure it out" layer.

The user dream: ANY person pastes ANY API key (free or paid, any
provider, any format) into the Settings panel and the agent just works.
Three pieces make it concrete:

1. KEY FINGERPRINTS -- most providers' keys carry recognizable shapes
   (sk-or-v1-..., sk-ant-..., gsk_...), so the dialog can fill in the
   provider's known base URL itself instead of asking the user to go
   read documentation.

2. A PRESET CATALOG -- for keys with no recognizable shape, a curated
   quick-pick of the common providers (OpenRouter, DeepSeek, xAI, ...)
   that fills the same fields with one tap, plus localhost presets
   (Ollama / LM Studio) that need no key at all.

3. DIALECT STREAMS -- "any format" also means any API SHAPE: most of
   the world speaks OpenAI-style /chat/completions, but Anthropic
   speaks /v1/messages with x-api-key headers, a mandatory max_tokens
   and content-block replies. This module owns the shared builders
   (URLs, headers, styles) so act_script's runtime dispatch,
   settings_checks' verify call, and the panel's model-list fetch all
   speak the IDENTICAL dialect per entry -- the same parity the
   credit-gate fix (act_script documentation) established: a check
   must see exactly what a build will send.

Everything here is pure data + pure functions -- importable from
act_script, settings_checks, settings_panel, and batteries with no
import cycles.
"""
import re

OPENAI_API_STYLE = "openai_compat"
ANTHROPIC_API_STYLE = "anthropic"

# Anthropic's /v1/messages REQUIRES max_tokens, so the no-max_tokens
# parity trick used for OpenAI-style entries can't apply -- instead the
# runtime dispatch and the settings check branch share THIS one value,
# which the battery pins as identical between both code paths. 8192 is
# enough headroom for a full single-file write while remaining cheap
# for a verify ping (which still only GENERATES a word or two).
ANTHROPIC_MAX_TOKENS_DEFAULT = 8192

ANTHROPIC_VERSION = "2023-06-01"


# -------------------------------------------------------------------------
# A provider hint is a small dict:
#   slug      -- stable id (also stored on entries as "provider_hint")
#   name      -- human label for the dialog
#   base_url  -- the messages API base (no trailing slash)
#   api_style -- OPENAI_API_STYLE or ANTHROPIC_API_STYLE
#   model_hint-- 1-3 CURRENT example model ids, for the placeholder text
#                (displayed as a hint only -- "Fetch models" is the real
#                list, per the no-stale-ids rule)
#   no_key_ok-- True for localhost providers where a missing key is fine
# -------------------------------------------------------------------------

def _slug(slug, name, base_url, model_hint="", api_style=OPENAI_API_STYLE,
          no_key_ok=False, models_url_override=""):
    return {"slug": slug, "name": name, "base_url": base_url,
            "api_style": api_style, "model_hint": model_hint,
            "no_key_ok": no_key_ok,
            "models_url_override": models_url_override}


_OPENROUTER = _slug("openrouter", "OpenRouter",
                    "https://openrouter.ai/api/v1",
                    "poolside/laguna-s-2.1:free, z-ai/glm-5.3")
_ANTHROPIC = _slug("anthropic", "Anthropic (Claude)",
                   "https://api.anthropic.com",
                   "claude-sonnet-4-8, claude-opus-4-6",
                   api_style=ANTHROPIC_API_STYLE)
_BLACKPIG = _slug("blackpig", "BlackPig AI",
                  "https://api.vv666.ai", "claude-opus-5-5",
                  api_style=ANTHROPIC_API_STYLE)
_GROQ = _slug("groq", "Groq",
              "https://api.groq.com/openai/v1",
              "openai/gpt-oss-120b, openai/gpt-oss-20b")
_OPENAI = _slug("openai", "OpenAI",
                "https://api.openai.com/v1", "gpt-5.2, gpt-5-mini")
_ZAI = _slug("zai", "Z.ai (Zhipu GLM)",
             "https://api.z.ai/api/paas/v4", "glm-5.3, glm-5.2")
_GOOGLE = _slug(
    "google", "Google AI Studio",
    "https://generativelanguage.googleapis.com/v1beta/openai",
    "gemini-3.8-flash, gemini-3.8-pro")
_XAI = _slug("xai", "xAI (Grok)", "https://api.x.ai/v1",
             "grok-4, grok-4-fast")
_CEREBRAS = _slug("cerebras", "Cerebras", "https://api.cerebras.ai/v1",
                  "llama-3.3-70b")
_DEEPSEEK = _slug("deepseek", "DeepSeek",
                  "https://api.deepseek.com", "deepseek-chat, deepseek-reasoner")
_MODELSCOPE = _slug("modelscope", "ModelScope",
                    "https://api-inference.modelscope.cn/v1",
                    "Qwen/Qwen3.5-35B-A3B, MiniMax/MiniMax-M2.5")
_LLM7 = _slug("llm7", "LLM7.io", "https://api.llm7.io/v1",
              "gpt-oss-20b, mistral-Nemo-Instruct-2407")
_KILO = _slug("kilo", "Kilo Code", "https://api.kilo.ai/api/gateway",
              "nvidia/nemotron-3-ultra-550b-a55b:free, kilo-auto/efficient")
_OVHCLOUD = _slug(
    "ovhcloud", "OVHcloud AI Endpoints",
    "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
    "qwen3.5-397b-a17b -- pick a TEXT model (their catalog also lists "
    "image models)")
_AION = _slug("aionlabs", "Aion Labs", "https://api.aionlabs.ai/v1",
              "aion-labs-aion-3.0")
_HUGGINGFACE = _slug("huggingface", "Hugging Face Router",
                     "https://router.huggingface.co/v1",
                     "meta-llama-3.1-8b-instruct, google/gemma-3-4b-it")
_AGNES = _slug("agnes", "Agnes AI", "https://apihub.agnes-ai.com/v1",
               "agnes-2.0-flash")
_DASHSCOPE = _slug("alibaba", "Alibaba Model Studio (DashScope)",
                   "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                   "qwen3-plus, qwen3-max")
_SILICONFLOW = _slug("siliconflow", "SiliconFlow",
                     "https://api.siliconflow.cn/v1",
                     "Qwen/Qwen3-8B, deepseek-ai/DeepSeek-R1-Distill-Qwen-7B")
_SAMBANOVA = _slug("sambanova", "SambaNova", "https://api.sambanova.ai/v1",
                   "DeepSeek-V3.1, MiniMax-M2.7")
_CHUTES = _slug("chutes", "Chutes.ai", "https://api.chutes.ai/v1",
                "deepseek-ai/DeepSeek-R1",
                models_url_override="https://llm.chutes.ai/v1/models")
_GLHF = _slug("glhf", "Glhf.chat", "https://glhf.chat/api/openai/v1",
              "meta-llama/Meta-Llama-3.1-70B-Instruct")
_AI21 = _slug("ai21", "AI21 Labs", "https://api.ai21.com/studio/v1",
              "jamba-large-1-7")
_NSCALE = _slug("nscale", "Nscale", "https://inference.api.nscale.com/v1",
                "llama-3-3-70b-instruct")
_NEBIUS = _slug("nebius", "Nebius", "https://api.studio.nebius.com/v1",
                "qwen3-235b-a22b")
_OPENCODE_ZEN = _slug("opencode-zen", "OpenCode Zen",
                      "https://opencode.ai/zen/v1",
                      "big-pickle, deepseek-v4-flash-free")
_BIGMODEL = _slug("zhipu-cn", "Zhipu AI (open.bigmodel.cn, China side)",
                  "https://open.bigmodel.cn/api/paas/v4",
                  "glm-4.7-flash -- for keys created ON that site; keys "
                  "made on z.ai/model-api use the Z.ai entry instead")
_OLLAMA_CLOUD = _slug("ollama-cloud", "Ollama Cloud",
                      "https://ollama.com/api",
                      "deepseek-v4-pro, minimax-m3",
                      models_url_override="https://ollama.com/api/tags")
_CLOUDFLARE = _slug(
    "cloudflare-ai", "Cloudflare Workers AI (needs your account id in "
    "the URL)", "https://api.cloudflare.com/client/v4/accounts/"
    "{account_id}/ai/v1",
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
_REQUESTY = _slug("requesty", "Requesty",
                  "https://router.requesty.ai/v1",
                  "minimaxi/minimax-m3.1-flash")
_VERCEL_AI = _slug("vercel-ai-gateway", "Vercel AI Gateway",
                   "https://ai-gateway.vercel.sh/v1",
                   "alibaba/qwen-3-14b")
_GITHUB_MODELS = _slug("github-models", "GitHub Models",
                       "https://models.github.ai/inference",
                       "openai/gpt-4o-mini -- sign in with a GitHub "
                       "personal access token")
_MANCER = _slug("mancer", "Mancer", "https://neuro.mancer.tech/oai/v1",
                "mythomax, llama2-70b")
_FEATHERLESS = _slug("featherless", "Featherless",
                     "https://api.featherless.ai/v1",
                     "meta-llama/Meta-Llama-3.1-8B-Instruct")
_NANOGPT = _slug("nanogpt", "NanoGPT", "https://nano-gpt.com/api/v1",
                 "fireworks/ember-1")
_UNOROUTER = _slug("unorouter", "UnoRouter",
                   "https://api.unorouter.com/v1",
                   "gpt-oss-120b:free (free), claude-haiku-5-5")
_OPENCODE = _slug(
    "opencode", "OpenCode Console",
    "https://opencode.ai/inference/openai/v1",
    "big-pickle, glm-5.3-flash-free -- pick a CHAT-COMPLETIONS family "
    "model (the console serves per-model API families; this is the one "
    "this client speaks)",
    models_url_override="https://opencode.ai/inference/v1/models")
_NVIDIA = _slug("nvidia", "NVIDIA (build.nvidia.com / NIM)",
                "https://integrate.api.nvidia.com/v1",
                "use Fetch models -- deepseek-ai/*, meta/*, nvidia/* ...")
_MOONSHOT = _slug("moonshot", "Moonshot (Kimi)",
                  "https://api.moonshot.ai/v1", "kimi-0905-preview")
_MISTRAL_G = _slug("mistral-generic", "Mistral (compatibility)",
                   "https://api.mistral.ai/v1", "mistral-small-latest")
_TOGETHER = _slug("together", "Together AI",
                  "https://api.together.xyz/v1", "")
_FIREWORKS = _slug("fireworks", "Fireworks AI",
                   "https://api.fireworks.ai/inference/v1", "")
_COHERE = _slug("cohere", "Cohere (OpenAI-compatibility)",
                "https://api.cohere.com/compatibility/v1", "command-a-03-2025")
_OLLAMA = _slug("ollama", "Ollama (local, no key)",
                "http://localhost:11434/v1", "llama3.3, qwen3",
                no_key_ok=True)
_LMSTUDIO = _slug("lmstudio", "LM Studio (local, no key)",
                  "http://localhost:1234/v1", "",
                  no_key_ok=True)
_SOMETHING_ELSE = _slug("custom", "Something else (set the base URL "
                        "yourself)", "", "")

# The quick-pick order in the dialog, curated: the most common +
# cheapest paths first, locals at the end, "custom" last.
COMMON_PROVIDERS = [
    # the free-tier gateways first -- the pipes a fresh user is most
    # likely to arrive at (freellm.net's directory order)
    _OPENROUTER, _UNOROUTER, _OPENCODE, _OPENCODE_ZEN, _NVIDIA,
    _MODELSCOPE, _HUGGINGFACE, _LLM7, _KILO, _REQUESTY, _VERCEL_AI,
    _GITHUB_MODELS, _SAMBANOVA, _SILICONFLOW,
    _DASHSCOPE, _BIGMODEL, _ZAI, _DEEPSEEK,
    # then the familiar established clouds
    _GROQ, _OPENAI, _GOOGLE, _XAI, _MOONSHOT, _CEREBRAS, _AION, _AGNES,
    _OVHCLOUD, _MISTRAL_G, _COHERE, _AI21, _TOGETHER, _FIREWORKS,
    _NSCALE, _NEBIUS, _CHUTES, _GLHF, _MANCER, _FEATHERLESS, _NANOGPT,
    _ANTHROPIC, _BLACKPIG,
    # cloud-gated and localhost escape hatches at the end
    _OLLAMA_CLOUD, _CLOUDFLARE, _OLLAMA, _LMSTUDIO, _SOMETHING_ELSE,
]

# -------------------------------------------------------------------------
# Key fingerprints -- ordered (most specific first). Each matcher sees the
# raw string the user pasted and returns True/False.
# -------------------------------------------------------------------------
_ZAI_KEY_RE = re.compile(r"^[0-9a-f]{32}\.[A-Za-z0-9]+$")


def _startswith(prefix):
    return lambda key: key.startswith(prefix)


KEY_FINGERPRINTS = [
    (_startswith("sk-or-v1-"), _OPENROUTER),
    (_startswith("nvapi-"), _NVIDIA),
    (_startswith("oc_sk_"), _OPENCODE),
    (_startswith("sk-ant-"), _ANTHROPIC),
    (_startswith("gsk_"), _GROQ),
    (_startswith("sk-proj-"), _OPENAI),
    (_startswith("sk-svcacct-"), _OPENAI),
    (_startswith("xai-"), _XAI),
    (_startswith("csk-"), _CEREBRAS),
    (lambda key: bool(_ZAI_KEY_RE.match(key)), _ZAI),
    (_startswith("AIza"), _GOOGLE),
    (_startswith("hf_"), _HUGGINGFACE),
    (_startswith("ghp_"), _GITHUB_MODELS),
    (_startswith("github_pat_"), _GITHUB_MODELS),
]


def detect_provider(api_key):
    """One pasted key -> a provider hint dict, or None when the shape
    says nothing (DeepSeek/Moonshot/OpenAI-legacy are all bare 'sk-...'
    and can't be told apart -- honest ambiguity beats a wrong guess)."""
    key = (api_key or "").strip()
    if not key:
        return None
    for matcher, hint in KEY_FINGERPRINTS:
        if matcher(key):
            return dict(hint)
    return None


def preset_by_name(name):
    """The dialog combobox maps its display strings back to hints."""
    for p in COMMON_PROVIDERS:
        if p["name"] == name:
            return dict(p)
    return None


_BASE_URL_TO_PRESET = {}


def _register_preset_urls(*presets):
    for p in presets:
        host = p["base_url"].rstrip("/").lower()
        if host:
            _BASE_URL_TO_PRESET[host] = p


_register_preset_urls(*COMMON_PROVIDERS)


def preset_for_base_url(base_url):
    """An entry whose base URL already matches a known provider's base
    URL -> its hint. Recognizes the full URL as stored and tolerates
    the trailing /chat/completions-style path remnants from hand-entry
    by matching progressively trimmed prefixes."""
    url = (base_url or "").strip().rstrip("/").lower()
    while url and url not in _BASE_URL_TO_PRESET:
        if "/" not in url[8:]:  # down to the host -- nothing left
            break
        url = url.rsplit("/", 1)[0]
    return dict(_BASE_URL_TO_PRESET[url]) if url in _BASE_URL_TO_PRESET else None


def infer_api_style(base_url, explicit=None):
    """The dialect an entry speaks. An explicit entry["api_style"] wins;
    otherwise the base URL decides by provider recognition, with
    'anthropic' in the host as a generic safety net. Default is the
    OpenAI-compatible stream virtually everything else on the internet
    speaks."""
    if explicit in (OPENAI_API_STYLE, ANTHROPIC_API_STYLE):
        return explicit
    hint = preset_for_base_url(base_url)
    if hint:
        return hint["api_style"]
    return (ANTHROPIC_API_STYLE if "anthropic" in (base_url or "").lower()
            else OPENAI_API_STYLE)


def entry_api_style(entry):
    """One line for every consumer -- the entry's dialect, settled."""
    return infer_api_style((entry or {}).get("base_url", ""),
                           explicit=(entry or {}).get("api_style") or None)


# -------------------------------------------------------------------------
# Shared request builders -- the single source of URL/headers truth, so
# dispatch (runtime), check (verify), and model fetching all send the
# IDENTICAL request skeleton for a given entry.
# -------------------------------------------------------------------------
def chat_url(base_url, api_style):
    """The messages endpoint for this entry."""
    base = (base_url or "").strip().rstrip("/")
    if api_style == ANTHROPIC_API_STYLE:
        if base.endswith("/v1/messages"):
            return base
        if base.endswith("/v1"):
            return base + "/messages"
        return base + "/v1/messages"
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def models_url(base_url, api_style, override=""):
    """The model LISTING endpoint (OpenAI-style and Anthropic both have
    one). A provider's own override wins when its catalog lives off the
    OpenAI-convention path (OpenCode's chat families sit under
    /inference/openai/v1/... while its catalog is /inference/v1/models --
    one endpoint serves every family). Returns None for styles/URLs we
    can't guess."""
    if override:
        return override
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return None
    if api_style == ANTHROPIC_API_STYLE:
        if base.endswith("/v1"):
            return base + "/models"
        return base + "/v1/models"
    if base.endswith("/models"):
        return base
    return base + "/models"


def headers_for(api_style, api_key):
    """Auth headers the same for runtime, check, and fetching. A missing
    key yields NO auth header -- local servers (Ollama/LM Studio) and
    pre-authenticated proxies answer happily this way."""
    key = (api_key or "").strip()
    headers = {"Content-Type": "application/json"}
    if api_style == ANTHROPIC_API_STYLE:
        if key:
            headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def chat_payload(api_style, model, system_prompt, user_prompt,
                 max_tokens=None):
    """The body for one messages call, in this entry's dialect. Anthropic
    requires max_tokens (shared default constant when none is given);
    OpenAI-compatible bodies carry NO max_tokens unless the caller
    explicitly asked for one -- see act_script's credit-gate comment
    for why that's load-bearing. `system_prompt` may be empty (Act
    Script's call_model always sends one; a bare check ping may not)."""
    user_prompt = user_prompt or " "
    if api_style == ANTHROPIC_API_STYLE:
        body = {"model": model,
                "max_tokens": max_tokens or ANTHROPIC_MAX_TOKENS_DEFAULT,
                "messages": [{"role": "user", "content": user_prompt}]}
        if system_prompt:
            body["system"] = system_prompt
        return body
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    body = {"model": model, "messages": messages}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    return body


# -------------------------------------------------------------------------
# Model cost-class bucketing -- the "separate FREE from TRIAL from PAID"
# part of fetching models. Providers report pricing in wildly different
# shapes, so this is conservative and HONEST: hard evidence first
# (numeric pricing), explicit flags next, naming tokens last, and
# anything with NO usable signal lands in "unstated" with that label
# rather than a fabricated verdict. Four outcome buckets:
#   free     -- every listed monetary price is 0, or the provider/id
#               marks it free (:free, -free, free as a token, free:true)
#   trial    -- id/metadata carries trial/preview (but the model costs?)
#   paid     -- pricing data exists and any amount is > 0
#   unstated -- no pricing signal at all (say so; never call it "paid")
# -------------------------------------------------------------------------
_MONEY_FIELDS = ("prompt", "completion", "request", "image",
                 "internal_reasoning", "input", "output", "per_1m")
_FREE_TOKENS = {"free", "gratis"}
_TRIAL_TOKENS = {"trial", "preview", "beta", "exp", "experimental"}


def _money_amounts(item):
    amounts = []
    pricing = item.get("pricing") or item.get("prices") or {}
    if isinstance(pricing, dict):
        for key, value in pricing.items():
            try:
                amounts.append(float(value))
            except (TypeError, ValueError):
                continue
    return amounts


def _tokens(text):
    return set(re.split(r"[^a-z0-9]+", (text or "").lower()))        - {""}


def classify_model_tier(item):
    """One provider model item (dict with at least id, or a bare id
    string) -> tier in {"free", "trial", "paid", "unstated"}."""
    if isinstance(item, str):
        item = {"id": item}
    if not isinstance(item, dict):
        return "unstated"
    model_id = str(item.get("id") or "")
    # 1. hard data: explicit numeric pricing
    amounts = _money_amounts(item)
    if amounts:
        if any(a > 0 for a in amounts):
            return "paid"
        if all(a == 0 for a in amounts):
            return "free"
    # 2. hard data: explicit flags -- truthy values in any of the
    # provider-native spellings (bool True, 1, "true"/"yes"), while
    # explicit-false spellings ("false", 0, None) never qualify.
    _FALSY = {False, 0, None, "", "false", "no", "0"}
    for key in ("free", "is_free", "zero_cost"):
        flag = item.get(key)
        if flag is not None and flag not in _FALSY:
            return "free"
    # 3. naming tokens in id + compact metadata -- but the
    # flag keys themselves are NOT evidence: {"free": "false"}
    # would otherwise tokenize its own key into "free" and
    # self-classify as free.
    tokens = _tokens(model_id)
    meta = " ".join(f"{k} {v}" for k, v in item.items() if k != "id"
                    and k not in ("free", "is_free", "zero_cost",
                                  "pricing", "prices")).lower()
    match_n = tokens | _tokens(meta)
    if match_n & _FREE_TOKENS:
        return "free"
    if match_n & _TRIAL_TOKENS:
        return "trial"
    return "unstated"


def classify_model_tiers(items):
    """A list of raw provider model items -> {"free": [ids...], "trial":
    [...], "paid": [...], "unstated": [...]}, order serde-safe (each
    bucket keeps the provider's own order, deduped by first sight)."""
    tiers = {"free": [], "trial": [], "paid": [], "unstated": []}
    seen = set()
    for item in (items or []):
        model_id = item.get("id") if isinstance(item, dict) else item
        model_id = str(model_id or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        tiers[classify_model_tier(item)].append(model_id)
    return tiers


def extract_reply_text(api_style, data):
    """Reply text out of a successful payload, both dialects. Anthropic
    answers with a content-block list; joined text blocks win; an
    OpenAI-shaped envelope is tolerated as a fallback (some Anthropic-
    compatible gateways answer in the OpenAI shape)."""
    if not isinstance(data, dict):
        return ""
    content = data.get("content")
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        if parts:
            return "".join(parts)
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""

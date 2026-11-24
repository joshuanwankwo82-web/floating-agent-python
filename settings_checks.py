"""Real connectivity checks for the Settings panel's green-check/red-x
status per model -- the user's own requirement for this feature was
explicit: "before it checks it make a silent call in its backend then
checks it if it connects", i.e. the green check must come from an actual
live backend call, never just "a key string is present".

Every function here takes whatever settings_store.py stored for one
model entry and returns `(ok: bool, detail: str)`. None of them ever
raise -- a missing SDK, a bad key, a timeout, or an unknown provider all
come back as a plain `(False, "<human-readable reason>")` instead of an
exception, so one bad entry can never crash the panel or block checking
everything else.

HONESTY UPGRADE (deliberate product decision): for every CHAT-
generation kind (gemini/groq/mistral and custom OpenAI-compatible
entries) the check now performs a REAL MINIMAL GENERATION -- a 1-token
"ping" -- because the green check the user actually relies on must mean
"this entry can answer", not merely "this key authenticates". A bare
model-listing check showed "Connected" for an account that was out of
balance: its key was valid for listing, and every real build call
against it failed with 402. The ping is CAPPED (see SPEND-SAFE CHECKING
below) and "Check all" verifies through free catalog/account lookups,
so checking no longer drains the balance it is trying to verify. Checks that carry a different, still
honest claim say so in their own wording: embed checkers do a real
1-item embed; image checkers verify credentials/balance/endpoint
without rendering (rendering costs real credits -- read each one's
docstring); the local Piper/Whisper checks stay fully offline; and
Tavily still spends one tiny search because it has no free ping
endpoint -- see check_tavily's own docstring.

All of these do real network I/O and so are meant to be called from a
background thread, never directly on the Tk main thread -- see
settings_panel.py's `_run_check` for the threading/callback wiring.
"""
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# -------------------------------------------------------------------------
# SPEND-SAFE CHECKING (fixes "Check all drains my tokens and flips models
# between Connected / Not connected"):
#
# 1. The single-row ping is CAPPED (CHECK_PING_MAX_TOKENS). The old ping
#    sent NO max_tokens (8192 on Anthropic) so every click could generate a
#    full reply -- reasoning models think for hundreds of tokens -- and
#    credit-gated gateways (OpenRouter style) reserved each model's FULL
#    max output against the balance, so as the checks themselves spent the
#    balance, the pricier models started answering 402 -> red.
# 2. "Check all" spends NO tokens: one free GET of the provider's model
#    catalog per (base URL, key) proves the key and that each model is
#    offered; OpenRouter's free /key endpoint reports remaining credit.
#    Only a model the catalog can't vouch for gets a capped ping.
# 3. Requests per provider are paced (sequential within a key, a short gap
#    between pings) instead of a burst that trips free-tier rate limits.
# 4. TEMPORARY failures (429, 5xx, timeouts, connection drops) return
#    ok=None == "couldn't tell right now": the panel keeps the model's
#    previous status instead of turning a working model red (and "Remove
#    not connected" can no longer delete it).
# -------------------------------------------------------------------------
CHECK_PING_MAX_TOKENS = 16          # enough for "OK" on every provider's minimum
CHECK_PING_PROMPT = "Reply with the single word OK."
CHECK_ALL_PARALLEL_PROVIDERS = 4    # different providers checked side by side
CHECK_ALL_PING_SPACING_S = 1.2      # gap between pings on the SAME key
TRANSIENT_HTTP = {408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522,
                  523, 524, 529}
_TRANSIENT_TEXT = re.compile(
    r"\b(429|500|502|503|504|529)\b|rate.?limit|too many requests|timed? ?out|"
    r"timeout|temporar|overloaded|unavailable|try again|connection (reset|"
    r"aborted|refused)|max retries", re.IGNORECASE)


def check_gemini(entry):
    api_key = entry.get("api_key", "")
    if not api_key:
        return False, "No API key configured."
    try:
        from google import genai
    except Exception as exc:  # noqa: BLE001
        return False, f"google-genai isn't installed: {exc}"
    try:
        client = genai.Client(api_key=api_key)
        # A REAL 1-token generation -- green means this entry can
        # genuinely answer. Billing-only failures (e.g. HTTP 402) never
        # show green: the generation itself is what gets verified, not
        # a free "list models" that any valid key passes.
        model = (entry.get("model") or "").strip() or "gemini-2.0-flash"
        _one_token_generation(
            lambda: client.models.generate_content(
                model=model, contents="ping",
                config={"max_output_tokens": 1}),
            lambda: client.models.generate_content(model=model,
                                                   contents="ping"))
        return True, f"Generated a reply with {model} -- this entry can chat."
    except Exception as exc:  # noqa: BLE001
        return False, _short(exc)


def check_gemini_embed(entry):
    """A real 1-item embed against the same client/key chat Gemini
    uses -- see memory.py's _embed_via_gemini, which calls the same API
    with the same key, just a different model id downstream of this
    check."""
    api_key = entry.get("api_key", "")
    if not api_key:
        return False, "No API key configured."
    try:
        from google import genai
    except Exception as exc:  # noqa: BLE001
        return False, f"google-genai isn't installed: {exc}"
    try:
        client = genai.Client(api_key=api_key)
        model = (entry.get("model") or "").strip() or "text-embedding-004"
        client.models.embed_content(model=model, contents="ping")
        return True, f"Embedded text with {model} -- this entry can embed."
    except Exception as exc:  # noqa: BLE001
        return False, _short(exc)


def check_groq(entry):
    api_key = entry.get("api_key", "")
    if not api_key:
        return False, "No API key configured."
    try:
        from groq import Groq
    except Exception as exc:  # noqa: BLE001
        return False, f"groq isn't installed: {exc}"
    try:
        client = Groq(api_key=api_key)
        # Real 1-token chat completion (see check_gemini's comment:
        # billing/quota failures can only surface through a real
        # generation, never through listing models).
        model = (entry.get("model") or "").strip() or "openai/gpt-oss-120b"
        # ^ Groq retired the llama-3.x ids 2026-08-16; gpt-oss-120b is
        #   their recommended replacement (see GROQ_MODEL in agent_overlay).
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1)
        return True, f"Generated a reply with {model} -- this entry can chat."
    except Exception as exc:  # noqa: BLE001
        return False, _short(exc)


def check_mistral(entry):
    api_key = entry.get("api_key", "")
    if not api_key:
        return False, "No API key configured."
    try:
        from mistralai import Mistral
    except Exception as exc:  # noqa: BLE001
        return False, f"mistralai isn't installed/importable: {exc}"
    try:
        client = Mistral(api_key=api_key)
        # Real 1-token chat completion (same justification as
        # check_gemini / check_groq above).
        model = (entry.get("model") or "").strip() or "mistral-small-latest"
        client.chat.complete(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1)
        return True, f"Generated a reply with {model} -- this entry can chat."
    except Exception as exc:  # noqa: BLE001
        return False, _short(exc)


def check_mistral_embed(entry):
    """A real 1-item embed -- same honesty rule as
    check_gemini_embed: green must mean this entry can embed, not just
    that the key authenticates."""
    api_key = entry.get("api_key", "")
    if not api_key:
        return False, "No API key configured."
    try:
        from mistralai import Mistral
    except Exception as exc:  # noqa: BLE001
        return False, f"mistralai isn't installed/importable: {exc}"
    try:
        client = Mistral(api_key=api_key)
        model = (entry.get("model") or "").strip() or "mistral-embed"
        client.embeddings.create(model=model, inputs="ping")
        return True, f"Embedded text with {model} -- this entry can embed."
    except Exception as exc:  # noqa: BLE001
        return False, _short(exc)


def check_tavily(entry):
    """Tavily has no free "ping" endpoint, so confirming a key for real
    unavoidably means spending one real (tiny) search call against it --
    unlike the other checks here, this one is deliberately never run
    automatically; see settings_panel.py, where it's only triggered by
    an explicit user click (right after adding/editing the key, or a
    manual recheck), same as every other check, but worth calling out
    here since it's the one exception to the "check calls are free"
    design the other functions rely on."""
    api_key = entry.get("api_key", "")
    if not api_key:
        return False, "No API key configured."
    try:
        from tavily import TavilyClient
    except Exception as exc:  # noqa: BLE001
        return False, f"tavily-python isn't installed: {exc}"
    try:
        client = TavilyClient(api_key=api_key)
        client.search("connectivity check", max_results=1)
        return True, "Connected."
    except Exception as exc:  # noqa: BLE001
        return False, _short(exc)


def check_local_piper(entry):
    """Piper runs fully offline -- there's nothing remote to "connect"
    to, so this checks the one thing that can actually fail locally: the
    piper package being importable and the chosen voice model file
    actually being present on disk."""
    try:
        import piper  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"piper-tts isn't installed: {exc}"
    try:
        import overlay_speech
        model_path = getattr(overlay_speech, "PIPER_VOICE_MODEL", None)
        if model_path and not os.path.isfile(model_path):
            return False, (f"Voice model not found at {model_path} -- run "
                            "setup_piper_voice.py to download it.")
    except Exception:  # noqa: BLE001
        pass  # overlay_speech not importable here (e.g. in a test) -- not fatal
    return True, "Installed and ready (fully offline, no API key needed)."


def check_local_whisper(entry):
    """Same idea as check_local_piper: faster-whisper runs fully
    offline, so this just confirms the package itself is importable."""
    try:
        import faster_whisper  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"faster-whisper isn't installed: {exc}"
    return True, "Installed (model downloads on first use, fully offline after that)."


def check_cloudflare_image(entry):
    """Verifies a Cloudflare Workers AI image entry: the API token must
    authenticate against the account's Workers AI model catalog. This is
    a real credential check (not mere reachability), and it does not
    spend any generation quota -- it only lists models."""
    base_url = (entry.get("base_url") or "").strip().rstrip("/")
    api_key = (entry.get("api_key") or "").strip()
    if not api_key:
        return False, ("No API token configured -- paste your own Cloudflare "
                       "API token in the Edit dialog.")
    try:
        from friday_image.providers import normalize_cloudflare_base
        base_url = normalize_cloudflare_base(base_url)
    except Exception:  # noqa: BLE001 -- fall through with the raw value
        pass
    if not base_url or "/accounts/" not in base_url:
        return False, ("Base URL must be your Cloudflare account URL: "
                       "https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>")
    root = base_url.split("/ai/")[0]
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return False, f"The 'requests' package isn't installed: {exc}"
    try:
        resp = requests.get(f"{root}/ai/models/search",
                            headers={"Authorization": f"Bearer {api_key}"},
                            timeout=8)
        if resp.status_code == 200:
            return True, ("Token authenticated -- Workers AI is reachable. "
                          "The Image Generation panel can use this entry.")
        if resp.status_code in (400, 401, 403):
            return False, (f"Cloudflare rejected the token (HTTP "
                           f"{resp.status_code}). Create a token with "
                           "Workers AI read/run permissions and try again.")
        if resp.status_code == 404:
            return False, ("Account not found (HTTP 404) -- check the "
                           "ACCOUNT_ID portion of the base URL.")
        return False, _short(Exception(f"Cloudflare answered HTTP {resp.status_code}"))
    except Exception as exc:  # noqa: BLE001
        return False, _short(exc)


def check_stability_image(entry):
    """Verifies a Stability AI image entry by reading the account's credit
    balance -- a real credential check that spends no generation credits."""
    api_key = (entry.get("api_key") or "").strip()
    if not api_key:
        return False, ("No API key configured -- create one at "
                       "platform.stability.ai -> Account -> API Keys.")
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return False, f"The 'requests' package isn't installed: {exc}"
    try:
        resp = requests.get("https://api.stability.ai/v1/user/balance",
                            headers={"Authorization": f"Bearer {api_key}",
                                     "Accept": "application/json"},
                            timeout=8)
        if resp.status_code == 200:
            credits = "?"
            try:
                credits = resp.json().get("credits", "?")
            except Exception:  # noqa: BLE001 -- balance detail is cosmetic
                pass
            return True, (f"Key authenticated -- credit balance: {credits}. "
                          "The Image Generation panel can use this entry.")
        if resp.status_code in (401, 403):
            return False, (f"Stability rejected the key (HTTP "
                           f"{resp.status_code}). Create a fresh key at "
                           "platform.stability.ai -> Account -> API Keys.")
        return False, _short(Exception(f"Stability answered HTTP {resp.status_code}"))
    except Exception as exc:  # noqa: BLE001
        return False, _short(exc)


def check_openai_image(entry):
    """Verifies an OpenAI-compatible image entry by listing models -- a
    real credential check that generates nothing."""
    base_url = (entry.get("base_url") or "").strip().rstrip("/")
    api_key = (entry.get("api_key") or "").strip()
    if not api_key:
        return False, "No API key configured."
    if "/v1" not in base_url:
        return False, ("Base URL must be an OpenAI-compatible /v1 API root, "
                       "e.g. https://api.openai.com/v1")
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return False, f"The 'requests' package isn't installed: {exc}"
    try:
        resp = requests.get(f"{base_url}/models",
                            headers={"Authorization": f"Bearer {api_key}"},
                            timeout=8)
        if resp.status_code == 200:
            return True, ("Key authenticated -- the Image Generation panel "
                          "can use this entry.")
        if resp.status_code in (401, 403):
            return False, f"Key rejected (HTTP {resp.status_code})."
        return False, _short(Exception(f"HTTP {resp.status_code}"))
    except Exception as exc:  # noqa: BLE001
        return False, _short(exc)


def check_ideogram_image(entry):
    """Best-effort Ideogram check: generate costs credits, so this only
    confirms the endpoint is reachable and the key isn't rejected."""
    api_key = (entry.get("api_key") or "").strip()
    if not api_key:
        return False, "No API key configured -- paste your Ideogram API key."
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return False, f"The 'requests' package isn't installed: {exc}"
    try:
        resp = requests.get("https://api.ideogram.ai/v1/ideogram-v3/generate",
                            headers={"Api-Key": api_key}, timeout=8)
        if resp.status_code in (400, 404, 405, 422):
            return True, ("Endpoint reached and the key was not rejected -- "
                          "a real render is the full confirmation.")
        if resp.status_code in (401, 403):
            return False, f"Key rejected (HTTP {resp.status_code})."
        return True, f"Endpoint reachable (HTTP {resp.status_code})."
    except Exception as exc:  # noqa: BLE001
        return False, _short(exc)


def check_generic_http(entry):
    """Real generation check for a custom entry (everything added
    through the panel's "+ Add Model" button), in THAT ENTRY'S OWN
    provider dialect (OpenAI-compatible or Anthropic -- see
    provider_autodetect). When a model name is stored, the check POSTS
    one CAPPED ping (CHECK_PING_MAX_TOKENS) to the same URL with the same
    headers the build pipeline uses, so green still means "this model
    really generated", at a cost of a few dozen tokens instead of a full
    reply. Credit is checked through the provider's free account
    endpoint where one exists (OpenRouter /key) rather than by making a
    max-size reservation. Temporary failures (429, 5xx, timeouts,
    dropped connections) return ok=None so the panel keeps the previous
    status. With NO model name stored the honest reachability claim is
    kept and labelled as exactly that -- generation explicitly NOT
    verified."""
    base_url = (entry.get("base_url") or "").strip()
    if not base_url:
        return False, "No API base URL configured to check."
    model = (entry.get("model") or "").strip()
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return False, f"The 'requests' package isn't installed: {exc}"
    import provider_autodetect
    style = provider_autodetect.entry_api_style(entry)
    headers = provider_autodetect.headers_for(style, entry.get("api_key", ""))

    if not model:
        # No model name -> no honest generation claim possible. Keep the
        # bare reachability probe but SAY it honestly.
        try:
            resp = requests.get(base_url, headers=headers, timeout=6)
            if resp.status_code < 500:
                return True, (f"Server responded (HTTP {resp.status_code}) -- "
                              "reachable. NOTE: no model name is set, so "
                              "generation was NOT verified -- add the model "
                              "and recheck to prove this entry can answer.")
            return False, f"Server error (HTTP {resp.status_code})."
        except Exception as exc:  # noqa: BLE001
            return False, _short(exc)

    # URL/headers come from the SAME builders act_script's runtime uses
    # (dialect parity). The body is a CAPPED ping -- see SPEND-SAFE
    # CHECKING at the top: an uncapped ping burned real tokens on every
    # click and made credit-gated gateways flip models to 402.
    url = provider_autodetect.chat_url(base_url, style)
    payload = provider_autodetect.chat_payload(
        style, model, "", CHECK_PING_PROMPT, max_tokens=CHECK_PING_MAX_TOKENS)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        if (resp.status_code == 400 and style != provider_autodetect.ANTHROPIC_API_STYLE
                and "max_tokens" in (resp.text or "")):
            # Newer OpenAI-family models refuse `max_tokens` and want
            # `max_completion_tokens`; keep the cap, just rename it.
            payload.pop("max_tokens", None)
            payload["max_completion_tokens"] = CHECK_PING_MAX_TOKENS
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
    except requests.exceptions.Timeout:
        return None, ("The provider didn't answer within 20 s -- probably busy. "
                      "Status left unchanged; try again in a minute.")
    except requests.exceptions.ConnectionError as exc:
        return None, (f"Couldn't reach {base_url} right now ({_short(exc)}). "
                      "Status left unchanged -- check your internet or the base URL.")
    except Exception as exc:  # noqa: BLE001
        return False, _short(exc)
    code = resp.status_code
    if 200 <= code < 300:
        detail = f"Generated a reply with {model} -- this entry can chat."
        info = openrouter_key_info(base_url, entry.get("api_key", ""))
        verdict = _credit_verdict(info, model)
        if verdict is not None and verdict[0] is False:
            return verdict
        if verdict is not None:
            detail += " " + verdict[1]
        return True, detail
    return _classify_http_failure(code, model, resp)


def _body_hint(resp):
    try:
        return (resp.text or "").strip().replace("\n", " ")[:120]
    except Exception:  # noqa: BLE001
        return ""


def _classify_http_failure(code, model, resp=None):
    """Non-2xx -> (ok, detail). ok=None means TEMPORARY: keep the old status."""
    if code == 401:
        return False, ("Key rejected (HTTP 401) -- generate a fresh API "
                       "key and update this entry.")
    if code == 403:
        hint = _body_hint(resp) if resp is not None else ""
        return False, ("Access refused (HTTP 403) -- the key is invalid, or a "
                       "spend/budget limit on it is used up"
                       + (f": {hint}" if hint else "."))
    if code == 402:
        return False, ("No usable balance on this account (HTTP 402) -- "
                       "top up at the provider's billing page, or swap "
                       "this entry for a funded one.")
    if code == 404:
        return False, (f"Not found (HTTP 404) -- the model '{model}' or the "
                       "chat endpoint doesn't exist at this base URL.")
    if code == 405:
        return False, ("Method Not Allowed (HTTP 405) -- this address looks "
                       "like a PAGE (e.g. the provider's dashboard/API-keys "
                       "screen), not its API endpoint. Put the API base in "
                       "the URL field, for example "
                       "https://api.deepseek.com for DeepSeek.")
    if code == 429:
        return None, (f"Rate-limited right now (HTTP 429) -- the key works but "
                      f"the provider is throttling requests. Status left "
                      "unchanged; it clears when the limit window resets.")
    if code in TRANSIENT_HTTP or code >= 500:
        hint = _body_hint(resp) if resp is not None else ""
        return None, (f"Provider temporarily unavailable (HTTP {code}) -- "
                      "status left unchanged, try again shortly."
                      + (f" Server said: {hint}" if hint else ""))
    hint = _body_hint(resp) if resp is not None else ""
    return False, (f"Rejected (HTTP {code})"
                   + (f" -- server says: {hint}" if hint else ""))


# -------------------------------------------------------------------------
# Free account lookups (no generation, no tokens)
# -------------------------------------------------------------------------
OPENROUTER_HOSTS = ["openrouter.ai"]   # hosts that serve OpenRouter's /key endpoint


def openrouter_key_info(base_url, api_key):
    """OpenRouter's free GET /api/v1/key: remaining credit on this key and
    free-model requests left today. None for other providers or on error."""
    if not api_key or not any(h in (base_url or "").lower() for h in OPENROUTER_HOSTS):
        return None
    try:
        import requests
        resp = requests.get(base_url.rstrip("/") + "/key", timeout=8,
                            headers={"Authorization": f"Bearer {api_key}"})
        if resp.status_code != 200:
            return None
        data = resp.json().get("data")
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _is_free_model_id(model):
    return str(model or "").lower().endswith(":free")


def _credit_verdict(info, model):
    """(ok, note) from OpenRouter key info, or None when nothing to say.
    ok False only when a PAID model can't run (no credit left / budget spent)."""
    if not info:
        return None
    notes = []
    free_req = info.get("free_model_daily_requests") or {}
    remaining = info.get("limit_remaining")
    budget = info.get("effective_budget") or {}
    if _is_free_model_id(model):
        if isinstance(free_req.get("remaining"), (int, float)):
            notes.append(f"{int(free_req['remaining'])} free requests left today.")
        return (True, " ".join(notes)) if notes else None
    if isinstance(remaining, (int, float)) and remaining <= 0:
        return False, ("This key's credit limit is used up -- paid models will "
                       "fail with HTTP 402. Top up or raise the key's limit on "
                       "openrouter.ai, or use a ':free' model.")
    if isinstance(budget.get("remaining_usd"), (int, float)) and budget["remaining_usd"] <= 0:
        return False, ("This key's spending budget is used up -- requests will be "
                       "refused until it resets or is raised on openrouter.ai.")
    if isinstance(remaining, (int, float)):
        notes.append(f"${remaining:,.2f} credit left on this key.")
    return (True, " ".join(notes)) if notes else None


# -------------------------------------------------------------------------
# "Check all": grouped, paced, deduplicated, and token-free where possible
# -------------------------------------------------------------------------
def _catalog(entry):
    """One free GET of the provider's model list.
    -> ("ok", set_of_ids) | ("rejected", detail) | ("transient", detail) | ("none", detail)"""
    import requests
    import provider_autodetect
    base_url = (entry.get("base_url") or "").strip()
    style = provider_autodetect.entry_api_style(entry)
    hint = provider_autodetect.preset_for_base_url(base_url) or {}
    url = provider_autodetect.models_url(
        base_url, style, override=hint.get("models_url_override", ""))
    if not url:
        return "none", "no model-listing URL for this base"
    try:
        resp = requests.get(url, timeout=10, headers=provider_autodetect.headers_for(
            style, entry.get("api_key", "")))
    except requests.exceptions.RequestException as exc:
        return "transient", (f"Couldn't reach {base_url} right now ({_short(exc)}). "
                             "Status left unchanged.")
    code = resp.status_code
    if code in (401, 403):
        return "rejected", _classify_http_failure(code, "", resp)[1]
    if code in TRANSIENT_HTTP or code >= 500:
        return "transient", _classify_http_failure(code, "", resp)[1]
    if code >= 400:
        return "none", f"HTTP {code}"
    try:
        data = resp.json()
    except ValueError:
        return "none", "catalog wasn't JSON"
    items = (data.get("data") or data.get("models") or []) if isinstance(data, dict) else data
    ids = set()
    for item in items or []:
        mid = (item.get("id") or item.get("name") or item.get("model")) if isinstance(item, dict) else item
        if mid:
            ids.add(str(mid).removeprefix("models/").lower())
    return ("ok", ids) if ids else ("none", "empty catalog")


def _check_provider_group(group, on_result, should_stop, spacing):
    """All entries sharing one (base URL, key): ONE catalog call, one
    credit lookup, then a capped ping only for models the catalog can't
    vouch for. Duplicate models are checked once."""
    first = group[0][1]
    state, payload = _catalog(first)
    if state in ("rejected", "transient"):
        ok = False if state == "rejected" else None
        for faculty, entry in group:
            on_result(faculty, entry["id"], ok, payload)
        return
    info = openrouter_key_info(first.get("base_url", ""), first.get("api_key", ""))
    by_model = {}
    for faculty, entry in group:
        by_model.setdefault((entry.get("model") or "").strip(), []).append((faculty, entry))
    pinged = False
    for model, members in by_model.items():
        if should_stop():
            return
        if state == "ok" and model.lower() in payload:
            result = (True, f"Key works and the provider offers {model} -- "
                            "checked without spending any tokens.")
            verdict = _credit_verdict(info, model)
            if verdict is not None:
                result = verdict if verdict[0] is False else (True, result[1] + " " + verdict[1])
        else:
            if pinged and spacing:
                time.sleep(spacing)
            result = run_check(dict(members[0][1]))
            pinged = True
        for faculty, entry in members:
            on_result(faculty, entry["id"], result[0], result[1])


def check_many(items, on_result, should_stop=lambda: False,
               max_parallel=CHECK_ALL_PARALLEL_PROVIDERS,
               spacing=CHECK_ALL_PING_SPACING_S):
    """The "Check all" engine. `items` is [(faculty, entry_dict), ...];
    on_result(faculty, entry_id, ok, detail) is called once per entry from
    worker threads (ok may be None = temporary, keep the old status).
    Blocks until every entry has a result. Never raises."""
    groups, solo = {}, []
    for faculty, entry in items:
        kind = entry.get("check_kind") or "generic_http"
        if kind == "generic_http" and (entry.get("model") or "").strip() \
                and (entry.get("base_url") or "").strip():
            key = ((entry.get("base_url") or "").strip().rstrip("/").lower(),
                   entry.get("api_key") or "", entry.get("api_style") or "")
            groups.setdefault(key, []).append((faculty, entry))
        else:
            solo.append((faculty, entry))
    lock = threading.Lock()
    done = set()

    def _safe_result(faculty, entry_id, ok, detail):
        with lock:
            if (faculty, entry_id) in done:
                return
            done.add((faculty, entry_id))
        try:
            on_result(faculty, entry_id, ok, detail)
        except Exception:  # noqa: BLE001
            pass

    def _run_group(group):
        try:
            _check_provider_group(group, _safe_result, should_stop, spacing)
        except Exception as exc:  # noqa: BLE001
            for faculty, entry in group:
                _safe_result(faculty, entry["id"], None,
                             f"Check failed unexpectedly: {_short(exc)}")

    def _run_solo(pair):
        faculty, entry = pair
        if should_stop():
            return
        ok, detail = run_check(dict(entry))
        _safe_result(faculty, entry["id"], ok, detail)

    with ThreadPoolExecutor(max_workers=max(1, max_parallel),
                            thread_name_prefix="FridayCheckAll") as pool:
        futures = [pool.submit(_run_group, g) for g in groups.values()]
        futures += [pool.submit(_run_solo, p) for p in solo]
        for f in futures:
            try:
                f.result()
            except Exception:  # noqa: BLE001
                pass


def fetch_model_list(entry, limit=64):
    """The 'Fetch models' button's engine: ask the provider for its REAL
    model catalog using the same URL/headers discipline as the check and
    the runtime (provider_autodetect helpers), and return the ids.
    Returns {"ok": bool, "ids": [...], "error": str|None} -- never
    raises; a POST-less/network-less test can drive it through a
    provider file replacement of requests.get, matching the pattern the
    rest of this module's batteries already use."""
    base_url = (entry.get("base_url") or "").strip()
    if not base_url:
        return {"ok": False, "ids": [], "error": "No base URL set yet."}
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "ids": [], "error": str(exc)}
    import provider_autodetect
    style = provider_autodetect.entry_api_style(entry)
    hint = provider_autodetect.preset_for_base_url(base_url) or {}
    url = provider_autodetect.models_url(
        base_url, style, override=hint.get("models_url_override", ""))
    if not url:
        return {"ok": False, "ids": [],
                "error": "I don't know the model-listing URL for this base."}
    headers = provider_autodetect.headers_for(style, entry.get("api_key", ""))
    try:
        resp = requests.get(url, headers=headers, timeout=8)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "ids": [], "error": _short(exc)}
    if resp.status_code >= 400:
        snippet = ""
        try:
            body = resp.json()
            snippet = (body.get("error", {}) or {}).get("message", "") or ""
        except Exception:  # noqa: BLE001
            snippet = (resp.text or "").strip()
        return {"ok": False, "ids": [],
                "error": f"HTTP {resp.status_code}"
                         + (f" -- {snippet.strip()[:160]}" if snippet else "")}
    try:
        data = resp.json()
    except ValueError:
        return {"ok": False, "ids": [],
                "error": f"HTTP {resp.status_code} but the body wasn't JSON "
                         "-- is this an API base, or a dashboard/webpage URL?"}
    # OpenAI-style {"data": [{"id": ...}]} is the convention, but a few
    # free-tier catalogs speak ollama {"models": [{"name"/"model"}]}.
    # Both parse into the same id list; the raw items still carry
    # whatever pricing metadata they have for tier classification.
    raw_items = data.get("data") or data.get("models") or []
    items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("name") or item.get("model")
        if model_id:
            normalized = dict(item)
            normalized["id"] = str(model_id)
            items.append(normalized)
    ids = [item["id"] for item in items]
    if not ids:
        return {"ok": False, "ids": [], "tiers": None,
                "error": "The provider answered but listed no models."}
    import provider_autodetect as _pa2
    tiers = _pa2.classify_model_tiers(items)
    return {"ok": True, "ids": ids[:limit], "tiers": tiers,
            "error": None}


def _one_token_generation(*attempts):
    """Runs the first generation attempt; SDK generations only differ
    in how you pass the generation-config object (dict-style config
    began working in the google-genai line while older sdks talk in
    model object terms), so walk through the variants until one
    real call goes through. Whichever path runs, one thing is invariant:
    generation bytes really crossed the wire."""
    first_error = None
    for attempt in attempts:
        try:
            attempt()
            return
        except Exception as exc:  # noqa: BLE001
            # Any failure means "try the next argument shape": an SDK
            # that rejects the config dict can raise ValueError/
            # InvalidArgument/HTTP-400 rather than TypeError, and only
            # retrying on TypeError turned perfectly healthy entries red.
            # Auth/quota failures repeat identically across variants, so
            # keeping the FIRST error loses nothing about the real cause.
            if first_error is None:
                first_error = exc
            continue
    if first_error is not None:
        raise first_error


def _short(exc):
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) <= 220 else text[:217] + "..."


CHECKERS = {
    "gemini": check_gemini,
    "gemini_embed": check_gemini_embed,
    "groq": check_groq,
    "mistral": check_mistral,
    "mistral_embed": check_mistral_embed,
    "tavily": check_tavily,
    "local_piper": check_local_piper,
    "local_whisper": check_local_whisper,
    "generic_http": check_generic_http,
    "cloudflare_image": check_cloudflare_image,
    "stability_image": check_stability_image,
    "openai_image": check_openai_image,
    "ideogram_image": check_ideogram_image,
}


def run_check(entry):
    """Dispatches to the right checker for `entry` based on its
    check_kind, falling back to the generic HTTP reachability check for
    anything unrecognized. Never raises. Returns (ok, detail) where ok is
    True / False / None (None = temporary problem, keep previous status)."""
    kind = entry.get("check_kind") or "generic_http"
    fn = CHECKERS.get(kind, check_generic_http)
    try:
        ok, detail = fn(entry)
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"Check failed unexpectedly: {_short(exc)}"
    # SDK-based checkers (Gemini/Groq/Mistral...) surface throttling and
    # outages as exception text; those are temporary, not "disconnected".
    if ok is False and kind != "generic_http" and _TRANSIENT_TEXT.search(detail or ""):
        return None, (f"{detail} -- looks temporary (rate limit / outage); "
                      "status left unchanged.")
    return ok, detail

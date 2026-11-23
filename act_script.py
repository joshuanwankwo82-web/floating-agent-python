"""Friday's ACT Script system -- real implementation.

ACT Script is Friday's heavier, multi-model orchestration layer for work
that's too deep for one single-model reply (see agent_overlay.AIWorker
for that normal, lightweight path). It is NOT a replacement for AIWorker
-- it's something the IDE chat's own model can reach for, one task at a
time, via the ide_run_act_script tool (see agent_tools.py), when a task
genuinely needs it.

Three pipelines, one shared principle: **the control loop lives only in
a coordinator function** (run_sequential / run_fan_out / run_debate,
picked by run_act_script's classification step). Every region below
(thinking/planning/coding/review/workers/aggregator/author/critic/
adjudicator/etc.) is a narrow function: it's handed exactly the state it
needs, does its one job, and hands back a result -- it never decides
what runs next, and never reaches into another region's private state.
That's deliberate, not incidental: it's what keeps each region
independently testable and swappable later.

  1. Sequential Rotation -- dependent, multi-step builds/refactors/
     migrations. One real model per step (round-robin), strict
     read-before-write, a shared Contract, mechanical (non-LLM) verify
     after every step, one bounded fix-forward retry on verify failure,
     a Review pass by a model that did not write the work it's judging.
  2. Fan-Out -- breadth/independent-perspective work (research, weighing
     options, debugging hypotheses). N models answer in parallel with
     zero cross-visibility, a different model aggregates (never
     averaging a contradiction, never dropping a claim, never just
     concatenating), mechanical verify where that's possible (URL
     reachability), then a Review pass by yet another different model.
  3. Debate -- whole-artifact quality (reports, strategy, writing that
     needs real scrutiny). One Author (constant across rounds) drafts,
     a different Critic reads it cold each round, the Author revises,
     a constant Adjudicator (never the Author, never that round's
     Critic) decides PROCEED or DONE, bounded by a hard round cap. One
     final cold read by yet another different model before it ships.

Standing rule enforced in all three: whichever model reviews/critiques/
judges a piece of work always differs from whichever model produced it
-- see next_provider()'s `exclude` argument, used at every single one of
those handoffs below.

### Model diversity, honestly
Every chat task can use every configured, callable chat model. Entries
formerly assigned to a matching task are tried first, with the remaining
compatible models available as fallbacks. Separate entries remain
independent rotation slots, including multiple credentials for one
provider. If only one real model is configured overall, the "reviewer
differs from producer" rule cannot be satisfied; next_provider()
reports that honestly instead of pretending the review is independent.

### Where the real API calls live
The three builtin dispatchers (_dispatch_gemini/_dispatch_groq/
_dispatch_mistral) call each settings_store entry's own api_key/model --
never a single hardcoded global key -- so two different entries of the
same provider kind (e.g. two separate Gemini keys, added under two
different faculties) are genuinely independent rotation slots.
_dispatch_generic_http is the one broadly-applicable call Friday can
make for a user-added custom provider it has no dedicated integration
for (OpenAI-compatible POST .../chat/completions -- see its own
docstring). GEMINI_MODEL/GROQ_MODEL/MISTRAL_MODEL/API key constants
below are still sourced from agent_overlay.py when importable (same
reasoning as before: agent_overlay.py pulls in PySide6, so the import is
best-effort, with a hand-copied env-var fallback for when it truly can't
be imported) -- they only matter now as settings_store.py's own
first-run default seed values and call_model()'s per-kind fallback-model
lists, not as the dispatchers' key source anymore.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


def _load_env_file(path=None):
    """Same tiny KEY=VALUE loader as agent_overlay.py's own
    _load_env_file -- duplicated here (not imported) for exactly the
    reason explained in this module's docstring: importing
    agent_overlay.py at all drags in PySide6."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


try:
    import agent_overlay as _overlay  # noqa: E402  (see module docstring)
    GEMINI_MODEL = _overlay.GEMINI_MODEL
    GEMINI_FALLBACK_MODELS = _overlay.FALLBACK_MODELS
    GROQ_MODEL = _overlay.GROQ_MODEL
    GROQ_FALLBACK_MODELS = _overlay.GROQ_FALLBACK_MODELS
    MISTRAL_MODEL = _overlay.MISTRAL_MODEL
    MISTRAL_FALLBACK_MODELS = _overlay.MISTRAL_FALLBACK_MODELS
    REQUEST_TIMEOUT_SECONDS = _overlay.AI_REQUEST_TIMEOUT_SECONDS
except Exception:  # noqa: BLE001 -- PySide6 missing, or any other import snag
    _load_env_file()
    GEMINI_MODEL = "gemini-3.8-flash"
    GEMINI_FALLBACK_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest"]
    GROQ_MODEL = "openai/gpt-oss-120b"  # llama-3.3-70b-versatile retired 2026-08-16
    GROQ_FALLBACK_MODELS = ["openai/gpt-oss-20b"]  # llama-3.1-8b-instant retired same day
    MISTRAL_MODEL = "mistral-small-latest"
    MISTRAL_FALLBACK_MODELS = ["ministral-8b-latest"]
    REQUEST_TIMEOUT_SECONDS = 30

import memory  # noqa: E402 -- plain stdlib-ish module, no Qt, safe to import eagerly
import settings_store  # noqa: E402 -- plain module, no Qt; single source of truth for region pools


# ---------------------------------------------------------------------------
# Provider plumbing
#
# Every Act Script task draws from the shared, callable chat-model pool
# in settings_store.py. ROLE_FACULTY is retained as a task preference:
# entries previously assigned to that task are tried first, followed by
# every other compatible model, so existing settings stay useful without
# isolating keys into separate pools.
# ---------------------------------------------------------------------------

ROLE_FACULTY = {
    "thinking": "Thinking",
    "planning": "Planning",
    "coding": "Coding",
    "reasoning": "Review",      # review / critic / adjudicator / aggregator / fan-out review
    "verify": "Verify",         # per-step acceptance evaluation (real-file evidence)
    "writing": "Text Generation",  # debate's Author
    "voice": "Voice",           # not used internally here -- agent_overlay.AIWorker's own chain
}


def _dispatch_gemini(entry, system_prompt, user_prompt, model_name):
    """Real call against one settings_store entry. Raises on failure --
    call_model() is what turns that into a clean (ok, error) result."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=entry.get("api_key", ""),
        http_options=types.HttpOptions(timeout=int(REQUEST_TIMEOUT_SECONDS * 1000)),
    )
    config = types.GenerateContentConfig(system_instruction=system_prompt)
    response = client.models.generate_content(
        model=model_name, contents=user_prompt, config=config,
    )
    return (getattr(response, "text", None) or "").strip()


def _dispatch_groq(entry, system_prompt, user_prompt, model_name):
    import groq

    client = groq.Groq(api_key=entry.get("api_key", ""), timeout=REQUEST_TIMEOUT_SECONDS)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def _dispatch_mistral(entry, system_prompt, user_prompt, model_name):
    from mistralai.client import Mistral

    client = Mistral(
        api_key=entry.get("api_key", ""),
        timeout_ms=int(REQUEST_TIMEOUT_SECONDS * 1000),
    )
    response = client.chat.complete(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def _dispatch_generic_http(entry, system_prompt, user_prompt, model_name):
    """POST to a user-added "+ Add Model" entry in ITS provider dialect
    -- the one broadly-applicable real call Friday makes for any
    provider it has no dedicated SDK integration for. Two dialects are
    spoken natively (see provider_autodetect's module docstring for the
    whole design): OpenAI-compatible .../chat/completions (OpenAI,
    OpenRouter, Groq, Together, Ollama, LM Studio, vLLM -- the large
    majority) and Anthropic's /v1/messages. The dialect comes from
    provider_autodetect.entry_api_style (explicit "api_style" on the
    entry first, recognized URL second). Everything else -- the no-
    max_tokens shape parity with the settings check, the honest error
    surfacing below -- stays identical across dialects, which is the
    point: a runtime build and its Settings check see the same gate."""
    import requests
    import provider_autodetect

    base = (entry.get("base_url") or "").strip()
    if not base:
        raise RuntimeError("no API base URL configured for this custom model.")
    style = provider_autodetect.entry_api_style(entry)
    url = provider_autodetect.chat_url(base, style)
    headers = provider_autodetect.headers_for(style, entry.get("api_key", ""))
    payload = provider_autodetect.chat_payload(
        style, model_name, system_prompt, user_prompt)
    resp = requests.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    if resp.status_code >= 400:
        # Surface the server's OWN reason (OpenRouter/z.ai/Anthropic
        # style bodies carry the useful detail: "Insufficient credits",
        # "model not found", ...), not just the bare HTTP status.
        snippet = ""
        try:
            body = resp.json()
            snippet = (body.get("error", {}) or {}).get("message", "") or str(body)
        except Exception:  # noqa: BLE001 -- body wasn't JSON at all
            snippet = (resp.text or "").strip()
        snippet = snippet.replace("\n", " ").strip()
        raise RuntimeError(
            f"HTTP {resp.status_code}"
            + (f" -- {snippet[:220]}" if snippet else ""))
    try:
        data = resp.json()
    except ValueError:
        # "Expecting value: line 1 column 1" only confused people: an
        # empty/HTML body means a gateway/guard answered instead of the
        # model (dead credits, wrong URL, WAF) -- say so, plainly.
        raise RuntimeError(
            f"the provider answered with an empty or non-JSON body "
            f"(HTTP {resp.status_code}) -- check this entry's credits and "
            f"base URL; the model itself never replied.")
    return provider_autodetect.extract_reply_text(style, data)


# check_kind -> callable(entry, system_prompt, user_prompt, model_name) ->
# text. Deliberately a plain module-level dict (not a hardcoded if/elif
# chain) so dev_tests/test_act_script.py can monkeypatch individual
# entries -- same seam this codebase already uses for memory.py's
# embedding calls (see test_friday_memory.py) -- to exercise the real
# Router/region control flow, tool execution, mechanical verify, and
# memory logging without needing live network credentials.
_DISPATCH = {
    "gemini": _dispatch_gemini,
    "groq": _dispatch_groq,
    "mistral": _dispatch_mistral,
    "generic_http": _dispatch_generic_http,
}

# The known fallback-model lists for Friday's three original builtin
# kinds -- tried, in order, after an entry's own configured "model"
# string, same resilience behavior this had before per-region pools
# existed. Custom (generic_http) entries have no such list -- Friday
# can't know a truly arbitrary provider's alternate model names.
_BUILTIN_FALLBACK_MODELS = {
    "gemini": GEMINI_FALLBACK_MODELS,
    "groq": GROQ_FALLBACK_MODELS,
    "mistral": MISTRAL_FALLBACK_MODELS,
}


@dataclass
class CallResult:
    ok: bool
    text: str = ""
    provider: str = ""
    model: str = ""
    error: str = ""


_NO_PROVIDER_MSG = (
    "Act Script needs a runnable chat model in Settings → Keys & Connect. "
    "Add or check a compatible model, then try again."
)


def _settings_data():
    """Fresh read of settings_store on every call (no caching) so a
    model added/edited/removed in the Settings panel takes effect on
    the very next Act Script run, no restart needed. Never raises --
    a settings-store hiccup degrades to \"nothing configured\" rather
    than crashing a run that may not even need a model."""
    try:
        return settings_store.load()
    except Exception:  # noqa: BLE001
        return {}


def _chat_capable_entries(data, faculty):
    """Entries under `faculty` that are actually real, callable chat
    models right now: a recognized check_kind, a non-empty API key, and
    a model name to call. Filters out embedding/local-audio/search
    entries and anything not yet configured."""
    return [
        e for e in data.get(faculty, [])
        if e.get("check_kind") in _DISPATCH and e.get("api_key") and e.get("model")
    ]


def _ordered_chat_pool(data, role):
    preferred = ROLE_FACULTY.get(role, "Thinking")
    faculties = [preferred] + [
        faculty for faculty in settings_store.FACULTIES
        if faculty != preferred
    ]
    pool = []
    seen_ids = set()
    for faculty in faculties:
        for entry in _chat_capable_entries(data, faculty):
            entry_id = entry.get("id")
            if entry_id in seen_ids:
                continue
            seen_ids.add(entry_id)
            pool.append(entry)
    return pool


def region_pool(role):
    """Return all callable chat models, preferring entries previously
    assigned to this task before other compatible connections. The
    legacy function name is retained for existing call sites."""
    return _ordered_chat_pool(_settings_data(), role)


def provider_label(role, provider_id):
    """Human-friendly display name for a provider id within `role`'s
    pool -- a user-added custom entry's own name (e.g. "My OpenRouter
    key") rather than its opaque internal id. Falls back to the raw id
    if the entry can't be found (e.g. removed from Settings mid-run).
    Display only -- callers must keep comparing/rotating on the raw id."""
    if not provider_id:
        return "(none)"
    for e in region_pool(role):
        if e["id"] == provider_id:
            return e.get("name") or provider_id
    return provider_id


def available_providers(role):
    """Ids of every real, callable model currently configured for
    the shared pool, ordered with models preferred for `role` first."""
    return [e["id"] for e in region_pool(role)]


def next_provider(role, after=None, exclude=None):
    """Round-robins over the shared pool, starting just after `after`
    (if present in this pool -- otherwise starts from the top,
    which is the expected, harmless outcome when `after` belongs to a
    different role's pool entirely). `exclude` is the "must differ from
    the producer" set -- skipped if at all possible. Returns
    (provider_id_or_None, degraded): degraded is True only when there
    was truly no way to honor `exclude` (e.g. a single model configured
    for this role at all) and a repeat had to be returned anyway --
    callers should surface that plainly rather than silently pretending
    the review was independent."""
    data = _settings_data()
    pool = _ordered_chat_pool(data, role)
    ids = [e["id"] for e in pool]
    if not ids:
        return None, True
    exclude = exclude or set()
    preferred = ROLE_FACULTY.get(role, "Thinking")
    preferred_ids = [
        e["id"] for e in _chat_capable_entries(data, preferred)
        if e.get("id") in ids
    ]
    if preferred_ids:
        start_idx = ((preferred_ids.index(after) + 1) % len(preferred_ids)
                     if after in preferred_ids else 0)
        ordered_preferred = (preferred_ids[start_idx:]
                             + preferred_ids[:start_idx])
        for pid in ordered_preferred:
            if pid not in exclude:
                return pid, False
        fallback_ids = [pid for pid in ids if pid not in preferred_ids]
    else:
        fallback_ids = ids
    start_idx = (fallback_ids.index(after) + 1) % len(fallback_ids) \
        if after in fallback_ids else 0
    ordered = fallback_ids[start_idx:] + fallback_ids[:start_idx]
    for pid in ordered:
        if pid not in exclude:
            return pid, False
    start_idx = (ids.index(after) + 1) % len(ids) if after in ids else 0
    degraded_order = ids[start_idx:] + ids[:start_idx]
    return degraded_order[0], True


def call_model(role, provider_id, system_prompt, user_prompt, exclude=None):
    """Try the selected Settings model, then rotate through other
    compatible models for this task when it fails. Each entry also gets
    its known builtin model fallbacks. Never raises -- coordinators can
    apply their documented failure route instead of losing the run to
    one exhausted or unavailable provider."""
    pool = region_pool(role)
    if not pool:
        return CallResult(ok=False, provider=provider_id,
                           error=f"{provider_id} isn't a configured chat model.")

    ids = [entry["id"] for entry in pool]
    start = ids.index(provider_id) if provider_id in ids else -1
    if start >= 0:
        candidates = pool[start:] + pool[:start]
    else:
        candidates = pool

    excluded = set(exclude or ())
    eligible = [entry for entry in candidates if entry["id"] not in excluded]
    if not eligible:
        # If no independent model exists, keep the already-selected
        # provider as a truthful degraded fallback rather than silently
        # skipping the required stage.
        eligible = candidates[:1]

    errors = []
    for entry in eligible:
        entry_id = entry["id"]
        dispatch = _DISPATCH.get(entry.get("check_kind"))
        if dispatch is None:
            errors.append(
                f"{entry.get('name', entry_id)} has no runnable chat integration")
            continue

        primary_model = entry.get("model") or ""
        extra_models = [
            model for model in _BUILTIN_FALLBACK_MODELS.get(
                entry.get("check_kind"), [])
            if model and model != primary_model
        ]
        models_to_try = ([primary_model] if primary_model else []) + extra_models
        last_error = None
        for model_name in models_to_try:
            try:
                text = dispatch(entry, system_prompt, user_prompt, model_name)
                return CallResult(ok=True, text=text or "(empty response)",
                                  provider=entry_id, model=model_name)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        errors.append(
            f"{entry.get('name', entry_id)} error: {last_error or 'no model configured'}")

    return CallResult(
        ok=False, provider=provider_id,
        error="No compatible model answered: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# Shared helpers: JSON extraction, mechanical verify, evidence/memory log
# ---------------------------------------------------------------------------

def _extract_json(text):
    """Models don't always obey "respond with ONLY JSON" -- strips
    markdown fences and pulls out the first {...}/[...] block before
    trying json.loads, so a stray "Sure, here you go:" preamble doesn't
    break parsing. Returns None (never raises) if nothing usable is
    found, so every call site can apply its own documented degrade
    path."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    candidate = match.group(1) if match else text
    try:
        return json.loads(candidate)
    except Exception:  # noqa: BLE001
        return None


_FILE_BLOCK_RE = re.compile(
    r"===FILE:\s*(?P<path>.+?)\s*===\n(?P<content>.*?)(?:\n===END===|\Z)",
    re.DOTALL,
)


def _extract_file_blocks(text):
    """Pulls (path, content) pairs out of Coding's ===FILE: ...=== /
    ===END=== format. Returns [] for a plain NO_FILE_CHANGES reply (or
    anything else that doesn't contain a single file block)."""
    if not text:
        return []
    return [(m.group("path").strip(), m.group("content"))
            for m in _FILE_BLOCK_RE.finditer(text)]


_FENCE_RE = re.compile(
    r"```(?P<info>[A-Za-z0-9+#._-]*)[ \t]*\n(?P<body>.*?)\n?```",
    re.DOTALL,
)
_COMMAND_FENCES = {"", "bash", "sh", "shell", "console", "terminal",
                   "cmd", "powershell", "ps1", "zsh", "text", "plaintext",
                   "json"}
_STANDALONE_PATH_RE = re.compile(
    r"^(?:#+\s*|[-*]\s*)?(?:\*\*)?(?:`)?"
    r"(?P<path>[A-Za-z0-9_][\w.\-]*(?:/[\w.\-]+)*\.[A-Za-z0-9]{1,10})"
    r"(?:`)?(?:\*\*)?\s*:?\s*$",
    re.IGNORECASE,
)
_COMMENT_PATH_RE = re.compile(
    r"^\s*(?:<!--|#+|//+|/\*)\s*"
    r"(?P<path>[A-Za-z0-9_][\w.\-]*(?:/[\w.\-]+)*\.[A-Za-z0-9]{1,10})"
    r"\s*(?:-->|--?>|;)?.?\s*$",
    re.IGNORECASE,
)


def _safe_rescue_path(path):
    """A rescued path must be a plain relative path that can never step
    out of the workspace -- enforced BEFORE a fence's bytes get anywhere
    near the disk, on top of ide_write_file's own checks."""
    if not path or "\\" in path:
        return None
    p = path.strip()
    if p.startswith("./"):
        p = p[2:]
    parts = p.split("/")
    if (not p or p.startswith("/") or any(part in ("..", "")
            for part in parts)):
        return None
    return p


def _rescue_file_blocks(text, file_hint=""):
    """Model-tolerant rescue -- the answer to 'building but no files'.
    Some models (free-tier coding models among them) answer with ordinary
    markdown ```fences``` instead of the ===FILE:...=== protocol markers.
    When the strict parsers found NOTHING, an unambiguous fence becomes a
    real write instead of a failed step. Three maps, most specific first:
      1. a standalone path line right above a fence  -> that path,
      2. a path inside the fence's top comment line -> that path,
      3. exactly one file hinted for this step       -> hinted path.
    Anything ambiguous (several unmarked fences, no hint) rescues
    nothing -- failing loudly beats writing the wrong file. The Verify
    region then judges the real on-disk bytes exactly like any strict
    write, so the rescue NEVER marks its own work passed."""
    if not text:
        return []
    fences = [(m, m.group("body")) for m in _FENCE_RE.finditer(text)
              if m.group("info").lower() not in _COMMAND_FENCES
              and m.group("body").strip()]
    if not fences:
        return []
    by_path = {}
    unmapped = []
    for m, body in fences:
        hit = None
        lines = [ln for ln in text[:m.start()].splitlines() if ln.strip()]
        if lines:
            hit = _STANDALONE_PATH_RE.match(lines[-1].strip())
        if not hit:
            first = next((ln for ln in body.splitlines() if ln.strip()), "")
            cpath = _COMMENT_PATH_RE.match(first)
            if cpath:
                hit, strip_start = cpath, True
                body = body.replace(first, "", 1).lstrip("\n")
        if hit:
            path = _safe_rescue_path(hit.group("path"))
            if path:
                if path not in by_path or len(body) > len(by_path[path]):
                    by_path[path] = body
        else:
            unmapped.append(body)
    hints = [p.strip() for p in (file_hint or "").split(",") if p.strip()]
    if len(hints) == 1 and unmapped and hints[0] not in by_path:
        hinted = _safe_rescue_path(hints[0])
        if hinted:
            by_path[hinted] = max(unmapped, key=len)
    return sorted(by_path.items(), key=lambda kv: kv[0])

def _workspace_root():
    """The IDE's real open workspace folder, if Agent Mode/the IDE is
    currently on -- None otherwise (callers fall back to os.getcwd())."""
    try:
        import agent_tools
        if agent_tools.is_agent_mode_on() and agent_tools._ide_app is not None:
            return agent_tools._ide_app.fs.root
    except Exception:  # noqa: BLE001
        pass
    return None





VERIFY_SYSTEM_TMPL = """You are the Verify region of Friday's ACT \
Script system -- the region that decides, once per step, whether that \
step's REAL, on-disk result satisfies its description and the shared \
Contract. You are not told what any other model intended or claimed; \
you are given HOST-SIDE GROUND TRUTH gathered after the step ran: the \
exact files written, fresh-read from disk, or an explicit note that \
nothing was written at all. Judge ONLY from that evidence. \
verified=false is the right answer whenever the evidence doesn't \
actually carry the step's required behavior -- never give the benefit \
of the doubt to thin air.

Contract: {contract}

This step: {description}

Host-side ground truth after the step ran:
{evidence}

Respond with ONLY a JSON object, no prose before or after:
{{"verified": true|false, "evidence": "<1-2 sentences citing the real \
files/content you checked>"}}"""


def _region_verify(contract, step, step_result, provider, exclude=None):
    """One acceptance verdict for a step, from the Verify region -- the
    model region the user configures under Settings -> Verify. The
    EVIDENCE the verdict is based on is always host-gathered ground
    truth (real file paths + fresh-read contents, or an explicit
    no-files-written note), so the verdict can never be spun from thin
    air. Returns (verified, evidence_text)."""
    written = step_result.get("written", [])
    if step_result.get("write_status") == "no_changes":
        evidence = ("(the step declared NO_FILE_CHANGES -- nothing was "
                    "written; current contents of the files it implicates "
                    "follow)\n"
                    + _sequential_read_hinted_files(step.get("file_hint", "")))
    elif written:
        parts = []
        for p in written:
            content = _read_file_or_note(p)
            parts.append(f"--- {p} ---\n{content[:4000]}")
        evidence = "\n\n".join(parts)
    else:
        evidence = "(no files were written this step)"
    system = VERIFY_SYSTEM_TMPL.format(
        contract=contract, description=step.get("description", ""),
        evidence=evidence,
    )
    result = call_model("verify", provider, system, "", exclude=exclude)
    if not result.ok:
        return False, f"the verify-region call failed: {result.error or 'no reply'}"
    data = _extract_json(result.text)
    if not data or "verified" not in data:
        return False, "the verify region's reply was not a usable verdict"
    return bool(data.get("verified")), str(data.get("evidence") or "").strip()


def _log_evidence(run_id, kind, payload):
    """Evidence/provenance trail for a run -- a short searchable summary
    via memory.remember() plus the full raw record via memory.kv_set(),
    matching the spec's "every step/round/worker answer is provenance"
    requirement. Best-effort: a memory hiccup must never abort an
    in-flight Act Script run."""
    payload = dict(payload or {})
    summary = payload.get("summary") or json.dumps(payload, default=str)[:300]
    try:
        memory.remember(f"[act_script:{run_id}] {kind}: {summary}", category="act_script")
    except Exception:  # noqa: BLE001
        pass
    try:
        key = f"act_script_run:{run_id}"
        record = memory.kv_get(key, default={"run_id": run_id, "entries": []})
        record["entries"].append({"kind": kind, "ts": time.time(), **payload})
        memory.kv_set(key, record)
    except Exception:  # noqa: BLE001
        pass
    _report_act_step(run_id, kind, payload)


# Evidence-kind -> (step-kind, label-prefix) for narrating a deep build
# mode run live into the IDE chat panel's task-steps card -- the same
# row feed the normal chat-panel tool loop already reports into. All
# best-effort: a panel that isn't listening changes nothing here.
_ACT_STEP_PREFIX = {
    "thinking": ("thought", "Thought"),
    "planning": ("phase", "Plan"),
    "step": ("tool", "Work"),
    "workers": ("tool", "Workers"),
    "aggregate": ("tool", "Aggregate answers"),
    "critique": ("tool", "Critique"),
    "adjudication": ("phase", "Adjudicator"),
    "review": ("verify", "Review"),
    "final_read": ("verify", "Final read"),
    "memory": ("phase", "Save provenance"),
}


def _report_act_step(run_id, kind, payload):
    """Best-effort live row in the IDE chat panel's task-steps card for
    one Act Script evidence event. Spins up each pipeline phase as a
    visible row so a long multi-model run narrates itself step by step,
    exactly like the ordinary chat-panel tool loop does."""
    try:
        import agent_tools  # lazy: act_script is itself imported lazily
        step_kind, prefix = _ACT_STEP_PREFIX.get(kind, ("tool", kind.title()))
        summary = str((payload or {}).get("summary") or "").strip().replace("\n", " ")
        label = f"{prefix} — {summary}" if summary else prefix
        status = "done"
        if kind == "step" and payload.get("verify_passed") is False:
            status = "blocked"
        elif kind == "adjudication" and "PROCEED" in summary.upper():
            status = "in_progress"
        elif kind == "review":
            # The review row carries the real verdict's state: a red
            # review (local_bug/spec_issue) blocks the row, so the task
            # card can't look done next to a review that found a real
            # problem (see chat.py's _finish_task_steps honesty rule).
            verdict = str((payload or {}).get("verdict") or "").lower()
            if verdict in ("local_bug", "spec_issue"):
                status = "blocked"
            elif verdict == "await_decision":
                status = "in_progress"
        agent_tools.report_ide_task_step(
            step_kind, label[:150], status,
            step_id=f"act:{run_id}:{kind}:{time.time_ns()}")
    except Exception:  # noqa: BLE001
        pass


def _read_file_or_note(path):
    """Fresh read-before-write -- used by Coding/Review so they're
    always working from the file's real current content, never from
    anything a previous step merely claimed."""
    try:
        import agent_tools
        content = agent_tools.ide_read_file(path)
        if content.startswith("Error reading"):
            return f"(doesn't exist yet: {path})"
        return content
    except Exception as exc:  # noqa: BLE001
        return f"(couldn't read {path}: {exc})"


def _write_files(file_blocks):
    """Writes every (path, content) pair for real via agent_tools'
    actual ide_write_file -- the same function a normal IDE-chat tool
    call would use, so these edits show up live in the IDE exactly like
    any other agent edit. Returns the list of paths actually written."""
    import agent_tools
    written = []
    for path, content in file_blocks:
        result = agent_tools.ide_write_file(path, content)
        if not result.startswith("Error"):
            written.append(path)
    return written


_EDIT_HEADER_RE = re.compile(
    r"^===EDIT:\s*(?P<path>.+?)\s*===\s*$", re.MULTILINE)
_PATCH_RE = re.compile(
    r"<<<ANCHOR\s*\n(?P<anchor>.*?)\n===[ \t]*\n(?P<repl>.*?)\n?>>>PATCH",
    re.DOTALL)


def _extract_edit_blocks(text):
    """Pulls (path, [(anchor, replacement), ...]) out of Coding's
    ===EDIT: format -- the professional alternative to ===FILE: for
    modifying an existing file surgically instead of resending the whole
    thing. Returns [] when no edit blocks are present."""
    if not text:
        return []
    results = []
    headers = list(_EDIT_HEADER_RE.finditer(text))
    for idx, h in enumerate(headers):
        start = h.end()
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
        body = text[start:end]
        patches = [(m.group("anchor"), m.group("repl"))
                   for m in _PATCH_RE.finditer(body)]
        if patches:
            results.append((h.group("path").strip(), patches))
    return results


def _apply_edit_blocks(edit_blocks):
    """Applies anchored patches surgically to real on-disk files --
    the "locate the line, add it, remove what conflicts" discipline in
    mechanical form. Every anchor must match the file's real current
    content EXACTLY ONCE: zero matches or more than one match fails
    loudly (nothing is written for that file), because an ambiguous
    anchor is how models corrupt the wrong spot. All of a file's
    patches are validated on the evolving content and only then written
    back in ONE real ide_write_file. Returns (applied_paths, error)."""
    import agent_tools
    applied = []
    for path, patches in edit_blocks:
        current = agent_tools.ide_read_file(path)
        if current.startswith("Error reading"):
            return applied, (f"{path}: can't anchored-edit a file that "
                             f"isn't on disk yet ({current})")
        content = current
        for i, (anchor, repl) in enumerate(patches, 1):
            hits = content.count(anchor)
            if hits == 0:
                return applied, (f"{path}: patch {i} anchor not found in "
                                 "the file's real current content")
            if hits > 1:
                return applied, (f"{path}: patch {i} anchor is ambiguous "
                                 f"({hits} matches) -- needs more "
                                 "surrounding context to be unique")
            content = content.replace(anchor, repl, 1)
        result = agent_tools.ide_write_file(path, content)
        if result.startswith("Error"):
            return applied, f"{path}: {result}"
        applied.append(path)
    return applied, None





# ---------------------------------------------------------------------------
# Thinking (top-level classification -- which pipeline actually fits)
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM = """You are the Thinking region of Friday's ACT Script \
system -- the first thing that sees any task routed here. Decide which \
of three pipelines actually fits this task, or whether it's too small \
to need any of them.

- sequential: a dependent, multi-step build/refactor/migration, where \
each step's real output must exist before the next step can safely \
start (e.g. "add a field to the model, migrate the DB, update the API, \
update the UI").
- fan_out: breadth / independent perspectives -- research questions, \
weighing options, debugging hypotheses, wanting several independent \
takes merged into one.
- debate: a single whole artifact where quality matters more than speed \
-- a report, a strategy document, a piece of writing -- worth drafting, \
critiquing cold, and revising before it ships.
- single: everything else -- a plain question or small task not worth \
the overhead of any of the above.

Respond with ONLY a JSON object, no prose before or after:
{"route": "sequential"|"fan_out"|"debate"|"single", "reason": "<one sentence>"}
"""

_VALID_ROUTES = {"sequential", "fan_out", "debate", "single"}


def thinking_classify(user_request, provider):
    result = call_model("thinking", provider, CLASSIFY_SYSTEM, user_request)
    if not result.ok:
        return "single", result
    data = _extract_json(result.text)
    route = (data or {}).get("route", "single")
    if route not in _VALID_ROUTES:
        route = "single"
    return route, result


# ---------------------------------------------------------------------------
# Sequential Rotation
# ---------------------------------------------------------------------------

SEQUENTIAL_THINKING_SYSTEM = """You are the Thinking region of Friday's \
ACT Script system, in Sequential Rotation mode -- for dependent, \
multi-step build/refactor/migration work where each step builds on the \
last. You do FOUR things, in order, and you never write any actual code \
or file content (that's the Coding region's job, later, one step at a \
time, each with only its own narrow slice visible):

1. EXPAND the user's request into a much bigger, detailed understanding \
("expanded"): the goals, the concrete behavior, the acceptance criteria, \
the edge cases, and the files likely to be touched. A tiny prompt must \
become a professional spec a builder could work from -- 4-10 sentences. \
Never invent requirements the user contradicted, but DO surface \
sensible professional defaults as decisions.
2. CLARIFY before anything is built: if something genuinely \
decision-blocking is ambiguous -- a fork that changes the shape of the \
result, which a professional would not silently pick for the user -- \
produce 1-3 short questions, each with 2-4 concrete selectable options. \
ONLY real ambiguity: never quiz the user over things a professional \
decides alone (tech defaults, naming, file structure, formatting). If \
nothing blocks, "questions" is [].
3. Write the Contract (2-5 sentences: what must be true when this is \
done -- the non-negotiable acceptance criteria).
4. Produce a rough ordered list of steps -- the minimum needed, split \
only where one step's real, verified output must exist before the next \
can safely start. If questions exist, steps are PRELIMINARY: nothing \
runs until the user answers.

Respond with ONLY a JSON object, no prose before or after:
{"expanded": "<the detailed spec>",
 "questions": [{"q": "<question>", "options": ["<option>", "..."]}],
 "contract": "<2-5 sentences>",
 "steps": ["<step 1 description>", "<step 2 description>", "..."]}"""


def _sequential_thinking(user_request, provider):
    result = call_model("thinking", provider, SEQUENTIAL_THINKING_SYSTEM, user_request)
    if not result.ok:
        return None, result
    data = _extract_json(result.text)
    if not data or not data.get("steps"):
        return None, result
    return data, result


SEQUENTIAL_PLANNING_SYSTEM_TMPL = """You are the Planning region of \
Friday's ACT Script system, in Sequential Rotation mode. The Thinking \
region already produced an expanded understanding, a Contract, and a \
rough step list -- your job is to turn that into a literal, executable \
step table. You add no new scope and write no code yourself.

Expanded understanding (the detailed spec):
{expanded}

Contract:
{contract}

Rough steps:
{steps}

For EACH step, write a comma-separated list of the relative file(s) the \
step will create or modify (its file_hint), so the Coding region can be \
handed their real current contents. Name files conservatively -- only \
what the step truly implicates. (Note: file_hint may be empty only for \
a step that touches no files at all.) Every step's result is afterwards \
judged by the Verify region against its description and the Contract -- \
so describe steps precisely enough to be judged.

Respond with ONLY a JSON object, no prose before or after:
{{"steps": [{{"description": "...", "file_hint": "relative/path.ext"}}, "..."]}}"""


def _sequential_planning(user_request, contract, rough_steps, provider, expanded=""):
    system = SEQUENTIAL_PLANNING_SYSTEM_TMPL.format(
        contract=contract, steps="\n".join(f"- {s}" for s in rough_steps),
        expanded=expanded or "(Thinking produced no expansion for this run.)",
    )
    result = call_model("planning", provider, system, user_request)
    if not result.ok:
        return None, result
    data = _extract_json(result.text)
    if not data or not data.get("steps"):
        return None, result
    return data, result


SEQUENTIAL_CODING_SYSTEM_TMPL = """You are the Coding region of \
Friday's ACT Script system, in Sequential Rotation mode, executing ONE \
step of a larger plan. You only ever see this one step's narrow slice \
-- not the whole plan, not any other step's internal reasoning -- just \
this step's description, the shared Contract, and the REAL current \
content of whatever file(s) are relevant (read fresh, just now -- trust \
this, not any assumption about what they "should" already contain).

Contract: {contract}

This step: {description}

Current real content of the relevant file(s):
{file_contents}

Make the real, concrete change(s) this step needs. Output ONLY blocks \
in one of these formats (plus NO_FILE_CHANGES) -- never any prose:

1. A file you are CREATING new (or rewriting wholesale because nothing \
of the old content is being kept) -- full content, never a diff:
===FILE: relative/path.ext===
<entire file content>
===END===

2. A file you are MODIFYING -- the professional, surgical default: \
locate exactly where the change belongs and patch just that. One or \
more anchored patches per file:
===EDIT: relative/path.ext===
<<<ANCHOR
<exact text copied VERBATIM from the CURRENT file content shown above, \
with enough surrounding context to occur exactly ONCE in that file>
===
<what to replace it with -- empty to delete that region, e.g. removing \
code that conflicts with the new behavior>
>>>PATCH
Rule of the trade: an anchor that does not match the real file text \
character-for-character, or that matches more than once, FAILS \
application mechanically and nothing is written -- precision is the \
job, and resending a whole untouched file when three lines change is \
not professional work.

If this step genuinely needs no file changes, output exactly: \
NO_FILE_CHANGES"""


def _sequential_read_hinted_files(file_hint):
    paths = [p.strip() for p in (file_hint or "").split(",") if p.strip()]
    if not paths:
        return "(no specific file hinted for this step yet -- use your judgment)"
    return "\n\n".join(f"--- {p} ---\n{_read_file_or_note(p)}" for p in paths)


def _sequential_do_step(contract, step, provider):
    file_contents = _sequential_read_hinted_files(step.get("file_hint", ""))
    system = SEQUENTIAL_CODING_SYSTEM_TMPL.format(
        contract=contract, description=step.get("description", ""),
        file_contents=file_contents,
    )
    result = call_model("coding", provider, system, step.get("description", ""))
    if not result.ok:
        return {"written": [], "model": None, "error": result.error,
                "provider": result.provider, "write_status": "error"}
    if result.text.strip() == "NO_FILE_CHANGES":
        return {"written": [], "model": result.model,
                "provider": result.provider, "error": None,
                "apply_error": None, "write_status": "no_changes"}
    file_blocks = _extract_file_blocks(result.text)
    edit_blocks = _extract_edit_blocks(result.text)
    rescued = False
    if not file_blocks and not edit_blocks:
        file_blocks = _rescue_file_blocks(result.text,
                                          step.get("file_hint", ""))
        rescued = bool(file_blocks)
    written = _write_files(file_blocks)
    edited, edit_error = _apply_edit_blocks(edit_blocks)
    written = sorted(set(written) | set(edited))
    if edit_error:
        # An anchored edit that can't be applied is a FAILED step --
        # nothing pretends it worked; the fix-forward gets the real
        # reason (not found / ambiguous) and one bounded retry.
        return {"written": written, "model": result.model,
                "provider": result.provider, "error": None,
                "apply_error": edit_error, "write_status": "edit_failed"}
    # "no_blocks" = the model returned something with ZERO usable blocks
    # AND nothing a fence-rescue could safely map to a path -- the step
    # produced nothing, which the step loop treats as a failure, never
    # as a pass.
    out = {"written": written, "model": result.model,
           "provider": result.provider, "error": None,
           "apply_error": None, "rescued": rescued,
           "write_status": "wrote" if written else "no_blocks"}
    return out


SEQUENTIAL_FIX_SYSTEM_TMPL = """You are the Coding region of Friday's \
ACT Script system, fixing ONE step failure -- not re-touching anything \
else. Same step, same Contract, one bounded fix attempt. The failure \
may be an execution fact (a reply with no usable blocks, a rejected \
anchor) or the Verify region's verdict on your real on-disk result -- \
whatever the output says, address exactly that.

Contract: {contract}
Step: {description}
How the step was checked: {verify_command}
Its verdict (why it failed): {verify_output}

Current real content of the file(s) you touched (fresh read, after your \
last attempt):
{file_contents}

Fix exactly what failed. The failure may be a mechanical verify error, \
a syntax error, or a rejected anchored edit (anchor not found / \
ambiguous) -- whatever it says, address exactly that, nothing else. \
Output ONLY blocks in the same formats as before (===FILE: for full \
content, or ===EDIT: with <<<ANCHOR...===...>>>PATCH for surgical \
fixes):
===FILE: relative/path.ext===
<entire file content>
===END==="""


def _sequential_fix_step(contract, step, written_paths, verify_command, verify_output, provider):
    file_contents = (
        "\n\n".join(f"--- {p} ---\n{_read_file_or_note(p)}" for p in written_paths)
        if written_paths else _sequential_read_hinted_files(step.get("file_hint", ""))
    )
    system = SEQUENTIAL_FIX_SYSTEM_TMPL.format(
        contract=contract, description=step.get("description", ""),
        verify_command=verify_command, verify_output=verify_output[:1000],
        file_contents=file_contents,
    )
    result = call_model("coding", provider, system, "")
    if not result.ok:
        return [], result.provider
    file_blocks = _extract_file_blocks(result.text)
    edit_blocks = _extract_edit_blocks(result.text)
    if not file_blocks and not edit_blocks:
        file_blocks = _rescue_file_blocks(result.text,
                                          step.get("file_hint", ""))
    written = _write_files(file_blocks)
    edited, _edit_error = _apply_edit_blocks(edit_blocks)
    return sorted(set(written) | set(edited)), result.provider


SEQUENTIAL_REVIEW_SYSTEM_TMPL = """You are the Review region of \
Friday's ACT Script system, in Sequential Rotation mode -- the final \
check before this multi-step build is reported done. CRITICAL: a \
different model wrote the work you're judging. Verify skeptically from \
the real current files, never from anything a prior step merely \
claimed.

Original user request: {user_request}
Contract: {contract}

Step-by-step evidence:
{evidence}

Current real content of every file touched:
{all_file_contents}

Judge against the Contract AND the original request. Respond with ONLY \
a JSON object, no prose before or after:
{{"verdict": "done"|"local_bug"|"spec_issue"|"cosmetic"|"await_decision", \
"report": "<3-6 plain-language sentences: what you checked, what you found>", \
"options": ["<short option label>", "..."]}}
Only include "options" when verdict is "await_decision" AND there are \
2-4 genuinely distinct, concrete paths forward worth offering as quick \
choices (e.g. "Use SQLite" / "Use Postgres") -- omit it (empty list) \
otherwise."""


def _sequential_review(user_request, contract, evidence_log, provider):
    all_paths = sorted({p for e in evidence_log for p in e.get("written", [])})
    all_file_contents = (
        "\n\n".join(f"--- {p} ---\n{_read_file_or_note(p)}" for p in all_paths)
        or "(no files were written)"
    )
    evidence_text = "\n".join(
        f"- {e['step']} -- provider {e['provider']}, files {e['written'] or '(none)'}, "
        f"verify {'PASSED' if e['verify_passed'] else 'FAILED'}"
        f"{' (fixed on retry)' if e['fixed'] else ''}: {e['verify_output'][:200]}"
        for e in evidence_log
    )
    system = SEQUENTIAL_REVIEW_SYSTEM_TMPL.format(
        user_request=user_request, contract=contract, evidence=evidence_text,
        all_file_contents=all_file_contents,
    )
    producer_providers = {e.get("provider") for e in evidence_log
                          if e.get("provider")}
    result = call_model("reasoning", provider, system, "",
                        exclude=producer_providers)
    data = _extract_json(result.text) if result.ok else None
    if not data:
        return {"verdict": "done" if result.ok else "spec_issue",
                "report": result.text if result.ok else (result.error or "Review failed."),
                "provider": result.provider}
    data["provider"] = result.provider
    return data


def run_sequential(user_request):
    run_id = uuid.uuid4().hex[:8]

    # Every role uses the shared pool, so one compatible model is enough
    # to start. next_provider() reports honestly if a later review cannot
    # be independent because no second model is available.
    if not region_pool("thinking"):
        return _NO_PROVIDER_MSG

    providers = available_providers("thinking")
    if not providers:
        return _NO_PROVIDER_MSG

    thinking_provider = providers[0]
    thinking_data, think_result = _sequential_thinking(
        user_request, thinking_provider)
    thinking_provider = think_result.provider or thinking_provider
    if thinking_data is None:
        return (f"Act Script (Sequential): couldn't produce a plan -- "
                f"{think_result.error or 'the model reply was not valid JSON.'}")
    contract = thinking_data.get("contract", "")
    expanded = thinking_data.get("expanded", "")
    rough_steps = thinking_data.get("steps", [])

    # Clarify-before-build: a professional asks the right question rather
    # than building the wrong thing confidently. When Thinking surfaces
    # genuinely decision-blocking questions, NOTHING is planned, coded,
    # or written -- the run pauses (real {{AWAIT_DECISION}} pause, same
    # machinery the review verdict uses); the user's answer arrives as a
    # follow-up turn and the run restarts with the ambiguity resolved.
    questions = [q for q in (thinking_data.get("questions") or [])
                 if isinstance(q, dict) and q.get("q")][:3]
    if questions:
        _log_evidence(run_id, "thinking", {
            "summary": f"asked {len(questions)} clarifying question(s) before building",
            "provider": thinking_provider, "questions": questions,
        })
        lines = [f"Act Script (Sequential, run `{run_id}`): before I build this, "
                 "let me pin down what you actually want -- I'd rather ask "
                 "than assume wrong:\n"]
        tag_options = None
        for i, q in enumerate(questions, 1):
            lines.append(f"{i}. {q['q']}")
            opts = [str(o) for o in (q.get("options") or []) if o][:4]
            if opts:
                lines.append("   " + " / ".join(opts))
                if (tag_options is None and len(opts) >= 2
                        and len(questions) == 1):
                    tag_options = opts
        text = "\n".join(lines)
        text += "\n\n{{AWAIT_DECISION}}"
        if tag_options:
            text += " {{OPTIONS: " + " | ".join(f'"{o}"' for o in tag_options) + "}}"
        return text

    _log_evidence(run_id, "thinking",
                  {"summary": contract, "provider": thinking_provider,
                   "rough_steps": rough_steps, "expanded": expanded[:400]})

    planning_provider, _ = next_provider("planning", after=thinking_provider)
    plan_data, plan_result = _sequential_planning(
        user_request, contract, rough_steps, planning_provider, expanded=expanded)
    planning_provider = plan_result.provider or planning_provider
    if plan_data and plan_data.get("steps"):
        steps = plan_data["steps"]
    else:
        # Degrade gracefully: run the rough steps as-is (no file hints),
        # rather than failing the whole run over a planning parse miss.
        steps = [{"description": s, "file_hint": ""} for s in rough_steps]
    _log_evidence(run_id, "planning",
                  {"summary": f"{len(steps)} step(s) planned", "provider": planning_provider, "steps": steps})

    evidence_log = []
    coder_provider = planning_provider
    verify_provider = planning_provider
    for i, step in enumerate(steps):
        coder_provider, _ = next_provider("coding", after=coder_provider)
        step_result = _sequential_do_step(contract, step, coder_provider)
        coder_provider = step_result.get("provider") or coder_provider
        step_provider = coder_provider
        verify_provider, _ = next_provider(
            "verify", after=verify_provider, exclude={coder_provider})
        write_status = step_result["write_status"]

        # The honesty gate -- EXECUTION facts (transport errors, replies
        # with no file/edit blocks, rejected anchored edits) are host
        # truth and fail the step directly. ACCEPTANCE (did the real
        # on-disk result do what the step said) is judged by the Verify
        # region, against host-gathered ground truth only:
        if write_status == "error":
            passed, output = False, (step_result["error"]
                                     or "the coding provider call failed")
        elif write_status == "edit_failed":
            passed, output = False, (step_result["apply_error"]
                                     or "an anchored edit failed to apply")
        elif write_status == "no_blocks":
            passed, output = False, ("the coding reply contained no file "
                                     "or edit blocks, so nothing was "
                                     "written -- the step did not happen")
        elif write_status == "no_changes":
            passed, output = True, ("(step declared NO_FILE_CHANGES; the "
                                    "Verify region judges acceptance on "
                                    "steps that do write)")
        else:
            passed, output = _region_verify(contract, step, step_result,
                                            verify_provider,
                                            exclude={coder_provider})
        fixed = False
        if not passed and write_status != "error":
            # One bounded fix attempt, proven afterwards by the same
            # Verify region -- never by a lighter second instrument.
            fixed_paths, fix_provider = _sequential_fix_step(
                contract, step, step_result["written"],
                "(Verify-region evaluation)", output, coder_provider,
            )
            if fix_provider:
                coder_provider = fix_provider
                step_provider = fix_provider
            if fixed_paths:
                step_result["written"] = sorted(set(step_result["written"]) | set(fixed_paths))
                passed2, output2 = _region_verify(contract, step, step_result,
                                                  verify_provider,
                                                  exclude={coder_provider})
                if passed2:
                    passed, output, fixed = True, output2, True
        evidence_log.append({
            "step": step.get("description", ""),
            "provider": step_provider,
            "written": step_result["written"],
            "rescued": step_result.get("rescued", False),
            "write_status": write_status,
            "verify_provider": verify_provider,
            "verify_passed": passed,
            "verify_output": output,
            "fixed": fixed,
        })
        if passed:
            status_text = ("verify OK" if write_status != "no_changes"
                           else "no file changes")
        else:
            status_text = "verify FAILED"
        _log_evidence(run_id, "step", {
            "summary": f"step {i + 1}: {step.get('description', '')} -> {status_text}",
            "provider": step_provider, "written": step_result["written"],
            "verify_passed": passed,
        })

    producer_providers = {e["provider"] for e in evidence_log if e.get("provider")}
    review_provider, review_degraded = next_provider(
        "reasoning", after=coder_provider, exclude=producer_providers)
    review = _sequential_review(user_request, contract, evidence_log, review_provider)
    review_provider = review.get("provider") or review_provider
    _log_evidence(run_id, "review", {
        "summary": review.get("report", "")[:300], "provider": review_provider,
        "verdict": review.get("verdict"),
    })

    verdict = review.get("verdict", "done")
    report = review.get("report", "(no report produced)")
    failed_steps = [e for e in evidence_log if not e["verify_passed"]]

    header = (
        f"**Act Script -- Sequential Rotation** (run `{run_id}`, {len(steps)} step(s); "
        f"reviewed by {provider_label('reasoning', review_provider)}"
        + (" -- only one model configured, so review could not be independent"
           if review_degraded else f" after {provider_label('coding', coder_provider)} coded the last step")
        + ")\n\n"
    )
    def _step_line(idx, e):
        # Honest per-step status: acceptance verdicts come from the
        # Verify region (its evidence is recorded in the run's log);
        # execution failures say exactly what happened.
        if not e["verify_passed"]:
            state = "verify FAILED"
        elif e.get("write_status") == "no_changes":
            state = "no file changes (step declared none)"
        elif e.get("rescued"):
            state = ("verify passed (recovered from a fenced reply -- "
                     "this model spoke markdown, not the file markers)")
        else:
            state = "verify passed"
        return (f"- Step {idx + 1}: {e['step']} -- "
                f"{provider_label('coding', e['provider'])} -- {state}"
                f"{' (fixed on retry)' if e['fixed'] else ''}")

    body = "\n".join(_step_line(i, e) for i, e in enumerate(evidence_log))
    if verdict == "await_decision":
        # Literal control tags, not prose -- this is what actually makes
        # the IDE chat panel pause, arm its 2-minute no-reply timer, and
        # (when options are present) render them as clickable choices.
        # See agent_overlay.py's _extract_await_decision/
        # _extract_decision_options -- they strip these tags before the
        # user ever sees them; everything before the tags is the
        # visible chat bubble text.
        options = [o for o in (review.get("options") or []) if o][:4]
        tags = "\n\n{{AWAIT_DECISION}}"
        if len(options) >= 2:
            tags += " {{OPTIONS: " + " | ".join(f'"{o}"' for o in options) + "}}"
        return header + body + f"\n\n\u26a0\ufe0f {report}" + tags
    tail = f"\n\nReview verdict: {verdict}. {report}"
    if failed_steps and verdict == "done":
        tail += (f"\n\n(Note: {len(failed_steps)} step(s) never passed mechanical "
                 f"verify even after a retry -- flagged above.)")
    return header + body + tail


# ---------------------------------------------------------------------------
# Fan-Out
# ---------------------------------------------------------------------------

FAN_OUT_THINKING_SYSTEM = """You are the Thinking region of Friday's \
ACT Script system, in Fan-Out mode -- for breadth/independent-\
perspective work: research questions, weighing options, debugging \
hypotheses, multi-reviewer checks. Frame the question precisely \
(standalone, self-contained -- a worker will see only this) and decide \
the merge rule the Aggregator must follow.

Respond with ONLY a JSON object, no prose before or after:
{"question": "<the precise, standalone question each worker answers>",
 "merge_rule": "<1-2 sentences: what counts as agreement vs. contradiction for THIS question>",
 "stakes": "simple"|"contested"|"high"}"""

_FAN_OUT_N_BY_STAKES = {"simple": 2, "contested": 4, "high": 6}

FAN_OUT_WORKER_SYSTEM_TMPL = """You are one independent worker in \
Friday's ACT Script Fan-Out system. Answer the question below \
completely on your own -- you have no visibility into any other \
worker's answer and never will. Be concrete, cite sources/evidence for \
claims where you can, and say plainly when you're not sure.

Question: {question}"""

FAN_OUT_AGGREGATOR_SYSTEM_TMPL = """You are the Aggregator region of \
Friday's ACT Script Fan-Out system. {n} independent workers answered \
the same question with zero visibility into each other. Merge rule: \
{merge_rule}

Rules you must follow, no exceptions:
- Never average a contradiction -- if workers disagree, state BOTH \
positions plainly and flag it as contested.
- Preserve per-claim attribution for anything contested or uncertain.
- Confidence must be evidence-based: HIGH only if at least 2 workers \
independently agree AND at least one cited a real source; MEDIUM if \
only one worker had a source; LOW/appendix-only if no source at all.
- Never silently drop a worker's claim, even a minority-of-one -- if \
you're not folding it into the main answer, say so and why.
- This must be a genuine rewrite/synthesis, never a concatenation of \
the workers' answers back to back.

Independent worker answers:
{worker_answers}

Produce the merged answer now."""

FAN_OUT_REVIEW_SYSTEM_TMPL = """You are the Review region of Friday's \
ACT Script Fan-Out system -- a different model than the Aggregator. \
Check: did the merge lose or mislabel anything a worker said with real \
confidence? Is the merge actually better than just picking the single \
best worker answer? Respond with ONLY a JSON object, no prose before or \
after:
{{"verdict": "pass"|"bad_merge", "note": "<1-3 sentences>"}}

Independent worker answers:
{worker_answers}

Merged answer under review:
{merged}"""

_URL_RE = re.compile(r"https?://[^\s)\]\"'<>]+")


def _fan_out_verify_urls(text, timeout=5, limit=5):
    """Mechanical (non-LLM) check: any URL the merge cites is at least
    reachable. Best-effort and silent on failure to reach the network at
    all -- this is a courtesy sanity check, not a hard gate."""
    urls = list(dict.fromkeys(_URL_RE.findall(text)))[:limit]
    if not urls:
        return ""
    try:
        import requests
    except Exception:  # noqa: BLE001
        return ""
    ok_count = 0
    for url in urls:
        try:
            resp = requests.head(url, timeout=timeout, allow_redirects=True)
            if resp.status_code < 400:
                ok_count += 1
        except Exception:  # noqa: BLE001
            try:
                resp = requests.get(url, timeout=timeout)
                if resp.status_code < 400:
                    ok_count += 1
            except Exception:  # noqa: BLE001
                continue
    return f"Mechanical check: {ok_count}/{len(urls)} cited URL(s) reachable."


def run_fan_out(user_request):
    run_id = uuid.uuid4().hex[:8]
    providers = available_providers("thinking")
    if not providers:
        return _NO_PROVIDER_MSG

    thinking_provider = providers[0]
    think_result = call_model("thinking", thinking_provider, FAN_OUT_THINKING_SYSTEM, user_request)
    thinking_provider = think_result.provider or thinking_provider
    data = _extract_json(think_result.text) if think_result.ok else None
    if not data or not data.get("question"):
        data = {"question": user_request,
                 "merge_rule": "State both sides plainly if workers disagree; never average.",
                 "stakes": "simple"}
    question = data["question"]
    merge_rule = data.get("merge_rule", "")
    stakes = data.get("stakes", "simple")
    n = _FAN_OUT_N_BY_STAKES.get(stakes, 2)
    _log_evidence(run_id, "thinking",
                  {"summary": question, "provider": thinking_provider, "stakes": stakes, "n": n})

    worker_providers = [providers[i % len(providers)] for i in range(n)]
    _log_evidence(run_id, "planning",
                  {"summary": f"N={n} workers: {worker_providers}", "stakes": stakes})

    worker_system = FAN_OUT_WORKER_SYSTEM_TMPL.format(question=question)
    worker_results = [None] * n
    with ThreadPoolExecutor(max_workers=max(n, 1)) as pool:
        futures = {
            pool.submit(call_model, "thinking", worker_providers[i], worker_system, question): i
            for i in range(n)
        }
        for future in futures:
            i = futures[future]
            try:
                worker_results[i] = future.result(timeout=120)
            except Exception as exc:  # noqa: BLE001
                worker_results[i] = CallResult(ok=False, provider=worker_providers[i], error=str(exc))

    good_workers = [
        (i, worker_results[i].provider or worker_providers[i], worker_results[i])
        for i in range(n)
        if worker_results[i] and worker_results[i].ok and worker_results[i].text.strip()
    ]
    if not good_workers:
        first_err = next((r.error for r in worker_results if r), "no responses")
        return f"Act Script (Fan-Out): every worker failed or timed out -- {first_err}"
    _log_evidence(run_id, "workers", {
        "summary": f"{len(good_workers)}/{n} workers answered",
        "answers": [{"provider": p, "text": r.text} for _, p, r in good_workers],
    })

    worker_answers_text = "\n\n".join(
        f"--- Worker {i + 1} ({p}) ---\n{r.text}" for i, p, r in good_workers
    )
    last_worker_provider = good_workers[-1][1]
    aggregator_provider, _ = next_provider(
        "reasoning", after=last_worker_provider, exclude=set(p for _, p, _ in good_workers),
    )
    agg_system = FAN_OUT_AGGREGATOR_SYSTEM_TMPL.format(
        n=len(good_workers), merge_rule=merge_rule, worker_answers=worker_answers_text,
    )
    worker_provider_ids = {p for _, p, _ in good_workers}
    agg_result = call_model("reasoning", aggregator_provider, agg_system, question,
                            exclude=worker_provider_ids)
    if not agg_result.ok:
        best = max(good_workers, key=lambda t: len(t[2].text))
        return (f"Act Script (Fan-Out): aggregator ({provider_label('reasoning', aggregator_provider)}) failed -- "
                f"{agg_result.error} -- falling back to the strongest single worker "
                f"answer ({provider_label('thinking', best[1])}):\n\n{best[2].text}")
    aggregator_provider = agg_result.provider or aggregator_provider
    merged = agg_result.text
    _log_evidence(run_id, "aggregate", {"summary": merged[:300], "provider": aggregator_provider})

    verify_note = _fan_out_verify_urls(merged)

    review_provider, review_degraded = next_provider("reasoning", after=aggregator_provider, exclude={aggregator_provider})
    review_system = FAN_OUT_REVIEW_SYSTEM_TMPL.format(
        worker_answers=worker_answers_text, merged=merged,
    )
    review_result = call_model("reasoning", review_provider, review_system, "",
                               exclude={aggregator_provider})
    review_provider = review_result.provider or review_provider
    review_data = _extract_json(review_result.text) if review_result.ok else None
    review_note = (review_data or {}).get("note", "")
    _log_evidence(run_id, "review", {"summary": review_note, "provider": review_provider})

    header = (
        f"**Act Script -- Fan-Out** (run `{run_id}`, {len(good_workers)}/{n} workers: "
        f"{', '.join(provider_label('thinking', p) for _, p, _ in good_workers)}; "
        f"aggregated by {provider_label('reasoning', aggregator_provider)}, "
        f"reviewed by {provider_label('reasoning', review_provider)}"
        + (" -- not fully independent, limited models configured" if review_degraded else "")
        + ")\n\n"
    )
    tail = f"\n\n{verify_note}" if verify_note else ""
    if review_data and review_data.get("verdict") == "bad_merge":
        tail += f"\n\n(Review flagged the merge: {review_note})"
    return header + merged + tail


# ---------------------------------------------------------------------------
# Debate
# ---------------------------------------------------------------------------

DEBATE_THINKING_SYSTEM = """You are the Thinking region of Friday's ACT \
Script system, in Debate mode -- for whole-artifact quality work: \
reports, strategy documents, writing that needs real scrutiny. Produce \
a Brief the Author will follow and separate Review Criteria the \
Critic/Adjudicator will judge against.

Respond with ONLY a JSON object, no prose before or after:
{"brief": "<type, length, audience, thesis, structure, tone, citation/anti-fabrication rules -- everything the Author needs, 4-8 sentences>",
 "review_criteria": "<what counts as BLOCKING vs MAJOR vs MINOR for this specific artifact, 3-5 sentences>",
 "stakes": "low"|"medium"|"high"}"""

_DEBATE_ROUNDS_BY_STAKES = {"low": 2, "medium": 3, "high": 4}

DEBATE_AUTHOR_SYSTEM_TMPL = """You are the Author in Friday's ACT \
Script Debate system -- the same model every round, owning this \
artifact end to end.

Brief: {brief}

{revision_note}
Write the full artifact now -- not a summary, not an outline, the real \
complete thing."""

DEBATE_CRITIC_SYSTEM_TMPL = """You are the Critic in round {round_num} \
of Friday's ACT Script Debate system -- reading this artifact cold, \
with NO access to any earlier round's critique, by design. A different \
model wrote it than you.

Review Criteria: {review_criteria}

Artifact under review:
{draft}

List every real issue you find, tagged BLOCKING, MAJOR, or MINOR. \
Respond with ONLY a JSON object, no prose before or after:
{{"issues": [{{"severity": "BLOCKING"|"MAJOR"|"MINOR", "issue": "..."}}], "overall_note": "..."}}
If there are truly no issues, use an empty issues list."""

DEBATE_ADJUDICATOR_SYSTEM_TMPL = """You are the Adjudicator in \
Friday's ACT Script Debate system -- the same model every round, never \
the Author, never that round's Critic. Judge whether the Author \
substantively addressed round {round_num}'s critique (every BLOCKING \
addressed, every MAJOR addressed-or-declined-with-a-stated-reason, \
nothing silently ignored), whether the critique itself was valid, and \
whether the revision introduced any new problems.

Round {round_num} critique:
{critique}

Author's revision after that critique:
{revision}

Respond with ONLY a JSON object, no prose before or after:
{{"verdict": "PROCEED"|"DONE", "note": "<1-3 sentences>"}}
PROCEED means another round is worth it; DONE means this is good enough to ship."""

DEBATE_FINAL_READ_SYSTEM_TMPL = """One last cold read before this \
ships -- you are yet another different model, seeing this for the \
first time. Review Criteria: {review_criteria}
Flag anything serious; otherwise say plainly that it's ready.

Artifact:
{draft}"""


def run_debate(user_request):
    run_id = uuid.uuid4().hex[:8]
    providers = available_providers("thinking")
    if not providers:
        return _NO_PROVIDER_MSG

    thinking_provider = providers[0]
    think_result = call_model("thinking", thinking_provider, DEBATE_THINKING_SYSTEM, user_request)
    thinking_provider = think_result.provider or thinking_provider
    data = _extract_json(think_result.text) if think_result.ok else None
    if not data or not data.get("brief"):
        data = {"brief": user_request,
                 "review_criteria": "Must be accurate, complete, well-organized, and free of fabrication.",
                 "stakes": "low"}
    brief = data["brief"]
    review_criteria = data.get("review_criteria", "")
    stakes = data.get("stakes", "low")
    max_rounds = _DEBATE_ROUNDS_BY_STAKES.get(stakes, 2)
    _log_evidence(run_id, "thinking", {"summary": brief, "provider": thinking_provider, "stakes": stakes})

    # Author draws from the Text Generation region (whole-artifact
    # writing); Adjudicator/Critic/final read draw from Review
    # (judgment work) -- see ROLE_FACULTY.
    author_provider, _ = next_provider("writing", after=thinking_provider)
    adjudicator_provider, _ = next_provider("reasoning", after=author_provider, exclude={author_provider})
    _log_evidence(run_id, "planning", {
        "summary": f"{max_rounds} round(s) max, author={author_provider}, adjudicator={adjudicator_provider}",
    })

    draft_result = call_model("writing", author_provider, DEBATE_AUTHOR_SYSTEM_TMPL.format(brief=brief, revision_note=""), user_request)
    if not draft_result.ok:
        return f"Act Script (Debate): the Author ({provider_label('writing', author_provider)}) failed to draft -- {draft_result.error}"
    author_provider = draft_result.provider or author_provider
    draft = draft_result.text

    history = []
    last_critic_provider = author_provider  # seed so round 1's critic can't equal the author
    round_num = 0
    note = "No rounds ran."
    while round_num < max_rounds:
        round_num += 1
        # Excludes the Author (critic must never judge its own producer's
        # work), the previous round's Critic (so it actually rotates),
        # AND the Adjudicator (the Adjudicator's own prompt above
        # promises "never that round's Critic" -- this is what keeps
        # that promise true rather than just asserting it in text).
        previous_critic_provider = last_critic_provider
        critic_provider, _ = next_provider(
            "reasoning", after=previous_critic_provider,
            exclude={author_provider, previous_critic_provider, adjudicator_provider},
        )
        critic_result = call_model(
            "reasoning", critic_provider,
            DEBATE_CRITIC_SYSTEM_TMPL.format(round_num=round_num, review_criteria=review_criteria, draft=draft),
            "", exclude={author_provider, adjudicator_provider,
                         previous_critic_provider},
        )
        critic_provider = critic_result.provider or critic_provider
        last_critic_provider = critic_provider
        critic_data = _extract_json(critic_result.text) if critic_result.ok else None
        issues = (critic_data or {}).get("issues", [])
        _log_evidence(run_id, "critique", {
            "summary": f"round {round_num}: {len(issues)} issue(s)",
            "provider": critic_provider, "issues": issues,
        })

        if not issues:
            note = "No issues raised this round."
            history.append({"round": round_num, "critic": critic_provider, "issues": issues, "draft": draft})
            break

        revision_note = (
            "A Critic (a different model, reading cold) raised these issues last round "
            "-- address every BLOCKING and either fix or explicitly explain-and-decline "
            "every MAJOR; never silently ignore anything:\n"
            + "\n".join(f"- [{it.get('severity')}] {it.get('issue')}" for it in issues)
        )
        revised_result = call_model(
            "writing", author_provider,
            DEBATE_AUTHOR_SYSTEM_TMPL.format(brief=brief, revision_note=revision_note),
            user_request, exclude={critic_provider, adjudicator_provider},
        )
        author_provider = revised_result.provider or author_provider
        revised = revised_result.text if revised_result.ok else draft

        adjudication = call_model(
            "reasoning", adjudicator_provider,
            DEBATE_ADJUDICATOR_SYSTEM_TMPL.format(
                round_num=round_num, critique=json.dumps(issues), revision=revised,
            ),
            "",
            exclude={author_provider, critic_provider},
        )
        adjudicator_provider = adjudication.provider or adjudicator_provider
        adj_data = _extract_json(adjudication.text) if adjudication.ok else None
        verdict = (adj_data or {}).get("verdict", "PROCEED" if round_num < max_rounds else "DONE")
        note = (adj_data or {}).get("note", "")
        _log_evidence(run_id, "adjudication", {
            "summary": f"round {round_num}: {verdict} -- {note}", "provider": adjudicator_provider,
        })
        history.append({
            "round": round_num, "critic": critic_provider, "issues": issues,
            "draft_before": draft, "draft_after": revised,
        })
        draft = revised
        if verdict == "DONE":
            break
    else:
        note = note or "Reached the round cap without the Adjudicator saying DONE -- declaring done-at-best."

    final_reader, _ = next_provider(
        "reasoning", after=last_critic_provider, exclude={author_provider, last_critic_provider, adjudicator_provider},
    )
    final_check = call_model(
        "reasoning", final_reader,
        DEBATE_FINAL_READ_SYSTEM_TMPL.format(review_criteria=review_criteria, draft=draft),
        "", exclude={author_provider, last_critic_provider, adjudicator_provider},
    )
    final_reader = final_check.provider or final_reader
    _log_evidence(run_id, "final_read", {
        "summary": (final_check.text[:300] if final_check.ok else final_check.error),
        "provider": final_reader,
    })

    try:
        memory.kv_set(f"act_script_debate_history:{run_id}", history)
    except Exception:  # noqa: BLE001
        pass
    _log_evidence(run_id, "memory", {"summary": "full draft+critique provenance stored", "rounds": len(history)})

    header = (
        f"**Act Script -- Debate** (run `{run_id}`, {round_num} round(s); "
        f"author={provider_label('writing', author_provider)}, "
        f"adjudicator={provider_label('reasoning', adjudicator_provider)}, "
        f"final read by {provider_label('reasoning', final_reader)})\n\n"
    )
    tail = (
        f"\n\n---\nFinal cold read ({provider_label('reasoning', final_reader)}): "
        f"{final_check.text if final_check.ok else '(skipped -- ' + final_check.error + ')'}"
    )
    return header + draft + tail


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_act_script(user_request, mode="auto"):
    """Friday's ACT Script entry point. `mode` is "auto" (Thinking
    classifies it) or one of "sequential"/"fan_out"/"debate" to force a
    pipeline directly. Returns the final report/answer/artifact as plain
    text -- ready to hand straight back to whichever lightweight model
    asked for this (see agent_tools.ide_run_act_script)."""
    providers = available_providers("thinking")
    if not providers:
        return _NO_PROVIDER_MSG

    route = mode if mode in ("sequential", "fan_out", "debate") else None
    if route is None:
        route, _ = thinking_classify(user_request, providers[0])

    if route == "sequential":
        return run_sequential(user_request)
    if route == "fan_out":
        return run_fan_out(user_request)
    if route == "debate":
        return run_debate(user_request)

    result = call_model("thinking", providers[0], "Answer directly and concisely.", user_request)
    if result.ok:
        return result.text
    return f"Couldn't get an answer: {result.error}"

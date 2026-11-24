"""Counts the tokens Friday pulls from its connected models.

Every place Friday calls a model hands the reply to record(); the token
counts come from the reply's own usage report, in whichever shape that SDK
or API uses:

  OpenAI / Groq / Mistral / OpenRouter ...   usage.prompt_tokens,
                                             usage.completion_tokens
  Anthropic-style APIs                       usage.input_tokens,
                                             usage.output_tokens
  Google Gemini                              usage_metadata.prompt_token_count,
                                             candidates_token_count (+ thoughts)
  embeddings                                 usage.prompt_tokens / total_tokens

A reply that reports no usage is estimated from its text (about 4
characters per token) and counted separately as "estimated", so the total
never silently undercounts.

Totals live in token_usage.json next to Friday's settings, keyed by
"host|model" so the Settings panel can show them per connected model and
per tab. Writes are batched (at most one every two seconds); listeners
(the Settings panel) hear about every change. Thread-safe -- replies land
on worker threads.
"""
import json
import os
import threading
import time
from urllib.parse import urlsplit

_lock = threading.RLock()
_data = None
_dirty = [False]
_save_timer = [None]
_listeners = []

# check_kind -> host, for built-in entries that have no base URL
KIND_HOSTS = {"gemini": "generativelanguage.googleapis.com",
              "gemini_embed": "generativelanguage.googleapis.com",
              "groq": "api.groq.com", "mistral": "api.mistral.ai",
              "mistral_embed": "api.mistral.ai"}


def host_of(entry_or_url):
    """A settings entry (or URL) -> the host its tokens are filed under."""
    if isinstance(entry_or_url, dict):
        url = (entry_or_url.get("base_url") or "").strip()
        if not url:
            return KIND_HOSTS.get(entry_or_url.get("check_kind") or "", "")
    else:
        url = str(entry_or_url or "")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") and parts.port:
        return f"{host}:{parts.port}"       # Ollama :11434 vs LM Studio :1234
    return host


def key_for(host, model):
    return f"{(host or '').lower()}|{str(model or '').strip()}"


# -- reading usage out of a reply ----------------------------------------------
def _get(obj, name):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _num(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _first(obj, *names):
    for name in names:
        v = _num(_get(obj, name))
        if v is not None:
            return v
    return None


def extract_usage(response):
    """(input_tokens, output_tokens) from an SDK object or a JSON dict, or
    None when the reply doesn't say. Shapes, in the order tried:

      Gemini SDK / REST     usage_metadata / usageMetadata
                            (prompt + candidates + thoughts token counts)
      OpenAI-compatible     usage.prompt_tokens / completion_tokens / total
      Anthropic             usage.input_tokens / output_tokens (+ cache reads
                            and writes, which are input too)
      Cohere                usage.billed_units / meta.billed_units, usage.tokens
      Ollama native         prompt_eval_count / eval_count
      Workers AI envelope   result.usage
    """
    if response is None:
        return None
    meta = _get(response, "usage_metadata")
    if meta is None:
        meta = _get(response, "usageMetadata")
    if meta is not None:
        prompt = _first(meta, "prompt_token_count", "promptTokenCount")
        out = (_first(meta, "candidates_token_count", "candidatesTokenCount") or 0) \
            + (_first(meta, "thoughts_token_count", "thoughtsTokenCount") or 0)
        total = _first(meta, "total_token_count", "totalTokenCount")
        if prompt is not None or total is not None:
            prompt = prompt or 0
            if not out and total:
                out = max(0, total - prompt)
            return prompt, out
    if _get(response, "prompt_eval_count") is not None or _get(response, "eval_count") is not None:
        return (_num(_get(response, "prompt_eval_count")) or 0,
                _num(_get(response, "eval_count")) or 0)
    usage = _get(response, "usage")
    if usage is None:
        meta = _get(response, "meta")                      # Cohere v1
        usage = _get(meta, "billed_units") if meta is not None else None
        if usage is None:
            result = _get(response, "result")              # Workers AI
            usage = _get(result, "usage") if isinstance(result, dict) else None
        if usage is None:
            return None
    for nested in ("billed_units", "tokens"):              # Cohere v2
        inner = _get(usage, nested)
        if inner is not None and _get(usage, "prompt_tokens") is None \
                and _get(usage, "input_tokens") is None:
            usage = inner
            break
    prompt = _first(usage, "prompt_tokens", "input_tokens", "promptTokens", "inputTokens")
    if prompt is not None:
        prompt += (_num(_get(usage, "cache_creation_input_tokens")) or 0) \
            + (_num(_get(usage, "cache_read_input_tokens")) or 0)
    out = _first(usage, "completion_tokens", "output_tokens", "completionTokens", "outputTokens")
    total = _first(usage, "total_tokens", "totalTokens")
    if prompt is None and out is None and total is None:
        return None
    prompt = prompt or 0
    if out is None:
        out = max(0, (total or 0) - prompt)               # embeddings: no output
    return prompt, out


def estimate(*texts):
    return max(1, sum(len(t or "") for t in texts) // 4)


# -- storage -------------------------------------------------------------------
def _path():
    import memory
    return os.path.join(memory._ensure_folder("settings"), "token_usage.json")


def _empty():
    return {"models": {}, "days": {}, "since": int(time.time())}


def _load():
    global _data
    if _data is None:
        try:
            with open(_path(), encoding="utf-8") as f:
                loaded = json.load(f)
            _data = loaded if isinstance(loaded, dict) and "models" in loaded else _empty()
        except Exception:  # noqa: BLE001
            _data = _empty()
        _data.setdefault("days", {})
    return _data


def _save_now():
    with _lock:
        _save_timer[0] = None
        if not _dirty[0] or _data is None:
            return
        _dirty[0] = False
        snapshot = json.dumps(_data)
    try:
        path = _path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(snapshot)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def _schedule_save():
    _dirty[0] = True
    if _save_timer[0] is None:
        t = threading.Timer(2.0, _save_now)
        t.daemon = True
        _save_timer[0] = t
        t.start()


def flush():
    """Write pending counts now (Friday's quit path, and tests)."""
    timer = _save_timer[0]
    if timer is not None:
        timer.cancel()
    _save_now()


def reload():
    """Forget the in-memory copy (tests point FRIDAY_MEMORY_PATH elsewhere)."""
    global _data
    with _lock:
        _data = None


# -- recording -----------------------------------------------------------------
def record(host, model, response=None, *, prompt_text="", reply_text="",
           input_tokens=None, output_tokens=None):
    """Count one model reply. Never raises -- metering must not break a call."""
    try:
        if input_tokens is None and output_tokens is None:
            usage = extract_usage(response)
        else:
            usage = (input_tokens or 0, output_tokens or 0)
        estimated = usage is None
        if estimated:
            if not (prompt_text or reply_text):
                return
            usage = (estimate(prompt_text), estimate(reply_text) if reply_text else 0)
        tokens_in, tokens_out = usage
        if tokens_in + tokens_out <= 0:
            return
        today = time.strftime("%Y-%m-%d")
        with _lock:
            data = _load()
            row = data["models"].setdefault(key_for(host, model), {
                "in": 0, "out": 0, "calls": 0, "estimated": 0, "days": {}})
            row["in"] += tokens_in
            row["out"] += tokens_out
            row["calls"] += 1
            if estimated:
                row["estimated"] += tokens_in + tokens_out
            row["days"][today] = row["days"].get(today, 0) + tokens_in + tokens_out
            for old in sorted(row["days"])[:-31]:            # keep a month
                del row["days"][old]
            data["days"][today] = data["days"].get(today, 0) + tokens_in + tokens_out
            for old in sorted(data["days"])[:-31]:
                del data["days"][old]
            _schedule_save()
            listeners = list(_listeners)
        for fn in listeners:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def record_entry(entry, model, response=None, **kwargs):
    """record() for a Settings entry (host from its base URL / kind)."""
    record(host_of(entry), model or (entry or {}).get("model"), response, **kwargs)


# -- reading -------------------------------------------------------------------
def usage_for(keys=None):
    """Totals for the given host|model keys (None = everything):
    {"total", "in", "out", "today", "estimated", "calls", "since"}."""
    today = time.strftime("%Y-%m-%d")
    out = {"total": 0, "in": 0, "out": 0, "today": 0, "estimated": 0, "calls": 0}
    with _lock:
        data = _load()
        rows = data["models"].items()
        for key, row in rows:
            if keys is not None and key not in keys:
                continue
            out["in"] += row.get("in", 0)
            out["out"] += row.get("out", 0)
            out["estimated"] += row.get("estimated", 0)
            out["calls"] += row.get("calls", 0)
            out["today"] += row.get("days", {}).get(today, 0)
        out["total"] = out["in"] + out["out"]
        out["since"] = data.get("since", 0)
    return out


def model_total(key):
    with _lock:
        row = _load()["models"].get(key) or {}
        return row.get("in", 0) + row.get("out", 0)


def top_models(limit=6):
    """[(host|model, total)] biggest first."""
    with _lock:
        rows = [(k, r.get("in", 0) + r.get("out", 0)) for k, r in _load()["models"].items()]
    return sorted(rows, key=lambda kv: -kv[1])[:limit]


def by_host():
    """[(host, {"total", "in", "out", "calls", "models"})] biggest first --
    every provider Friday has pulled tokens from, so they can be totalled."""
    hosts = {}
    with _lock:
        for key, row in _load()["models"].items():
            host = key.partition("|")[0] or "unknown"
            h = hosts.setdefault(host, {"total": 0, "in": 0, "out": 0, "calls": 0, "models": 0})
            h["in"] += row.get("in", 0)
            h["out"] += row.get("out", 0)
            h["calls"] += row.get("calls", 0)
            h["models"] += 1
    for h in hosts.values():
        h["total"] = h["in"] + h["out"]
    return sorted(hosts.items(), key=lambda kv: -kv[1]["total"])


def reset():
    with _lock:
        global _data
        _data = _empty()
        _schedule_save()
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass


def add_listener(fn):
    with _lock:
        if fn not in _listeners:
            _listeners.append(fn)


def remove_listener(fn):
    with _lock:
        if fn in _listeners:
            _listeners.remove(fn)


def short(n):
    """12345 -> '12.3k', 4560000 -> '4.56M'."""
    n = int(n or 0)
    if n < 1000:
        return str(n)
    for div, unit in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if n >= div:
            v = n / div
            return (f"{v:.2f}" if v < 10 else f"{v:.1f}" if v < 100 else f"{v:.0f}") \
                .rstrip("0").rstrip(".") + unit
    return str(n)

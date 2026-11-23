"""Friday's AI model/API-key settings: one list of "model" entries per
"faculty" (Thinking, Research, Transcription, ...) -- what the new
Settings panel (settings_panel.py) reads and writes.

This is sensitive data (raw API keys), so it gets the exact same
treatment as the rest of Friday's local memory (see memory.py's own
module docstring): one small JSON blob, Fernet-encrypted at rest, in its
own folder under Friday's existing `~/.friday/` memory directory, locked
down with the same OS-level permission restrictions. Rather than
duplicating that machinery, this module just reuses memory.py's already-
built primitives (`_ensure_folder`/`_write_encrypted`/`_read_encrypted`/
`_lock_down`) directly -- this file only owns the *shape* of the data
(faculties, model entries, defaults), not how it's stored on disk.

The persisted faculty keys are retained for compatibility and task
preference ordering (see act_script.py's ROLE_FACULTY), but the Settings
panel presents one flat connection list and chat-capable entries are
shared across chat tasks. Entries formerly matched to a task are tried
first; other compatible chat models are fallbacks. Non-chat services
remain available only through their supported integrations.
Image Generation entries are now genuinely callable: the first usable
entry carrying an API token powers the native Image panel through
friday_image/providers.py's Cloudflare Workers AI / Stability AI
adapters, resolved live by image_backend_resolver.py. The panel is
cloud-only: with no usable key it shows honest setup guidance instead of
rendering anything. Video Generation and
Embedding remain pure provider scaffolding for user-added entries -- no
AI generation/embedding call wiring exists for a custom entry there yet;
the Video panel still performs plainly labeled deterministic local
renders outside this API-key binding. (Embedding's own two pre-seeded
entries are still real; a custom addition there is reachability-checked
only.)
"""
import os
import uuid

import memory

# Legacy storage buckets, kept in stable order for task preference and
# service routing. The Settings UI presents these entries in one flat list.
FACULTIES = [
    "Voice",
    "Thinking",
    "Planning",
    "Review",
    "Verify",
    "Coding",
    "Image Generation",
    "Audio Generation",
    "Video Generation",
    "Research",
    "Embedding",
    "Text Generation",
    "Transcription",
]

_SETTINGS_FILENAME = "ai_models.json.enc"


def _settings_path():
    folder = memory._ensure_folder("settings")
    return os.path.join(folder, _SETTINGS_FILENAME)


def _env(name):
    return os.environ.get(name, "") or ""


def _entry(id_, name, model, check_kind, api_key="", builtin=True, base_url=""):
    return {
        "id": id_,
        "name": name,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "builtin": builtin,          # True = a capability Friday can genuinely call today
        "check_kind": check_kind,    # which settings_checks.py function verifies this one
        "status": "unknown",         # "unknown" | "connected" | "error"
        "detail": "",                # last check's human-readable result
        "last_checked": None,        # unix timestamp, or None if never checked
    }


def _default_data():
    """Faculties start EMPTY: every key Friday ever uses is one the user
    deliberately added in the Settings panel -- the .env file is never
    mirrored, pre-seeded, or consulted for model calls anywhere. This is
    what makes 'the agent provably uses the settings-panel keys' true by
    construction. (Local, key-less capabilities that don't dial a provider
    -- Piper/faster-whisper -- are the only pre-seeded entries; Research/
    Embedding keep their own service keys as before, separate from the
    shared chat-model pool.)"""
    return {
        "Voice": [],
        "Thinking": [],
        "Planning": [],
        "Review": [],
        "Verify": [],
        "Coding": [],
        "Image Generation": [],
        "Audio Generation": [
            _entry("piper", "Piper (local)",
                   "en_GB-northern_english_male-medium", "local_piper"),
        ],
        "Video Generation": [],
        "Research": [
            _entry("tavily", "Tavily", "search",
                   "tavily", _env("TAVILY_API_KEY")),
        ],
        "Embedding": [
            _entry("gemini_embed", "Google Gemini", "text-embedding-004",
                   "gemini_embed", _env("GEMINI_API_KEY")),
            _entry("mistral_embed", "Mistral", "mistral-embed",
                   "mistral_embed", _env("MISTRAL_API_KEY")),
        ],
        "Text Generation": [],
        "Transcription": [
            _entry("faster_whisper", "faster-whisper (local)", "small.en",
                   "local_whisper"),
        ],
    }


def load():
    """Returns the full {faculty: [entry, ...]} dict, creating (and
    saving) the default seed data on first-ever use. Also quietly backfills
    any faculty key missing from an older saved file (e.g. after an app
    update added a new faculty) so callers never need to guard against a
    KeyError here."""
    data = memory._read_encrypted(_settings_path())
    if not data:
        data = _default_data()
        save(data)
        return data
    changed = False
    if "Review" not in data and "Reasoning" in data:
        # "Reasoning" was renamed to "Review" (it was always only ever
        # used for review/critic/adjudicator/aggregator-review work --
        # see act_script.py's ROLE_FACULTY -- so the old name was just
        # a mislabel, not a different capability). Carry an existing
        # user's models straight over under the new name rather than
        # losing them or leaving an orphaned "Reasoning" section no
        # panel will ever show again.
        data["Review"] = data.pop("Reasoning")
        changed = True
    for fac in FACULTIES:
        if fac not in data:
            # Backfill new task/service buckets without moving or dropping
            # any entries already saved by an earlier version.
            data[fac] = []
            changed = True
    if changed:
        save(data)
    return data


def save(data):
    """Writes `data` back to disk, encrypted, with the same permission
    lockdown as every other file in Friday's memory folder."""
    path = _settings_path()
    memory._write_encrypted(path, data)
    memory._lock_down(path)


def _new_custom_model_entry(faculty, name, model, api_key, base_url):
    if faculty not in FACULTIES:
        raise ValueError(f"Unknown faculty: {faculty!r}")
    check_kind = "generic_http"
    if faculty == "Image Generation":
        try:
            from friday_image.providers import detect_image_provider
            check_kind = {
                "cloudflare": "cloudflare_image",
                "stability": "stability_image",
                "openai": "openai_image",
                "ideogram": "ideogram_image",
            }.get(detect_image_provider({"base_url": base_url}), "generic_http")
        except Exception:  # noqa: BLE001 -- plain reachability stays honest
            check_kind = "generic_http"
    return _entry(f"custom-{uuid.uuid4().hex[:10]}", name, model,
                  check_kind, api_key, builtin=False, base_url=base_url)


def add_model(data, faculty, name, model, api_key, base_url=""):
    """Adds one user-supplied model entry to its task or service bucket
    and saves immediately. Custom chat models use the OpenAI-compatible
    HTTP dispatcher; image entries use the matching delivered image
    provider adapter when their base URL is recognized."""
    entry = _new_custom_model_entry(faculty, name, model, api_key, base_url)
    data.setdefault(faculty, []).append(entry)
    save(data)
    return entry


def add_models(data, faculty, provider_name, models, api_key, base_url="",
               api_style="", provider_hint=""):
    """Adds a batch of discovered models from one provider/key. Each
    model becomes its own connection row; existing rows for the same
    endpoint, key and model are left untouched. The batch is persisted
    with one encrypted write."""
    if faculty not in FACULTIES:
        raise ValueError(f"Unknown faculty: {faculty!r}")
    existing = {
        (str(entry.get("model") or "").strip(),
         entry.get("api_key") or "", (entry.get("base_url") or "").rstrip("/"))
        for entry in data.get(faculty, [])
    }
    added = []
    for model in models or []:
        model = str(model or "").strip()
        identity = (model, api_key or "", (base_url or "").rstrip("/"))
        if not model or identity in existing:
            continue
        entry = _new_custom_model_entry(
            faculty, f"{provider_name} - {model}", model, api_key, base_url)
        if api_style:
            entry["api_style"] = api_style
        if provider_hint:
            entry["provider_hint"] = provider_hint
        data.setdefault(faculty, []).append(entry)
        added.append(entry)
        existing.add(identity)
    if added:
        save(data)
    return added


def remove_model(data, faculty, model_id):
    """Removes one model entry by id and saves immediately. Safe no-op
    if the faculty/id doesn't exist."""
    data[faculty] = [m for m in data.get(faculty, []) if m.get("id") != model_id]
    save(data)


def connection_counts(data):
    """Count configured entries by their most recent connectivity state.

    Unknown and in-progress checks stay separate from confirmed failures;
    they are not safe to classify or bulk-remove as disconnected.
    """
    counts = {"connected": 0, "not_connected": 0, "not_checked": 0}
    for entries in data.values():
        for entry in entries:
            status = entry.get("status")
            if status == "connected":
                counts["connected"] += 1
            elif status == "error":
                counts["not_connected"] += 1
            else:
                counts["not_checked"] += 1
    return counts


def remove_disconnected_models(data):
    """Remove only entries with a failed connectivity check and save once.

    Entries that are unchecked or currently checking are retained.
    Returns the number of removed entries.
    """
    removed = 0
    for faculty, entries in data.items():
        remaining = [entry for entry in entries
                     if entry.get("status") != "error"]
        removed += len(entries) - len(remaining)
        data[faculty] = remaining
    if removed:
        save(data)
    return removed


def update_check_result(data, faculty, model_id, ok, detail, checked_at):
    """Writes a connectivity-check result back onto the matching entry
    and saves. Safe no-op if the entry was removed in the meantime."""
    for entry in data.get(faculty, []):
        if entry.get("id") == model_id:
            entry["status"] = "connected" if ok else "error"
            entry["detail"] = detail
            entry["last_checked"] = checked_at
            save(data)
            return


def mask_key(key):
    """A fully redacted ("hashed out") display form of an API key --
    never any real characters from it, just a fixed-width bullet mask
    plus a length hint so two different keys are still visually
    distinguishable without revealing anything about either one. Even a
    short visible tail is deliberately avoided -- a handful of real
    characters still meaningfully narrows a brute-force attempt for a
    short key."""
    if not key:
        return "not set"
    return "\u2022" * 10 + f"  ({len(key)} chars)"

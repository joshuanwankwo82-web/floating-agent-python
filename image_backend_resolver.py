"""Chooses the Image Generation panel's backend from Friday's Settings.

This is the single wiring point between "a key typed into the Settings
panel" and "the Image panel generating with it". The panel is CLOUD-ONLY:
keyed Image Generation entries are tried in panel order (or the engine
picker's explicit choice is honored), and the genuinely usable provider
adapter is returned (delivered today: Cloudflare Workers AI and
Stability AI -- see friday_image/providers.py). There is deliberately no
local rendering fallback: without a usable key the panel gets
NoProviderBackend, whose honest setup guidance is shown instead of an
image. A broken chosen entry surfaces as MisconfiguredProviderBackend so
the user fixes the real entry instead of being silently re-routed.

Kept as a tiny root module (rather than inside friday_image) because it
deliberately depends on the app's settings_store; the friday_image
package itself stays independently importable.
"""
from __future__ import annotations

from friday_image.providers import (MisconfiguredProviderBackend,
                                    NoProviderBackend, build_image_backend)

IMAGE_FACULTY = "Image Generation"
# Legacy saved picker value: releases before cloud-only mode could store
# "local"; it now simply means "automatic".
LOCAL_SENTINEL = "local"


def _load_settings(settings_data):
    if settings_data is not None:
        return settings_data
    try:
        import settings_store
        return settings_store.load()
    except Exception:  # noqa: BLE001 -- never break the panel over settings
        return None


def resolve_image_backend(settings_data: dict | None = None,
                          entry_id: str | None = None):
    """Return the backend the Image panel should use right now.

    ``entry_id`` semantics:
      * a specific Settings entry id -- exactly that entry (a
        misconfigured one surfaces as MisconfiguredProviderBackend, not a
        silent swap);
      * ``None`` (or a vanished/legacy id) -- automatic: the first
        genuinely usable keyed entry;
      * no usable keyed entry -- NoProviderBackend (cloud-only empty
        state; there is no local rendering).

    ``settings_data`` may be injected by tests; when omitted, the live
    encrypted Settings store is read. Never raises -- panel startup must
    survive a missing/broken settings file.
    """
    if entry_id == LOCAL_SENTINEL:
        entry_id = None
    data = _load_settings(settings_data)
    entries = (data or {}).get(IMAGE_FACULTY, [])
    keyed = [e for e in entries if (e.get("api_key") or "").strip()]
    if entry_id:
        match = next((e for e in keyed if e.get("id") == entry_id), None)
        if match is not None:
            try:
                return build_image_backend(match)
            except ValueError as problem:
                return MisconfiguredProviderBackend(
                    match.get("name") or match.get("model") or "cloud image entry",
                    str(problem),
                )
        # The chosen entry was removed meanwhile -- fall through honestly.
    for entry in keyed:
        try:
            return build_image_backend(entry)
        except ValueError:
            continue
    misconfigured = next(iter(keyed), None)
    if misconfigured is not None:
        # Keys exist but none is usable: show the first real problem.
        try:
            build_image_backend(misconfigured)
        except ValueError as problem:
            return MisconfiguredProviderBackend(
                misconfigured.get("name") or misconfigured.get("model")
                or "cloud image entry",
                str(problem),
            )
    return NoProviderBackend()


def image_engine_catalog(settings_data: dict | None = None) -> list[dict]:
    """The Image Generation entries for the panel's engine picker: every
    keyed entry with a ready label and an honest usable/needs-attention
    verdict. Never raises; returns [] when settings are unreadable."""
    data = _load_settings(settings_data)
    catalog: list[dict] = []
    for entry in (data or {}).get(IMAGE_FACULTY, []):
        if not (entry.get("api_key") or "").strip():
            continue
        fallback = entry.get("name") or entry.get("model") or "provider"
        try:
            backend = build_image_backend(entry)
            short = getattr(backend, "model", "").rsplit("/", 1)[-1]
            catalog.append({
                "id": entry.get("id", ""),
                "usable": True,
                "label": f"{backend.name} · {short}" if short else backend.name,
            })
        except ValueError as problem:
            catalog.append({
                "id": entry.get("id", ""),
                "usable": False,
                "label": f"{fallback} (needs attention)",
                "problem": str(problem),
            })
    return catalog

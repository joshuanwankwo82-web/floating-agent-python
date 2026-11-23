# Quiet Image → Friday Image Generation integration

This document records the integration of the approved standalone Quiet Image
project (`/home/user/quiet-image/`) into Friday's real **Image Generation**
work panel. It follows the same application-body pattern as Quiet Chat and
Quiet Video: Friday keeps native panel chrome and lifecycle, while the ported
CustomTkinter child application owns the image workspace.

![Native Friday Image Generation panel](../friday-image-preview.png)

## Preserved Friday architecture

- `AppPanelManager` still launches `image_gen` through the same custom-panel
  registry (`custom_panel`/`embedded`, no OS process or window handle).
- `conversation_panels.ImageGenPanel` remains Friday's native `tk.Toplevel`
  with panel_ui's titlebar buttons, drag behavior, active-panel glow,
  status/error border, geometry maximization/restoration and close hooks.
- The legacy single-line input bar is removed for Image Generation only; the
  richer child UI owns the prompt/brief and reference-image controls.
- The panel minimizes to Friday's real pill and restores to the exact same
  child view, project and draft.
- Manager-facing public references remain available (`entry`, `_send_btn`,
  `_attach_btn`, `chat_view`) while all behavior belongs to `QuietImageView`.
- Future provider plumbing is retained as a code seam: `ImageBackend` is
  injectable. `LocalArtBackend` is the only shipped backend and is labeled
  as such.

## New `friday_image/` modules

| File | Responsibility |
|---|---|
| `view.py` | Complete CustomTkinter child UI: projects rail, creative brief, controls, preview/console, image queue, results and status bar. |
| `model.py` | Validated requests, projects/render jobs, JSON-safe state encoding and truthful interrupted-job recovery. |
| `renderer.py` | Deterministic Pillow renderer: live previews, PNG/JPEG/WebP output, thumbnails, cancellation checks and atomic files. |
| `backend.py` | Honest default local art backend plus future explicit-provider injection seam. |
| `storage.py` | Friday encrypted kv adapter (`image_panel_ui:v1`) for project metadata, output settings and file paths. |
| `icons.py` | Pillow line icons and rectangular previews whose native Tk images belong to this panel's interpreter. |
| `widgets.py` | Quiet controls plus label/scroll/button sweep for CTk timers inside Friday's native windows. |

## Controls and outputs

- Prompt, character count and suggestion all update the project brief and
  deterministic seed truthfully.
- Style, composition, aspect ratio, quality, texture detail, seed, output
  format and optional reference image feed the real local renderer.
- The preview redraw is generated locally without starting a saved render.
- PNG, JPEG and WebP are real Pillow outputs. A thumbnail is derived from the
  completed output.
- Detail-stage progress, status, cancellation and completed results are real.
- Result actions reveal/open, rename/export and copy the output path.
- Render output defaults to `<Friday memory>/generated/images/`; opening the
  folder is handled by the platform screenshot/open path at runtime.

## Honesty/security boundaries

- Deterministic local image art render = real functionality.
- AI image generation = not implemented. The UI and tool-facing text say this
  directly; no provider credential in Settings is read or used by this panel.
- Prompt text and reference images are never copied into state, uploaded, or
  sent to a network service.
- Project metadata and rendered output paths are stored through Friday's
  existing Fernet-encrypted kv memory, separate from semantic/vector memory.
- A corrupt/unreadable encrypted record is not overwritten: the view starts a
  clearly labeled temporary session and leaves the record untouched.
- A still-running render is never silently restarted or restated as active
  after restart; persisted interruption is marked truthfully.

## Focused verification

Latest direct results for the new integration path:

| Suite | Result |
|---|---:|
| `test_friday_image_model.py` — validation, encrypted state, existing/corrupt state behavior, format/reference/cancel/preview/seed tests | **12/12** |
| `test_friday_image_gui.py` — real `ImageGenPanel` widgets, titlebar, palette, draft, mocked reference picker, preview, fast PNG render, cancel/reopen/compact bounds/timer cleanup | **13/13** |
| `test_friday_image_integration.py` — actual `AppPanelManager` GUI thread, interpreter-owned fonts/images, pill restore, status border, bounds, close during render and repeated-open cleanup | **5/5** |

See [Quiet Image verification](QUIET_IMAGE_VERIFICATION.md) for the complete
command-level evidence captured for this delivery.

The existing `app_panels` key/label/alias registry remains `image_gen` /
`Image Generation`; behavior of the Circle's chat, Settings AI keys, IDE,
Profile placeholder, Quiet Chat and Quiet Video was not otherwise reimplemented.

## Addendum — Cloud image provider wiring (2026-10-15)

The panel's `ImageBackend` seam now has its first real provider: a Cloudflare
Workers AI adapter (`friday_image/providers.py`) selected live from Settings
via `image_backend_resolver.py`. Behaviour: first Settings → Image Generation
entry carrying an API token becomes the active backend (badge `cloud · model`);
no token → the delivered deterministic local renderer keeps serving unchanged
(badge `local renderer · no AI provider connected`); a malformed keyed entry
surfaces as an explicit setup-needed error instead of a silent fallback. The
resolver is consulted on every Generate, so Settings changes apply without a
restart. Settings entries added to that faculty now receive the real
`check_kind="cloudflare_image"` credential check (token authentication against
the account's Workers AI catalog — no generation quota is spent). User-facing
setup: docs/CLOUD_IMAGE_PROVIDER.md. Focused regression: dev_tests/
test_friday_image_cloud.py. GUI suites were not rerun for this addendum (no
display server in the verification sandbox).

## Addendum 2 — Multi-provider image backends + priority semantics (2026-10-15)

Field testing exposed that a single misconfigured keyed entry blocked later
valid ones. The resolver now tries keyed Image Generation entries in order
and activates the first genuinely usable one; MisconfiguredProviderBackend
appears only when at least one entry carries a key and none can be used.
A second delivered provider joins Cloudflare Workers AI: Stability AI
(multipart form POST to /v2beta/stable-image/generate/{sd3,core,ultra},
credit-balance check kind `stability_image`, provider chosen per entry from
its base-URL host). settings_store.add_model assigns the matching real check
kind per provider. Focused suite now 39 tests; the exact user screenshot
scenario (broken STABILITY above a valid Cloudflare entry) is covered by an
end-to-end live-store proof.

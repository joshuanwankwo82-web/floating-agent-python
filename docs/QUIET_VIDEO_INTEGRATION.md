# Quiet Video → Friday Video Generation integration

This document records the integration of the approved standalone Quiet Video
project (`/home/user/quiet-video/`) into Friday's real **Video Generation**
work panel. It is an application-body integration following the same pattern
as Quiet Chat: Friday keeps the native panel chrome and lifecycle, and the
ported CustomTkinter child application owns the video workspace.

![Native Friday Video Generation panel](../friday-video-preview.png)

## Preserved Friday architecture

- `AppPanelManager` still launches `video_gen` through the same custom-panel
  registry (`custom_panel`/`embedded`, no OS process or window handle).
- `conversation_panels.VideoGenPanel` remains Friday's native `tk.Toplevel`
  with panel_ui's titlebar buttons, drag behavior, active-panel glow,
  status/error border, geometry maximization/restoration and close hooks.
- The panel still minimizes to Friday's real pill and restores to the exact
  same child view and draft.
- Future provider plumbing is not deleted: the child's `VideoBackend`
  interface is injectable. `LocalMotionBackend` is the only shipped backend,
  and its label says so.

## New `friday_video/` modules

| File | Responsibility |
|---|---|
| `view.py` | Complete CustomTkinter child UI: projects rail, prompt/brief, settings, preview, console, render queue, results and status bar. |
| `model.py` | Validated requests, project/render jobs, JSON-safe state encoding and honest interrupted-job recovery. |
| `renderer.py` | Deterministic Pillow/imageio renderer: poster stills, GIF frames, MP4 frames, progress and cleanup on cancellation/failure. |
| `backend.py` | Honest default local motion backend plus future provider injection seam. |
| `storage.py` | Friday encrypted kv adapter (`video_panel_ui:v1`) for project metadata, output settings and file paths. |
| `icons.py` | Pillow line icons and rectangular previews whose native Tk images belong to this panel's interpreter. |
| `widgets.py` | Quiet controls plus label/scroll/button sweep for CTk timers inside Friday's native windows. |

## Controls and outputs

- Prompt, character count and suggestion all update the project brief and
  preview truthfully.
- Style, aspect ratio, duration, FPS, quality, camera motion, seed, format
  and optional reference image feed the real local renderer.
- Poster previews are generated locally without starting a video render.
- GIF support uses imageio/Pillow; MP4 support uses imageio's bundled FFmpeg
  wrapper, declared in `requirements.txt` as `imageio` + `imageio-ffmpeg`.
- Frame progress, status, cancellation and completed results are real.
- Completed projects show output/poster/reveal/copy-path actions.
- Render output defaults to `<Friday memory>/generated/videos/`; the header
  folder action can select another persistent destination.

## Honesty/security boundaries

- Deterministic local motion render = real functionality.
- AI video generation = not implemented. The UI says this directly; no
  provider credential in Settings is read or used by this panel.
- Prompt text and reference images are not copied into Friday state, emailed,
  uploaded, or sent to a network service.
- Project metadata and rendered output paths are stored through Friday's
  existing Fernet-encrypted kv memory, separate from semantic/vector memory.
- A corrupt/unreadable encrypted record is not overwritten: the view starts
  a clearly labeled temporary session and leaves the record untouched.
- A still-running render is never silently restated as active after restart;
  persisted interruption is marked truthfully.

## Verification

Latest direct results for the new integration path:

| Suite | Result |
|---|---:|
| `test_friday_video_model.py` — validation, encrypted state, existing/corrupt state behavior, local GIF/MP4/reference/cancel/poster tests | **12/12** |
| `test_friday_video_gui.py` — real `VideoGenPanel` widgets, titlebar, palette, draft, browser-free reference picker, preview, fast GIF render, cancel/reopen/compact bounds/timer cleanup | **13/13** |
| `test_friday_video_integration.py` — actual `AppPanelManager` GUI thread, interpreter-owned fonts/images, pill restore, status border, bounds, close during render and repeated-open cleanup | **5/5** |
| `test_conversation_panels.py` — registration/manager lifecycle plus the remaining Profile placeholder checks; Image and Video have dedicated suites | **104/104** |
| Full `dev_tests/test_*.py` regression under Xvfb | **38/38 scripts passed** |

The existing `app_panels` key/label/alias registry remains `video_gen` /
`Video Generation`; behavior of the Circle's chat, Settings AI keys, the
IDE, Image Generation and the Profile placeholder was not otherwise
reimplemented by the Video task. Image Generation later received its own
Quiet Image integration.

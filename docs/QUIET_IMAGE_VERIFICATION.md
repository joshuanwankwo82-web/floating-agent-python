# Quiet Image integration verification

Date: 2026-10-15

This page records the actual verification evidence for integrating the completed
standalone Quiet Image workspace into Friday's native **Image Generation**
panel. The integrated result stays in the Friday checkout; the standalone
`/home/user/quiet-image/` source remains separate and unchanged.

## Integrated preview evidence

[`../friday-image-preview.png`](../friday-image-preview.png) is a real Xvfb
capture of `conversation_panels.ImageGenPanel`, not a mockup. Before the
capture, the test driver:

1. Seed Friday memory with a temporary private folder.
2. Entered a real creative brief and selected Glass/Rings/16:9/Draft/PNG.
3. Invoked the panel's real **Generate** button.
4. Waited for the local renderer to finish and write a real PNG plus thumbnail.
5. Scrolled the native workbench so the returned art preview and console were
   visible.
6. Captured the panel's actual native window after it was withdrawn/re-shown.

The screenshot's status bar reports **Image rendered locally**, and the queue
contains the completed job. The preview was produced by the local deterministic
renderer; no AI provider was contacted or simulated.

## Focused new tests

| Suite | Result | What it proves |
|---|---:|---|
| `python -m unittest dev_tests.test_friday_image_model -v` | **12/12** | Request validation, seed determinism, all panel formats, live preview shapes, reference-image handling, cancellation, encrypted state round-trip, fresh/corrupt-state behavior, interrupted-job recovery and textual export. |
| `xvfb-run -a python -m unittest dev_tests.test_friday_image_gui -v` | **13/13** | Real panel widgets, quiet palette/titlebar, prompt draft, real control-to-preview updates, mocked file picker, fast PNG render, real output/thumbnail, cancellation without partial files, encrypted reopen history, compact bounds, interpreter-owned CTk images, tuple fonts, timer/tooltips/render cleanup and close-during-render semantics. |
| `xvfb-run -a python -m unittest dev_tests.test_friday_image_integration -v` | **5/5** | Real `AppPanelManager`/`image_gen` lifecycle, separate GUI-owner interpreter, minimize-to-pill/restore, Friday status border, manager bounds, close during active render, truthful cancelled-state persistence and clean repeated opens. |

Focused combined result: **30/30 Image integration checks passed**.

## Existing Friday regression confirmations

| Suite in final full run | Result |
|---|---:|
| `dev_tests/test_conversation_panels.py` | **104/104** checks |
| `dev_tests/test_friday_video_model.py` | **12/12** tests |
| `dev_tests/test_friday_video_gui.py` | **13/13** tests |
| `dev_tests/test_friday_video_integration.py` | **5/5** tests |
| `dev_tests/test_friday_chat_model.py` | **15/15** tests |
| `dev_tests/test_friday_chat_gui.py` | **26/26** tests |
| `dev_tests/test_friday_chat_integration.py` | **5/5** tests |
| `dev_tests/test_app_panels.py` | **175/175** checks |
| `dev_tests/test_settings_panel.py` | **75/75** checks |
| `dev_tests/test_friday_memory.py` | **118/118** checks |
| `dev_tests/test_act_script.py` | **77/77** checks |
| `dev_tests/test_friday_glow.py` | **34/34** checks |

The full command ran every `dev_tests/test_*.py` script sequentially under
`xvfb-run -a` with Qt's required XCB libraries installed:

**41/41 test scripts exited successfully, 0 failures.**

## Static/integrity checks

- `python -m compileall -q` completed cleanly for `friday_image/`,
  `conversation_panels.py`, the new tests, and panel-facing documentation-hook
  modules.
- Fresh imports passed for the Image model, renderer, backend, encrypted
  storage, widgets/icons, view and `conversation_panels.ImageGenPanel`.
- `git diff --check` completed cleanly.
- The panel/state path uses `image_panel_ui:v1` in Friday's encrypted kv layer;
  a corrupt record switch is tested and leaves the unreadable data untouched.
- The Image panel did not add any semantic/vector-memory write.
- Friday's separate Circle, IDE, Settings, Chat and Video integrations kept
  their dedicated regression suites green.
- `/home/user/quiet-image/`, `/home/user/quiet-video/`, `/home/user/quiet-chat/`
  and `/home/user/quiet-ide/` remain standalone workspaces, separate from this
  checkout.

## Boundaries and non-claims

- The shipped backend is a deterministic local Pillow art renderer only.
- No API key is read from an image-provider connection in Settings.
- No network, remote provider, upload, fake connection, generated-content
  label or AI-image model call is present.
- `ImageBackend.render()` is the explicit future-provider seam; integrating a
  provider must be a separate, billed/account-aware implementation.
- The final regression did not exercise real provider APIs, microphone/speaker
  hardware, a production Windows window manager, OS application embedding, or
  a live browser. The browser prerequisite checks pass; live Chromium checks
  are skipped when Playwright's browser binary is absent in this sandbox.

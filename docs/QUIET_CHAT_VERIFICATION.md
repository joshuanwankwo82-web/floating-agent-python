# Quiet Chat integration — verification

Verified on **12 October 2026** against Friday checkout
`01ed9ecaa1716322b1a60d30c37853100005100e`, with the changes in this delivery.

## Environment

Linux/Xvfb; Python **3.13.16**, CustomTkinter **5.2.2**, Pillow **12.3.0**,
NumPy **2.3.5**, cryptography **50.0.2**. Qt-dependent regression tests used
PySide6 Essentials/Qt **6.12.0** with the offscreen plugin and the necessary
`libxkbcommon0` system library. Provider SDKs were installed for mocked
existing-regression imports; no live model service was used for this work.

## New targeted tests: 46 passed

| Suite | Result |
|---|---:|
| `test_friday_chat_model.py` — data validation, Unicode/export, encryption, missing/corrupt history, separation from semantic memory | **15/15 tests** |
| `test_friday_chat_gui.py` — real native widgets, palette, composer, Enter/Shift+Enter, drafts/history, file-name references, copy/export/delete, async replies/errors, narrow layouts, 125% widget scaling, scoping and teardown | **26/26 tests** |
| `test_friday_chat_integration.py` — real AppPanelManager GUI owner thread, second Tk interpreter, actual minimized pill/restore, live border/status and bounds, pending close/reopen and repeated lifecycle | **5/5 tests** |

The targeted GUI suite captures both Python callback exceptions and Tcl
`bgerror` messages and asserts neither occurred. The targeted manager suite
completed without Tcl shutdown warnings. The screenshots were captured from
real `conversation_panels.ChatPanel` windows and checked for callback errors.

## Relevant regression suites

All of these completed successfully on the final implementation:

| Existing suite | Result |
|---|---:|
| Conversation panels: registration/lifecycle for all four; unchanged Profile/Image/Video draft-surface coverage | **156/156 checks** |
| Direct import independence: Settings, Notepad, conversation panels, app panels | **4/4 fresh-process imports** |
| App panels | **175/175 checks** |
| Settings panel | **75/75 checks** |
| Friday memory | **118/118 checks** |
| Friday state border/glow | **34/34 checks** |
| Panel-status notifications | **100/100 checks** |
| Panel-status dispatch | **11/11 checks** |
| Window-management overhaul | **143/143 checks** |
| Embedded IDE chat | **14/14 checks** |
| IDE minimize-to-pill | **30/30 checks** |
| IDE activity bar | **21/21 checks** |
| Hidden circle-response bubble | **8/8 checks** |

Some existing IDE/pill/window-management suites emitted Tk shutdown timer
warnings while all their assertions passed. Those unrelated paths were not
rewritten here; the dedicated new Chat tests check cleanup explicitly and
ran cleanly. These passing check totals are not a claim that every suite in
the repository or every production platform has been tested.

### Why the old Chat assertions changed

Before implementation, `test_conversation_panels.py` already failed by
calling single-line Entry methods on the existing CTkTextbox Chat prototype.
It also asserted superseded shell colours, Canvas attachment controls and a
red-hover Send button. Those obsolete Chat-only expectations were replaced
with the dedicated real-GUI tests above. Chat/Image/Video assertions are
retained in their dedicated integration suites; Profile keeps its draft-shell
checks, and registration/native lifecycle still cover all four panels. Test
storage is redirected to temporary Friday memory directories.

## Scope/integrity checks

- Every Python source in the modified checkout parses successfully.
- `git diff --check` is clean.
- The entire earlier `quiet-chat/` and `quiet-ide/` trees match their recorded
  pre-task SHA-256 hashes. Original Quiet Chat's **10/10 model tests** also pass.
- ProfilePanel and the legacy Canvas icon class remain the original draft
  primitives. Chat, Image and Video host their dedicated Quiet child views.
  Friday's core circle/IDE-memory execution paths remain outside this panel
  work; integration edits are limited to panel-facing wiring and later task
  documentation.
- The source archive excludes Git internals, runtime caches, credentials,
  dependency directories and temporary test data.

## Not verified / not implemented

- No production Windows native-window run, actual microphone/audio device,
  real OS application embedding, provider API request or live Friday
  conversation/tool flow was tested by this UI integration.
- Mic/speaker and file-content ingestion are not connected to this Chat
  panel. Replies remain explicitly simulated; the live agent’s existing
  circle/IDE routes are unchanged.
- Encrypted UI history is separate from semantic AI memory and still uses
  Friday’s existing local-key security model. Explicit user exports are
  plaintext; unsent drafts are in-memory only.
- CustomTkinter’s broader process-global appearance/scaling architecture and
  the repository’s mixed Qt/Tk ownership are not being replaced. This change
  adds safe ownership/cleanup for the newly ported Chat interface only.

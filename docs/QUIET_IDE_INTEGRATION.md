# Quiet IDE Integration

This build incorporates the approved Quiet workspace design into Friday's
main `IDEApp` (`ide/ide.py`). It is a presentation-layer integration: the
IDE's existing editor, terminal, filesystem sandbox, media previews, Git
support, run/build tasks, Agent Mode chat and minimize/restore behavior are
still provided by the original Friday IDE runtime.

## What changed

- `ide/quiet/` contains the new presentation factories and workspace mixin:
  palette tokens, line icons, common CustomTkinter widgets, editable native
  panes/tabs, activity-bar panels and the embedded Quiet-style chat pane.
- `ide/ide.py` receives a small install hook and explicit Tk font/image
  owners so the Quiet layout can be layered over the existing `IDEApp`
  without moving the agents, tools or providers.
- `ide/media_codecs.py` accepts an optional Tk `master` for images created
  by the IDE's second interpreter.
- `ide/tests/design_test.py` updates two obsolete design expectations that
  no longer describe the CustomTkinter terminal header or multiline
  composer.
- `launch_ide.py` provides an offline UI-only entry point. Use
  `python launch_ide.py --no-chat` for the workbench without Friday's chat
  pane. In the offline chat launcher, Friday is clearly marked
  **Agent not connected**; it does not fabricate replies.
- `dev_tests/test_quiet_workbench_gui.py` and
  `dev_tests/test_quiet_interpreter_ownership.py` cover the new Quiet
  workspace and interpreter/event-loop ownership boundaries.

## What did not change

Friday's agent/runtime modules were not adapted or replaced during this UI
integration. In particular, the following files remain byte-identical to
their pre-IDE-integration state:

- `agent_tools.py`
- `agent_overlay.py`
- `act_script.py`
- `tool_schemas.py`
- `memory.py`
- `app_panels.py`
- `window_backend.py`
- `conversation_panels.py`
- `browser_tools.py`
- `overlay_speech.py`
- `settings_panel.py`
- `settings_store.py`
- `notepad_panel.py`
- `panel_ui.py`

Tool scoping stays the same: Friday's IDE tools are active through Agent
Mode, and the offline standalone IDE does not invent an AI backend.

## Launching

### Full Friday Agent Mode

```bash
python -m pip install -r requirements.txt
python agent_overlay.py
```

Ask Friday to turn on Agent Mode or open the IDE. The Quiet IDE window is
the same Agent Mode window used by Friday.

### Offline, UI-only workbench

Install only the lighter IDE requirements if the voice, model and browser
stack is not needed:

```bash
python -m pip install -r requirements-ide.txt
python launch_ide.py --no-chat
```

`--no-chat` is optional; without it the IDE still runs but the chat pane is
marked unavailable because no live Friday agent is attached.

## Verification completed in this integration

All suites were run from the project checkout in a fresh GUI test session:

- Built-in `ide/tests/run_all.py`: **222/222 PASS** across 9 suites.
- New Quiet workbench GUI suite: **21/21 PASS**.
- Interpreter ownership suite: **PASS** — Qt/app-panel first, Tk IDE second;
  64 IDE-scoped tools remain exposed alongside the 104-tool full set.
- Fresh regression runner: **22/22 suites PASS**.
  - IDE/agent tools: 178/178
  - Agent activity visualization: 30/30
  - IDE activity bar: 21/21
  - IDE chat panel: 14/14
  - IDE chat server: 16/16
  - IDE chat tool scope: 29/29
  - IDE close guard: 6/6
  - Geometry/placeholders: 26/26
  - Minimize-pill behavior: 30/30
  - Build-protocol permission flow: 69/69
  - Act Script behavior: 77/77
  - Pending decision timeout: 33/33
  - Tool-loop iteration ceiling: 15/15
  - Friday glow/state: 34/34
  - Panel status dispatch: 11/11
  - Panel status notifications: 100/100
  - Window management overhaul: 143/143
  - Panel import independence: PASS
  - Conversation panels: 156/156
  - App panels: 175/175
  - Settings panel: 75/75
  - Friday Chat integration: PASS

Two historical non-failure notes remain environmental, not design changes:
`test_build_protocol_permission.py` can print a harmless callback-cleanup
message during teardown, and Python 3.13 may print a Tcl thread-ownership
runtime warning in the window-management suite. Both suites pass.

## Scope of the separate redesign workspace

`/home/user/friday-ide-redesign/` remains a standalone prototype/reference
project and was left unchanged after the approved design was applied to this
main checkout.

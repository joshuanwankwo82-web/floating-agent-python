# Friday — Architecture Map

This document is a map of the codebase: what every file is, how the pieces
fit together, and — in particular — how Friday's "brain" actually works.
It's a companion to `README.md` (written for an end user setting the project
up): a snapshot, organized by what things *are*, meant to make the project
legible at a glance.

---

## 1. The Brain: Listen → Think → Act → Speak (+ Remember, + Aware)

Friday is a loop with four stages, plus two that run quietly alongside
all of them. Every one of those stages lives in a specific, dedicated
place in the code:

```
            ┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐
   you ───► │  LISTEN  │ ───► │  THINK   │ ───► │   ACT    │ ───► │  SPEAK   │ ───► you
    speak   └──────────┘      └────┬─────┘      └──────────┘      └──────────┘
  or type    agent_overlay.py      │              agent_tools.py   agent_overlay.py
             (wake word +          │ recalls       (tools the       (TTS, spoken
              faster-whisper       │ relevant       LLM can call —   aloud; reply
              speech-to-text)      │ memories       see §1.3)        text is never
                                   │ + what's                        shown on screen)
                                   │ on screen
                                   ▼ into context
                      ┌──────────┐   ┌──────────┐
                      │ REMEMBER │   │  AWARE   │ ← queried fresh every turn, not stored
                      └──────────┘   └──────────┘
                       memory.py      agent_tools.py
                      (encrypted      (live panel/IDE
                       files; see      state; see §1.5)
                       §1.4) — every
                       exchange
                       logged here
                       too, after
                       Speak
```

Think is still Gemini → Groq → Mistral, each guided by `personality.md`
and `thinking_skills.md` (see §1.2) — **preceded by tier 0: whatever the
user connected in the Settings panel** (edited keys/custom models under
each region) — Remember and Aware sit alongside it rather than replacing
anything: Remember quietly supplies relevant *past* context into Think and
logs the result once Speak is done; Aware supplies what's *currently* true
on screen, freshly checked every single turn rather than stored anywhere.

### 1.1 Listen — `agent_overlay.py` + `overlay_speech.py`
A frameless, always-on-top circle pinned to the screen's top-right corner.
It idles in the background listening for the wake word ("Friday"), then
transcribes whatever follows using `faster-whisper` running **entirely
locally** (no internet, no API key). If `faster-whisper` isn't installed or
its one-time model download fails, it falls back to the free Google Web
Speech API instead, so talking to Friday still works either way. You can
also just click the circle (or say "bring up text chat") to type instead —
typed requests skip the voice-specific confirmation step below entirely.
Because speech-to-text can mishear things, any long or action-sounding
spoken request gets summarized back as a yes/no confirmation first (see
`_needs_confirmation()` near the top of `agent_overlay.py`) — and that
cross-check exchange itself is logged to memory just like any other turn,
not just whatever request it eventually triggers.

### 1.2 Think — `agent_overlay.py` + `personality.md` + `thinking_skills.md` + `speech_delivery.md`
Whatever you asked gets answered by whichever model the Settings panel
says to use first — the panel is the live source of truth, region-labeled
so each surface's work draws from its own region:

0. **Settings panel, region-aware** (`_settings_pool_plan()`): the
   circle's chat draws from Voice, the IDE's own chat panel from Coding,
   both falling back to Thinking's pool when their own region has nothing
   — the exact rule Act Script's `region_pool()` already documents. A key
   added/edited on a builtin entry (Gemini/Groq/Mistral under any region)
   is called with **full tool-calling** (`_try_*_with()`), ahead of the
   tiers below; an entry whose key is literally the same as the matching
   `.env` constant is deduped (the matching tier below covers that exact
   credential). Custom "+ Add Model" entries keep their documented
   last-resort plain-text route via Act Script's dispatch — a new region-
   aware `_try_voice_extras()` reads Coding for the IDE, Voice otherwise.

…and then the three original `.env`-backed providers, each one only
kicking in if everything before it has no key configured, is out of quota,
or errors out:

1. **Google Gemini** (free tier)
2. **Groq** (Llama 3.3 70B, free, very fast — backup)
3. **Mistral** (Mistral Small, free — third fallback)

Conversation continuity travels with Think on **every** surface: the IDE
chat panel builds a fresh AIWorker per message, so memory context
(`_load_memory_context()`, the recent-turns ring the IDE's own replies
already feed) loads for ide-scoped workers too — a follow-up like
"continue" sees the previous task instead of a bare word. The IDE surface
still skips the screen snapshot (it knows its own context).

Every call is guided by three Markdown files, concatenated into one
`SYSTEM_PROMPT` (`_load_system_prompt()` + `_load_thinking_skills()` +
`_load_speech_delivery()`) — none of them are hardcoded in Python, so
editing any of them changes Friday's behavior with no code changes needed:

- **`personality.md`** — Friday's voice and tone (composed/dry-witted/
  "gentleman's AI" style, directness rules, how it handles emotional
  topics, and — since the memory system shipped — when to proactively
  call `remember`/`add_task` rather than only when explicitly asked to).
- **`thinking_skills.md`** — Friday's reasoning *method*, separate from
  how he sounds: classifying a task as easy/moderate/hard before acting
  on it, decomposing and sequencing hard problems, deliberately slowing
  down and naming risk for high-stakes/"nerve-wracking" requests instead
  of rushing them, verifying a result against the original ask before
  calling it done, and diagnosing (not blindly retrying) when something
  fails. Applies to every reply regardless of which provider answers.
- **`speech_delivery.md`** — Friday's human-like speech *delivery*:
  natural pacing, hesitation, varied emphasis, and a small set of bracket
  cues (`[pause]`, `[sighs]`, `[whispers]`, ...) the LLM may write into a
  reply. These aren't just decorative text — `overlay_speech.py`'s
  `_parse_speech_cues()` gives every recognized one a real mechanical
  effect when the reply is actually spoken (see §1.2.1 below), and
  strips them out entirely (never reading a cue's bracket text aloud as
  literal words) anywhere a reply is only ever shown, not spoken.

#### 1.2.1 Speech-delivery cues: honest limits of an offline voice
Piper and classic SAPI/pyttsx3 (§1.6's TTS backends) are a single fixed
voice each — neither can literally sigh, chuckle, clear its throat, or
change its own prosody mid-sentence the way a human (or an expensive
cloud "expressive" TTS model) can. Rather than either ignoring
speech_delivery.md's cues entirely or letting them leak through as
spoken-aloud bracket text ("bracket pause bracket"), `overlay_speech.py`
converts every recognized cue into the nearest *honest* mechanical
equivalent before any backend ever sees the text:

- `[pause]` / `[brief pause]` / `[long pause]` — a real, interruptible
  beat of silence between the two halves of the reply, sized to the tag.
- `[inhales]`, `[deep breath]`, `[exhales]`, `[sighs]`, `[soft sigh]`,
  `[clears throat]`, `[chuckles]`, `[laughs softly]`, `[hmm]` — also
  become a short timed pause (can't be sounded out), rather than being
  spoken as literal words.
- `[whispers]` / `[voice softens]` — the words immediately after really
  are spoken at reduced volume, lasting until the next cue (or the end
  of the reply).
- `[voice trembles]` — becomes a brief hesitant pause; a true vocal
  tremor isn't something either backend can produce.
- Any other/unrecognized bracket tag still becomes a short generic pause
  rather than ever being read aloud literally, and a markdown-style
  `[text](url)` link is specifically excluded from ever matching as a cue.

`SpeechWorker` (in `overlay_speech.py`) plays a cue-segmented reply as a
sequence of real TTS calls interleaved with real `time.sleep`-based
pauses, but still only emits its `started_speaking` signal once per
reply — not once per segment — so the circle's "talking" animation
behaves exactly as it did before this feature existed. A reply is only
ever displayed as readable text in one place, the IDE's "Friday AI
Chat" side panel (`ide.py`'s `ChatSidePanel`, reached via
`agent_overlay._ide_chat_ask()`) — since that text is never spoken,
`_strip_cues_for_display()` is used there (and for memory logging, both
there and in the circle's own chat path) instead: pause-family cues
become a plain ellipsis, everything else is simply dropped, so the panel
and Friday's own conversation history never show literal bracket "stage
direction" text.

### 1.3 Act — `agent_tools.py` (+ `app_panels.py`, `ide/ide.py`)
This is where an LLM reply can reach for a tool instead of (or in addition
to) just answering in words. Every tool is a plain, type-hinted, docstring-
documented Python function in `agent_tools.py` — Gemini introspects these
directly (`GEMINI_TOOLS`), while Groq/Mistral get an explicit JSON schema
version of the same functions (`TOOL_SCHEMAS`) plus a manual dispatch loop
(`call_tool()`). The ~82 tools fall into clear groups:

| Category | Examples | What it touches |
|---|---|---|
| **Plain file access** | `read_file`, `write_file`, `edit_file`, `list_directory` | Anywhere on disk the OS user can reach — unscoped, for general assistant use outside the IDE |
| **App/window management** | `agent_mode_on/off`, `open_app`, `close_app`, `minimize_app`, `restore_app`, `maximize_app`, `list_open_apps`, `type_in_app`, `save_app_file` | The IDE panel + native app panels (Chrome/Notepad/Calculator/Settings) — see §2, the single-active-panel model |
| **IDE workspace files** | `ide_create_file`, `ide_write_file`, `ide_edit_file`, `ide_read_file`, `ide_rename`, `ide_copy`, `ide_move`, `ide_search_workspace`, `ide_workspace_tree`, `ide_analyze_file_for_deletion` → `ide_delete_file`, `ide_analyze_folder_for_deletion` → `ide_delete_folder` | Scoped **only** to the IDE's currently-open workspace folder (`WorkspaceFS` in `ide.py`) — a path can never resolve outside it |
| **IDE tabs/editor UI** | `ide_open_file` (opens/focuses a *visible* tab — text, image, or PDF — unlike `ide_read_file`, which never touches the UI), `ide_switch_tab`, `ide_list_open_tabs`, `ide_close_tab`, `ide_get_active_tab_content` (reads the live, possibly-unsaved buffer), `ide_save_file`/`ide_save_all` (native Ctrl+S/Save All, capturing live human edits `ide_write_file` can't see), `ide_goto_line`, `ide_find_replace`, `ide_undo/redo`, `ide_editor_action` | The open editor/tab UI itself |
| **IDE run/build/terminal** | `ide_run_file`, `ide_run_project`, `ide_build_project`, `ide_list_tasks`/`ide_run_task` (every auto-detected npm/pytest/cargo/make/... task by name, not just the one obvious run/build task), `ide_stop_task`, `ide_run_terminal_command`, `ide_get_terminal_output`, `ide_clear_terminal`, `ide_clone_repository`, `ide_open_in_browser` | The IDE's integrated terminal/task runner |
| **IDE layout/view control** | `ide_set_theme`, `ide_toggle_view` (show/hide sidebar/terminal/minimap), `ide_show_sidebar` (switch *which* sidebar view — explorer/search/git/run/extensions — and ensure it's visible), `ide_zoom`, `ide_set_opacity`, `ide_toggle_always_on_top`, `ide_toggle_maximize` | The IDE window's own chrome |
| **IDE git** | `ide_git_init/status/add/commit/push/pull/branch/checkout` | A workspace's `.git`, via subprocess (any other git operation — diff, log, stash, rebase, reset, ... — is reachable too, just via `ide_run_terminal_command` rather than a dedicated wrapper) |
| **Time / web / calendar** | `get_current_datetime`, `web_search`, `list_calendar_events`, `create_calendar_event` | Tavily (web search), Google Calendar |
| **Memory & tasks** | `remember`, `recall`, `forget`, `add_task`, `list_tasks`, `complete_task`, `delete_task` | `memory.py`'s encrypted on-disk folder store — see §1.4. Always available, unlike the IDE-gated tools above |

Deliberately **not** included anywhere: any destructive/irreversible action
on files outside the IDE's own workspace. Inside the IDE's own workspace,
Friday may delete a single file (`ide_analyze_file_for_deletion` →
`ide_delete_file`) or a whole folder (`ide_analyze_folder_for_deletion` →
`ide_delete_folder`), each gated by its own mandatory two-step
analyze-then-delete flow (a fresh, matching analysis is required
immediately before the delete call, re-checked for staleness via a content
fingerprint) and each moving its target to a recoverable trash folder
(`.friday_trash`) rather than deleting it outright. Project infrastructure
(`.git`, `node_modules`, a venv, build/cache output — `SKIP_DIRS` in
`ide.py`) and the workspace root itself are always refused for folder
deletion regardless of what the analysis finds. Whether Friday proceeds
straight to deleting something that analysis comes back clean on, or stops
and asks the user first, is a prompt-level judgment call (see
`personality.md`'s "Agent Mode" section) — the code only enforces that
*nothing* can be deleted without a fresh matching analysis immediately
first, not whether the user was asked. Also deliberately not exposed as tools:
Quick Open/Command Palette and the Keyboard Shortcuts/About dialogs — every
task a human would reach for those for already has a direct, more reliable
tool equivalent (`ide_search_workspace`/`ide_workspace_tree`/`ide_open_file`),
so there's no reason to make the model drive a human fuzzy-finder UI instead.
Two sidebar views reachable via `ide_show_sidebar` are UI-only and have no
matching tool capability behind them, by design of the UI itself, not a gap:
"run" is just a Run File shortcut (no real breakpoint debugger exists in this
IDE at all), and "extensions" is a static, non-functional mockup (its
"Install" button is a no-op) — see `FRIDAY_IDE_CAPABILITIES.md` for the full,
explicit can/can't-do inventory.

**The IDE's embedded "Friday AI Chat" side panel is scoped to a strict
subset of all this** — `ChatSidePanel` → `agent_overlay._ide_chat_ask()`
always builds its `AIWorker` with `ide_scoped=True`, which trims every
provider's tool list down to just the IDE workspace/editor/run/git/layout
tools (`agent_tools.IDE_CHAT_TOOL_NAMES`/`get_ide_chat_gemini_tools()`/
`get_ide_chat_tool_schemas()`), with `agent_tools.call_tool()` itself
refusing anything outside that set as a second, independent backstop
(in case a model ever requests a tool name it was never even offered).
This chat box is strictly for coding tasks — writing, editing, running
code in the open workspace — never app/window/panel management
(`open_app`/`close_app`/`minimize_app`/`restore_app`/`maximize_app`/
`list_open_apps`, turning Agent Mode off, or anything else Friday's
normal floating circle can otherwise do). A standing product decision,
not a technical limitation: the floating circle remains the only
surface that can act on panels/windows at all.

Three real OS threads are involved every time this panel sends a
message, and every hop between them is a known, specific failure mode
if skipped — this bit the panel for real once (see
`dev_tests/test_ide_chat_server.py`'s module docstring for the full
postmortem) and is worth keeping in mind for any future change here:
1. **The IDE's own dedicated Tk thread** (`agent_tools._tk_worker_loop`,
   running a genuine, uninterrupted `app.mainloop()`) is what
   `ChatSidePanel._send()` — and so `_ide_chat_ask()`'s own body —
   actually executes on. Only this thread may ever touch a Tk widget.
2. **Qt's main thread** (`agent_overlay.main()`, running `app.exec()`)
   is what `CircleOverlay` and every other Qt widget/timer live on.
   `_ide_chat_ask()` marshals every touch of the circle onto it via
   `_on_qt_main_thread()` (a persistent `QObject` + `Signal(object)` +
   `Qt.QueuedConnection` invoker, built once in `main()`) rather than
   calling it directly — including, critically, building, wiring up, and
   starting the `AIWorker` itself: a `QThread` subclass's own signals,
   connected to a plain Python callback (not a bound `QObject` slot),
   get delivered via a queued call targeting wherever the `QThread`
   *object* was constructed, not wherever its `run()` actually executes
   — build it on the Tk thread (which pumps no Qt events at all) and
   that delivery is silently dropped forever, which is what made
   messages typed into this panel appear to freeze/never go through.
3. **`AIWorker`'s own background thread** (its `run()`), where the
   actual Gemini/Groq/Mistral call happens, is not safe to touch either
   Qt or Tk widgets from directly either — same reasoning as above,
   confirmed directly by reproduction: a `QTimer.singleShot()` scheduled
   from a thread with no Qt event dispatcher of its own silently never
   fires its callback.

Pushing Friday's live state into the IDE's own window-border glow
(`CircleOverlay._push_ide_status()` → `ide.py`'s `set_friday_status()`)
makes the same Qt-main → Tk hop, the other direction: it goes through
`agent_tools._post_on_main()` (the same `_TkThreadBridge`-backed,
fire-and-forget primitive `agent_tools.py`'s own app-panel code already
used for this) rather than calling into the Tk widget directly.

### 1.4 Remember — `memory.py`
As discussed directly with the user, this is deliberately **not** one
opaque database file — it's a folder of real, individually-encrypted
files and folders on disk (default `~/.friday/`, overridable via
`FRIDAY_MEMORY_PATH` or `set_memory_dir()` for tests), organized so any
one fact, task, or conversation has its own addressable home. Full
design rationale (including every edge case and the exact file layout)
lives in `memory.py`'s own module docstring, so it isn't duplicated here
— the summary:

| Folder | Role |
|---|---|
| `facts/` | Deliberately-saved facts/preferences (`remember`/`recall`/`forget` tools below), one encrypted file per fact, each with an embedding — semantic ("vector DB") search |
| `conversations/<session-id>/` | Every exchange, logged automatically (no tool call needed) after each reply, also embedded — one folder per run of the app, one file per turn — the same semantic search, over the raw transcript, across every session |
| `tasks/<id>/` | Structured to-dos (`add_task`/`list_tasks`/`complete_task`/`delete_task`) — one folder per task, plain exact fields, no embedding involved |
| `kv/` | Small key→value session state, one encrypted file per key — the "localStorage" role, given there's no browser here to provide a literal one |
| `uploads/`, `generated/{images,videos,documents}/` | Empty scaffolding reserved for upcoming features (uploading files to Friday; Friday-generated media) — not wired to anything yet, just given a stable, organized, individually-addressable home in advance |

Every `*.json.enc` file is a whole JSON object encrypted with
[Fernet](https://cryptography.io/) (AES128-CBC + HMAC, via the
`cryptography` package), using one local key generated on first use and
stored in `key.bin` right next to the data — by design, not a prompted
passphrase, since memory here is meant to be read only by the user and
by Friday itself on this same machine, with no separate login step. This
protects the data at rest (copy the folder elsewhere, or read it with
some other program, and it's unreadable without `key.bin`). On top of
that, the whole folder is locked down at the OS level the moment it's
first touched (`chmod 700` on POSIX; best-effort `icacls` on Windows) so
*other* user accounts/processes on a shared machine can't read or write
into it at all. **Being explicit about a real limit here rather than
overclaiming it:** this does not, and cannot, stop the owning OS user
(or Friday's own process, running as that user) from deleting the
folder — no plain-filesystem permission scheme can fully prevent that.
`key.bin`'s loss is equally unrecoverable: it is not backed up or
escrowed anywhere, so losing it means every encrypted file becomes
permanently unreadable. A plain-text `README.txt` at the folder root
(and in each reserved subfolder) spells this out for a human who
stumbles onto the folder directly.

**Self-protecting lockdown, not self-locking.** The Windows `icacls`
step used to be one combined call,
`icacls path /inheritance:r /grant:r "user:perm"` — strip inherited
permissions and grant the current user full control, in one shot. In
practice, on at least one real machine, the grant half silently failed
(swallowed on purpose — locking down permissions must never be allowed
to block memory from working) while the inheritance-stripping half
still went through, leaving `key.bin` with *no* effective permissions
at all, including for its own owner: every subsequent read of it —
Settings included — failed with `PermissionError`. `_lock_down`'s
Windows path (`_lock_down_windows` in `memory.py`) is now ordered
grant-then-verify-then-strip instead: it grants access first, confirms
the file is actually still readable, only then strips inherited
permissions, and immediately re-enables inheritance
(`icacls path /inheritance:e`) if that last step somehow still broke
access anyway. A failure anywhere in that sequence now leaves a file at
least as accessible as it already was, never less. Reading `key.bin`
itself goes through `_open_readable_with_retry`, which retries a
transient `PermissionError` with a short backoff first (the realistic
Windows case of antivirus/indexing briefly locking a file moments after
it's written), then attempts an active ACL repair
(`_repair_windows_permissions`: `icacls /reset`, re-grant, `takeown`,
re-grant again — self-healing a file already broken by the old logic,
since a future code fix can't retroactively repair bytes already on
disk) before finally raising a clear, actionable error naming the exact
file and an `icacls /reset` command to run by hand, rather than a bare
`[Errno 13]`. `_read_encrypted` uses the same retry (without the repair
step) but treats a still-unrecoverable error as "skip this one item"
like any other corrupt file, since losing one fact/task/turn is
recoverable in a way that losing `key.bin` itself is not.

Because there's no SQL index behind any of this, search means
decrypting and scanning every file in the relevant folder(s) rather than
one indexed query — an accepted tradeoff at the scale of one person's
local assistant, chosen deliberately over a faster single opaque file
because a real folder of individually-encrypted files is what was
actually wanted here. (Note: this folder-based design is new — an
existing older `~/.friday/memory.db` SQLite file, if one exists from
before this change, is not auto-migrated into it.)

Turning text into a vector for the semantic folders reuses the same
tried-in-order provider philosophy as Think itself: Gemini's embedding
model, then Mistral's, then a local, dependency-free hashed-bag-of-words
vector if neither is reachable — see `embed_text()`. Embeddings from
different providers are never compared against each other (each file
remembers exactly which one produced it); recall transparently falls
back to a plain substring match if nothing in the *current* provider's
space has been saved yet.

Every reply is one single, stateless request to the model — there is no
running chat-history object anywhere in this file. Memory is the *only*
thing that carries anything from one message to the next:

At the start of `AIWorker.run()` (`agent_overlay.py`) — deliberately in
`run()`, on the background thread, not in `__init__` on the UI thread,
so a real embedding-provider network call never blocks the UI before the
worker thread even starts — `memory.build_context_block()` is called once
and folded into that call's system prompt. It combines two different
kinds of recall:

- **Recent turns** — the last ~6 exchanges of *this running session*,
  kept in a tiny in-process ring buffer (`remember_recent_turn()` /
  `get_recent_turns()`), always included regardless of relevance, with
  zero embedding/disk/network involved. This exists specifically to
  close a real gap: `log_turn_async()` (below) is fire-and-forget on a
  background thread so a slow embedding call never delays a reply, but
  that means a turn can take a visible moment before it's actually
  searchable — if the user asks something back immediately, a pure
  embedding-search could lose the race and look like it "forgot." The
  recent-turns buffer has no such delay. It resets on restart; long-term,
  cross-session recall is still entirely the embedded tables below.
- **Semantic search** — `memories` + `conversation_log`, by meaning,
  across all history including past sessions.

After each successful reply, both `remember_recent_turn()` (synchronous,
instant) and `log_turn_async()` (fire-and-forget, embedded, permanent)
are called together — the automatic-logging half. The
`remember`/`recall`/`forget`/`add_task`/`list_tasks`/`complete_task`/
`delete_task` tools (§1.3's table) are the explicit, LLM-initiated half of
the same system.

### 1.5 Aware — `agent_tools.get_environment_snapshot()`
A second, deliberately separate kind of context, folded into the same
system prompt as Remember but never stored anywhere: what's actually on
screen *right now*. `AIWorker._load_environment_context()` calls this
once per reply (on the background thread, same reasoning as the memory
lookup) and gets back a short, always-current block covering:

- Every open panel — the IDE itself, plus native apps Friday opened
  (Notepad, Calculator, a browser, ...) — and whether each is open or
  minimized.
- Which *one* of those, if any, currently holds the shared maximized/
  focused slot (§2's single-active-panel model) — marked as "currently
  active/focused."
- If Agent Mode is on: the IDE's sidebar view (Explorer/Search/Source
  Control/...) and whether it's visible, plus the active editor tab's
  path and whether it has unsaved changes.

This runs even for the voice-confirmation `summarize_only` calls (unlike
the memory lookup, which skips them) — it's a local, instant introspection
call, not a network one, so there's no latency reason to withhold it, and
knowing what's open can matter even for phrasing a yes/no cross-check
sensibly. `list_open_apps()` and `ide_list_open_tabs()` (§1.3's table)
remain as explicit, on-demand tools for deeper detail than the ambient
summary carries — the two are complementary, not a replacement for each
other.

### 1.6 Speak — `agent_overlay.py` + `overlay_speech.py`
Whatever text comes back is spoken aloud with the OS's default voice (or a
configured Piper voice — see `setup_piper_voice.py`). This is deliberately
audio-only: the reply is never rendered as readable text on screen, only
the circle's outline/pulse animation (green = success, red = error)
indicates Friday is replying.

---

## 2. The panel model: one active panel, everything else a pill

Friday owns two kinds of on-screen panel, unified under one shared rule:

- **The IDE** (`ide/ide.py`) — Friday's own built-from-scratch code editor.
- **App panels** (`app_panels.py`) — three flavors: real separate OS
  windows Friday can't embed (Chrome — see that file's module docstring
  for why), a real OS window reparented into a Friday-drawn frame
  (Calculator, `EMBEDDABLE_APPS`), and fully-native panels with no real
  OS process behind them at all (Notepad, Settings — `CUSTOM_PANEL_APPS`).
  Notepad is a Tk `Toplevel` subclass with CustomTkinter chrome
  (`notepad_panel.py`); Settings is a Qt Quick (QML) window
  (`settings_panel/`) behind a small adapter that speaks the same panel
  protocol — see §2.1. Keeping the Tk panels as native top-levels
  preserves the exact geometry and window handle behavior expected by the
  panel manager and embedded-app host.
  Panel chrome and controls use CustomTkinter; Tk-native text editors,
  scroll canvases, and the embedded native-window host remain Tk widgets
  where their underlying Tk behavior is required.

Conceptually, the IDE, Notepad, and Settings are all **Friday's own
custom work surfaces** — tools Friday itself reaches for to get things
done (writing and running code in the IDE; drafting or reading plain text
in Notepad; reviewing/adding AI model API keys in Settings) — rather than
just app shortcuts being launched for the user's sake. They happen to
live in different files (`ide/ide.py`'s editor, `notepad_panel.py`,
the `settings_panel/` package), but
all three are governed by the exact same single-active-panel rule below,
and all show up identically in `get_environment_snapshot()` (§1.5) as
"panels," not as different kinds of thing. Settings specifically is
opened the same way as any other app — `open_app("settings")`, or
aliases like "AI settings"/"model settings" (see `KNOWN_APPS["settings"]`
in `app_panels.py`) — there's no separate UI entry point for it.

**The rule (current design, as of the most recent window-management
overhaul): exactly one panel is ever visible on screen at a time.** It's
shown fully maximized, full screen, at Friday's own reference bounds.
Opening or restoring *any* panel immediately makes it the one active
panel and minimizes whatever was active before it to a small pill stacked
under the circle (in order — most recently minimized pill closest to the
circle). There is no smaller "docked to the side" state left — a panel is
always either the one maximized/active panel, or fully minimized to a
pill. `maximize_app()` and `restore_app()` are therefore the same action
under the hood; `restore_app("all")` restores only the single
most-recently-minimized panel, since "restore everything" isn't
meaningful any more when at most one panel can ever be on screen.

This logic is centralized in `agent_tools.py` (`_MINIMIZED_STACK`,
`_maximized_panel_key`, `_place_new_or_restored_panel`, `_AgentHost`) and
shared identically by the IDE and every app panel, rather than duplicated
per panel type.

### 2.1 The Settings panel — `settings_panel/` + `settings_store.py` + `settings_checks.py`
Lets the user see and manage every AI model/API key Friday knows about,
grouped into twelve fixed **faculties** (Voice, Thinking, Planning, Review, Coding, Image
Generation, Audio Generation, Video Generation, Research, Embedding, Text
Generation, Transcription), each its own section with its own model list
and its own "+ Add Model" button. Split across three files by concern,
the same split memory.py's own docs/code follow:

- **`settings_store.py`** — the data: one list of model entries per
  faculty, persisted as a single encrypted blob (reusing memory.py's own
  `_ensure_folder`/`_write_encrypted`/`_read_encrypted`/`_lock_down`
  primitives directly rather than duplicating them) at
  `<memory dir>/settings/ai_models.json.enc`. Seeded once, on first use,
  with Friday's actual already-wired providers and their current env-var
  keys (Gemini/Groq/Mistral under Thinking, Tavily under Research,
  Gemini+Mistral's embedding models under Embedding, local Piper under
  Audio Generation, local faster-whisper under Transcription). Thinking/
  Planning/Coding/Review/Text Generation are now real, dedicated
  dispatch targets — act_script.py's `region_pool()`/`ROLE_FACULTY` do a
  fresh, uncached `settings_store.load()` on every Act Script run and
  route that region's actual traffic through whatever's configured
  there (falling back to Thinking's pool only if the region's own
  faculty is empty), including a genuinely callable "+ Add Model" entry
  (an OpenAI-compatible `/chat/completions` POST — see
  `_dispatch_generic_http`), not just a saved-for-later key. Image
  Generation remains provider scaffolding — no image-provider call
  wiring exists yet. Video Generation's AI-provider faculty is likewise
  not wired to an AI video model, but its Friday panel now contains a
  complete deterministic local GIF/MP4 motion renderer (`friday_video/`)
  with an explicit future-provider seam; it does not claim its output is
  AI-generated, and it defaults outputs into Friday memory's
  `generated/videos/` folder. `mask_key()` is the only thing ever shown
  for a stored key — a
  fixed-width bullet mask plus a length hint, never any real
  characters, not even a short trailing fragment.
- **`model_kinds.py`** — what kind each fetched model is. `classify(item)`
  reads the provider's own label (type field, input/output modalities,
  endpoint or capability lists) and falls back to the model id;
  `settings_checks.fetch_model_list` fetches the extra lists some
  providers keep their labels on (OpenRouter `?output_modalities=all`,
  NanoGPT `/image-models` etc., xAI `/image-generation-models` etc.,
  Google's native `/v1beta/models`, SiliconFlow `?sub_type=`, Cohere
  `/v1/models`, Cloudflare `/ai/models/search`, LM Studio `/api/v0/models`,
  Nebius `?verbose=true`). The panel uses it to file models under the
  right tab and to pick a check that fits (`openai_embed`,
  `catalog_listed`).
- **`model_rank.py`** — quality scores for ranking. `build_scores(items)`
  turns OpenRouter catalog `benchmarks` into three tables:
  - text: `artificial_analysis.intelligence_index`. Models that only have
    a Design Arena Elo are quantile-mapped onto that scale.
  - image: the Elo for the image, graphic design, logo and image editing
    categories.
  - audio: the Elo for text-to-speech and audio realism.

  `norm(id)` turns each provider's spelling of an id into one name: it
  strips the org prefix, `:free`, `@region`, `[1m]`, quantization and
  date suffixes, and converts Ollama `:120b` and Fireworks `5p2`.
  `score_for` / `score_label` / `percentile` are the lookups. When a
  model has no measured score, there are two labelled estimates
  (`is_estimate`):
  - `"price"`: a least-squares fit of the measured index on log price,
    release date and context, run inside `build_scores` (pure Python,
    `_solve`). The low tail is squashed into (0, lowest measured).
  - `"family"`: the nearest benchmarked sibling, worked out at lookup time
    and cached (`_sibling`), shifted 2.5 points per doubling of
    parameters for text. The panel
  ranks by percentile, so the All tab can mix text and image models.
  The sources are `model_scores.json` (bundled) and a cache in the
  settings folder; whichever is newer wins. The cache is refreshed by
  `refresh_in_background` (weekly) and by any OpenRouter fetch
  (`settings_checks.fetch_model_list` → `update_from_catalog`).
- **`token_meter.py`** — the token calculator's counter. `record(host,
  model, response)` reads usage in the OpenAI, Anthropic, Gemini or
  embeddings shape (`extract_usage`), or estimates from text when there
  is none. It keeps totals per `host|model` with a 31-day daily history
  in `settings/token_usage.json`. It is thread-safe and batches writes to
  disk (at most one every 2 s, plus `flush()` on quit and when the panel
  closes). Listeners hear every change.
  - Call sites — every place Friday calls a model:
    - `act_script._dispatch_*` (via `_meter`). This covers every added
      provider, and the Friday chat's voice-extras path goes through
      `call_model`.
    - `agent_overlay`'s Gemini/Groq/Mistral chat paths (via `_meter_reply`).
    - `memory._embed_via_gemini/_mistral`.
    - `friday_image.providers` (`_meter_image`, for all four cloud
      backends).
    - Every generating check in `settings_checks` (`_meter`).
  - `by_host()` totals per provider; the panel names each host after its
    preset. Local servers keep their port (`localhost:11434` is Ollama,
    `:1234` is LM Studio).
  - The panel's `tokenUsage` property and `TokenMeter.qml` show the
    totals. Updates are throttled to about 3 per second through a queued
    signal.
- **`settings_checks.py`** — one function per provider (`check_gemini`,
  `check_groq`, `check_mistral`, `check_tavily`, `check_local_piper`,
  `check_local_whisper`) that makes a real, cheap backend call (listing
  models rather than spending a real generation, wherever the SDK offers
  that) and returns `(ok, detail)` — never raises, so one bad/misconfigured
  entry can't break checking everything else. This is what the user
  explicitly asked for: the green check must come from "a silent call in
  its backend," never just "a key string is non-empty." Any provider
  Friday has no real call code for (every custom entry added through
  "+ Add Model") falls back to `check_generic_http` — a plain reachability
  GET against the entry's API base URL, honestly labeled in the panel as
  "reachable," not "verified," since that's the most truthful claim
  possible without knowing that provider's actual API shape. Tavily has
  no free ping endpoint, so (unlike the others) its check unavoidably
  spends one real search call — checks are therefore only ever run
  on-demand (a per-row "Check" button, a per-faculty-group "Check all,"
  or automatically once right after a model is added), never silently
  in the background on every panel open, to avoid burning API quota
  nobody asked to spend.
- **`settings_panel/`** — the panel itself, in Qt Quick (QML), so its
  fades, slides, list add/remove animations and spinners are GPU-drawn
  in step with the display and run on Qt's render thread:
  - `backend.py` — the only Python behind the UI: a `ConnectionsModel`
    (one row per model, changed row-by-row so the list can animate) and
    `SettingsBackend` slots/signals that call straight into
    `settings_store`, `settings_checks`, `provider_autodetect` and
    `user_profile`. Checks and model fetches run on worker threads and
    come back through queued Qt signals, never blocking the UI.
  - `qml/` — `Main.qml` (frameless tool window: no taskbar button,
    Keys & Connect / Profile switcher, the active-panel glow ring),
    `KeysPage.qml`, `ProfilePage.qml`, `FieldDialog.qml` (Add/Edit model:
    key auto-detection, provider presets, Fetch models with FREE/TRIAL/
    PAID tiers, "Add all FREE + TRIAL"), `ConnectionRow.qml` (one row,
    with its ★ score badge and Σ tokens), `TokenMeter.qml` (the token
    calculator pill and breakdown beside Check all), and shared controls.
    `EdgeAutoScroll.qml` keeps the user-directed cursor-edge auto-scroll
    (pointer within 64 px of a list edge glides it, up to 250 px/s).
  - `__init__.py` — `create_window()` and the `SettingsPanel` adapter.
    The panel manager drives every panel from its own Tk thread, so the
    adapter speaks the manager's protocol (`withdraw`, `deiconify`,
    `destroy`, `maximize_to`, `move_to_slot`, `restore_default`,
    `set_active_panel`, `set_friday_status`, `show_page`) and posts each
    call to the Qt GUI thread without waiting — the two threads can never
    deadlock. Placement arrives in physical pixels (like Tk and
    `get_circle_rect()`) and is converted to Qt's logical pixels per
    screen. The window's own minimize/close buttons call back into the
    manager (pill under the circle, single-active-panel bookkeeping).
    `python -m settings_panel` runs it standalone.

Wired into `app_panels.py` exactly the way Notepad is: `"settings"` is a
`KNOWN_APPS` entry (aliases: "settings", "settings panel", "ai settings",
"model settings", "api settings", "ai models", "model manager") and a
`CUSTOM_PANEL_APPS` member, with `_CUSTOM_PANEL_CLASSES` mapping each
custom-panel key to its real class so `_open_custom_panel` can construct
either one through one shared dispatch instead of two near-duplicate
code paths.

**Fail-safe construction.** `SettingsBackend` catches a failing
`settings_store.load()` and exposes the message as `loadError`; the Keys
page then shows that error (and what to do) instead of the list, and the
titlebar's close button still works — a corrupted settings file can never
leave an inert, un-closeable blank window. Settings needs Friday's Qt app
(it always exists inside Friday); without one the adapter raises a clear
error and `open_app` reports it instead of opening nothing.

---

## 3. File-by-file map

### Core brain / UI
| File | Role |
|---|---|
| `agent_overlay.py` | Entry point. The floating circle (`CircleOverlay`), the Think worker (`AIWorker`), provider fallback, voice confirmation and the Idle/Listening/Loading/Responding state machine. Run this to start Friday. |
| `overlay_speech.py` | Listen/Speak details split out of `agent_overlay.py`: text-to-speech (`SpeechWorker`), wake word and microphone listening (`VoiceListenWorker`). Re-exported by `agent_overlay.py` under the original names. |
| `voice_gate.py` | Pure-logic gate that keeps the mic off until the user turns voice chat on (long-press on the circle). |
| `agent_tools.py` | The Act layer: every tool the LLM can call, single-active-panel placement and the Tk-thread bridge to the IDE and panels. Tool docstrings double as Gemini's tool descriptions. |
| `tool_schemas.py` | Short tool/parameter descriptions for Groq/Mistral. `build_tool_schemas()` takes types and required-ness from the real signatures in `agent_tools.py`, so the two can't drift. |
| `browser_tools.py` | Playwright-backed `browser_*` tools (navigate, read, click, fill, console). |
| `act_script.py` | The Act Script engine behind `ide_run_act_script`: classify a build request, plan, run and verify steps, and report honestly. |
| `memory.py` | The Remember layer: encrypted per-file store under `~/.friday/` for semantic memories, tasks, conversation turns and a key/value cache. |
| `user_profile.py` | The user's own profile (name, preferences, GitHub authors, web credentials), Fernet-encrypted next to the settings store. |
| `provider_autodetect.py` | "Paste any key" support: guesses the provider and endpoint from a key's format. |

### Panels
| File | Role |
|---|---|
| `app_panels.py` | `AppPanelManager`: opens, embeds, minimizes-to-pill and closes Friday's panels (IDE, Notepad, Chat, Image, Video, Settings…). |
| `window_backend.py` | OS process-launch and win32 window primitives (`RealLauncher`, `RealWindowBackend`, `_PanelHostBridge`), swappable for fakes in tests. |
| `panel_ui.py` | Shared layout constants and positioning helpers for the native panels. |
| `notepad_panel.py` | `CustomNotepadPanel`, Friday's own Notepad (Friday types into it directly). |
| `conversation_panels.py` | Native Toplevel hosts for the Chat, Image and Video child apps below. |
| `friday_chat/` | Quiet Chat interface: model, view, widgets, icons, encrypted storage, live provider bridge. |
| `friday_image/` | Quiet Image interface. Cloud-only rendering via `providers.py` (Cloudflare Workers AI, Stability, OpenAI-compatible, Ideogram). |
| `friday_video/` | Quiet Video interface: local deterministic motion rendering. |
| `friday_media/` | Code shared by Image and Video: icon bank, widgets/scroll frame, `EncryptedPanelStore`. `friday_image`/`friday_video` keep thin `icons.py`/`widgets.py` re-exports. |
| `image_backend_resolver.py` | Picks the Image panel's backend from the keys in Settings. |
| `settings_panel/` | Settings panel in Qt Quick (QML): models and API keys by faculty, profile, credentials — see §2.1. |
| `settings_store.py` | Encrypted settings data, seed defaults, `mask_key()`. |
| `model_kinds.py` | Sorts provider models into text / image / video / audio / transcription / embedding / other, from each provider's own labels (researched per provider, documented in the module) with a model-name fallback. |
| `model_rank.py` + `model_scores.json` | Quality scores (Artificial Analysis index / Design Arena Elo via OpenRouter) that rank the model lists best first. |
| `token_meter.py` | Counts tokens pulled from every model call, for the Settings token calculator. |
| `settings_checks.py` | Per-provider "Check" buttons (`check_gemini`, `check_groq`, `check_tavily`, …). |
| `git_bridge.py` | Connects the IDE's Source Control actions to the GitHub authors saved in Settings. |
| `touchpad_scroll.py` | Pointer-routed wheel/touchpad scrolling that behaves the same on every platform. |

### The IDE
| File | Role |
|---|---|
| `ide/ide.py` | Friday's code editor (~10,100 lines): explorer, tabs, terminal, git, run/build detection, themes, chat overlay. Also runs standalone: `python ide/ide.py [folder]`. |
| `ide/quiet/` | The Quiet workspace redesign: CTk chrome, panels, chat, debugger (`debug_session.py`, `_debug_driver.py`), diagnostics, tokens and icons. |
| `ide/media_codecs.py` | Pure-Python BMP/TGA/JPEG decoders and PDF helpers for the file-preview pane. |
| `ide/tests/` | The IDE's own suite (`python ide/tests/run_all.py`, 279 checks). |
| `launch_ide.py` | Starts the IDE alone, with no voice stack or API key. |

### Runtime prompts (`prompts/`)
| File | Role |
|---|---|
| `personality.md` | Friday's tone and behavior. Becomes `SYSTEM_PROMPT`; edit this, not code. |
| `thinking_skills.md` | Reasoning method appended to the system prompt — see §1.2. |
| `speech_delivery.md` | Pacing and bracket cues (`[pause]`, `[sighs]`…) that `overlay_speech.py` acts on. |
| `development_skill.md`, `FRONTEND_SKILL.md`, `UI_UX_FRONTEND_DESIGN_RESEARCH.md` | Build skills Friday reads on demand (`_read_own_skill_file`). |

### Tests and tools
| File | Role |
|---|---|
| `dev_tests/` | Project-wide regression suite (~69 files). Run any file directly: `python dev_tests/<file>.py`. |
| `tools/` | Stand-alone helpers: `diagnose_mic.py`, `list_voices.py`, `setup_piper_voice.py`, `test_embedding_standalone.py`. |

### Config and assets
| File | Role |
|---|---|
| `requirements.txt` / `requirements-ide.txt` | Full dependencies / IDE-only dependencies. |
| `RUN_FRIDAY.bat` | One-click Windows launcher (installs requirements, starts the circle). |
| `.env` (not committed) | Optional fallback for `TAVILY_API_KEY` and embedding keys. Chat-model keys live only in Settings. |
| `previews/` | Screenshots used in `README.md`. |

---

## 4. Documented-intentional code that looks unused but isn't

A few things in the codebase will flag as "dead code" under a naive static
scan but are deliberate, and were confirmed during this cleanup pass to be
either actively exercised by tests or explicitly kept as forward-looking
scaffolding — **do not remove these**:

- **`app_panels.py`: `move_to_slot`, `place_in_side_slot`, `restore_default`,
  `unmaximize_panel`** — lower-level panel-geometry mechanics that predate
  the current single-active-panel model (§2). Not reachable through
  `agent_tools.py`'s unified placement system any more (a panel there is
  always either fully maximized or fully minimized, nothing smaller), but
  kept as the standalone/no-`agent_host` fallback path (e.g. a bare
  `AppPanelManager(agent_host=None)`, or `tools/test_embedding_standalone.py`)
  and are directly exercised by `dev_tests/test_app_panels.py`.
- **`RealLauncher.launch`'s unused `file_path` parameter** — kept for
  backward source-compatibility with any caller still passing it, per its
  own docstring; Notepad no longer routes through this launcher at all.
- **Various Tkinter/Qt event-handler overrides** (`paintEvent`,
  `mousePressEvent`, `_key_return`, etc. in `ide.py`/`agent_overlay.py`) —
  invoked by the GUI framework itself via polymorphism/dynamic dispatch,
  not by any direct call site in this codebase, so a plain text search for
  callers won't find one. This is normal and expected for GUI code.

## 5. Clean-up history

Earlier passes removed `PILL_GAP`, `FILE_COLORS` and a dead `X if False
else Y` branch. The 2026-11 clean-up (branch `cleanup`) additionally:

- deleted committed `.pyc` files (5.6 MB), historical audit/verification
  docs and a stale nested test copy; moved runtime prompts to `prompts/`;
- fixed real bugs the stale tests exposed (IDE minimize/restore messages,
  Image panel success marker, a Tk "main thread is not in main loop" crash
  from CTkOptionMenu's internal dropdown font, IDE `sys.path` order);
- merged the duplicated Image/Video icons, widgets and storage into
  `friday_media/`;
- removed verified-dead code: `RealTyper` and the `pywinauto` path,
  `LocalArtBackend`, `ProviderPlaceholderMessage`, `save_state`/
  `load_state`, `export_notes`, `ease_out_back`, `find_hwnd_for_pid` and
  others, plus unused imports;
- generated tool schemas from the real signatures (`tool_schemas.py`);
- trimmed long bug-history comments and docstrings to their summaries
  (tool docstrings untouched — Gemini reads them).

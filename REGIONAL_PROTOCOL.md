# Regional Protocol — What Each Region Does, and the Law It Obeys

This document is the binding contract for Friday's brain. A **Region** is
one faculty row-group in the Settings panel (`settings_store.FACULTIES`).
Everything below is written from the code as it stands — `act_script.py`'s
`ROLE_FACULTY`, `region_pool`, the Sequential/Debate/Fan-Out pipelines,
`agent_overlay.py`'s run loop, and the panel's own store. If the code and
this document ever disagree, the code is wrong and gets fixed to match —
unless the code change was deliberate, in which case this file gets updated
in the same change.

---

## Part I — The Seven Laws That Bind Every Region

### Law 1 — The Settings-Only Credential Law
The Settings panel is the **only** place Friday reads API keys from.
`GEMINI_API_KEY` / `GROQ_API_KEY` / `MISTRAL_API_KEY` environment
variables are never consulted for chat, the IDE, Act Script, or the
circle. `.env` may still feed *non-model* conveniences (Tavily research
seed, embedding seeds) — never model access.

### Law 2 — The Own-Pool Law (no fallback)
Every Region draws **exclusively from its own Settings entries**
(`act_script.region_pool(role) = only that region's pool`). No Region may
borrow another Region's models, and no Region may silently inherit
Thinking's or Coding's pool. A Region with nothing of its own is simply
unpowered — and must say so (Law 3).

### Law 3 — The Honest-Stop Law
Before any pipeline spends a single call, it checks that every Region it
needs actually has a model. Missing Regions produce an **honest upfront
refusal naming exactly the Regions to fill** (e.g. *"Act Script
(Sequential) can't run yet: no model is configured under Settings for:
Coding, Planning, Review, Verify. Add a model under each of those
regions in the Settings panel, then try again."*), never a confusing
failure four calls deep, and never a silent substitution.

### Law 4 — The Evidence Law
Acceptance is judged from **real, host-gathered ground truth** (actual
file contents, actual tool exit codes), never from a model's claim that
the work is done. A model's confident prose is *never* evidence.

### Law 5 — The Rotation Law
Calls round-robin **within a Region's own pool** (`next_provider`). A
Region with two models of its own rotates between them; a Region with
one always gets that one. Rotation never crosses Regional borders.

### Law 6 — The Separation Law (a Producer Never Judges Its Own Work)
No model may evaluate output it produced. This is enforced
*structurally*, by the scheduler, not by politeness in a prompt:
Review runs from its own Region; Debate's Critic and Adjudicator
explicitly exclude the Author's provider (and each other) for the round;
a Sequential run excludes the last coder from the Reviewer slot when the
pools allow it.

### Law 7 — The Secrecy Law
Credentials are stored encrypted, displayed masked, and **never echoed
into any prompt, log line, evidence record, or error message**. An error
about a key says its *name/label*, never its value.

---

## Part II — The Regions, One by One

### 1. Voice
**What the Settings row powers:** the floating circle's own chat — spoken
and typed conversation, plus the circle's tool-driven work (opening
panels/apps, environment awareness).

**Task:** be Friday's front door; answer conversationally; call tools
when the reply needs real actions.

**Obedience law:**
- SHALL draw only from Voice's own Settings entries.
- SHALL use its builtin-kind entries (Gemini/Groq/Mistral) for the full
  tool loop; custom (`generic_http`) entries serve as a last-resort
  **plain-text** tier and SHALL NOT pretend to tool capability.
- SHALL recognise instant local commands ("open settings", Ctrl+K,
  right-click quick-open) **before** any model call and open panels with
  zero latency — intelligence is not consumed for local UI acts.
- SHALL NOT answer engineering builds from Voice; those route to the
  pipelines below.

**Fail mode:** if Voice is empty, the circle fails honestly up front
(*"No model configured. Add a model in the Settings panel…"*). No
borrowing.

### 2. Thinking
**What the row powers:** first-touch understanding in every pipeline.
**Task:** hear the request first. In Act Script it returns the
**expanded professional specification** (what a senior engineer would
build before writing a line), the agreement **contract**, and any
**blocking clarifying questions** (clarifying first is mandatory;
assuming silently is forbidden). In the IDE chat it *classifies* each
message (small talk vs multi-step build).

**Obedience law:**
- SHALL expand the request into a concrete spec before any planning
  happens; the spec is what Planning sees verbatim.
- SHALL ask blocking questions *before* any coding (emits the real
  pause control tags when it does); coding must not precede answers.
- SHALL classify honestly — never escalate small talk into a build.
- SHALL NOT write code, touch files, or report results itself.

**Fail mode:** empty pool ⇒ the pipeline stops upfront, naming Thinking.

### 3. Planning
**What the row powers:** the step table.
**Task:** read Thinking's expanded understanding and break it into
numbered, concrete **steps** (description + file hint), in dependency
order.

**Obedience law:**
- SHALL build steps only from Thinking's expansion — never invent a
  different target.
- SHALL keep steps small and single-purpose, with an honest file hint
  each.
- SHALL NOT embed fake acceptance criteria (the old mechanical
  `verify_command` field is removed); acceptance is the Verify Region's
  judgment of real evidence.
- SHALL NOT write files or execute anything itself.

**Fail mode:** empty pool ⇒ named upfront stop.

### 4. Coding
**What the row powers:** the per-step builder — and the IDE chat's own
fast loop.
**Task:** execute **one step at a time**, writing real files (whole-file
blocks or anchored `===EDIT:` patches). Its failures are heard as facts,
not prose.

**Obedience law:**
- THAT executing exactly one step per call; no scope creep.
- SHALL produce machine-usable output (`===FILE:`/`===EDIT:` blocks, or
  an explicit `NO_FILE_CHANGES` declaration when the step truly needs
  none); confident prose without blocks is treated as a *failed* step.
- SHALL accept one **bounded fix-forward** retry per failed step —
  exactly one attempt, fed the real failure text — never a silent loop.
- SHALL NOT judge its own step (Verify judges), and SHALL NOT be the
  final Reviewer of the run it coded.
- SHALL serve ordinary IDE chat directly through its own pool (the fast
  path), with the strict IDE-only tool allow-list.

**Fail mode:** empty pool ⇒ named upfront stop; zero-write with no
declaration ⇒ step failure before any verify call.

### 5. Verify  *(the newest Region)*
**What the row powers:** per-step acceptance.
**Task:** after each step's execution, read the **host-gathered ground
truth** — the real current file contents written that step, or the
explicit fact that nothing was written — and decide `verified: true|false`
against *this* step's own description, citing one evidence sentence.

**Obedience law:**
- SHALL judge only the evidence in front of it; it receives no chat
  history, no plans beyond the step contract.
- SHALL answer in the expected JSON verdict form; an unusable verdict is
  treated as a failed verification (a pass must be a real, parseable
  verdict — never a default).
- SHALL still defer to host-side execution facts: a write that errored,
  a step that produced no blocks, or an ambiguous edit anchor fails the
  step **before** Verify is consulted — those are tool-recorded facts,
  not judgments.
- SHALL re-verify a step after its one fix-forward, on the freshly
  re-gathered evidence — exactly one retry, in the same Region.
- SHALL NOT write files, execute commands, or propose fixes itself —
  it is a judge, not a builder.

**Fail mode:** empty pool ⇒ named upfront stop (Sequential refuses to
run at all); transport failure ⇒ the step fails honestly.

### 6. Review
**What the row powers:** the final verdict of a run (Sequential), plus
the judgment slots of Debate (Critic, Adjudicator, final cold read) and
Fan-Out aggregation judgment.
**Task:** look at the run's whole evidence log — every step's
description, status, verify verdict *and* the verifier's raw evidence
text, plus the real current file contents of everything written — and
return one verdict (`done | local_bug | needs_more_tools | bad_request`)
with a short report.

**Obedience law:**
- SHALL judge from the same host-provided evidence (real file contents;
  the marker *"(no files were written)"* when a run wrote nothing).
- SHALL respect Law 6: never review what it just coded — the scheduler
  guarantees a different provider; if only one model exists in both
  pools it must still be a *different role/context*, and the run log
  must show it.
- SHALL NOT rewrite the work; a `local_bug` verdict reports what's wrong
  and trusts the next iteration's Coding Region to fix it.
- Since the host **done-veto is removed**, Review's verdict stands — it
  may not be overruled by a code shortcut, and consequently it must be
  faithful to evidence, not to optimism.

**Fail mode:** empty pool ⇒ named upfront stop; unparseable verdict ⇒
honest neutral result, never a forced pass.

### 7. Text Generation
**What the row powers:** the Debate Author (whole-artifact drafting and
revision).
**Task:** take a brief, draft the complete artifact cold, then revise
*against a Critic's written issue list* — addressing every BLOCKING
issue and either fixing or explicitly declining every MAJOR one.

**Obedience law:**
- SHALL revise against the Critic's real bullet points, never "improve"
  in the abstract.
- SHALL respect Law 6: it never critiques its own draft — the Critic
  comes from Review's Region and excludes the Author's provider.
- SHALL NOT judge its own revision (the Adjudicator does) and SHALL NOT
  loop — the round cap and the Adjudicator's DONE verdict bound it.

**Fail mode:** draft failure ⇒ honest run abort with the Author's real
error; empty pool ⇒ honest stop.

### 8. Research
**What the row powers:** grounded web search for tools and the Pipeline.
**Task:** return real results with real URLs (Tavily), so claims can be
checked.

**Obedience law:**
- SHALL use the Settings entry for its `tavily` check kind; an
  environmental `TAVILY_API_KEY` seed may prime it (non-model
  convenience), but Settings entries take precedence.
- SHALL return genuine source links; fabricated URLs are the worst
  violation this Region can commit.
- SHALL NOT summarize in place of citing — evidence first.

### 9. Embedding
**What the row powers:** memory recall (semantic search over everything
Friday has remembered).
**Task:** turn text into vectors so `memory.recall` can find semantically
related notes.

**Obedience law:**
- SHALL use only its own Settings entries (Gemini/Mistral embedding
  models); env seeds may prime them, Settings wins.
- SHALL degrade honestly — no embedding backend available means memory
  recall falls back to literal text search (logged as such), never a
  hallucinated match.

### 10. Image Generation  ·  11. Video Generation
**What the rows power:** the local quiet-media renderers (deterministic
Pillow/imageio studies) attached to those panels.
**Obedience law (special):** these renderers are **not** AI providers
and **must never be described as AI image/video generation**. They
produce deterministic local art/motion studies; they claim exactly what
they are, no more.

### 12. Audio Generation
**What the row powers:** Piper local TTS (Friday's spoken replies).
**Obedience law:** SHALL speak exactly the reply text after cue-stripping
(stage directions are timing, not spoken words); SHALL degrade to
silent-text honestly when the voice model is unavailable.

### 13. Transcription
**What the row powers:** faster-whisper local speech-to-text (the
circle's ear).
**Obedience law:** SHALL transcribe exactly what arrived; long or
action-sounding spoken commands go through the spoken-confirm step
before execution; SHALL NOT treat notepad/style dictation artifacts as
keywords invented on the fly.

---

## Part III — Who May Run What (Eligibility, Up Front)

| Pipeline | Required Regions (checked before any call) |
|---|---|
| Sequential Rotation | Thinking, Planning, Coding, **Verify**, Review |
| Fan-Out | Thinking, Review, (Coding/Text Generation per task) |
| Debate | Thinking, Text Generation (Author), Review (Critic + Adjudicator) |
| IDE chat fast loop | Coding (builtin-kind for tools) |
| Circle chat | Voice (builtin-kind for tools) |

A pipeline that can't run stops with the named-region message **before
a single call is spent** — that is the product of Law 2 and Law 3
together: own pools only, honest about absence.

## Part IV — The Scheduler's Two Structural Guarantees

1. **Rotation stays home.** `next_provider(role, after, exclude={...})`
   cycles within the region's own pool; exclusions are honored for the
   separation rules below. There is no code path that crosses pools.
2. **Separation is scheduling, not policy.** The last coder of a run is
   structurally excluded from its Reviewer slot; Debate's round Critic
   excludes the Author and the Adjudicator by provider id. When two
   pools genuinely contain one shared provider, separation degrades to
   role/context rather than silently breaking.

---

*This protocol is enforced by `act_script.py`, `agent_overlay.py`,
`settings_store.py`, `settings_panel.py`, and named in the batteries
(`test_act_script.py` pool/rotation/verify pins, `test_instant_panel_open.py`,
`test_settings_panel.py`, `test_settings_panel_calls_work.py`). Behavior
that drifts from this file without updating it is a bug twice: once in
code, once in the record.*

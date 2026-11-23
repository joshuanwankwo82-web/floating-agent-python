"""
Floating Agent Overlay — now with a real "brain": Gemini (primary) with
Groq and Mistral as fast backup providers, spoken replies, and voice input
with a "Friday" wake word (typing is still available on request).

A frameless, transparent, always-on-top circle that pins itself to the
top-right corner of your screen and floats above every application window
(not just a browser tab).

States:

  IDLE       - small plain circle outline. This is also the default,
               background "always listening for the wake word" state — say
               "Friday" to get its attention. Click to open the
               typed text box instead.
  LISTENING  - circle enlarges and turns blue. Two ways to get here:
                 - you said the wake word with nothing after it — it speaks
                   a short acknowledgment ("Sir?", "Yes?", etc.) and then
                   actively listens for your actual request; or
                 - you clicked the circle, or said something like "bring up
                   text chat" — a small text box pops up so you can type
                   instead (press Enter to send, Esc/click the circle again
                   to cancel).
  LOADING    - circle shrinks back to idle size; the same ring animates as
               a rotating spinner while the AI call is in flight -- and,
               once the reply is back, for as long as speech synthesis
               itself needs to actually start producing audio (so the
               "talking" animation never visibly gets ahead of the sound).
  RESPONDING - circle enlarges again and turns green, shows the animated
               sound-wave/equalizer, and a text bubble shows the reply
               (also spoken aloud). Clicking the circle at any point during
               this state immediately interrupts the speech (cuts it off
               mid-sentence) and drops straight into actively listening for
               your next request, same as finishing normally. Once the
               reply finishes on its own (or is interrupted this way), it
               moves to a brief LISTENING window — ready for a follow-up
               with no need to repeat the wake word — and only falls back
               to Idle (background wake-word listening) after a few
               seconds of silence.

Voice input is heard with `faster-whisper` — a fast, local speech-to-text
engine that runs entirely on your own machine (no internet, no API key,
nothing ever leaves your computer). It downloads its model once, the same
one-time-download shape as Friday's Piper voice. If it isn't installed or its
one-time model download can't complete, voice input quietly falls back to
the free Google Web Speech API (needs internet) so talking to Friday still
works either way — see requirements.txt. The typed box is still there any
time voice isn't convenient: click the circle, or say something like
"bring up text chat".

Voice cross-check confirmation: because speech-to-text can mishear words,
any spoken request that's either long or sounds like it wants Friday to DO
something (as opposed to just answer a question) gets summarized back as
a short yes/no question first — e.g. "...am I to create a header that
adapts to different screen sizes and scales up for larger displays?" —
instead of being acted on immediately. Confirm it (say "yes"/"correct"/
etc.) and it runs for real; say anything else and Friday just asks you to
restate it, no need to repeat the wake word either way. Typed requests
skip this entirely — see _needs_confirmation() near the top of this file
for exactly what triggers it, and CONFIRMATION_WORD_THRESHOLD /
CONFIRMATION_ACTION_VERBS if you want to tune the trigger.

Setup:
    pip install -r requirements.txt
    python agent_overlay.py  (or launch_ide.py for the IDE)
    Then open the Settings panel and add a chat model. Task-matched models
    are tried first, with other compatible connections available as fallbacks.
    The Settings panel is the ONLY place Friday reads API keys
    from -- .env is never consulted for model access.

You only need one of the three keys. Together, Gemini, Groq, and Mistral
make up the "think" part of the brain (the LLM step) — Gemini is tried
first, then Groq, then Mistral, each one kicking in automatically only if
every provider before it has no key, is out of quota, or errors out (in
whatever order you've actually configured keys for).
"""


import math
import inspect
import os
import random
import re
import socket
import sys
import threading
import time

# google-genai (the Gemini SDK) logs a one-time, purely informational
# WARNING the first time it sees automatic function calling used via
# generate_content() (it suggests Chat.send_message instead) -- harmless
# either way, this script never accumulates chat history across turns so
# there's no "chat" object to hand the tool loop to. Nothing here
# configures Python's logging module otherwise, so that one log line
# would reach the default "no handler configured" fallback and print
# straight to stderr -- confusing on Windows specifically, where
# PowerShell renders *any* stderr line from a native command as a red
# "NativeCommandError", making a harmless log line look like a crash.
# Capping this one logger at ERROR keeps real failures (which this file
# already surfaces itself, as friendly chat replies) visible while
# dropping that specific noise.
import logging
logging.getLogger("google_genai").setLevel(logging.ERROR)

import json

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, QRectF, QPointF, QTimer, QThread, QObject, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QGuiApplication, QAction, QPainterPath, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QWidget, QMenu, QLineEdit, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem,
)

import agent_tools
import app_panels
import memory

# ---- Tunables ----------------------------------------------------------
WINDOW_SIZE = 52          # overall (invisible) window size, gives room to scale
BASE_DIAMETER = 19        # circle diameter at rest / idle / loading
ACTIVE_SCALE = 2.0        # how much bigger it gets while listening / responding
MARGIN_TOP = 16
MARGIN_RIGHT = 16
BORDER_WIDTH = 2.2

FILL_COLOR = QColor(20, 20, 24, 140)          # translucent dark fill
BORDER_COLOR = QColor(255, 255, 255, 210)      # circle outline (idle)
BORDER_COLOR_HOVER = QColor(255, 255, 255, 255)
BORDER_COLOR_LISTENING = QColor(59, 130, 246)   # blue, only while listening
BORDER_COLOR_LOADING = QColor(255, 255, 255, 210)  # spinner-ring color while loading
BORDER_COLOR_WORKING = QColor(232, 163, 61, 240)   # orange: a panel is mid-task
BORDER_COLOR_RESPONDING = QColor(46, 204, 113)  # green, only while responding
BORDER_COLOR_ERROR = QColor(231, 76, 60)        # red, responding-with-error

ICON_COLOR = QColor(255, 255, 255, 235)        # waveform / spinner / equalizer color

# Equalizer (RESPONDING) bars
WAVE_BAR_COUNT = 7
WAVE_ENVELOPE = [0.40, 0.65, 0.85, 1.00, 0.85, 0.65, 0.40]

LISTEN_RAMP_MS = 700          # time for the flat line to grow into a full waveform

# How long the response bubble stays up before auto-returning to idle
# (rough "reading time" based on reply length), unless dismissed sooner.
# This path only applies when nothing is being spoken aloud (TTS off,
# unavailable, or an empty reply) — when speech IS playing, the real
# trigger is _on_speech_finished (see MAX_SPEECH_SAFETY_MS below), not
# this reading-time estimate.
MIN_RESPONSE_MS = 4000
MAX_RESPONSE_MS = 20000
MS_PER_CHAR = 60

# Safety-net ceiling used ONLY while a spoken reply is actually playing.
# The equalizer/response bubble is meant to stay up for the entire spoken
# reply, however long — that's driven by the finished_speaking signal
# firing exactly when the OS voice engine stops talking, not by this
# timer. This value just guards against a hung/crashed TTS engine that
# never emits that signal, so it's set high enough to comfortably outlast
# any real reply instead of cutting the animation off early.
MAX_SPEECH_SAFETY_MS = 180000  # 3 minutes

# How long the Listening text box waits with no typing before it gives up
# and falls back to idle (covers both a fresh click and the automatic
# re-open after a response finishes speaking).
LISTEN_INACTIVITY_MS = 4000  # ~3-5s

STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_LOADING = "loading"
STATE_RESPONDING = "responding"


def _load_env_file(path=None):
    """Reads KEY=VALUE pairs from a local .env file into os.environ.

    Values already set in the real environment take priority (so you can
    still override via a real env var if you want), but for most people
    the .env file next to this script is the single place to put keys.
    """
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


_load_env_file()

GEMINI_MODEL = "gemini-3.8-flash"
# If the primary model is overloaded/unavailable/retired, try these next,
# in order, before giving up. "gemini-flash-latest" is a Google-maintained
# alias that always points at their current recommended Flash model, so
# it's the most future-proof fallback as models get retired over time.
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest"]
RETRIES_PER_MODEL = 2          # extra attempts on the *same* model before moving on
RETRY_BACKOFF_SECONDS = 1.5    # multiplied by attempt number between retries

# Hard ceiling on a single network request to any provider. Without this,
# a stalled connection (TCP connects fine but the server/a proxy/a VPN
# silently never sends a reply -- a real, observed failure mode, not a
# hypothetical one) hangs the underlying SDK call forever: no exception,
# no retry, no fallback to the next provider, nothing -- Friday just sits
# there looking unresponsive with no way to tell the difference between
# "thinking" and "stuck." Every provider client below is now built with
# this explicit timeout so a stall surfaces as a real, catchable error
# (classified as connectivity-related by _looks_like_connectivity_error
# below, which arms the same automatic-retry-once-back-online machinery
# a hard connection failure already does) within a bounded, known time
# instead of an indefinite hang.
AI_REQUEST_TIMEOUT_SECONDS = 30

# How many local tool-call round trips Groq/Mistral's own manual
# tool-calling loop (_run_tool_loop below) allows within a single reply,
# before giving up and telling the user to rephrase/split the request.
# (Gemini doesn't use this at all -- passing it plain Python functions
# turns on the SDK's *own* automatic function calling, which runs its
# whole multi-turn loop internally, inside one network call, with its
# own internal cap.) A real multi-file build easily needs far more than
# a handful of these: fetching a skill doc, reading/listing the
# workspace, then a create+write pair per file -- a modest multi-page
# site (HTML/CSS/JS, maybe a couple of extra pages) can genuinely need
# 15-25+ round trips on its own. An ordinary chat reply only needs a
# couple at most and returns as soon as the model stops requesting
# tools either way, so a generous ceiling here costs nothing for small
# asks and only matters for the substantial ones it was too low for.
# Deliberately higher for genuine coding work (the IDE's own embedded
# chat panel, `ide_scoped=True` -- see AIWorker.__init__) than for the
# circle's general-purpose chat, which rarely needs this many.
TOOL_LOOP_MAX_ITERATIONS = 10
TOOL_LOOP_MAX_ITERATIONS_IDE = 40




def _load_system_prompt(path=None):
    """Loads the assistant's personality/behavior instructions from
    personality.md next to this script, if present, falling back to a
    short built-in default otherwise.

    Edit personality.md to change how the assistant talks, introduces
    itself, banters, and handles emotional topics — no code changes
    needed. This is sent to whichever provider (Gemini/Groq/Mistral)
    actually answers, so the personality is consistent across all three.
    """
    path = path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "personality.md"
    )
    default = (
        "You are a concise voice-style assistant shown in a tiny floating popup. "
        "Keep replies short and conversational (1-4 sentences) unless the user "
        "explicitly asks for more detail."
    )
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                # The personality file defines *how* to talk; this app-specific
                # note makes sure that still fits a tiny popup + spoken reply.
                return (
                    content
                    + "\n\n---\n\n"
                    "Formatting note for this interface: your reply is shown in "
                    "a small floating text popup and read aloud with "
                    "text-to-speech. Keep replies conversational and reasonably "
                    "brief (a sentence or a few) unless the user explicitly asks "
                    "for more detail or a longer explanation. Avoid markdown "
                    "formatting like headers, bold, or bullet-heavy lists — "
                    "plain spoken-style sentences read better here."
                )
        except Exception:
            pass  # fall through to the built-in default below
    return default


def _load_thinking_skills(path=None):
    """Loads the assistant's reasoning/problem-solving method from
    thinking_skills.md next to this script, if present. This is
    deliberately separate from personality.md: that file governs how
    Friday *sounds*, this one governs how he *reasons* -- classifying
    task difficulty, breaking down hard problems, slowing down for
    high-stakes ones, verifying before declaring done, and so on. Edit
    thinking_skills.md to change the reasoning method; no code changes
    needed. Returns "" (not a hard failure) if the file is missing or
    unreadable -- this is additive guidance on top of personality.md's
    SYSTEM_PROMPT, not something the assistant can't function without.
    """
    path = path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "thinking_skills.md"
    )
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                return "\n\n---\n\n" + content
        except Exception:
            pass  # additive guidance only -- never block startup on this
    return ""


def _load_development_skill(path=None):
    """Loads Friday's software-development operating standard from
    development_skill.md next to this script, if present. Separate from
    personality.md (how Friday talks), thinking_skills.md (how Friday
    reasons generally), and speech_delivery.md (how replies sound) --
    this one is specifically the professional engineering discipline for
    IDE/coding work: Definition of Done, the plan -> implement -> validate
    -> review workflow, risk classification, and the anti-patterns list
    (never claim an unrun test passed, never claim something was
    deployed/committed when it wasn't, etc.). It's an adapted version of
    a user-supplied "Professional Software Development Agent Operating
    Standard" -- trimmed of the enterprise CI/CD/production-infra
    content that doesn't apply to this local, no-deploy-pipeline project,
    and reconciled with this project's own already-established autonomy
    rules (finish tasks without checking in after every step; the
    2-minute unanswered-pause rule above governs the cases where a pause
    really is warranted; routine/reversible-via-trash deletions don't
    need authorization). Edit development_skill.md to change the
    engineering standard; no code changes needed. Additive guidance only,
    same as thinking_skills.md: returns "" (not a hard failure) if the
    file is missing or unreadable.
    """
    path = path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "development_skill.md"
    )
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                return "\n\n---\n\n" + content
        except Exception:
            pass  # additive guidance only -- never block startup on this
    return ""


def _load_speech_delivery(path=None):
    """Loads Friday's human-like speech/delivery guidance from
    speech_delivery.md next to this script, if present. Separate from
    personality.md (how Friday talks/banters) and thinking_skills.md
    (how Friday reasons) -- this one is specifically about *delivery*:
    natural pacing, hesitation, emphasis, and the small set of bracket
    cues (`[pause]`, `[sighs]`, `[whispers]`, ...) that overlay_speech.py
    actually gives a real mechanical effect to when the reply is spoken
    aloud (see _parse_speech_cues there) -- not just decorative text.
    Additive guidance only, same as thinking_skills.md: returns "" (not a
    hard failure) if the file is missing or unreadable.
    """
    path = path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "speech_delivery.md"
    )
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                return "\n\n---\n\n" + content
        except Exception:
            pass  # additive guidance only -- never block startup on this
    return ""


SYSTEM_PROMPT = (
    _load_system_prompt() + _load_thinking_skills()
    + _load_development_skill() + _load_speech_delivery()
)


# Groq: a second, very fast LLM provider used as backup. If the user's
# Settings-panel Gemini entry is out of quota or errors out, their Groq
# entry kicks in automatically (and vice versa). Keys come ONLY from the
# Settings panel. Get a free key at https://console.groq.com/keys — no
# credit card required, then add it in Settings.
# Groq retired llama-3.3-70b-versatile AND llama-3.1-8b-instant for
# free/developer tiers on 2026-08-16 (console.groq.com/docs/deprecations) --
# their own recommended replacements are the gpt-oss pair below. (qwen/
# qwen3.6-27b, the other suggested replacement, was itself retired
# 2026-09-14, so it is NOT used here.)
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_FALLBACK_MODELS = ["openai/gpt-oss-20b"]

# Mistral: a third LLM provider, tried last if both Gemini and Groq are
# unavailable/unconfigured/exhausted. French-made, generous free "Experiment"
# tier. Get a free key (no credit card, phone verification) at
# https://console.mistral.ai/api-keys. Gemini, Groq, and Mistral together
# make up the "think" stage of the brain — whichever one actually answers,
# the rest of the app (display + speech) doesn't care which provider it was.
MISTRAL_MODEL = "mistral-small-latest"
MISTRAL_FALLBACK_MODELS = ["ministral-8b-latest"]

# Text-to-speech: speaks the reply out loud using the OS's default voice
# (SAPI5 on Windows via pyttsx3 — fully offline, no API, no cost).
ENABLE_SPEECH = True

# Everything else involved in actually speaking text aloud and actually
# listening via the microphone -- the TTS tunables/helpers/SpeechWorker,
# and the wake-word/voice-confirmation helpers/VoiceListenWorker -- lives
# in overlay_speech.py now (split out purely to keep this file a readable
# size; see that module's own docstring for the full rationale and for
# why this stays a plain star-free, explicit re-export rather than a
# `from overlay_speech import *`). Imported with their exact original
# names so every reference below (and every `agent_overlay.NAME` a test
# or another file reaches for) keeps working unchanged.
from overlay_speech import (  # noqa: E402
    ENABLE_VOICE_MODE, USE_PIPER_VOICE, PIPER_VOICE_MODEL,
    TTS_RATE, TTS_VOLUME, TTS_VOICE_NAME,
    WHISPER_MODEL_SIZE, WHISPER_COMPUTE_TYPE, WHISPER_DEVICE,
    WHISPER_BEAM_SIZE, WHISPER_INITIAL_PROMPT,
    WAKE_PAUSE_THRESHOLD_SECONDS, COMMAND_PAUSE_THRESHOLD_SECONDS,
    WAKE_WORDS, WAKE_ACK_PHRASES,
    TEXT_CHAT_TRIGGER_PHRASES, COMMAND_CAPTURE_TIMEOUT_MS,
    PAUSE_THRESHOLD_SECONDS, PHRASE_TIME_LIMIT_SECONDS,
    CONFIRMATION_WORD_THRESHOLD, CONFIRMATION_ACTION_VERBS,
    CONFIRMATION_AFFIRMATIVE_PHRASES, CONFIRMATION_SUMMARY_INSTRUCTION,
    SpeechWorker, VoiceListenWorker,
    _normalize_for_wake_check, _extract_wake_command,
)
from voice_gate import VoiceChatGate
from overlay_speech import (  # noqa: F401 -- re-export used by tests
    _needs_confirmation, _is_affirmative,
    _piper_voice_cache, _speak_with_piper, _speak_with_onecore_voice,
    _get_piper_voice, _find_onecore_voice_token,
    _load_whisper_model, _transcribe_locally,
    _strip_cues_for_display,
)


# -------------------------------------------------------------------------
# Connectivity-error classification, shared by all three providers'
# outer catch-alls in AIWorker (_try_gemini/_try_groq/_try_mistral) and
# by AIWorker.run()'s own error-combining logic below. The one goal: any
# time a provider call failed because *the internet itself* isn't
# reachable right now -- not because of a bad API key, a quota limit, or
# the provider's own servers being down -- that failure is tagged with
# this one unmistakable, greppable marker string instead of each
# provider inventing its own wording for the same underlying situation.
# That lets two things happen further down the pipeline that otherwise
# couldn't: (1) AIWorker.run() can collapse 2-3 near-duplicate
# per-provider "can't reach X" messages into one clean sentence when
# *every* provider failed the same way, and (2) whoever submitted the
# request (CircleOverlay / _ide_chat_ask) can recognize "this specific
# kind of failure is worth automatically retrying once the connection
# comes back" (see _ConnectivityRetryTracker) instead of surfacing a
# dead-end error the user would otherwise have to manually retry.
# -------------------------------------------------------------------------
CONNECTIVITY_ERROR_TAG = "🔌 No internet connection detected"

# What the user sees when a provider technically "succeeded" but its final
# message contained zero text (the old "(empty response)" bubble -- seen
# live after a build whose closing summary never materialized). The
# transcript-mining variant below names the real actions instead.
EMPTY_REPLY_FALLBACK_NO_TRANSCRIPT = (
    "The model finished without any text in its reply — say 'continue' "
    "and I'll pick it up from here."
)

# IDE-chat tasks must run to completion: an empty final message is
# recovered, recap'd, and continued AUTOMATICALLY (up to this many extra
# rounds) instead of stalling on "say 'continue'".
AUTO_CONTINUE_EMPTY_REPLIES_MAX = 2


# -------------------------------------------------------------------------
# Text-level tool calls -- the last-resort tier's action dial.
#
# Settings entries added with "+ Add Model" are plain-text (chat-
# completions) calls, no native function schemas -- and several free
# models answer action requests by writing tool calls INTO their reply
# text in various dialects (<tool_call>name<arg_key>k</arg_key>
# <arg_value>v</arg_value></tool_call>, Hermes JSON, name(k=v), or a
# bare name). Before this, those replies rendered raw -- the user saw
# pseudo-XML and nothing actually happened (live screenshot: a build
# request answered with literal markup for ide_create_folder).
#
# Instead, the markup is now stripped from the visible text and the
# call is EXECUTED through the exact same agent_tools.call_tool desk
# the native loop uses (same ide-scoped whitelist, same narration
# events), then the result is handed back to the model for its next
# turn. A reply without markup answers plainly, as before.
# -------------------------------------------------------------------------
TEXT_TOOL_CALL_ROUNDS_MAX = 6

_TEXT_TOOL_CALL_RE = re.compile(
    r"<tool_call>(?P<body>.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
_ARG_PAIR_RE = re.compile(
    r"<arg_key>\s*(?P<k>.*?)\s*</arg_key>\s*"
    r"<arg_value>\s*(?P<v>.*?)\s*</arg_value>",
    re.DOTALL | re.IGNORECASE)
_FN_CALL_RE = re.compile(
    r"^(?P<name>[A-Za-z_][\w.]*)\s*\((?P<args>.*)\)\s*$", re.DOTALL)
_BARE_NAME_RE = re.compile(r"^[A-Za-z_][\w.]*$")

# --- dialect 5: the pipe-delimited family (live screenshot: a build
# request answered in <|tool_call_begin|><|tool_call|>NAME
# <|tool_call_arg|>{json}<|tool_call_end|> tokens, which then rendered
# raw into the chat and executed NOTHING). Segments may sit inside a
# <|tool_calls_section_begin|> wrapper, arrive bare, or dangle
# unterminated on a mid-stream cut -- all three are recognized.
_PIPE_SEGMENT_RE = re.compile(
    r"<\|tool_call\|>\s*(?P<name>[A-Za-z_][\w.\-]*)"
    r"(?:\s*<\|tool_call_arg\|>\s*(?P<args>.*?))?"
    r"(?=<\|tool_call\|>|<\|tool_calls?_section_(?:begin|end)\|>"
    r"|<\|tool_call_begin\|>|<\|tool_call_end\|>|$)",
    re.DOTALL | re.IGNORECASE)
_PIPE_CONTROL_TOKENS_RE = re.compile(
    r"<\|tool_calls_section_(?:begin|end)\|>|<\|tool_call_(?:begin|end)\|>"
    r"|<\|tool_call_arg\|>", re.IGNORECASE)

# A pipe begin/end BLOCK whose body skipped the inner <|tool_call|>
# token and carries direct JSON/Hermes text instead; the block body is
# handed to the same body-parser the XML dialect uses.
_PIPE_BLOCK_RE = re.compile(
    r"<\|tool_call_begin\|>(?P<body>.*?)(?:<\|tool_call_end\|>|$)",
    re.DOTALL | re.IGNORECASE)

# The lazy-model stall (live 2026-10-22 reply to "BUILD ME A WEB
# CALCULATOR": "Let me check what's currently open in your workspace
# before I build anything new." -- then silence). An action-shaped
# request answered by prose that ANNOUNCES acting, with no tool call
# anywhere, is a stuck start, not an answer: the engine pushes the
# model ONCE to issue the real calls (or honestly say why it can't).
_ACTION_REQUEST_RE = re.compile(
    r"\b(build|creat|make|writ|implement|fix|delet|rename|mov|copy|open|"
    r"run|execut|generat|add|remov|edit|chang|updat|scaffold|spin|"
    r"install|set ?up|deploy|refactor)\w*", re.IGNORECASE)
_ACTION_INTENT_RE = re.compile(
    r"\b(let me|i'?ll|i will|i'?m (?:going|about) to|going to|about to|"
    r"first,? i'?ll|before i|i (?:need|have) to)\b", re.IGNORECASE)
_IDE_DIRECT_FS_REQUEST_RE = re.compile(
    r"^\s*(?:(?:please|go ahead and|let['’]s)\s+)?"
    r"(?:(?:can|could|would)\s+you\s+(?:please\s+)?|"
    r"i\s+(?:want|need)\s+you\s+to\s+)?"
    r"(?:create|make|add|write|rename|move|copy|delete|remove)\b"
    r".{0,240}\b(?:file|folder|directory)\b",
    re.IGNORECASE)


def _parse_pipe_segment(match):
    """One <|tool_call|> segment -> (name, args), or None when the args
    JSON is broken -- we never invent arguments for a call we cannot
    read (no-fabrication law); the segment simply isn't executed."""
    name = (match.group("name") or "").strip()
    if not _BARE_NAME_RE.match(name):
        return None
    raw = (match.group("args") or "").strip()
    if not raw:
        return name, {}
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return None
        return (name, data) if isinstance(data, dict) else None
    kwargs = {m.group("k").strip(): _coerce_tool_value(m.group("v"))
              for m in _KWARG_RE.finditer(raw)}
    return name, kwargs
_KWARG_RE = re.compile(
    r"(?P<k>[A-Za-z_][\w]*)\s*=\s*(?P<v>.*?)(?:,\s*(?=[A-Za-z_][\w]*\s*=)|$)",
    re.DOTALL)


def _coerce_tool_value(v):
    """Best-effort typing for values that travel as text: 'true'/123/
    quoted strings become real bool/int/str, JSON lists/dicts parse.
    Anything else stays the string it arrived as -- tools are tolerant."""
    v = (v or "").strip()
    if not v:
        return v
    head = v[0]
    if (head in "[{\"" or v.lower() in ("true", "false", "null")
            or head.isdigit() or (head in "+-" and len(v) > 1
                                  and v[1].isdigit())):
        try:
            return json.loads(v)
        except Exception:  # noqa: BLE001
            return v
    return v

def _parse_tool_call_body(body):
    """One <tool_call> ... </tool_call> body -> (name, args), any dialect.
    Returns None when the body carries no recognizable tool call at
    all, so the caller never fabricates a call from prose."""
    body = (body or "").strip()
    if not body:
        return None
    pair = _ARG_PAIR_RE.search(body)
    if pair:
        head = body[:pair.start()].strip()
        if _BARE_NAME_RE.match(head):
            args = {k.strip(): _coerce_tool_value(v)
                    for k, v in _ARG_PAIR_RE.findall(body)}
            return head, args
    if body.startswith("{"):
        try:
            data = json.loads(body)
            name = str(data.get("name") or "").strip()
            args = data.get("arguments") or data.get("args") or {}
            if isinstance(name, str) and _BARE_NAME_RE.match(name) \
                    and isinstance(args, dict):
                return name, args
        except Exception:  # noqa: BLE001
            pass
    fn = _FN_CALL_RE.match(body)
    if fn and _BARE_NAME_RE.match(fn.group("name")):
        kwargs = {m.group("k").strip(): _coerce_tool_value(m.group("v"))
                  for m in _KWARG_RE.finditer(fn.group("args"))}
        return fn.group("name"), kwargs
    if _BARE_NAME_RE.match(body):
        return body, {}
    return None


def _extract_text_tool_calls(text):
    """All recognizable <tool_call> blocks in a reply -> (calls,
    visible_text). The markup is STRIPPED from what the user sees --
    visible_text keeps the model's real prose and never leaks the
    pseudo-XML. An unterminated trailing tag (a mid-stream cutoff)
    is still parsed and still stripped when it parses cleanly."""
    text = text or ""
    calls = []
    # Dialect 5 first: <|...|> control tokens are unambiguous tool
    # syntax, so every trace leaves the visible text whether or not a
    # segment ended up executable. The user never sees a pipe token.
    for m in _PIPE_SEGMENT_RE.finditer(text):
        parsed = _parse_pipe_segment(m)
        if parsed:
            calls.append(parsed)
    visible = _PIPE_SEGMENT_RE.sub("", text)

    def _pipe_block_sub(m):
        parsed = _parse_tool_call_body(m.group("body"))
        if parsed:
            calls.append(parsed)
            return ""
        return m.group("body") or ""   # unparseable stays as evidence

    visible = _PIPE_BLOCK_RE.sub(_pipe_block_sub, visible)
    visible = _PIPE_CONTROL_TOKENS_RE.sub("", visible)
    for m in _TEXT_TOOL_CALL_RE.finditer(visible):
        parsed = _parse_tool_call_body(m.group("body"))
        if parsed:
            calls.append(parsed)
    visible = _TEXT_TOOL_CALL_RE.sub("", visible)
    idx = visible.lower().find("<tool_call>")
    if idx != -1:
        parsed = _parse_tool_call_body(visible[idx + len("<tool_call>"):])
        if parsed:
            calls.append(parsed)
        visible = visible[:idx]
    return calls, visible.strip()


def _resolve_text_tool_name(name, ide_scoped):
    """Model-invented spellings -> real tool ids. Exact match first,
    then the ide_ prefix default (IDE chat), then a single close match
    among the allowed ids -- strict enough that 'delete_everything'
    can never resolve onto anything existing. Returns None when no
    match is defensible, and refuses anything outside the IDE-chat
    whitelist in ide_scoped mode, the same rule call_tool enforces."""
    import agent_tools
    allowed = set(agent_tools.IDE_CHAT_TOOL_NAMES) if ide_scoped \
        else set(agent_tools.TOOL_FUNCTIONS)
    if name in allowed:
        return name
    if ide_scoped and ("ide_" + name) in allowed:
        return "ide_" + name
    import difflib
    close = difflib.get_close_matches(name, sorted(allowed), n=1, cutoff=0.78)
    return close[0] if close else None


def _remap_tool_args(name, args):
    """Free models alias parameter names ('name' instead of 'path')
    constantly. When a tool takes exactly ONE parameter and the model
    passed exactly one value under a different key, ride it onto the
    real parameter; anything else passes through untouched (a wrong
    key on a multi-arg tool is the model's problem to retry, fed back
    as the tool's own TypeError message)."""
    import agent_tools
    if not isinstance(args, dict) or len(args) != 1:
        return args
    func = agent_tools.TOOL_FUNCTIONS.get(name)
    if func is None:
        return args
    import inspect
    try:
        params = list(inspect.signature(func).parameters)
    except Exception:  # noqa: BLE001
        return args
    if len(params) != 1 or params[0] in args:
        return args
    (only_value,) = args.values()
    return {params[0]: only_value}


def _run_text_tool_entry(entry, role, system_prompt, user_prompt,
                         ide_scoped):
    """One last-resort entry turned action-capable: ask the model,
    execute any text-level tool calls in its reply through the shared
    call_tool desk, feed results back, repeat until a clean prose
    answer (or the round cap). Returns (ok, text, error) -- the same
    (ok, text, error) shape _attempt_settings_entry uses."""
    import act_script
    results = act_script.call_model(role, entry["id"], system_prompt,
                                    user_prompt)
    if not results.ok:
        return False, None, results.error
    transcript = []          # (name, args, result) -- real executions
    seen_calls = set()
    reply = results.text or ""
    nudged = False
    for _round in range(TEXT_TOOL_CALL_ROUNDS_MAX):
        calls, visible = _extract_text_tool_calls(reply)
        if not calls:
            if visible:
                # The lazy-model stall: the reply TALKS about acting
                # but carries no call, so nothing happens and the user
                # waits forever. Push the model exactly ONCE to issue
                # the real calls; if it still answers in prose, that
                # prose is the honest final answer.
                if (ide_scoped and not nudged
                        and _ACTION_REQUEST_RE.search(user_prompt or "")
                        and _ACTION_INTENT_RE.search(visible)):
                    nudged = True
                    nudge = (
                        "(System continuation -- the user asked you to "
                        "ACT (their request: \"" + (user_prompt or "")[:300]
                        + "\"), and your reply said you WOULD act but "
                        "included NO tool call, so nothing actually "
                        "happened. Issue the real tool calls NOW -- one "
                        "per line, in whatever tool-call markup form "
                        "you use -- or, if you cannot act, reply in "
                        "plain prose explaining why. Never announce an "
                        "action without emitting the call that does "
                        "it.)")
                    nxt = act_script.call_model(
                        role, entry["id"], system_prompt, nudge)
                    if not nxt.ok:
                        return True, (
                            visible + "  (I pushed the model to run the "
                            "action it announced, but the follow-up "
                            "call failed: " + str(nxt.error) + ".)"), None
                    reply = nxt.text or ""
                    continue
                return True, visible, None
            break            # markup-only tail handled below via transcript
        import agent_tools
        fresh, repeated = [], []
        for cname, cargs in calls:
            real = _resolve_text_tool_name(cname, ide_scoped)
            if real is None:
                transcript.append((f"(unknown: {cname})", cargs,
                                   "not executed -- no such tool"))
                continue
            mapped = _remap_tool_args(real, cargs)
            canon = (real, json.dumps(mapped, sort_keys=True, default=str))
            if canon in seen_calls:
                repeated.append(real)
                continue
            seen_calls.add(canon)
            out = agent_tools.call_tool(
                real, json.dumps(mapped), ide_scoped=ide_scoped)
            transcript.append((real, mapped, out))
            fresh.append((real, mapped, out))
        if not fresh:
            break            # the model only repeated itself or named
                             # unknown tools -- stop WITHOUT burning calls
        log_lines = ("\n".join(
            f"- {n}({json.dumps(a, default=str)[:160]}) -> {r[:300]}"
            for n, a, r in transcript))
        followup = (
            "(System continuation -- you asked for these tool calls "
            "in your last message; each ran for real. Results so far:\n"
            f"{log_lines}\n\nThe user's original request was: "
            f"{user_prompt}\n\nContinue from these results: issue the "
            "next step the same way, or if everything the user wanted is "
            "now done, reply with ONLY the short closing message they "
            "should see -- no tags, no markup.)")
        nxt = act_script.call_model(role, entry["id"], system_prompt,
                                    followup)
        if not nxt.ok:
            return True, _text_tool_transcript_summary(
                transcript, f" (the follow-up call failed: {nxt.error})"), None
        reply = nxt.text or ""
    # Round cap or an unanswerable final reply: honest close-out mined
    # from the REAL transcript, never pretending beyond it.
    return True, _text_tool_transcript_summary(transcript), None


def _text_tool_transcript_summary(transcript, suffix=""):
    """When a turn ends mid-actions without a model-authored closing
    line, build the close-out from what ACTUALLY ran so the user still
    gets a truthful, readable answer instead of silence or leftover
    markup."""
    done = [f"{n}" for n, a, r in transcript if not n.startswith("(")]
    skipped = [n for n, a, r in transcript if n.startswith("(")]
    parts = []
    if done:
        parts.append("Action" + ("s" if len(done) != 1 else "")
                     + " completed: " + ", ".join(done) + ".")
    if skipped:
        parts.append("I couldn't run: " + ", ".join(skipped) + " "
                 "(no matching tool exists). ")
    if not parts:
        parts.append("(The model's reply contained nothing but a tool "
                     "call tag it didn't finish -- nothing ran yet.)")
    return " ".join(parts).strip() + suffix


def _ide_act_script_protocol(prompt):
    """The ACT Script IS the protocol the IDE chat follows: before the
    lightweight provider loop answers an IDE-chat turn on its own,
    act_script's Thinking region classifies the request. Simple file and
    folder operations stay on the normal provider path, which offers the
    IDE's direct tools without spending extra calls on multi-stage
    planning. Other engineering work may use Sequential Rotation,
    Fan-Out, or Debate; ordinary questions stay on the normal chat path.
    Returns (handled, text); (False, "") means "let the normal path answer
    this". Never raises, never lets a protocol bookkeeping hiccup block
    the turn.
    """
    if _IDE_DIRECT_FS_REQUEST_RE.match(prompt or ""):
        return False, ""
    try:
        import act_script
    except Exception:  # noqa: BLE001
        return False, ""
    try:
        providers = act_script.available_providers("thinking")
        if not providers:
            return False, ""
        route, _ = act_script.thinking_classify(prompt, providers[0])
        if route not in ("sequential", "fan_out", "debate"):
            return False, ""  # "single" -> the normal chat loop answers
        text = act_script.run_act_script(prompt, mode=route)
        if not text or _is_empty_model_reply(text):
            return False, ""
        return True, text
    except Exception:  # noqa: BLE001
        return False, ""


def _is_empty_model_reply(text):
    """Recognizes every flavor of a provider-succeeded-but-empty reply:
    the bare fallback constant and the transcript-mined variant (see
    _empty_reply_fallback / the Gemini paths' own mining)."""
    t = (text or "").strip()
    return (t == EMPTY_REPLY_FALLBACK_NO_TRANSCRIPT
            or t.startswith("(My closing reply came back empty"))

# -------------------------------------------------------------------------
# Per-provider short-term circuit breaker.
#
# AI_REQUEST_TIMEOUT_SECONDS (see its own comment further below) fixed a
# real indefinite-hang bug, but on its own it created a *new*, milder
# but still real problem: if one specific configured provider is
# reliably unreachable for this user (a firewall/VPN/ISP route that
# blocks just that one vendor's domain while everything else on the
# internet works completely fine, for example) — run() still tries it
# FIRST, in the same fixed Gemini -> Groq -> Mistral order, on *every
# single message*, each time paying that provider's full timeout before
# falling through to whichever one actually works. That's exactly what
# "it does eventually answer, but it's slow every single time" looks
# like from the user's side — a real, reported symptom, not a
# hypothetical one.
#
# The fix: the *first* failure for a given provider always gets its
# full, honest timeout and attempt -- this is never used to avoid ever
# trying a provider. Only once it has just failed for a connectivity
# reason does a short cooldown kick in, during which *subsequent*
# messages skip straight past it to the next configured provider
# instead of re-discovering the same "still down" fact the slow way
# every time. A success immediately clears the cooldown, so a
# provider that recovers is tried again right away rather than waiting
# out whatever was left of it.
# -------------------------------------------------------------------------
PROVIDER_SKIP_COOLDOWN_SECONDS = 90
_provider_circuit_lock = threading.Lock()
_provider_last_connectivity_failure = {}  # provider name -> time.time() of last failure


def _provider_recently_unreachable(name):
    """True if `name` (\"Gemini\"/\"Groq\"/\"Mistral\") failed for a
    connectivity reason within the last PROVIDER_SKIP_COOLDOWN_SECONDS."""
    with _provider_circuit_lock:
        last_fail = _provider_last_connectivity_failure.get(name)
    return last_fail is not None and (
        time.time() - last_fail < PROVIDER_SKIP_COOLDOWN_SECONDS)


def _record_provider_connectivity_failure(name):
    with _provider_circuit_lock:
        _provider_last_connectivity_failure[name] = time.time()


def _record_provider_recovered(name):
    """Clears a provider's cooldown -- called the moment it succeeds, so
    a transient blip doesn't keep being skipped after it's actually
    back, rather than waiting for the cooldown to simply expire."""
    with _provider_circuit_lock:
        _provider_last_connectivity_failure.pop(name, None)


def _skipped_provider_message(name):
    """What a provider's error slot is filled with when it's skipped via
    the circuit breaker above rather than actually attempted this round
    -- still carries CONNECTIVITY_ERROR_TAG so run()'s own "every
    provider failed the exact same way" collapsing logic still treats
    it identically to a provider that was actually retried and failed
    again, which is the honest outcome anyway."""
    return (f"{CONNECTIVITY_ERROR_TAG} ({name}, skipped -- it failed for "
            "the same reason very recently; retrying it again shortly).")

# Lowercase substrings matched against an exception's own str() as a
# fallback net, since httpx/requests/urllib3 (whatever each provider's
# SDK happens to wrap under the hood) don't all raise the same plain
# socket/connection exception types for the same real-world failure —
# some wrap it in their own HTTP-client-specific error class with no
# direct inheritance from anything socket-related.
_CONNECTIVITY_ERROR_MARKERS = (
    "name or service not known",
    "name resolution",
    "nodename nor servname",
    "getaddrinfo failed",
    "connection refused",
    "failed to establish a new connection",
    "max retries exceeded",
    "read timed out",
    "connection timed out",
    "network is unreachable",
    "temporary failure in name resolution",
    "connection aborted",
    "connection reset",
    "remote end closed connection",
)


def _looks_like_connectivity_error(exc):
    """True if `exc` (any exception raised while trying to reach an AI
    provider) looks like it was actually caused by *no internet
    connection being available at all*, rather than anything specific
    to that provider (a bad key, a quota limit, the provider's own
    servers being down, a malformed request, etc). Checks the exception
    itself and walks its __cause__/__context__ chain (SDKs commonly
    wrap a raw socket/urllib3 error inside their own exception class via
    `raise ... from exc`, so the connectivity signal is often one or two
    levels down rather than on the exception actually caught), then
    falls back to matching known connectivity-failure phrases in the
    text of the whole chain as a safety net for SDKs/HTTP clients that
    don't chain exceptions the normal way.
    """
    connectivity_types = [
        socket.gaierror, socket.timeout, ConnectionError, TimeoutError, OSError,
    ]
    try:
        # All three provider SDKs (google-genai/groq/mistralai) use httpx
        # under the hood, so a request that hits AI_REQUEST_TIMEOUT_SECONDS
        # above raises one of these -- not a plain TimeoutError/OSError,
        # and its str() is often just a bare "timed out" with none of the
        # phrases in _CONNECTIVITY_ERROR_MARKERS, so without this it would
        # wrongly fall through to a generic, unhelpful "error: timed out"
        # message instead of the proper connectivity message (and the
        # automatic once-back-online retry that message arms). Best
        # effort: if httpx somehow isn't importable, the type/text checks
        # below still catch most real-world cases on their own.
        import httpx
        connectivity_types.append(httpx.TransportError)  # covers Timeout/Connect/etc.
    except Exception:  # noqa: BLE001
        pass
    connectivity_types = tuple(connectivity_types)
    seen = set()
    node = exc
    chain = []
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        chain.append(node)
        if isinstance(node, connectivity_types):
            return True
        node = getattr(node, "__cause__", None) or getattr(node, "__context__", None)
    text = " | ".join(str(n) for n in chain).lower()
    return any(marker in text for marker in _CONNECTIVITY_ERROR_MARKERS)


# -------------------------------------------------------------------------
# Agent Mode: the circle's own position is now completely independent of
# the IDE (it stays pinned to its usual top-right-of-screen spot, or
# wherever the user has dragged it, the whole time) — the only thing
# that still relates the two is the open/close "pop" animation (see
# get_circle_rect() below and ide.py's _play_open_animation/
# _play_close_animation), which uses the circle's *current* on-screen
# spot as the point the IDE visually grows out of / shrinks back into.
# -------------------------------------------------------------------------


class AIWorker(QThread):
    """The "think" part of the brain: runs one prompt through Gemini, then
    Groq, then Mistral as backups, off the UI thread. Tries whichever
    provider(s) have an API key configured, in that order — each one only
    kicks in if every provider before it fails outright (no key, out of
    quota, erroring, etc.). Whichever one actually answers, the rest of
    the app (text display + speech) doesn't need to know or care which."""

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, prompt, parent=None, summarize_only=False, ide_scoped=False,
                 chat_history="", chat_surface=False):
        """`summarize_only=True` is used for the voice cross-check
        confirmation step (see _needs_confirmation near the top of this
        file): wraps `prompt` in CONFIRMATION_SUMMARY_INSTRUCTION and
        disables tool-calling for this call entirely, so it can only ever
        produce a short confirmation question back, never actually act on
        the request -- that still happens as a normal, separate, full
        AIWorker(original_text) call once the user confirms.

        `ide_scoped=True` is for prompts that came in through the IDE's
        own embedded "Friday AI Chat" panel (see _ide_chat_ask below): a
        standing requirement that this specific chat surface only ever
        writes/edits/runs code in the open workspace, never
        opens/closes/minimizes panels or anything else Friday's normal
        floating circle can otherwise do. Trims which tools the model is
        even offered (agent_tools.get_ide_chat_gemini_tools/
        get_ide_chat_tool_schemas) and, as a backstop, which ones
        agent_tools.call_tool() will actually run -- see its docstring.
        """
        super().__init__(parent)
        self._summarize_only = summarize_only
        self._ide_scoped = ide_scoped
        self._chat_surface = bool(chat_surface)
        self._chat_history = str(chat_history or "")
        self._prompt = (
            CONFIRMATION_SUMMARY_INSTRUCTION.format(command=prompt)
            if summarize_only else prompt
        )
        # The "automatic recall" half of memory (see memory.py's module
        # docstring): whatever's semantically relevant to this prompt,
        # folded into every provider's system prompt below. Deliberately
        # NOT computed here in __init__ -- __init__ runs synchronously on
        # whatever thread constructs this AIWorker (the main/UI thread,
        # for both call sites), so doing a real embedding-API network
        # call here would block the UI before the background thread even
        # starts. It's computed once, up front, in run() instead (see
        # below) -- still a single lookup shared across the
        # Gemini/Groq/Mistral fallback chain, just on the right thread.
        # Skipped entirely for the confirmation-summary call: that call
        # already can't act on anything (no tools), and burning an extra
        # embedding call just to phrase a yes/no question back isn't
        # worth the latency.
        self._memory_context = ""
        # "What's on screen right now" -- which panels are open, which
        # one's active, the IDE's sidebar/active-tab state. Same
        # deliberately-deferred-to-run() reasoning as _memory_context
        # above (a Tk-thread round-trip belongs on the background
        # thread, not __init__ on the UI thread). See
        # agent_tools.get_environment_snapshot().
        self._environment_context = ""
        # Settings API keys already attempted during run(); the final
        # custom-entry tier skips matching keys so a failed credential
        # is not retried in a different dispatch path.
        self._attempted_api_keys = set()

    def _system_prompt(self):
        """SYSTEM_PROMPT (personality.md + thinking_skills.md +
        speech_delivery.md), plus
        whatever's relevant from memory and whatever's currently on
        screen, for this specific prompt -- see __init__'s
        _memory_context/_environment_context above.

        The PROFILE block rides the same train: user_profile knows the
        user's name/craft/links (never credentials); an empty profile
        adds nothing."""
        try:
            import user_profile
            profile_block = user_profile.prompt_summary()
        except Exception:  # noqa: BLE001
            profile_block = ""
        if profile_block:
            profile_block = "\n\n" + profile_block
        chat_guidance = (
            "\n\nThis is Friday's written Chat panel, not spoken voice output. "
            "Answer naturally in text, using Markdown and the level of detail "
            "appropriate to the user's request. Preserve continuity with the "
            "conversation context below. Do not claim to have opened or read "
            "attached files; this panel currently provides filenames only."
            if self._chat_surface else ""
        )
        return (SYSTEM_PROMPT + profile_block + self._memory_context
                + self._environment_context + self._chat_history + chat_guidance)

    def _load_memory_context(self):
        """Does the actual automatic-recall lookup and stores it in
        self._memory_context. Broken out of run() as its own method so
        it's exactly one call either way: run() calls it for real (on
        the background thread, before trying any provider), and tests
        can call it directly to check the memory-folding logic without
        needing a real/mocked AI provider to exercise all of run().
        No-op for summarize_only calls (see __init__)."""
        if self._summarize_only:
            return
        try:
            self._memory_context = memory.build_context_block(self._prompt)
        except Exception:  # noqa: BLE001
            pass  # memory is a courtesy -- never block a reply on it

    def _load_environment_context(self):
        """Does the actual screen/panel-state lookup and stores it in
        self._environment_context. Broken out of run() for the same
        testability reason as _load_memory_context() above. NOT skipped
        for summarize_only -- the confirmation-summary call still
        benefits from knowing what's open (e.g. phrasing "close that"
        sensibly), and it's a local introspection call, not a network
        one, so there's no extra latency reason to skip it."""
        try:
            self._environment_context = agent_tools.get_environment_snapshot()
        except Exception:  # noqa: BLE001
            pass  # same courtesy-only spirit as memory above


    # -- Gemini ------------------------------------------------------------
    @staticmethod
    def _classify_gemini_quota_error(exc):
        """Google's 429s always say status RESOURCE_EXHAUSTED, for both
        per-minute rate limits *and* per-day quota exhaustion — but the
        error details embed a quotaId that says which one, plus sometimes
        a suggested retryDelay. Returns (is_daily, retry_after_seconds).
        """
        text = str(getattr(exc, "details", "") or exc)
        is_daily = "perday" in text.lower().replace(" ", "")
        match = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', text)
        retry_after = float(match.group(1)) if match else None
        return is_daily, retry_after

    @classmethod
    def _gemini_friendly_message(cls, exc):
        code = getattr(exc, "code", None)
        if code == 429:
            is_daily, retry_after = cls._classify_gemini_quota_error(exc)
            if is_daily:
                return (
                    "⏳ Gemini's free daily quota is used up. It resets at "
                    "midnight Pacific time — try again later, or check "
                    "usage at aistudio.google.com/usage."
                )
            wait_note = f" (~{int(retry_after)}s)" if retry_after else ""
            return f"⏳ Gemini rate limit hit{wait_note}."
        if code in (401, 403):
            return "🔑 Gemini API key problem — check this entry's key in the Settings panel."
        if code == 404:
            return "🔍 Gemini model not found/retired. Update GEMINI_MODEL in agent_overlay.py."
        return f"⚠️ Gemini error: {exc}"

    def _try_gemini_with(self, api_key, model=None):
        """One Gemini attempt with an explicit credential -- the ONLY
        call path now. The api_key always comes from the Settings panel
        entry being tried (run()'s `_attempt_settings_entry`), so a key
        the user added or edited there is genuinely what gets dialed."""
        if not api_key:
            return False, None, None  # simply not configured — not an error

        try:
            from google import genai
            from google.genai import types, errors
        except Exception as exc:
            return False, None, f"⚠️ google-genai not installed: {exc}"

        try:
            # HttpOptions.timeout is milliseconds, not seconds -- see
            # AI_REQUEST_TIMEOUT_SECONDS's own comment above for why this
            # is set explicitly at all (the SDK's own default is no
            # timeout whatsoever, i.e. an indefinite hang on a stalled
            # connection).
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=int(AI_REQUEST_TIMEOUT_SECONDS * 1000)),
            )
            config_kwargs = {"system_instruction": self._system_prompt()}
            if not self._summarize_only:
                # Passing plain Python functions here turns on Gemini's
                # automatic function calling: the SDK introspects each
                # function's type hints/docstring, calls it locally
                # whenever the model requests it, and feeds the result
                # back in — response.text is already the final answer.
                # Omitted entirely for the cross-check summary call (see
                # AIWorker.__init__) so it's physically unable to act.
                config_kwargs["tools"] = (
                    agent_tools.get_ide_chat_gemini_tools() if self._ide_scoped
                    else agent_tools.get_active_gemini_tools()
                )
            config = types.GenerateContentConfig(**config_kwargs)

            # Try the primary model first, then each fallback in turn.
            # Within each model, retry a couple of times on 5xx/overload
            # errors (these are usually transient) before moving on.
            primary_model = model or GEMINI_MODEL
            models_to_try = [primary_model] + [
                m for m in FALLBACK_MODELS if m != primary_model
            ]
            last_error = None

            for model_name in models_to_try:
                for attempt in range(RETRIES_PER_MODEL + 1):
                    try:
                        response = client.models.generate_content(
                            model=model_name,
                            contents=self._prompt,
                            config=config,
                        )
                        text = (getattr(response, "text", None) or "").strip()
                        return True, (text or EMPTY_REPLY_FALLBACK_NO_TRANSCRIPT), None
                    except errors.ServerError as exc:
                        last_error = exc
                        if attempt < RETRIES_PER_MODEL:
                            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                        continue
                    except errors.ClientError as exc:
                        last_error = exc
                        code = getattr(exc, "code", None)
                        if code in (401, 403):
                            return False, None, self._gemini_friendly_message(exc)
                        if code == 429:
                            is_daily, retry_after = self._classify_gemini_quota_error(exc)
                            if is_daily:
                                break
                            if attempt < RETRIES_PER_MODEL:
                                wait_s = retry_after or (
                                    RETRY_BACKOFF_SECONDS * (attempt + 1)
                                )
                                time.sleep(min(wait_s, 10))
                                continue
                        break

            return False, None, (
                self._gemini_friendly_message(last_error)
                if last_error is not None
                else "⚠️ Gemini: all models unavailable."
            )
        except Exception as exc:  # noqa: BLE001
            if _looks_like_connectivity_error(exc):
                return False, None, f"{CONNECTIVITY_ERROR_TAG} (Gemini)."
            return False, None, f"⚠️ Gemini error: {exc}"

    # -- shared tool-call loop (Groq + Mistral) -------------------------------
    @staticmethod
    def _empty_reply_fallback(messages):
        """Honest closing text for the (rare) case a provider completes
        without ANY final message: mine the real tool transcript rather
        than printing a bare "(empty response)". Names what actually
        happened, stays truthful about the reply being empty, and tells
        the user exactly how to proceed."""
        tool_results = [m for m in messages if m.get("role") == "tool"]
        if not tool_results:
            return EMPTY_REPLY_FALLBACK_NO_TRANSCRIPT
        names = list(dict.fromkeys(
            m.get("name", "a tool") for m in tool_results))
        last = (tool_results[-1].get("content") or "").strip().splitlines()
        hint = (last[-1].strip() if last else "")[:120]
        return (
            f"(My closing reply came back empty, but I did complete "
            f"{len(tool_results)} action{'s' if len(tool_results) != 1 else ''}: "
            f"{', '.join(names)}."
            + (f" The last one returned: \u2018{hint}\u2019. " if hint else " ")
            + "Say 'continue' to keep going.)"
        )

    @staticmethod
    def _run_tool_loop(create_fn, messages, ide_scoped=False,
                        max_iterations=TOOL_LOOP_MAX_ITERATIONS):
        """Multi-turn tool-calling loop shared by Groq and Mistral — both
        expose the same OpenAI-style response shape (response.choices[0]
        .message, with .tool_calls entries exposing .id/.function.name/
        .function.arguments). Executes any requested tool call locally via
        agent_tools.call_tool and feeds the result back in, repeating until
        the model returns a final plain-text answer (or the iteration cap
        is hit, to guarantee this always terminates).

        `create_fn(messages)` should perform exactly one API call and
        return the raw response object. `messages` is mutated in place as
        the conversation grows. `ide_scoped` is forwarded straight to
        agent_tools.call_tool() -- see AIWorker.__init__'s docstring.
        """
        for _ in range(max_iterations):
            response = create_fn(messages)
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                final = (message.content or "").strip()
                if final:
                    return final
                # The model ended with a completely EMPTY message even
                # though it just did real work (seen live: a finished
                # build whose reply bubble literally said "(empty
                # response)"). A silent bubble is unacceptable -- give
                # it exactly one chance to close out properly with a
                # plain-text summary of what it actually did (max two
                # extra round trips, outside the iteration cap, no more
                # acting). If even that comes back empty, the caller's
                # own honest fallback text takes over instead.
                for _followup in range(2):
                    messages.append({"role": "assistant", "content": ""})
                    messages.append({"role": "user", "content": (
                        "(System note, not from the user: your last message "
                        "came back completely empty -- no text at all. Do not "
                        "call any more tools; just write the closing reply the "
                        "user should have seen: a short summary in your normal "
                        "voice of what you actually did, what worked, and "
                        "anything still blocked and why.)"
                    )})
                    followup = create_fn(messages)
                    followup_text = (
                        followup.choices[0].message.content or "").strip()
                    if followup_text:
                        return followup_text
                return ""

            # Reasoning text the model emitted alongside its tool calls:
            # show it as a visible "thought" row in the IDE chat panel's
            # task-steps card, so the user sees the reasoning thread that
            # leads to each action (the tool calls themselves are
            # narrated by agent_tools.call_tool below).
            if ide_scoped and (message.content or "").strip():
                agent_tools.report_ide_task_step(
                    "thought",
                    "Thought — " + (message.content or "").strip().replace("\n", " ")[:140],
                    "done")

            messages.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })
            for tc in tool_calls:
                result = agent_tools.call_tool(
                    tc.function.name, tc.function.arguments, ide_scoped=ide_scoped
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": result,
                })

        return (
            "Reached the tool-call limit before finishing — try rephrasing "
            "or breaking the request into smaller steps."
        )

    # -- Groq ----------------------------------------------------------------
    @staticmethod
    def _groq_friendly_message(exc):
        import groq

        if isinstance(exc, groq.RateLimitError):
            return "⏳ Groq rate limit hit."
        if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)):
            return "🔑 Groq API key problem — check this entry's key in the Settings panel."
        if isinstance(exc, groq.NotFoundError):
            return "🔍 Groq model not found. Update GROQ_MODEL in agent_overlay.py."
        if isinstance(exc, groq.InternalServerError):
            return "⚠️ Groq's servers are having issues. Try again shortly."
        if isinstance(exc, (groq.APIConnectionError, groq.APITimeoutError)):
            return f"{CONNECTIVITY_ERROR_TAG} (Groq)."
        if _looks_like_connectivity_error(exc):
            return f"{CONNECTIVITY_ERROR_TAG} (Groq)."
        return f"⚠️ Groq error: {exc}"

    def _try_groq_with(self, api_key, model=None):
        """One Groq attempt with an explicit credential -- see
        _try_gemini_with()'s docstring for the Settings-panel-only
        key-source rule this follows."""
        if not api_key:
            return False, None, None  # simply not configured — not an error

        try:
            import groq
        except Exception as exc:
            return False, None, f"⚠️ groq package not installed: {exc}"

        try:
            # See AI_REQUEST_TIMEOUT_SECONDS's comment above -- explicit
            # here for the same reason, even though Groq's own SDK
            # default (60s) is already bounded rather than infinite.
            client = groq.Groq(api_key=api_key, timeout=AI_REQUEST_TIMEOUT_SECONDS)
            primary_model = model or GROQ_MODEL
            models_to_try = [primary_model] + [
                m for m in GROQ_FALLBACK_MODELS if m != primary_model
            ]
            last_error = None

            for model_name in models_to_try:
                for attempt in range(RETRIES_PER_MODEL + 1):
                    try:
                        messages = [
                            {"role": "system", "content": self._system_prompt()},
                            {"role": "user", "content": self._prompt},
                        ]

                        def _create(msgs, _model=model_name):
                            kwargs = {}
                            if not self._summarize_only:
                                # Omitted entirely for the cross-check
                                # summary call (see AIWorker.__init__) so
                                # it's physically unable to act.
                                kwargs["tools"] = (
                                    agent_tools.get_ide_chat_tool_schemas()
                                    if self._ide_scoped
                                    else agent_tools.get_active_tool_schemas()
                                )
                                kwargs["tool_choice"] = "auto"
                            return client.chat.completions.create(
                                model=_model, messages=msgs, **kwargs
                            )

                        text = self._run_tool_loop(
                            _create, messages, ide_scoped=self._ide_scoped,
                            max_iterations=(TOOL_LOOP_MAX_ITERATIONS_IDE
                                            if self._ide_scoped
                                            else TOOL_LOOP_MAX_ITERATIONS),
                        )
                        return True, (text or self._empty_reply_fallback(messages)), None
                    except (groq.RateLimitError, groq.InternalServerError,
                            groq.APIConnectionError, groq.APITimeoutError) as exc:
                        # Transient — worth a short retry, then fall back.
                        last_error = exc
                        if attempt < RETRIES_PER_MODEL:
                            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                        continue
                    except (groq.AuthenticationError, groq.PermissionDeniedError) as exc:
                        return False, None, self._groq_friendly_message(exc)
                    except groq.APIStatusError as exc:
                        # Model-specific problem — move to next fallback.
                        last_error = exc
                        break

            return False, None, (
                self._groq_friendly_message(last_error)
                if last_error is not None
                else "⚠️ Groq: all models unavailable."
            )
        except Exception as exc:  # noqa: BLE001
            if _looks_like_connectivity_error(exc):
                return False, None, f"{CONNECTIVITY_ERROR_TAG} (Groq)."
            return False, None, f"⚠️ Groq error: {exc}"

    # -- Mistral -----------------------------------------------------------
    @staticmethod
    def _mistral_friendly_message(exc):
        # The mistralai SDK doesn't use one exception class per status code
        # (unlike Groq) — everything raises the same error type with a
        # `.status_code` attribute, so classify off of that instead.
        status = getattr(exc, "status_code", None)
        if status == 429:
            return "⏳ Mistral rate limit hit."
        if status in (401, 403):
            return "🔑 Mistral API key problem — check this entry's key in the Settings panel."
        if status == 404:
            return "🔍 Mistral model not found. Update MISTRAL_MODEL in agent_overlay.py."
        if status is not None and status >= 500:
            return "⚠️ Mistral's servers are having issues. Try again shortly."
        if _looks_like_connectivity_error(exc):
            return f"{CONNECTIVITY_ERROR_TAG} (Mistral)."
        return f"⚠️ Mistral error: {exc}"

    def _try_mistral_with(self, api_key, model=None):
        """One Mistral attempt with an explicit credential -- see
        _try_gemini_with()'s docstring for the Settings-panel-only
        key-source rule this follows."""
        if not api_key:
            return False, None, None  # simply not configured — not an error

        try:
            from mistralai.client import Mistral
            from mistralai.client.errors import MistralError
        except Exception as exc:
            return False, None, f"⚠️ mistralai not installed: {exc}"

        try:
            # See AI_REQUEST_TIMEOUT_SECONDS's comment above.
            client = Mistral(
                api_key=api_key,
                timeout_ms=int(AI_REQUEST_TIMEOUT_SECONDS * 1000),
            )
            primary_model = model or MISTRAL_MODEL
            models_to_try = [primary_model] + [
                m for m in MISTRAL_FALLBACK_MODELS if m != primary_model
            ]
            last_error = None

            for model_name in models_to_try:
                for attempt in range(RETRIES_PER_MODEL + 1):
                    try:
                        messages = [
                            {"role": "system", "content": self._system_prompt()},
                            {"role": "user", "content": self._prompt},
                        ]

                        def _create(msgs, _model=model_name):
                            kwargs = {}
                            if not self._summarize_only:
                                # Omitted entirely for the cross-check
                                # summary call (see AIWorker.__init__) so
                                # it's physically unable to act.
                                kwargs["tools"] = (
                                    agent_tools.get_ide_chat_tool_schemas()
                                    if self._ide_scoped
                                    else agent_tools.get_active_tool_schemas()
                                )
                                kwargs["tool_choice"] = "auto"
                            return client.chat.complete(
                                model=_model, messages=msgs, **kwargs
                            )

                        text = self._run_tool_loop(
                            _create, messages, ide_scoped=self._ide_scoped,
                            max_iterations=(TOOL_LOOP_MAX_ITERATIONS_IDE
                                            if self._ide_scoped
                                            else TOOL_LOOP_MAX_ITERATIONS),
                        )
                        return True, (text or self._empty_reply_fallback(messages)), None
                    except MistralError as exc:
                        last_error = exc
                        status = getattr(exc, "status_code", None)
                        if status in (401, 403):
                            return False, None, self._mistral_friendly_message(exc)
                        if status == 429 or (status is not None and status >= 500):
                            # Transient — worth a short retry, then fall back.
                            if attempt < RETRIES_PER_MODEL:
                                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                                continue
                        # Any other status (400/404/422/etc.), or retries
                        # exhausted — move on to the next fallback model.
                        break

            return False, None, (
                self._mistral_friendly_message(last_error)
                if last_error is not None
                else "⚠️ Mistral: all models unavailable."
            )
        except Exception as exc:  # noqa: BLE001
            if _looks_like_connectivity_error(exc):
                return False, None, f"{CONNECTIVITY_ERROR_TAG} (Mistral)."
            return False, None, f"⚠️ Mistral error: {exc}"

    def _settings_pool_plan(self):
        """Ordered Settings-panel entries this reply should try, with
        full tool-calling. The panel is the ONLY source of truth for
        configured models. The task-matched models are ordered first,
        followed by all other compatible chat models. Builtin providers
        use native function calling; custom OpenAI-compatible connections
        use the IDE-scoped text-tool bridge. Never raises -- a settings
        hiccup means "no panel entries"."""
        try:
            import act_script
        except Exception:  # noqa: BLE001
            return []
        role = "coding" if self._ide_scoped else "voice"
        try:
            pool = act_script.region_pool(role)
        except Exception: # noqa: BLE001
            return []
        if not pool:
            return []
        seen = set()
        plan = []
        for entry in pool:
            kind = (entry.get("check_kind") or "").strip()
            key = entry.get("api_key") or ""
            if not kind or not key:
                continue
            if kind not in ("gemini", "groq", "mistral", "generic_http"):
                continue
            fingerprint = (kind, key, entry.get("model") or "")
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            plan.append(entry)
        return plan

    def _attempt_settings_entry(self, entry):
        """One real, tool-capable call against a Settings-panel entry,
        dispatched by its check_kind to whichever provider path above
        speaks its dialect. Returns (ok, text_or_none, friendly_error)."""
        kind = (entry.get("check_kind") or "").strip()
        key = entry.get("api_key") or ""
        model = entry.get("model") or None
        if kind == "gemini":
            return self._try_gemini_with(key, model)
        if kind == "groq":
            return self._try_groq_with(key, model)
        if kind == "mistral":
            return self._try_mistral_with(key, model)
        if kind == "generic_http":
            import act_script

            role = "coding" if self._ide_scoped else "voice"
            if self._summarize_only:
                # This worker exists only to confirm a transcription; do
                # not interpret model-authored tool-call markup as action.
                result = act_script.call_model(
                    role, entry["id"], self._system_prompt(), self._prompt)
                return (result.ok,
                        result.text if result.ok else None,
                        None if result.ok else result.error)
            return _run_text_tool_entry(
                entry, role, self._system_prompt(), self._prompt,
                self._ide_scoped)
        return False, None, None

    def _try_voice_extras(self):
        """Returns (ok, text_or_none, friendly_error_or_none). Last-
        resort tier, tried only after Gemini/Groq/Mistral above have
        all already failed or aren't configured: any remaining model
        connection, such as a second key or custom endpoint. Routes
        through act_script.py's shared-pool dispatch (region_pool/
        call_model) so this never duplicates that logic. Note the real
        trade-off: native function-calling requires schemas the generic
        chat-completions path doesn't send, so actions from this tier
        arrive as TEXT-LEVEL tool calls -- <tool_call>markup the model
        writes into its prose -- rescued and executed by
        _run_text_tool_entry above (stripped from the visible text, run
        through the same call_tool desk, same IDE whitelist). Models
        that never emit markup simply answer in plain prose. It's why
        this tier only ever runs after every tool-capable builtin
        option hasalready been exhausted, never before: native tool
        calling stays the preferred channel; this is the safety net
        that keeps action requests honest when only generic entries
        exist.
        """
        try:
            import act_script
        except Exception:  # noqa: BLE001
            return False, None, None  # Act Script unavailable -- silently skip, not an error

        try:
            # Keep task ordering: compatible models formerly assigned to
            # this task are tried before the rest of the shared pool.
            entries = act_script.region_pool(
                "coding" if self._ide_scoped else "voice")
        except Exception:  # noqa: BLE001
            return False, None, None

        # Skip whichever entries were already tried this run -- either
        # Entry keys already attempted by run()'s own Tier-0 plan above
        # (their keys are collected in self._attempted_api_keys there)
        # are skipped -- only an entry genuinely beyond those is worth a
        # fresh attempt here.
        already_tried_keys = set(getattr(self, "_attempted_api_keys", set()))
        extras = [e for e in entries if e.get("api_key") not in already_tried_keys]
        if not extras:
            return False, None, None

        role = "coding" if self._ide_scoped else "voice"
        failures = []  # (entry name, real error) for EVERY model, not just
        # the last one -- a screen that shed GLM's and Kimi's errors and
        # surfaced only DeepSeek's (the last attempt in the tier) taught
        # the wrong lesson entirely: with several shared models the
        # failure line must tell you what EVERY one of them said.
        for entry in extras:
            # Text-only entries still become ACTION-capable here through
            # the text-level tool-call rescue above (markup stripped from
            # what the user sees; each call executed through the same
            # call_tool desk as native function calling).
            ok, text, err = _run_text_tool_entry(
                entry, role, self._system_prompt(), self._prompt,
                self._ide_scoped)
            if ok:
                return True, text, None
            name = (entry.get("name") or entry.get("model")
                    or entry.get("id") or "model").strip()
            failures.append((name, err or "unknown error"))
        if not failures:
            return False, None, None
        if len(failures) == 1:
            _name, err = failures[0]
            return False, None, f"⚠️ Other connected models: {err}"
        lines = [f"⚠️ Other connected models: none of the "
                 f"{len(failures)} models answered —"]
        lines += [f"  {name}: {err}" for name, err in failures]
        return False, None, "\n".join(lines)

    # -- entry point ---------------------------------------------------------
    def run(self):
        # Resolve the shared Settings pool, with task-matched models first.
        role = "coding" if self._ide_scoped else "voice"
        try:
            import act_script
            active_pool = act_script.region_pool(role)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(
                f"Couldn't load compatible models from Settings: {exc}"
            )
            return
        if not active_pool:
            surface = "IDE work" if self._ide_scoped else "Friday chat"
            self.failed.emit(
                f"No runnable model is configured in Settings → Keys & Connect "
                f"for {surface}. Add or check a compatible model, then "
                "try again."
            )
            return

        # The IDE chat follows the ACT Script protocol directly (see
        # _ide_act_script_protocol below): engineering work goes through
        # Thinking-classify -> Sequential Rotation / Fan-Out / Debate;
        # plain small questions fall through to the normal provider
        # loop, which runs with the strict IDE-only tool allow-list.
        # ide_run_act_script remains as the explicit-tool escape hatch.
        #
        # Memory context loads for EVERY surface -- the IDE's own chat
        # panel included. Each _ide_chat_ask() builds a brand-new
        # AIWorker with only this turn's typed text, so without the
        # recent-turns/facts block a follow-up like "continue" arrived
        # as a bare word with zero context: Friday visibly "forgot" the
        # task it was just working on. The IDE path already feeds this
        # exact same recent-turns ring in its on_ok handler, so loading
        # it here is what makes that write actually readable.
        self._load_memory_context()
        if not self._ide_scoped:
            # "What's on screen" only matters to the floating circle's
            # own conversation -- the IDE panel knows its own context.
            self._load_environment_context()

        if self._ide_scoped:
            # The IDE chat follows the ACT Script protocol (think -> plan
            # -> act with per-step provider rotation -> independent
            # review). Small talk/simple questions fall through to the
            # ordinary provider loop below -- see the helper.
            handled, protocol_text = _ide_act_script_protocol(self._prompt)
            if handled:
                self.succeeded.emit(protocol_text)
                return

        def _attempt(name, try_fn):
            """Runs one provider's _try_X(), wired into the circuit
            breaker above: skips the call entirely (near-zero cost)
            if this provider just failed for a connectivity reason very
            recently, clears its cooldown the moment it actually
            succeeds, and starts its cooldown the moment it fails for a
            connectivity reason -- see PROVIDER_SKIP_COOLDOWN_SECONDS's
            own comment for why this exists at all."""
            if _provider_recently_unreachable(name):
                return False, None, _skipped_provider_message(name)
            ok, text, err = try_fn()
            if ok:
                _record_provider_recovered(name)
            elif err and CONNECTIVITY_ERROR_TAG in err:
                _record_provider_connectivity_failure(name)
            return ok, text, err

        # Settings-panel-only, by design: the ONLY keys Friday ever dials
        # are ones the user deliberately added in the Settings panel.
        # The old env-var provider tiers (GEMINI/GROQ/MISTRAL from .env)
        # are gone -- no silent environmental key fallback anywhere.
        # Region-aware: Coding drives the IDE's build/chat work, Voice
        # drives the circle's chat. Each attempt rides the circuit
        # breaker so a cooked network doesn't stall every turn.
        panel_errors = []
        self._attempted_api_keys = set()
        for entry in self._settings_pool_plan():
            label = (entry.get("name") or entry.get("check_kind")
                     or "Settings model").strip()
            ok, text, err = _attempt(
                label, lambda e=entry: self._attempt_settings_entry(e))
            if ok:
                self.succeeded.emit(text or EMPTY_REPLY_FALLBACK_NO_TRANSCRIPT)
                return
            self._attempted_api_keys.add(entry.get("api_key") or "")
            if err:
                panel_errors.append((label, err))

        # Last resort across compatible Settings connections; custom
        # entries use the documented text-level tool-call path.
        # Not wired into the circuit breaker above -- it's already the
        # last resort, so there's no "skip it" cost to save here.
        extra_ok, extra_text, extra_err = self._try_voice_extras()
        if extra_ok:
            self.succeeded.emit(extra_text)
            return

        # Everything the user actually configured in Settings failed (or
        # nothing is configured) — surface the most useful combination
        # of messages we have.
        tried_errors = [
            *panel_errors,
            ("Other connected models", extra_err),
        ]
        tried_errors = [(name, err) for name, err in tried_errors if err]

        if not tried_errors:
            final_error = "All providers failed."
        elif len(tried_errors) == 1:
            final_error = tried_errors[0][1]
        elif all(CONNECTIVITY_ERROR_TAG in err for _, err in tried_errors):
            # Every provider failed the exact same underlying way — there's
            # no internet, full stop — so 2-3 near-identical "can't reach
            # X" lines would just be noise. One clean message instead,
            # plus a nod to the fact this gets retried automatically (see
            # _ConnectivityRetryTracker) rather than needing the user to
            # ask again once the connection is back.
            final_error = (
                f"{CONNECTIVITY_ERROR_TAG} — I'll keep retrying "
                "automatically once it's back."
            )
        else:
            first_name, first_err = tried_errors[0]
            rest = "\n\n".join(
                f"Also tried {name}: {err}" for name, err in tried_errors[1:]
            )
            final_error = f"{first_err}\n\n{rest}"
        self.failed.emit(final_error)


# ---- Instant panel opening (zero AI round trip) -----------------------
# Asking the circle to "open settings" used to mean a full AIWorker run:
# classify, a real model call, an open_app tool call, THEN the panel --
# several seconds of intelligence spent on a purely local UI action that
# needs none. Three fast paths now skip the model entirely (with the AI
# path still there untouched for everything genuinely conversational):
# this command recognizer (typed **and** spoken input, see
# CircleOverlay._on_query_submitted), the circle's right-click "Open
# panel" menu, and the Ctrl+K type-to-filter launcher.
_PANEL_OPEN_FILLERS = {
    "the", "a", "an", "my", "please", "panel", "app", "window",
    "again", "now", "for", "me", "ok", "okay",
}


def _resolve_panel_open_command(text):
    """Maps a bare "open/launch/show <panel>" request -- typed or said
    out loud -- to an app_panels key WITHOUT calling any model. Returns
    the key, or None when the message is anything else (the caller then
    runs perfectly normal AI path).

    Conservative by design on two fronts, because a false positive here
    would steal a genuine conversation from the model:

    * The message must START with the open intent -- "open settings"
    matches, "tell me how to open settings" is a real question for the
    model and stays one.
    * The remainder must resolve through app_panels.resolve_app_key to
    exactly ONE known key (via the same alias table the AI's open_app
    tool itself uses -- no parallel vocabulary to drift out of sync).
    Anything ambiguous or unknown returns None and goes to the AI.
    """
    chars = []
    for ch in (text or "").strip().lower():
        chars.append(ch if (ch.isalnum() or ch.isspace()) else " ")
    tokens = [w for w in "".join(chars).split() if w and w not in _PANEL_OPEN_FILLERS]
    if not tokens:
        return None
    if tokens[0] in ("open", "launch", "show"):
        rest = tokens[1:]
    elif len(tokens) >= 2 and tokens[:2] in (["pull", "up"], ["bring", "up"]):
        rest = tokens[2:]
    else:
        return None
    if not rest:
        return None
    matches = set()
    for size in range(len(rest), 0, -1):
        for i in range(len(rest) - size + 1):
            key = app_panels.resolve_app_key(" ".join(rest[i:i + size]))
            if key:
                matches.add(key)
    return matches.pop() if len(matches) == 1 else None


class PanelLauncherPopup(QWidget):
    """Ctrl+K-style instant panel launcher: type a few letters to filter
    Friday's panels (from app_panels.KNOWN_APPS -- same source as the
    open_app tool), Enter/click to open with ZERO model call. Esc or
    losing focus dismisses without opening anything."""

    opened = Signal(str)  # app_panels key

    def __init__(self, parent=None):
        super().__init__(parent)
        _translucent_panel(self)
        self.setFixedSize(320, 280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("Open panel — type to filter, Enter to open")
        self.list = QListWidget(self)
        self.list.setStyleSheet(
            "QListWidget { background: transparent; color: white; border: none;"
            "  font-size: 13px; outline: none; }"
            "QListWidget::item { padding: 6px 8px; border-radius: 6px; }"
            "QListWidget::item:selected { background: #3b82f6; }")
        layout.addWidget(self.edit)
        layout.addWidget(self.list)
        self.edit.textChanged.connect(self._refilter)
        self.edit.returnPressed.connect(self._commit_current)
        self.list.itemActivated.connect(self._commit_item)
        self.list.itemClicked.connect(self._commit_item)

        self._refilter("")  # populate immediately, like every Ctrl+K launcher

    def _refilter(self, needle):
        needle = (needle or "").strip().lower()
        parts = [p for p in needle.split() if p]
        self.list.clear()
        for key, meta in app_panels.KNOWN_APPS.items():
            hay = f"{key} {meta['label']} {' '.join(meta['aliases'])}".lower()
            if all(p in hay for p in parts):
                item = QListWidgetItem(meta["label"], self.list)
                item.setData(Qt.UserRole, key)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _commit_item(self, item):
        if item is None:
            return
        self.opened.emit(item.data(Qt.UserRole))
        self.hide()

    def _commit_current(self):
        item = self.list.currentItem()
        if item is None and self.list.count():
            item = self.list.item(0)
        self._commit_item(item)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        if event.key() in (Qt.Key_Down, Qt.Key_Up) and self.edit.hasFocus():
            self.list.setFocus()
            self.list.keyPressEvent(event)
            self.edit.setFocus()
            return
        super().keyPressEvent(event)

    def show_near(self, circle_geo):
        x = circle_geo.x() + min(circle_geo.width(), 80)
        y = circle_geo.y() + circle_geo.height() + 8
        self.move(x, y)
        self.edit.clear()
        self._refilter("")
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()


def _translucent_panel(widget):
    """Give a frameless QWidget a rounded, semi-opaque dark panel look."""
    widget.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    widget.setAttribute(Qt.WA_TranslucentBackground)
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setAttribute(Qt.WA_ShowWithoutActivating, False)
    widget.setStyleSheet(
        "QWidget { background-color: rgba(24, 24, 28, 235); border-radius: 14px; }"
        "QLineEdit { background: transparent; color: white; border: none; "
        "font-size: 13px; }"
        "QLabel { background: transparent; color: white; font-size: 13px; }"
    )


class InputPopup(QWidget):
    """Small text box that appears while LISTENING, replacing mic input."""

    submitted = Signal(str)
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        _translucent_panel(self)
        self.setFixedSize(230, 42)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("Type your question…")
        self.edit.returnPressed.connect(self._on_return)
        layout.addWidget(self.edit)

    def _on_return(self):
        text = self.edit.text().strip()
        if text:
            self.submitted.emit(text)
        else:
            self.cancelled.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)

    def show_near(self, circle_geo):
        x = circle_geo.x() + circle_geo.width() - self.width()
        y = circle_geo.y() + circle_geo.height() + 8
        self.move(x, y)
        self.edit.clear()
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()


_circle_overlay = None  # the one live CircleOverlay instance, set by its
                        # own __init__ -- used by _ide_chat_ask() below


class _MainThreadInvoker(QObject):
    """A persistent QObject constructed on, and living on, the real Qt
    GUI thread -- its only job is running an arbitrary callable there on
    request, no matter which thread asked. See _on_qt_main_thread()'s
    docstring for why this exists: _ide_chat_ask() can be entered from a
    thread Qt doesn't own at all (the IDE's own dedicated Tk thread, when
    a message comes from its embedded chat panel), and even the AIWorker
    callbacks it sets up for itself run on AIWorker's own background
    QThread rather than this one (see that docstring too). A signal
    connected with Qt.QueuedConnection is the standard, supported way to
    post a call onto a specific QObject's own thread regardless of which
    thread emits it -- unlike connecting straight to a plain Python
    closure (what this file used to do here), which Qt can't detect any
    thread affinity for and so always just runs directly, synchronously,
    on whichever thread emitted the signal.
    """
    _invoke = Signal(object)

    def __init__(self):
        super().__init__()
        self._invoke.connect(self._run, Qt.QueuedConnection)

    def _run(self, fn):
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass


_main_thread_invoker = None  # built in main() below, on the real Qt
                             # thread, once a QApplication exists


def _on_qt_main_thread(fn):
    """Runs fn() on the Qt GUI thread -- safe to call from any thread,
    including ones Qt has never heard of (e.g. the IDE's own dedicated
    Tk thread).

    This matters because CircleOverlay (`ov`, below) is a QWidget: its
    timers, animations, and repaints are only safe to touch from the one
    thread that owns it. Before this existed, _ide_chat_ask() called
    straight into `ov._enter_state(...)` from whatever thread invoked it
    -- the IDE's Tk thread, for a message typed into its embedded chat
    panel -- and `_settle()` below did the same thing again from
    AIWorker's own background QThread once the AI call finished. Both
    are genuine Qt threading violations, not just theoretical: calling
    QTimer.singleShot() from a thread with no Qt event dispatcher of its
    own (confirmed by direct reproduction, not guessed) silently
    schedules a callback that never fires -- which is exactly how the
    IDE's chat panel could look "frozen" (an exchange that never
    resolves, or a reply bubble that lands but leaves the circle stuck
    mid-response) depending on exactly when the dropped callback would
    otherwise have run.

    A silent no-op if there's no QApplication/invoker yet (e.g. a unit
    test exercising _ide_chat_ask in isolation without calling main()) --
    there's then no main thread worth marshaling onto anyway.
    """
    if _main_thread_invoker is None:
        return
    _main_thread_invoker._invoke.emit(fn)


# -------------------------------------------------------------------------
# 2-minute unanswered-pause autonomy rule (personality.md's Agent Mode
# section): when Friday pauses mid-task to ask about a major decision --
# sharing a plan, flagging a risk, asking which of several reasonable
# options to take -- that pause must not block forever. If 120 seconds
# pass with no reply on the same conversation surface, Friday is expected
# to stop waiting, pick the most professional option itself, say so out
# loud, and actually carry the work out.
#
# The one piece of this a model reply alone can never supply is real
# wall-clock awareness -- a single request/response turn has no sense of
# how much time elapses afterward. So the contract is split in two:
#   - the model signals "this reply is a pause I'm waiting on an answer
#     for" by ending it with the literal control tag {{AWAIT_DECISION}}
#     (see personality.md) -- never shown or spoken, stripped by
#     _extract_await_decision below before the reply ever reaches the
#     user, same spirit as speech_delivery.md's bracket cues but a
#     deliberately different bracket style so the two can never collide
#     or be mistaken for one another (see _CUE_TAG_RE in
#     overlay_speech.py);
#   - this file supplies the actual clock: _PendingDecisionTracker arms a
#     real QTimer the moment that tag is seen, and -- if nothing
#     cancels it first, which any new message on that same surface does
#     -- fires a synthetic continuation prompt once the timeout elapses,
#     telling Friday no one answered and to decide and proceed, then
#     delivers whatever it comes back with the normal way (spoken for
#     the circle, a new chat bubble for the IDE's embedded "Friday AI
#     Chat" panel).
#
# Two independent trackers exist -- one per conversation surface (see
# CircleOverlay._decision_tracker and _ide_chat_decision_tracker below) --
# so a pause on one surface never auto-resolves just because the *other*
# surface happens to see activity.
_AWAIT_DECISION_TAG_RE = re.compile(
    r"\{\{\s*AWAIT[_ -]DECISION\s*\}\}\s*", re.IGNORECASE)
AWAIT_DECISION_TIMEOUT_MS = 120_000  # 2 minutes, per the user's standing rule


def _extract_await_decision(text):
    """Returns (clean_text, is_pending_decision). `clean_text` has the
    {{AWAIT_DECISION}} control tag removed -- it's a pure signal to this
    code, never literal content a user should see or hear.
    `is_pending_decision` is True only when the tag was actually present.
    """
    if not text:
        return text, False
    if not _AWAIT_DECISION_TAG_RE.search(text):
        return text, False
    cleaned = _AWAIT_DECISION_TAG_RE.sub("", text).rstrip()
    return cleaned, True


# -------------------------------------------------------------------------
# Inline decision options (Task 65's Permission mechanism): the IDE's own
# embedded "Friday AI Chat" panel renders a pending {{AWAIT_DECISION}}
# pause as clickable option buttons -- plus the chat box's own always-
# present text entry as the free-text "custom option" -- instead of
# leaving the user to retype an answer from scratch, the same spirit as
# this agent's own ask_user tool presenting predefined options alongside
# a custom-response field. A reply signals this by also including
# {{OPTIONS: "Option A" | "Option B" | "Option C"}} right alongside
# {{AWAIT_DECISION}} (see personality.md) -- 2 to 4 short option labels,
# pipe-separated, double-quoted.
#
# IDE-chat-only, deliberately: the floating circle's conversation is
# voice/spoken, where clickable buttons have no meaning -- its own
# decision path (CircleOverlay._decision_tracker) never looks for this
# tag and is never offered it in personality.md's instructions, but as a
# defense-in-depth backstop against a model emitting it somewhere it was
# never asked to, the circle path still runs text through
# _extract_decision_options() and discards the parsed options, purely
# so the raw tag text itself is never spoken aloud or shown (see
# AIWorker._on_reply_ok below).
_OPTIONS_TAG_RE = re.compile(
    r"\{\{\s*OPTIONS\s*:\s*(.*?)\s*\}\}\s*", re.IGNORECASE | re.DOTALL)
_OPTIONS_ITEM_RE = re.compile(r'"([^"]*)"')


def _extract_decision_options(text):
    """Returns (clean_text, options_or_None). `clean_text` has the
    {{OPTIONS: ...}} control tag removed -- same pure-signal spirit as
    _extract_await_decision. `options` is a list of 2-4 short strings
    when the tag was present and parsed at least one quoted option,
    otherwise None (a malformed/empty tag is treated as absent rather
    than raising or showing the user a broken tag).
    """
    if not text:
        return text, None
    m = _OPTIONS_TAG_RE.search(text)
    if not m:
        return text, None
    cleaned = _OPTIONS_TAG_RE.sub("", text).rstrip()
    items = [s.strip() for s in _OPTIONS_ITEM_RE.findall(m.group(1))]
    items = [s for s in items if s][:4]
    return cleaned, (items if items else None)


def _accepts_options_arg(fn):
    """True if `fn` can be called with a 3rd positional argument --
    used to decide whether an on_done-shaped callback gets the new
    `options` parameter or the original 2-arg (ok, text) call, without
    ever actually attempting (and possibly double-performing) the call
    itself to find out. Any callable this can't introspect (some C
    builtins, bound methods wrapping them, etc.) is assumed to accept
    it -- genuinely bare 2-arg callbacks are always plain Python
    functions/lambdas in this codebase, so that case is the one this
    needs to actually detect; the fallback only ever broadens, it never
    narrows, what already worked before this parameter existed.
    """
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return True
    if any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params):
        return True
    positional = [
        p for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                      inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    return len(positional) >= 3


class _PendingDecisionTracker:
    """One per conversation surface -- see module comment above. Owns at
    most one in-flight 2-minute timer at a time; arming a new one (a
    fresh pause) or cancelling (a new real user message arrived)
    invalidates whatever was pending before via a generation counter,
    rather than holding an actual QTimer handle to stop -- simpler, and
    safe even though arm()/cancel() happen from different call sites
    over the tracker's lifetime.
    """

    def __init__(self, resume, timeout_ms=AWAIT_DECISION_TIMEOUT_MS):
        self._generation = 0
        self._question = None
        self._resume = resume  # resume(question_text) -- called once the timeout fires
        self._timeout_ms = timeout_ms

    def arm(self, question_text):
        """Call only from the Qt main thread (QTimer requirement -- see
        _on_qt_main_thread's docstring for exactly what goes wrong
        otherwise). Starts, or restarts, the clock on `question_text` --
        the cleaned reply Friday just gave, kept only as context for the
        eventual auto-continuation prompt."""
        self._generation += 1
        gen = self._generation
        self._question = question_text
        QTimer.singleShot(self._timeout_ms, lambda: self._fire(gen))

    def cancel(self):
        """Call whenever a new real user message arrives on this
        surface -- the user came back, so whatever was pending no longer
        needs an automatic decision; Friday's ordinary reply to that new
        message handles it instead."""
        self._generation += 1
        self._question = None

    def _fire(self, gen):
        if gen != self._generation:
            return  # answered, or superseded by a newer pause -- stale
        question = self._question
        self._generation += 1
        self._question = None
        if question:
            self._resume(question)


CONNECTIVITY_POLL_MS = 5000  # how often to re-check for a working connection


class _ConnectivityRetryTracker:
    """One per conversation surface (circle / IDE chat panel) -- same
    reasoning as _PendingDecisionTracker's own module comment: a stalled
    request on one surface should never auto-resolve just because
    activity happens on the *other* one. Armed exactly once a request
    fails with CONNECTIVITY_ERROR_TAG in its error message (see
    AIWorker.run() and the three _try_* providers above) -- polls
    agent_tools.has_internet_connectivity() every CONNECTIVITY_POLL_MS
    and, the instant it comes back True, resubmits the *original*
    request text through the normal pipeline exactly once.

    Deliberately re-runs the original request rather than sending a
    synthetic "you're back online, continue" nudge the way the 2-minute
    unanswered-pause rule's auto-continue does: that request never
    actually ran at all (the call failed before the model ever saw it),
    so there's nothing to "continue" -- just retry it for real, with the
    exact text the user (or whatever system event) originally asked for.

    Same generation-counter cancellation pattern as
    _PendingDecisionTracker, but polling on a fixed interval rather than
    firing once after a fixed delay -- "is the internet back yet" is
    inherently a thing that has to be kept checking, not something with
    a single deadline to time out into.
    """

    def __init__(self, resume, poll_ms=CONNECTIVITY_POLL_MS):
        self._generation = 0
        self._resume = resume  # resume(original_text) -- called once connectivity returns
        self._poll_ms = poll_ms

    def arm(self, original_text):
        """Call only from the Qt main thread (QTimer requirement -- see
        _PendingDecisionTracker.arm's docstring). Starts, or restarts,
        polling for connectivity on `original_text`'s behalf."""
        self._generation += 1
        gen = self._generation
        self._schedule_poll(gen, original_text)

    def cancel(self):
        """Call whenever a new real request supersedes the stalled one
        (same trigger point _PendingDecisionTracker.cancel() uses) --
        once the user has moved on, the old stalled request is abandoned
        rather than retried behind their back."""
        self._generation += 1

    def _schedule_poll(self, gen, original_text):
        QTimer.singleShot(self._poll_ms, lambda: self._poll(gen, original_text))

    def _poll(self, gen, original_text):
        if gen != self._generation:
            return  # cancelled, or superseded by a newer arm() -- stale
        if agent_tools.has_internet_connectivity():
            self._generation += 1
            self._resume(original_text)
        else:
            self._schedule_poll(gen, original_text)


_pending_chat_workers = set()  # keeps each _ide_chat_ask() AIWorker alive
                               # until it finishes -- it has no QObject
                               # parent to rely on for that when no
                               # circle is on screen to own it (parent=
                               # the circle would otherwise do this, the
                               # same way the circle's own chat path
                               # keeps its worker alive via self._worker)


def _ide_chat_ask(text, on_done, is_auto_continue=False,
                   _empty_retry=0):
    """Registered with agent_tools.set_chat_handler() in main() below.
    Runs `text` through Friday's normal AI brain -- the exact same AIWorker
    a spoken/typed command goes through -- and calls on_done(ok, result)

    once finished. This is called directly, in-process, by the IDE's own
    embedded 'Friday AI Chat' panel (ide.py's ChatSidePanel) -- no socket,
    no separate process, just a plain function call. Note that this
    still crosses a real thread boundary, though: the IDE runs its own
    dedicated Tk mainloop() thread (see agent_tools._TkThreadBridge), so
    this function's own body executes *there*, not on this process's Qt
    GUI thread -- any touch of `ov` (CircleOverlay, a QWidget) below goes
    through _on_qt_main_thread() rather than calling it directly, for
    exactly that reason. The AIWorker itself is built (and its signals
    connected, and started) entirely on the Qt main thread too, for a
    more subtle reason than ov -- see _start_worker()'s own docstring
    below, this is what was actually silently swallowing every reply
    that came in through this panel.

    Also drives the floating circle's own state (loading -> responding),
    exactly as a chat message typed inside the IDE should visibly
    register on the circle too -- but only when the circle is otherwise
    idle, so it never interrupts an unrelated live voice/typed
    conversation already in progress.

    Standing scope restriction: this AIWorker is constructed with
    ide_scoped=True, so it's only ever offered (and will only ever run)
    the ide_* coding tools -- never the app/window/panel-management
    tools (open_app/minimize_app/etc.) or anything else Friday's normal
    floating circle can do. This chat box is strictly for coding tasks
    in the open workspace; see AIWorker.__init__ and
    agent_tools.get_ide_chat_gemini_tools/call_tool for where that's
    actually enforced.

    `is_auto_continue=True` only for the 2-minute unanswered-pause
    autonomy rule's own synthetic follow-up call (see
    _ide_chat_auto_continue below) -- a real message typed by the user
    into the chat box never sets this. It skips re-cancelling the pause
    timer (the timer firing is *why* this call is happening) and tells
    on_ok/on_fail below not to misattribute the synthetic nudge text to
    the user in memory.
    """
    if not is_auto_continue:
        # A real message just arrived in this chat panel -- any
        # previously-armed 2-minute pause timer for it no longer needs
        # to fire; this message (whether or not it actually answers the
        # pending question) is handled the normal way below instead.
        _ide_chat_decision_tracker.cancel()
        # Same for a stalled connectivity retry -- the user re-engaging
        # with this chat panel (whether or not their new message is
        # actually related to whatever previously failed) supersedes it.
        _ide_chat_connectivity_retry.cancel()
        # A genuine new message clears any stale waiting/done/error pill
        # color from a previous turn immediately -- see
        # agent_tools.report_panel_status's own docstring. Auto-continue/
        # retry calls skip this so *their* own eventual outcome (not a
        # premature reset back to "normal" the instant they start) is
        # what the user sees reflected on the pill.
        agent_tools.report_panel_status("ide", "normal")
    ov = _circle_overlay
    drive_state = (ov is not None and ov._state == STATE_IDLE)
    if drive_state:
        _on_qt_main_thread(lambda: ov._enter_state(STATE_LOADING))

    def _settle(ok):
        if not drive_state:
            return

        def _apply():
            ov._last_was_error = not ok
            if ov._state == STATE_LOADING:
                ov._enter_state(STATE_RESPONDING)
                # Scheduled from right here, inside the main-thread
                # invoker's own callback, so this QTimer is created on
                # the thread that actually runs a Qt event dispatcher --
                # scheduling it from AIWorker's own background thread
                # (as this used to, before _on_qt_main_thread existed)
                # silently drops the callback forever instead of firing
                # it, since that thread has no Qt event loop of its own.
                QTimer.singleShot(2000, ov._finish_responding)

        _on_qt_main_thread(_apply)


    def on_ok(reply):
        if _is_empty_model_reply(reply) and _empty_retry < AUTO_CONTINUE_EMPTY_REPLIES_MAX:
            # The task was mid-flight and the provider's closing message
            # contained no text at all. Never park the user's task on
            # "say continue" -- keep driving it ourselves, with an honest
            # spoken note so it never looks like magic silence.
            _on_qt_main_thread(lambda: _announce_panel_event(
                "Sir, the model went quiet mid-task — continuing it "
                "automatically."))
            _ide_chat_ask(
                "continue",
                on_done,
                is_auto_continue=True,
                _empty_retry=_empty_retry + 1)
            return
        _settle(True)
        # 2-minute unanswered-pause autonomy rule: strip the control tag
        # before the reply ever reaches memory or the chat bubble (it's
        # a pure signal, never literal content -- see
        # _extract_await_decision's docstring), and arm this panel's own
        # pause timer when it was present. Done up front, before any of
        # the cue-stripping/memory/display steps below, same as the
        # circle's own _on_reply_ok.
        reply, is_pending_decision = _extract_await_decision(reply)
        # Task 65's Permission mechanism: a companion {{OPTIONS: ...}}
        # tag, only ever meaningful alongside {{AWAIT_DECISION}} on this
        # IDE-chat surface (see _extract_decision_options's module
        # comment) -- parsed here, before display/memory, same reasoning
        # as the await-decision tag just above. `decision_options` is
        # None unless the model actually offered concrete choices.
        reply, decision_options = _extract_decision_options(reply)
        # Unlike the circle's own voice/typed chat path, this reply is
        # never spoken -- the IDE's "Friday AI Chat" panel is a plain
        # text bubble (ide.py's ChatSidePanel._add_bubble) -- so
        # speech_delivery.md's bracket cues ([pause]/[sighs]/[whispers]/
        # ...) would just show up as literal bracket noise here instead
        # of doing anything useful. Clean them out once, up front, and
        # use that same cleaned text for both the visible bubble and
        # memory, so conversation history never carries them either.
        clean_reply = _strip_cues_for_display(reply) if reply else reply
        # Same automatic memory logging the circle's own chat path does
        # (_on_reply_ok above) -- the IDE's chat panel is a second front
        # door onto the exact same AIWorker/"think" stage, so it should
        # feed the same memory rather than being a silent blind spot.
        # remember_recent_turn() is synchronous/instant (no embedding, no
        # disk) so the very next message -- even one typed a second later
        # -- already sees this exchange; log_turn_async() is the slower,
        # permanent, embedded copy for long-term/cross-session recall.
        if text:
            memory.remember_recent_turn("user", text)
            memory.log_turn_async("user", text)
        if clean_reply:
            memory.remember_recent_turn("friday", clean_reply)
            memory.log_turn_async("friday", clean_reply)
        if is_pending_decision:
            # arm() creates a QTimer -- must happen on the Qt main
            # thread (see _on_qt_main_thread's docstring); on_ok itself
            # runs on AIWorker's own background QThread (a plain Python
            # closure connected to a QThread signal runs synchronously
            # on whichever thread emitted it, same reason _settle above
            # marshals its own ov-touching work).
            _on_qt_main_thread(
                lambda: _ide_chat_decision_tracker.arm(clean_reply or reply))
        # Minimized-panel pill color + proactive voice cue (see
        # agent_tools.report_panel_status/CircleOverlay.announce_panel_event):
        # "waiting" (orange) when this reply paused for the user's
        # permission to proceed, "done" (green) otherwise. Scoped
        # deliberately to this chat panel's own worker completions, not
        # the circle's general conversation touching ide_* tools --
        # same per-surface boundary the 2-minute pause rule above
        # already draws.
        agent_tools.report_panel_status("ide", "waiting" if is_pending_decision else "done")
        # The panel's FULL report: this reply text is exactly what the
        # user finds back in the IDE chat on restore; the circle also
        # speaks it then (see _on_panel_restored).
        agent_tools.store_panel_report(
            "ide",
            clean_reply or "The IDE finished its task.")
        if agent_tools.is_panel_minimized("ide"):
            announcement = (
                "Sir, the IDE needs your permission before proceeding."
                if is_pending_decision else
                "The IDE is done with its task, sir."
            )
            _on_qt_main_thread(lambda: _announce_panel_event(announcement))
        # Options only ever make sense alongside an actual pending
        # decision -- a stray {{OPTIONS: ...}} tag on an otherwise
        # finished reply (no {{AWAIT_DECISION}}) is ignored rather than
        # rendered, since there would be nothing live to resolve by
        # clicking it.
        _opts = decision_options if is_pending_decision else None
        if _accepts_options_arg(on_done):
            on_done(True, clean_reply, _opts)
        else:
            # Backward-compatible with any `on_done` written before the
            # `options` parameter existed (2-arg signature) -- ide.py's
            # real callback always accepts it, but tests/other callers
            # that only ever cared about (ok, text) shouldn't have to be
            # rewritten just because a feature they don't exercise grew
            # an extra, optional piece of information. Checked by
            # inspecting the signature up front, deliberately not a
            # bare try/except TypeError around the call itself -- that
            # would risk silently double-invoking (and so double-acting
            # on) a callback whose *body* happens to raise its own
            # unrelated TypeError, rather than one that just doesn't
            # accept a 3rd argument.
            on_done(True, clean_reply)

    def on_fail(err):
        _settle(False)
        # Same pill-color treatment for a failure -- red either way, but
        # only a *connectivity* failure also arms the automatic retry
        # (see _ide_chat_connectivity_retry/_ConnectivityRetryTracker):
        # retrying a bad API key or a quota error on its own would just
        # fail again the exact same way, so that's left for the user to
        # actually address.
        agent_tools.report_panel_status("ide", "error")
        if err:
            agent_tools.store_panel_report(
                "ide", f"The task did not complete: {err}")
        is_connectivity = err and CONNECTIVITY_ERROR_TAG in err
        if is_connectivity:
            _on_qt_main_thread(
                lambda: _ide_chat_connectivity_retry.arm(text))
        if agent_tools.is_panel_minimized("ide"):
            announcement = (
                "Sir, the IDE's work has stalled — no internet "
                "connection. I'll resume it automatically once it's "
                "back."
                if is_connectivity else
                "Sir, the IDE ran into a problem and couldn't continue."
            )
            _on_qt_main_thread(lambda: _announce_panel_event(announcement))
        on_done(False, err)

    def _start_worker():
        """Builds, wires up, and starts the AIWorker -- run entirely on
        the Qt main thread via _on_qt_main_thread() below, same as every
        other Qt-touching step in this function, but for a sharper
        reason here: a QThread subclass's OWN signals (succeeded/failed)
        auto-connected to a plain Python callable (on_ok/on_fail below,
        not a bound QObject slot) get delivered via a queued call
        targeting the QThread *object's* own thread affinity -- i.e.
        wherever it was constructed -- not wherever run() itself
        actually executes, and not wherever .connect() happened to be
        called from either. Build it on a thread with no Qt event
        dispatcher of its own (the IDE's Tk thread, in particular) and
        that queued delivery is silently never pumped: on_ok/on_fail
        then never fire at all, which is the actual, complete
        explanation for messages typed into the IDE's chat panel
        appearing to freeze/never go through -- confirmed by direct
        reproduction (a QThread built on a plain background thread,
        emitting a signal connected to a plain function, simply never
        calls it, no error, no warning).

        No QObject parent passed to AIWorker() on purpose -- the circle
        (when there is one) isn't a meaningful "owner" of a chat-panel
        request, and in tests it may not even be a real QObject.
        _pending_chat_workers is what actually keeps this alive until it
        finishes.
        """
        # ide_scoped=True: standing requirement -- the IDE's embedded
        # chat panel is strictly for coding tasks (writing/editing/
        # running code in the open workspace). It must never be able to
        # open/close/minimize panels or do anything else Friday's normal
        # floating circle can otherwise do -- see AIWorker.__init__'s
        # docstring.
        worker = AIWorker(text, parent=None, ide_scoped=True)
        _pending_chat_workers.add(worker)

        def _cleanup():
            _pending_chat_workers.discard(worker)
            worker.deleteLater()

        worker.succeeded.connect(on_ok)
        worker.failed.connect(on_fail)
        worker.finished.connect(_cleanup)
        worker.start()

    _on_qt_main_thread(_start_worker)
    if not is_auto_continue:
        # A real user message = a genuinely new task: restart step
        # numbering and mark the panel as actively WORKING so the
        # circle spinner + orange panel/pill borders light up (see
        # agent_tools.register_panel_status_listener + the circle's
        # working ring). Auto-continue/retry rounds deliberately skip
        # this -- they belong to the same ongoing task.
        agent_tools.begin_ide_task_step_sequence()
        agent_tools.report_panel_status("ide", "working")


def _ide_chat_auto_continue(question):
    """Fires when _ide_chat_decision_tracker's 2-minute no-reply timeout
    elapses -- the user never replied to Friday's last pause-for-a-
    major-decision question in the IDE's embedded chat panel. Per
    personality.md's Agent Mode section, re-runs _ide_chat_ask with a
    synthetic system-level nudge standing in for the user's own next
    message, so the usual AIWorker -> on_ok -> memory flow handles the
    follow-through -- the only difference is where the eventual reply
    lands: nobody is sitting on a `host.ask(...)` call waiting for this
    one (the timer fired on its own, not in response to anything typed
    just now), so it's delivered with agent_tools.notify_ide_chat
    instead of an on_done callback a caller is already holding.
    """
    synthetic = (
        "(System note, not from the user: about two minutes have passed "
        "with no reply to your last question in this chat panel — \"%s\". "
        "No one is coming back to answer it right now. Per the 2-minute "
        "unanswered-pause rule, decide the most professional, reasonable "
        "option yourself, say plainly that you're proceeding without a "
        "reply and why, and then actually carry out the pending work.)"
        % question
    )

    def _deliver(ok, result, options=None):
        agent_tools.notify_ide_chat(
            result if ok else "\u26a0 %s" % result, options if ok else None)

    _ide_chat_ask(synthetic, _deliver, is_auto_continue=True)


def _ide_chat_retry_after_connectivity(original_text):
    """Fires when _ide_chat_connectivity_retry detects the internet is
    back after a request in the IDE's embedded chat panel failed with
    CONNECTIVITY_ERROR_TAG (see _ide_chat_ask's on_fail below). Simply
    re-runs the exact original request -- it never actually went through
    the first time, so this is a real retry, not a continuation -- and
    delivers the eventual result via agent_tools.notify_ide_chat, same
    as _ide_chat_auto_continue above, since nobody is sitting on an
    on_done callback waiting for it any more (the original caller's
    on_done already fired once, with the failure).
    """
    def _deliver(ok, result, options=None):
        agent_tools.notify_ide_chat(
            result if ok else "\u26a0 %s" % result, options if ok else None)

    _ide_chat_ask(original_text, _deliver, is_auto_continue=True)


# One shared tracker for the IDE's embedded chat panel -- see the
# _PendingDecisionTracker/_extract_await_decision block near
# _on_qt_main_thread above for the full story. The circle's own
# voice/typed chat gets its own separate instance per CircleOverlay (see
# CircleOverlay.__init__'s self._decision_tracker) so a pause on one
# surface never auto-resolves just because the other sees activity.
_ide_chat_decision_tracker = _PendingDecisionTracker(_ide_chat_auto_continue)

# Same one-per-surface reasoning, for the "lost internet mid-task, retry
# once it's back" behavior (see _ConnectivityRetryTracker above). The
# circle's own voice/typed chat gets its own separate instance too (see
# CircleOverlay.__init__'s self._connectivity_retry).
_ide_chat_connectivity_retry = _ConnectivityRetryTracker(
    _ide_chat_retry_after_connectivity)


_pending_label_workers = set()  # keeps each _ide_speak_label() SpeechWorker
                               # alive until it finishes, same reason as
                               # _pending_chat_workers above.


def _ide_speak_label(text):
    """Registered with agent_tools.set_speak_label_handler() in main()
    below. Speaks `text` aloud through Friday's own configured voice --
    called directly, in-process, from the Tk side of this shared main
    thread, by ide.py's minimized-panel "pill" (see MinimizedPill) when
    it's hovered, to announce which panel it represents (e.g. "IDE").

    Deliberately silent (not an error, just a no-op) if speech is off,
    if there's no circle at all (e.g. ide.py running standalone), or if
    Friday is already mid-conversation (not idle) -- this is a minor
    cosmetic cue and must never interrupt, queue behind, or compete with
    a real voice/typed exchange already in progress. Also silently
    ignores a new request while a previous label announcement is still
    playing, rather than piling them up.
    """
    if not (ENABLE_SPEECH and text and text.strip()):
        return
    ov = _circle_overlay
    if ov is None or ov._state != STATE_IDLE:
        return
    if _pending_label_workers:
        return  # already announcing a label -- don't stack another on top

    worker = SpeechWorker(text, parent=None)
    _pending_label_workers.add(worker)

    def _cleanup():
        _pending_label_workers.discard(worker)
        worker.deleteLater()

    worker.finished.connect(_cleanup)
    worker.start()


def _announce_panel_event(text):
    """Best-effort: hands `text` to the circle's own proactive
    panel-status announcement queue (see CircleOverlay.announce_panel_event)
    if a circle is actually on screen right now. No-ops otherwise (e.g.
    a standalone `python ide.py` run with no Friday circle at all).
    Always call this via _on_qt_main_thread(...) from a non-Qt thread --
    announce_panel_event can start a QTimer.
    """
    ov = _circle_overlay
    if ov is not None:
        ov.announce_panel_event(text)


class CircleOverlay(QWidget):
    def __init__(self):
        super().__init__()
        global _circle_overlay
        _circle_overlay = self  # lets _ide_chat_ask() reach the one live
        # circle instance without a constructor-injected reference —
        # there's only ever one of these per process.
        # Panels restored (from a pill, etc.) hand the circle the panel's
        # stored full work report for spoken delivery -- per-panel
        # reports are written by report sources in agent_tools and the
        # chat workers above.
        agent_tools.register_panel_restore_listener(self._on_panel_restored)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.resize(WINDOW_SIZE, WINDOW_SIZE)

        self._scale = 1.0
        self._hover = False
        self._state = STATE_IDLE
        # Panels currently executing work (IDE builds etc). While any
        # exist and the circle itself is idle, the ring just spins --
        # the full wave/equalizer animation stays reserved for actual
        # circle interactions (asked a question / answering).
        self._panels_working = set()
        agent_tools.register_panel_status_listener(self._on_panel_status)
        self._drag_offset = None
        self._press_global_pos = None
        self._dragged = False
        self._drag_suspended_panel = None
        self._worker = None
        self._speech_worker = None
        # True whenever the most recent reply (from either the circle's own
        # voice/typed flow, or a chat message relayed in from the IDE's
        # right-side panel) ended in an error, so STATE_RESPONDING can be
        # painted red instead of green -- and so _push_ide_status() can
        # tell the IDE which color its own border glow should be.
        self._last_was_error = False

        self._anim = QPropertyAnimation(self, b"scale")
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutBack)

        self._pulse_phase = 0.0
        self._listen_elapsed_ms = 0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._tick_animation)

        # Safety-net: guarantees we move on from Responding even if speech
        # fails/hangs/is disabled (normally _on_speech_finished beats it).
        self._transition_timer = QTimer(self)
        self._transition_timer.setSingleShot(True)
        self._transition_timer.timeout.connect(self._finish_responding)

        # While the typed text box is open, closes it back to idle if the
        # user doesn't type anything for a few seconds. Restarted on every
        # keystroke, and every time the text box is (re-)opened.
        self._inactivity_timer = QTimer(self)
        self._inactivity_timer.setSingleShot(True)
        self._inactivity_timer.timeout.connect(self._on_listen_inactivity)

        self.input_popup = InputPopup()
        self.input_popup.submitted.connect(self._on_query_submitted)
        self.input_popup.cancelled.connect(lambda: self._enter_state(STATE_IDLE))
        self.input_popup.edit.textChanged.connect(self._on_listen_activity)

        # Ctrl+K instant panel launcher -- same zero-AI fast path as
        # the 'open <panel>' command recognizer and the right-click
        # quick-open menu (see _resolve_panel_open_command above).
        self.panel_launcher = PanelLauncherPopup()
        self.panel_launcher.opened.connect(self._open_panel_by_key)
        self._launcher_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._launcher_shortcut.activated.connect(self.show_panel_launcher)

        # -- voice input: wake word + speech-to-text --------------------
        self._voice_worker = None
        self._voice_worker_ok = False   # True once the mic has been confirmed usable
        self._capturing_voice_command = False  # True only during the post-ack listen window
        self._voice_ack_worker = None

        # -- voice cross-check confirmation (see _needs_confirmation) ----
        # Long or action-sounding spoken requests get summarized back for
        # a quick yes/no before Friday actually acts on them, to catch
        # speech-to-text mistakes before they become the wrong file edit
        # or command. _awaiting_confirmation is True only during the
        # follow-up listen window right after Friday asks "...is that right?";
        # _pending_confirmation_text holds the original (verbatim, not
        # summarized) request that runs for real once confirmed.
        self._awaiting_confirmation = False
        self._pending_confirmation_text = None
        # 2-minute unanswered-pause autonomy rule -- see the
        # _PendingDecisionTracker/_extract_await_decision block above.
        self._decision_tracker = _PendingDecisionTracker(
            self._auto_continue_pending_decision)
        # "Lost internet mid-task, retry once it's back" -- see
        # _ConnectivityRetryTracker above. Independent of the IDE chat
        # panel's own instance (_ide_chat_connectivity_retry) -- each
        # conversation surface tracks its own stalled request.
        self._connectivity_retry = _ConnectivityRetryTracker(
            self._retry_after_connectivity)
        # Queued proactive notifications about some OTHER minimized
        # panel's status changing (paused for a decision / done / hit an
        # error) -- see announce_panel_event/_flush_panel_announcements.
        self._pending_panel_announcements = []
        self._confirm_worker = None

        self._command_timeout_timer = QTimer(self)
        self._command_timeout_timer.setSingleShot(True)
        self._command_timeout_timer.timeout.connect(self._on_command_capture_timeout)

        # Voice chat (STT/mic listening) is OFF by default: Friday runs
        # text-to-text until the user explicitly asks for voice -- here,
        # that ask is a long-press on the circle (see mousePressEvent).
        # The gate lazily builds the VoiceListenWorker only on first
        # enable, so no mic is ever opened at startup.
        self._voice_gate = VoiceChatGate(
            self._voice_driver_factory,
            announce=self.announce_panel_event,
            on_changed=self._on_voice_chat_changed)
        self._long_press_fired = False
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.timeout.connect(self._on_long_press)

        self._pill_reposition_timer = QTimer(self)
        self._pill_reposition_timer.setSingleShot(True)
        self._pill_reposition_timer.setInterval(24)
        self._pill_reposition_timer.timeout.connect(
            agent_tools.reposition_minimized_pills)
        self._drag_panel_restore_timer = QTimer(self)
        self._drag_panel_restore_timer.setSingleShot(True)
        self._drag_panel_restore_timer.setInterval(30)
        self._drag_panel_restore_timer.timeout.connect(
            self._restore_drag_suspended_panel)

        if 0:  # was: if ENABLE_VOICE_MODE -- worker now starts on demand
            pass

        app_ = QApplication.instance()
        if app_ is not None:
            app_.aboutToQuit.connect(self._shutdown_voice_worker)

        self._position_top_right()
        self.setMouseTracking(True)

    # -- voice chat gate (STT on demand only) ------------------------------
    def _voice_driver_factory(self):
        """Called by the gate ONLY when the user explicitly turns voice
        chat on -- the mic is otherwise never touched."""
        if not ENABLE_VOICE_MODE:
            return {"ok": False,
                    "reason": ("voice input isn't available in this "
                               "environment (Needs SpeechRecognition, "
                               "PyAudio and faster-whisper)")}
        if self._voice_worker is not None:
            return {"ok": True, "worker": self._voice_worker}
        try:
            worker = VoiceListenWorker(self)
            worker.heard.connect(self._on_voice_heard)
            worker.mic_unavailable.connect(self._on_mic_unavailable)
            self._voice_worker = worker
            self._voice_worker_ok = True
            return {"ok": True, "worker": worker}
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "reason": str(ex)}

    def _on_voice_chat_changed(self, enabled):
        """Gate-compatible UI hook: voice-listening flags get reset when
        the mic powers down mid-capture."""
        if not enabled:
            self._capturing_voice_command = False
            self._awaiting_confirmation = False
            self._pending_confirmation_text = None
            self._command_timeout_timer.stop()

    def voice_chat_enabled(self):
        """Public query used by settings/handlers/tests."""
        return self._voice_gate.enabled

    def set_voice_chat_enabled(self, enabled):
        """Programmatic path mirroring the long-press toggle."""
        return self._voice_gate.set_enabled(enabled)

    def _on_long_press(self):
        """Held-down long enough: this press is the voice-chat toggle,
        not a click."""
        self._long_press_fired = True
        self._voice_gate.toggle()

    def _shutdown_voice_worker(self):
        # Stops the background mic thread cleanly on quit instead of
        # letting Qt tear it down mid-listen.
        if self._voice_worker is not None:
            self._voice_worker.stop()
            self._voice_worker.resume()  # in case it was paused, so the loop can see _stop_requested
            self._voice_worker.wait(2000)

    # -- positioning -------------------------------------------------
    def _position_top_right(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        bounds = screen.availableGeometry()
        x = bounds.right() - self.width() - MARGIN_RIGHT
        y = bounds.top() + MARGIN_TOP
        self.move(x, y)

    def _available_screen_geometry(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _snap_to_nearest_corner(self):
        bounds = self._available_screen_geometry()
        if bounds is None:
            return
        side = "left" if self.geometry().center().x() < bounds.center().x() else "right"
        x = (bounds.left() + MARGIN_RIGHT if side == "left" else
             bounds.right() - self.width() - MARGIN_RIGHT)
        y = bounds.top() + MARGIN_TOP
        self.move(x, y)

    def _restore_drag_suspended_panel(self):
        key = self._drag_suspended_panel
        if key is None:
            return
        if agent_tools.restore_circle_drag_panel(key):
            self._drag_suspended_panel = None
        else:
            self._drag_panel_restore_timer.start()

    def moveEvent(self, event):
        if hasattr(self, "_pill_reposition_timer"):
            self._pill_reposition_timer.start()
        super().moveEvent(event)

    def _physical_pixel_ratio(self):
        """Best-effort devicePixelRatio for whichever screen this circle
        is currently on — used by get_circle_rect() below to convert its
        own logical-pixel geometry into the physical-pixel space ide.py's
        Tk window geometry calls use (see _declare_dpi_awareness).
        Returns 1.0 (a no-op) on anything that fails, which is also
        simply the correct value on Linux/macOS."""
        try:
            screen = self.screen() if hasattr(self, "screen") else None
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            if screen is not None:
                dpr = screen.devicePixelRatio()
                if dpr and dpr > 0:
                    return dpr
        except Exception:  # noqa: BLE001
            pass
        return 1.0

    def get_circle_rect(self):
        """(center_x, center_y, diameter) of this circle on screen right
        now, in *physical* screen pixels — the space ide.py's own Tk
        window geometry calls use, not Qt's own logical pixels (see
        _physical_pixel_ratio). Used purely cosmetically by
        agent_tools._AgentHost.get_circle_rect() to give the IDE's
        open/close "pop" animation (ide.py's _play_open_animation/
        _play_close_animation) a point to grow out of / shrink back
        into. None if, for any reason, a sane rect can't be worked out —
        the animation just plays as a plain fade instead, Agent Mode
        itself is entirely unaffected either way."""
        try:
            dpr = self._physical_pixel_ratio()
            g = self.geometry()
            cx = (g.x() + g.width() / 2.0) * dpr
            cy = (g.y() + g.height() / 2.0) * dpr
            diameter = max(g.width(), g.height()) * dpr
            return (cx, cy, diameter)
        except Exception:  # noqa: BLE001
            return None

    # -- animated "scale" property ------------------------------------
    def getScale(self):
        return self._scale

    def setScale(self, value):
        self._scale = value
        self.update()

    scale = Property(float, getScale, setScale)

    # -- animation tick (waveform / spinner / equalizer) -----------------
    def _tick_animation(self):
        self._pulse_phase += 0.18
        if self._pulse_phase > 2 * math.pi:
            self._pulse_phase -= 2 * math.pi
        if self._state == STATE_LISTENING:
            self._listen_elapsed_ms = min(
                self._listen_elapsed_ms + self._anim_timer.interval(), LISTEN_RAMP_MS
            )
        self.update()

    # -- state machine -----------------------------------------------------
    def _enter_state(self, state, show_input=True):
        self._state = state
        self._transition_timer.stop()
        self._inactivity_timer.stop()
        self._command_timeout_timer.stop()

        if state == STATE_IDLE:
            target_scale = 1.0
            self._anim_timer.stop()
            # Unless a panel is still mid-task: the idle working ring
            # keeps spinning until the task actually completes (or
            # a circle interaction temporarily supersedes it).
            if self._panels_working:
                self._anim_timer.start()
            self.input_popup.hide()
            self._capturing_voice_command = False
            if self._voice_worker is not None:
                self._voice_worker.set_command_capture(False)
            # Any pending cross-check confirmation is abandoned the moment
            # we land back in Idle for any reason (cancel click, timeout,
            # inactivity) — never let a stale "yes/no" expectation survive
            # into an unrelated future interaction.
            self._awaiting_confirmation = False
            self._pending_confirmation_text = None
            # Idle is the default "ready" state now — the mic is quietly
            # listening in the background for the wake word the whole time.
            if (self._voice_worker is not None and self._voice_worker_ok
                    and self._voice_gate.enabled):
                self._voice_worker.resume()
            # Flush any proactive panel-status announcement that was
            # queued while Friday was busy (see announce_panel_event) --
            # deferred to right after this method returns (QTimer(0))
            # rather than called straight from inside _enter_state
            # itself, since _flush_panel_announcements ultimately calls
            # _show_response, which calls _enter_state again -- calling
            # back into this same method while it's still running would
            # re-enter it instead of starting a clean new call.
            if self._pending_panel_announcements:
                QTimer.singleShot(0, self._flush_panel_announcements)

        elif state == STATE_LISTENING:
            target_scale = ACTIVE_SCALE
            self._pulse_phase = 0.0
            self._listen_elapsed_ms = 0
            self._anim_timer.start()
            if show_input:
                # Typed text box (manual click, or the "bring up text chat"
                # voice command) — mic stays paused while you type.
                self.input_popup.show_near(self.geometry())
                self._inactivity_timer.start(LISTEN_INACTIVITY_MS)
                if self._voice_worker is not None:
                    self._voice_worker.pause()
            else:
                # Waiting for a spoken command right after the wake-word
                # acknowledgment — same waveform visual, no text box.
                self.input_popup.hide()

        elif state == STATE_LOADING:
            target_scale = 1.0
            self._anim_timer.start()
            self.input_popup.hide()
            if self._voice_worker is not None:
                self._voice_worker.pause()

        elif state == STATE_RESPONDING:
            target_scale = ACTIVE_SCALE
            self._anim_timer.start()
            # Mic stays paused through the whole reply so it never hears
            # (and reacts to) Friday's own voice coming out of the speakers.
            if self._voice_worker is not None:
                self._voice_worker.pause()

        self._anim.stop()
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(target_scale)
        self._anim.start()
        self.update()
        self._push_ide_status()

    def _push_ide_status(self):
        """Push this state into the IDE and active app-panel borders.
        self._state/_last_was_error are already current by the time
        _enter_state calls this.

        App-panel updates are posted to their Tk host without waiting;
        the IDE update is separately marshaled to its Tk thread. This
        method can run on Qt's main thread or another thread, so neither
        path touches a Tk widget directly."""
        state, is_error = self._state, bool(self._last_was_error)
        agent_tools.report_friday_status(state, is_error)
        app = agent_tools._ide_app
        if app is None:
            return

        def _push():
            try:
                app.set_friday_status(state, is_error)
            except Exception:  # noqa: BLE001
                pass

        try:
            agent_tools._post_on_main(_push)
        except Exception:  # noqa: BLE001
            pass

    def toggle(self):
        if self._state == STATE_IDLE:
            self._enter_state(STATE_LISTENING)  # manual click always opens the typed box
        elif self._state == STATE_RESPONDING:
            # Interrupt whatever Friday is saying right now and drop
            # straight into actively listening for the next request --
            # no need to repeat the wake word right after cutting it off.
            self._stop_speaking()
            self._enter_state(STATE_LISTENING, show_input=False)
            self._begin_command_capture()
        else:
            # Clicking again at any point cancels back to idle -- this
            # also covers the brief Loading window where a reply has
            # arrived and speech is already being synthesized/started but
            # hasn't audibly begun yet (see _show_response), so it can't
            # keep talking a moment later after being "cancelled".
            if self._worker is not None:
                self._worker = None  # let any in-flight call finish silently
            self._stop_speaking()
            self._enter_state(STATE_IDLE)

    def _stop_speaking(self):
        """Immediately cuts off whatever Friday is currently saying (or about
        to say). Best-effort: whichever TTS backend is actually active
        gets asked to stop right away -- see SpeechWorker.request_stop().
        Never blocks: the state machine moves on the instant this
        returns, regardless of whether the underlying audio actually
        managed to stop immediately. Safe to call even when nothing is
        speaking (e.g. plain cancel-to-idle clicks) -- it's just a no-op
        then."""
        worker = self._speech_worker
        self._speech_worker = None
        if worker is not None:
            try:
                worker.request_stop()
            except Exception:  # noqa: BLE001
                pass

    def _on_listen_activity(self, _text=None):
        # Any keystroke in the text box resets the inactivity countdown.
        if self._state == STATE_LISTENING:
            self._inactivity_timer.start(LISTEN_INACTIVITY_MS)

    def _on_listen_inactivity(self):
        if self._state == STATE_LISTENING:
            self._enter_state(STATE_IDLE)

    # -- voice input: wake word + speech-to-text ---------------------------
    def _on_voice_heard(self, text):
        """Fires for every phrase VoiceListenWorker understands, any time
        it isn't paused. Only acts on it if we're idle/background-listening
        (the wake word) or specifically waiting for the command right after
        a wake-word acknowledgment — otherwise (mid-reply, typing, etc.) it
        shouldn't be possible to get here since the mic is paused, but the
        state check is kept as a defensive no-op just in case."""
        if self._capturing_voice_command:
            self._capturing_voice_command = False
            self._command_timeout_timer.stop()
            if self._voice_worker is not None:
                self._voice_worker.set_command_capture(False)
            if self._awaiting_confirmation:
                self._on_confirmation_reply(text)
            else:
                self._handle_voice_command(text)
            return

        if self._state != STATE_IDLE:
            return  # busy with something else — ignore stray mic input

        hit, trailing = _extract_wake_command(text)
        if not hit:
            return  # not the wake word — keep quietly listening in the background

        if self._voice_worker is not None:
            self._voice_worker.pause()

        if trailing:
            # Said the whole request in one breath, e.g. "Friday, what's the
            # weather?" — skip the acknowledgment and act immediately.
            self._handle_voice_command(trailing)
        else:
            self._speak_wake_ack_then_capture()

    def _speak_wake_ack_then_capture(self):
        self._enter_state(STATE_LISTENING, show_input=False)
        ack = random.choice(WAKE_ACK_PHRASES)
        self._voice_ack_worker = SpeechWorker(ack, parent=self)
        self._voice_ack_worker.finished_speaking.connect(self._begin_command_capture)
        self._voice_ack_worker.finished.connect(self._voice_ack_worker.deleteLater)
        self._voice_ack_worker.start()

    def _begin_command_capture(self):
        if self._state != STATE_LISTENING:
            return  # cancelled (e.g. clicked away) while the ack was playing
        self._capturing_voice_command = True
        self._command_timeout_timer.start(COMMAND_CAPTURE_TIMEOUT_MS)
        if self._voice_worker is not None:
            self._voice_worker.set_command_capture(True)
            self._voice_worker.resume()

    def _on_command_capture_timeout(self):
        # Waited long enough after the acknowledgment and heard nothing
        # usable — quietly go back to background wake-word listening rather
        # than leaving the waveform up indefinitely.
        if self._capturing_voice_command:
            self._capturing_voice_command = False
            if self._voice_worker is not None:
                self._voice_worker.set_command_capture(False)
            self._enter_state(STATE_IDLE)

    def _handle_voice_command(self, text):
        normalized = _normalize_for_wake_check(text)
        if any(phrase in normalized for phrase in TEXT_CHAT_TRIGGER_PHRASES):
            self._enter_state(STATE_LISTENING)  # typed box (show_input defaults True)
            return
        if not text.strip():
            self._enter_state(STATE_IDLE)
            return
        if _needs_confirmation(text):
            self._start_confirmation(text)
            return
        self._on_query_submitted(text)

    # -- voice cross-check confirmation -------------------------------------
    def _start_confirmation(self, text):
        """Kicks off the summary call for a long/action-sounding spoken
        request (see _needs_confirmation) instead of acting on it right
        away. `text` is kept verbatim in _pending_confirmation_text and is
        what actually runs, unmodified, once the user confirms -- the
        summary is only ever used for the spoken question back to them."""
        self._pending_confirmation_text = text
        self._enter_state(STATE_LOADING)
        self._confirm_worker = AIWorker(text, parent=self, summarize_only=True)
        self._confirm_worker.succeeded.connect(self._on_confirmation_summary_ok)
        self._confirm_worker.failed.connect(self._on_confirmation_summary_err)
        self._confirm_worker.finished.connect(self._confirm_worker.deleteLater)
        self._confirm_worker.start()

    def _on_confirmation_summary_ok(self, summary_text):
        if self._state != STATE_LOADING:
            return  # cancelled while the summary call was in flight
        if not summary_text or not summary_text.strip():
            # Nothing usable came back for the question itself -- don't
            # leave the user waiting on a confirmation that was never
            # actually asked; just run the request for real instead.
            pending = self._pending_confirmation_text
            self._pending_confirmation_text = None
            if pending:
                self._on_query_submitted(pending)
            else:
                self._enter_state(STATE_IDLE)
            return
        self._awaiting_confirmation = True
        self._last_was_error = False
        # Logged as its own real exchange -- a genuine question Friday
        # asked out loud -- even though the pending action itself (the
        # original verbatim request) gets logged separately, as its own
        # normal turn, once it actually runs for real (see
        # _on_confirmation_reply below / _on_reply_ok above). Without
        # this, the cross-check question-and-answer dance would be a
        # silent gap in "every conversation."
        memory.remember_recent_turn("friday", summary_text)
        memory.log_turn_async("friday", summary_text)
        # Reuses the normal speak-then-listen-for-a-follow-up machinery
        # (_show_response -> ... -> _finish_responding -> LISTENING +
        # _begin_command_capture) unchanged -- the only difference is what
        # happens to whatever gets captured next, handled by the
        # _awaiting_confirmation check in _on_voice_heard above.
        self._show_response(summary_text, speak=True)

    def _on_confirmation_summary_err(self, _message):
        # The summary call itself failed for a technical reason (provider
        # hiccup, no key, etc.) -- rather than making the user repeat their
        # whole request, just run it for real without the cross-check this
        # one time, same graceful-degradation spirit as every other
        # fallback in this file.
        if self._state != STATE_LOADING:
            return
        pending = self._pending_confirmation_text
        self._pending_confirmation_text = None
        if pending:
            self._on_query_submitted(pending)
        else:
            self._enter_state(STATE_IDLE)

    def _on_confirmation_reply(self, text):
        """Fires once for whatever's captured right after Friday asks its
        cross-check question. Confirmed -> the original verbatim request
        runs for real (full tool access, same as any other command);
        anything else (plain "no", a correction, an unrelated reply) ->
        asked to just restate it fresh, no need to repeat the wake word."""
        self._awaiting_confirmation = False
        original = self._pending_confirmation_text
        self._pending_confirmation_text = None
        # The user's actual spoken reply to the cross-check question --
        # logged here regardless of which branch runs next, same
        # "complete the dialogue" reasoning as the question itself above.
        if text:
            memory.remember_recent_turn("user", text)
            memory.log_turn_async("user", text)
        if original and _is_affirmative(text):
            self._on_query_submitted(original)
        else:
            self._last_was_error = False
            reply = "My mistake — go ahead and say that again."
            memory.remember_recent_turn("friday", reply)
            memory.log_turn_async("friday", reply)
            self._show_response(reply, speak=True)

    def _on_mic_unavailable(self, reason):
        # `reason` is already a complete, friendly sentence from
        # VoiceListenWorker (e.g. "...voice mode is disabled; click the
        # circle to type instead."), so it's shown/spoken as-is.
        self._voice_worker_ok = False
        self._show_response(reason, speak=True)

    def _auto_continue_pending_decision(self, question):
        """Fires when self._decision_tracker's 2-minute no-reply timeout
        elapses (see the _PendingDecisionTracker block near
        _on_qt_main_thread) -- nobody answered Friday's last
        pause-for-a-major-decision question. Per personality.md's Agent
        Mode section, re-enters the normal query pipeline with a
        synthetic system-level nudge standing in for the user's own next
        message, so the usual AIWorker -> _show_response -> memory flow
        handles the follow-through -- spoken aloud exactly like any
        other reply.
        """
        if self._state != STATE_IDLE:
            # Something else is already happening on this surface (a new
            # real command, mid-reply, etc.) -- don't barge in; whatever
            # that is already superseded the stale pause.
            return
        synthetic = (
            "(System note, not from the user: about two minutes have "
            "passed with no reply to your last question — \"%s\". No one "
            "is coming back to answer it right now. Per the 2-minute "
            "unanswered-pause rule, decide the most professional, "
            "reasonable option yourself, say plainly that you're "
            "proceeding without a reply and why, and then actually carry "
            "out the pending work.)" % question
        )
        self._on_query_submitted(synthetic, is_auto_continue=True)

    def _retry_after_connectivity(self, original_text):
        """Fires when self._connectivity_retry detects the internet is
        back after the circle's own query failed with
        CONNECTIVITY_ERROR_TAG (see _on_reply_err below). Re-submits the
        exact original query -- it never actually ran the first time, so
        this is a genuine retry, not a continuation -- through the
        normal pipeline, so it ends up spoken/remembered exactly like
        any other reply once it succeeds.
        """
        if self._state != STATE_IDLE:
            return  # something else already superseded the stalled request
        self._on_query_submitted(original_text, is_auto_continue=True)

    def _on_panel_status(self, key, status):
        """agent_tools register_panel_status_listener hook: keep the set
        of panels currently mid-task and the idle working-ring spinner
        in sync. Never raises into agent_tools (it iterates listeners
        inside try/except anyway)."""
        if status in agent_tools.PANEL_WORKING_STATUSES:
            self._panels_working.add(key)
        else:
            self._panels_working.discard(key)
        self._sync_working_ring()

    def _sync_working_ring(self):
        """The working ring needs the pulse timer to keep ticking while
        idle (unlike a steady state, the spinner is drawn from
        _pulse_phase). During any real circle interaction its own
        animation supersedes and the spinner isn't drawn at all -- the
        panel keeps working, the ring pauses visually, and resumes the
        moment Friday is idle again."""
        if (self._state == STATE_IDLE and self._panels_working
                and not self._anim_timer.isActive()):
            self._anim_timer.start()
        elif (self._state == STATE_IDLE and not self._panels_working
                and self._anim_timer.isActive()):
            self._anim_timer.stop()  # consistent with a normal idle stop
        self.update()

    def _on_panel_restored(self, key):
        """A tracked panel just became visible again: if Friday finished
        work there while it was minimized, speak the FULL report now (the
        short 'Sir, the IDE is done...' cue already fired when the work
        completed). The panel itself still displays its own reply, so the
        report lands in both places, per the agreed design."""
        text = agent_tools.take_panel_report(key)
        if not text:
            return
        label = "IDE" if key == "ide" else str(key).title()
        # Voice has a practical upper bound; the full text stays on the
        # panel for anything longer.
        spoken = text if len(text) <= 1200 else text[:1200].rsplit(" ", 1)[0] + " …(full report on the panel)"
        self.announce_panel_event(
            f"Reporting what was done in the {label}, sir. {spoken}")

    def announce_panel_event(self, text):
        """Best-effort proactive notification that some OTHER minimized
        panel's status just changed -- e.g. "Sir, the IDE needs your
        permission before proceeding." or "The IDE is done with its
        task, sir." Only ever worth calling for a panel that's actually
        minimized right now (see agent_tools.is_panel_minimized): the
        whole premise is that its real window isn't on screen, so there
        is nothing else to cue the user that something happened.

        Unlike _ide_speak_label's purely cosmetic hover announcement
        (silently dropped whenever Friday isn't idle, never queued),
        these are substantive enough to be worth not losing: queued in
        self._pending_panel_announcements and spoken either right away
        (if idle right now) or the next time _enter_state naturally
        lands back in Idle (see there), oldest first.
        """
        if not text:
            return
        if (self._pending_panel_announcements
                and self._pending_panel_announcements[-1] == text):
            return  # the exact same announcement is already queued
        self._pending_panel_announcements.append(text)
        if self._state == STATE_IDLE:
            self._flush_panel_announcements()

    def _flush_panel_announcements(self):
        """Speaks the oldest still-pending panel-status announcement, if
        Friday is actually idle right now (never interrupts a live
        voice/typed exchange or a reply already in flight) and there is
        one -- see announce_panel_event. More than one queued
        announcement drains naturally over time: _finish_responding's
        own path back through _enter_state(STATE_IDLE) calls this again
        once each one finishes.
        """
        if self._state != STATE_IDLE or not self._pending_panel_announcements:
            return
        text = self._pending_panel_announcements.pop(0)
        self._last_was_error = False
        self._show_response(text, speak=True)
        memory.remember_recent_turn("friday", text)
        memory.log_turn_async("friday", text)

    # -- talking to Gemini ---------------------------------------------------
    def _on_query_submitted(self, text, is_auto_continue=False):
        if not is_auto_continue:
            # A real message just arrived on this surface -- any
            # previously-armed 2-minute pause timer no longer needs to
            # fire; this message (whether or not it actually answers the
            # pending question) is handled the normal way below instead.
            self._decision_tracker.cancel()
            # Same for a stalled connectivity retry -- the user sending
            # a new request supersedes whatever previously failed.
            self._connectivity_retry.cancel()
        self.input_popup.hide()
        if not is_auto_continue:
            # Zero-AI fast path: a bare "open <panel>" request opens
            # the panel locally RIGHT NOW -- no worker, no model call,
            # no seconds of latency for what is a purely local UI
            # action. Anything else falls through to AIWorker below
            # exactly as before.
            if self._open_panel_instant_if_recognized(text) is not None:
                return
        self._enter_state(STATE_LOADING)

        # Remembered so _on_reply_ok below can log *both* sides of this
        # exchange to the conversation log once the reply actually comes
        # back -- AIWorker's own succeeded signal only carries the reply
        # text, not the original query.
        self._last_query_text = text

        self._worker = AIWorker(text, parent=self)
        self._worker.succeeded.connect(self._on_reply_ok)
        self._worker.failed.connect(self._on_reply_err)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_reply_ok(self, text):
        if self._state != STATE_LOADING:
            return  # user cancelled while we were waiting
        self._last_was_error = False
        # 2-minute unanswered-pause autonomy rule: strip the control tag
        # before anything below ever speaks it, shows it, or feeds it
        # back into memory -- it's a pure signal, never literal content
        # (see _extract_await_decision's docstring) -- and remember
        # whether to arm this surface's own pause timer once the reply
        # has actually gone out.
        text, is_pending_decision = _extract_await_decision(text)
        # Defense-in-depth only -- the circle is voice/spoken, where
        # {{OPTIONS: ...}} (Task 65's IDE-chat-only Permission tag) has
        # no meaning; personality.md never tells the model to use it
        # here. Still run it and discard the parsed list so the raw tag
        # itself is stripped and never spoken aloud if a model ever
        # emits it on this surface anyway (see _extract_decision_options's
        # module comment).
        text, _unused_decision_options = _extract_decision_options(text)
        self._show_response(text, speak=True)
        # Memory logging below uses the cue-stripped text (speech_delivery.md's
        # bracket "stage directions" like [pause]/[sighs] removed), not the
        # raw reply handed to _show_response/SpeechWorker above -- those cues
        # only mean something as timing/volume instructions for speech, and
        # would otherwise sit as literal bracket noise in conversation
        # history that gets fed back to the model as future context.
        clean_text = _strip_cues_for_display(text) if text else text
        # Automatic half of memory (see memory.py's module docstring):
        # every successful exchange is logged + embedded in the
        # background, with no tool call needed, so Friday can recall it
        # later even if nothing in it was ever explicitly "remembered".
        # Fire-and-forget and started only *after* _show_response above
        # -- logging must never add latency to a reply the user is
        # already waiting on. Error replies are deliberately not logged
        # here (see _on_reply_err) -- nothing about a quota/auth error
        # message is worth recalling later.
        #
        # remember_recent_turn() runs first and synchronously (no
        # embedding, no disk I/O) -- it's what makes the *very next*
        # message, even one typed a second later, already able to see
        # this exchange, without waiting on log_turn_async()'s slower
        # background embedding call to actually land. See memory.py's
        # "Recent-turns buffer" section for the full story -- this is
        # what closes the "I just told you X and you forgot" gap.
        query = getattr(self, "_last_query_text", "")
        if query:
            memory.remember_recent_turn("user", query)
            memory.log_turn_async("user", query)
        if clean_text:
            memory.remember_recent_turn("friday", clean_text)
            memory.log_turn_async("friday", clean_text)
        if is_pending_decision:
            # Already on the Qt main thread here (see _on_reply_ok's own
            # callers -- AIWorker.succeeded is connected to this bound
            # method, which Qt auto-delivers via a queued connection
            # onto CircleOverlay's own thread), so arm() -- which starts
            # a real QTimer -- can be called directly, no
            # _on_qt_main_thread marshaling needed (unlike the IDE
            # chat's own on_ok, which runs on a plain background thread).
            self._decision_tracker.arm(clean_text or text)

    def _on_reply_err(self, message):
        if self._state != STATE_LOADING:
            return
        # `message` already comes with a friendly emoji/context prefix
        # from AIWorker (e.g. quota/auth/model errors from either provider).
        self._last_was_error = True
        self._show_response(message, speak=False)
        # "Lost internet mid-task, retry once it's back" -- only for a
        # genuine connectivity failure (see CONNECTIVITY_ERROR_TAG); a
        # bad API key or quota limit would just fail the same way again,
        # so those are left for the user to actually address instead of
        # being silently retried forever.
        if message and CONNECTIVITY_ERROR_TAG in message:
            query = getattr(self, "_last_query_text", "")
            if query:
                # Already on the Qt main thread here (see _on_reply_ok's
                # own comment just above for why) -- arm() can be called
                # directly, no _on_qt_main_thread marshaling needed.
                self._connectivity_retry.arm(query)

    def _show_response(self, text, speak):
        """Deliberately never displays `text` anywhere on screen: the
        reply is audible only (via the TTS call below), never shown as
        a readable bubble, by design -- nothing in this method renders
        `text` itself, it's only used to size the speech-safety timer
        below.

        When speech is going to play, this stays in Loading (the
        spinner) rather than jumping straight to Responding: Piper's
        one-time voice-model warm-up and its actual synthesis both take
        a visible beat, and switching to the "talking" animation before
        there's any sound to go with it looks like it's lying. Instead,
        _on_speech_started -- fired the instant the speech worker is
        actually about to produce audio -- is what flips it to
        Responding, keeping the animation in sync with the sound.
        """
        will_speak = speak and ENABLE_SPEECH and bool(text.strip())

        if not will_speak:
            # Nothing will be spoken (TTS off/unavailable/empty reply) —
            # no speech signals are coming, so just show Responding right
            # away, timed as a rough "reading time" instead.
            self._enter_state(STATE_RESPONDING)
            duration = max(MIN_RESPONSE_MS, min(
                MAX_RESPONSE_MS,
                len(_strip_cues_for_display(text)) * MS_PER_CHAR))
            self._transition_timer.start(duration)
            return

        # Speech is coming -- stay in Loading until it actually starts.
        # This timer is a generous fallback covering both "waiting for
        # speech to start" and (once it has) "waiting for it to finish",
        # in case TTS hangs or crashes without ever emitting its signals.
        self._enter_state(STATE_LOADING)
        self._transition_timer.start(MAX_SPEECH_SAFETY_MS)

        self._speech_worker = SpeechWorker(text, parent=self)
        self._speech_worker.started_speaking.connect(self._on_speech_started)
        self._speech_worker.finished_speaking.connect(self._on_speech_finished)
        self._speech_worker.finished.connect(self._speech_worker.deleteLater)
        self._speech_worker.start()

    def _on_speech_started(self):
        # The active speech worker is actually about to produce audio --
        # this is what flips the circle into its "talking" animation, in
        # sync with the sound instead of ahead of it.
        if self._state == STATE_LOADING and self._speech_worker is not None:
            self._enter_state(STATE_RESPONDING)
            # Restart the safety net now that real speaking has begun, in
            # case finished_speaking never arrives for some reason.
            self._transition_timer.start(MAX_SPEECH_SAFETY_MS)

    def _on_speech_finished(self):
        # As soon as speaking wraps up, move on right away instead of
        # waiting out the full safety-net timer.
        self._finish_responding()

    def _finish_responding(self):
        # The response bubble/equalizer stays up for as long as it's
        # speaking (or, before that, the spinner stays up while speech is
        # still starting -- see _show_response/_on_speech_started). Once
        # done (or the safety-net timer fires either way), move into a
        # brief actively-listening window instead of dropping straight
        # back to idle — same place clicking mid-response jumps to, and
        # the same mechanism the wake-word ack uses: no need to repeat the
        # wake word for a quick follow-up. If nothing comes in within
        # COMMAND_CAPTURE_TIMEOUT_MS, _on_command_capture_timeout() falls
        # back to Idle (background wake-word listening) on its own.
        if self._state in (STATE_RESPONDING, STATE_LOADING):
            self._enter_state(STATE_LISTENING, show_input=False)
            self._begin_command_capture()

    # -- painting ------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        diameter = BASE_DIAMETER * self._scale
        rect = QRectF(0, 0, diameter, diameter)
        rect.moveCenter(QRectF(0, 0, self.width(), self.height()).center())

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(FILL_COLOR))
        painter.drawEllipse(rect)

        if self._state == STATE_LOADING:
            self._draw_loading_ring(painter, rect)
        else:
            if self._state == STATE_RESPONDING:
                border = BORDER_COLOR_ERROR if self._last_was_error \
                    else BORDER_COLOR_RESPONDING
            elif self._state == STATE_LISTENING:
                border = BORDER_COLOR_LISTENING
            elif self._hover:
                border = BORDER_COLOR_HOVER
            else:
                border = BORDER_COLOR
            painter.setPen(QPen(border, BORDER_WIDTH))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(rect)

        if self._state == STATE_LISTENING:
            self._draw_listening_wave(painter, rect)
        elif self._state == STATE_RESPONDING:
            self._draw_equalizer(painter, rect)
        elif (self._state == STATE_IDLE and self._panels_working
                and not self._last_was_error):
            self._draw_working_ring(painter, rect)

    def _draw_listening_wave(self, painter, circle_rect):
        diameter = circle_rect.width()
        cx = circle_rect.center().x()
        cy = circle_rect.center().y()

        amplitude_ratio = self._listen_elapsed_ms / LISTEN_RAMP_MS
        line_width = diameter * 0.62
        max_amp = diameter * 0.17 * amplitude_ratio
        stroke_width = max(1.6, diameter * 0.045)

        num_points = 28
        path = QPainterPath()
        for i in range(num_points + 1):
            t = i / num_points
            x = cx - line_width / 2 + t * line_width
            envelope = math.sin(math.pi * t)
            y = cy + max_amp * envelope * math.sin(6.0 * t + self._pulse_phase * 2.2)
            point = QPointF(x, y)
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)

        pen = QPen(ICON_COLOR, stroke_width)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def _draw_working_ring(self, painter, circle_rect):
        """Same spinner as loading, orange: at least one panel is
        mid-task right now (distinct from the circle's own reply flow,
        which owns the loading/listening/responding visuals)."""
        pen = QPen(BORDER_COLOR_WORKING, BORDER_WIDTH)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        rotation_deg = (self._pulse_phase * (180.0 / math.pi) * 3.0) % 360.0
        start_angle = int(-rotation_deg * 16)
        painter.drawArc(circle_rect, start_angle, 270 * 16)

    def _draw_loading_ring(self, painter, circle_rect):
        pen = QPen(BORDER_COLOR_LOADING, BORDER_WIDTH)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        rotation_deg = (self._pulse_phase * (180.0 / math.pi) * 3.0) % 360.0
        start_angle = int(-rotation_deg * 16)
        span_angle = 270 * 16
        painter.drawArc(circle_rect, start_angle, span_angle)

    def _draw_equalizer(self, painter, circle_rect):
        diameter = circle_rect.width()
        cx = circle_rect.center().x()
        cy = circle_rect.center().y()

        bar_width = diameter * 0.09
        gap = diameter * 0.035
        max_height = diameter * 0.60

        total_width = WAVE_BAR_COUNT * bar_width + (WAVE_BAR_COUNT - 1) * gap
        start_x = cx - total_width / 2

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(ICON_COLOR))

        for i, env in enumerate(WAVE_ENVELOPE):
            wobble = (abs(math.sin(self._pulse_phase * 1.6 + i * 0.9)) * 0.5) + 0.5
            bar_h = max(bar_width, env * max_height * wobble)

            bar_rect = QRectF(
                start_x + i * (bar_width + gap),
                cy - bar_h / 2,
                bar_width,
                bar_h,
            )
            painter.drawRoundedRect(bar_rect, bar_width / 2, bar_width / 2)

    # -- hover state -----------------------------------------------------
    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    # -- click to toggle state / drag to move -----------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_global_pos = event.globalPosition().toPoint()
            self._dragged = False
            # potential long-press (hold to toggle voice chat) -- canceled
            # by mouse movement (drag) or an early release (normal click)
            self._long_press_fired = False
            self._long_press_timer.start(LONG_PRESS_MS)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            current = event.globalPosition().toPoint()
            if (not self._dragged and
                    (current - self._press_global_pos).manhattanLength() > 4):
                self._dragged = True
                self._long_press_timer.stop()  # gesture became a drag
                self._drag_panel_restore_timer.stop()
                if self._drag_suspended_panel is None:
                    self._drag_suspended_panel = (
                        agent_tools.minimize_active_panel_for_circle_drag())
            if self._dragged:
                pos = current - self._drag_offset
                bounds = self._available_screen_geometry()
                if bounds is not None:
                    max_x = max(bounds.left(), bounds.right() - self.width() + 1)
                    max_y = max(bounds.top(), bounds.bottom() - self.height() + 1)
                    pos.setX(max(bounds.left(), min(pos.x(), max_x)))
                    pos.setY(max(bounds.top(), min(pos.y(), max_y)))
                self.move(pos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._long_press_timer.stop()
            if self._long_press_fired:
                # that press already toggled voice chat at the timeout --
                # it must not also open the input popup on release
                self._long_press_fired = False
            elif not self._dragged:
                self.toggle()
            if self._dragged:
                self._snap_to_nearest_corner()
                if self._drag_suspended_panel is not None:
                    self._drag_panel_restore_timer.start()
            self._drag_offset = None
            self._press_global_pos = None
            self._dragged = False

    def _open_panel_instant_if_recognized(self, text, speak=True):
        """Zero-AI "open <panel>" path (see _resolve_panel_open_command):
        opens the panel right here -- no AIWorker, no model round trip --
        and mirrors _on_reply_ok's own memory bookkeeping so the exchange
        is recallable next turn exactly like a model-answered one would
        be. Returns the open result message, or None when `text` isn't a
        recognized bare panel request (caller then runs the AI path)."""
        key = _resolve_panel_open_command(text)
        if not key:
            return None
        msg = agent_tools.open_app(key)
        self._last_query_text = text
        self._show_response(msg or "", speak=speak)
        memory.remember_recent_turn("user", text)
        memory.log_turn_async("user", text)
        clean = _strip_cues_for_display(msg) if msg else msg
        if clean:
            memory.remember_recent_turn("friday", clean)
            memory.log_turn_async("friday", clean)
        return msg

    def _open_panel_by_key(self, key):
        """Point-and-click half of the instant-open path (right-click
        menu, Ctrl+K launcher): open it, and don't speak or narrate
        anything -- the panel popping up IS the feedback."""
        agent_tools.open_app(key)

    def show_panel_launcher(self):
        self.panel_launcher.show_near(self.geometry())

    # -- right-click menu --------------------------------------------------
    def _build_context_menu(self):
        menu = QMenu(self)
        open_menu = menu.addMenu("Open panel (instant -- no AI)")
        for key, meta in app_panels.KNOWN_APPS.items():
            act = open_menu.addAction(meta["label"])
            act.triggered.connect(lambda _=False, k=key: self._open_panel_by_key(k))
        menu.addSeparator()
        launcher_action = QAction("Quick launcher   (Ctrl+K)", self)
        launcher_action.triggered.connect(self.show_panel_launcher)
        menu.addAction(launcher_action)
        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)
        return menu

    def contextMenuEvent(self, event):
        self._build_context_menu().exec(event.globalPos())


# How long the circle must be HELD (not clicked, not dragged) before
# the press counts as the voice-chat toggle gesture -- long enough to
# be deliberate, short enough not to feel sluggish.
LONG_PRESS_MS = 900


def _declare_dpi_awareness():
    """Windows only: explicitly request Per-Monitor-V2 DPI awareness for
    this process, the same way ide/ide.py does, and for the same reason —
    Win32 APIs return coordinates *virtualized* (i.e. silently rescaled)
    to whatever DPI the *calling* process claims to be aware of, not the
    real physical pixels, unless the caller has actually opted in.
    Without this, on any display with Windows scaling other than 100%
    CircleOverlay.get_circle_rect() (which converts its own logical
    on-screen position into the physical-pixel space ide.py's Tk window
    geometry calls use, for the open/close pop animation's origin point)
    would drift or land in the wrong place entirely — this must run
    before QApplication() so Qt doesn't get to decide DPI awareness for
    us first."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        except (AttributeError, OSError):
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
            except (AttributeError, OSError):
                ctypes.windll.user32.SetProcessDPIAware()  # Vista+ fallback
    except Exception:  # noqa: BLE001
        pass  # never let a platform quirk here block startup


def _hide_console_window():
    """Hide the console attached to python.exe without hiding Qt windows."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_console_window = kernel32.GetConsoleWindow
    get_console_window.restype = wintypes.HWND
    console = get_console_window()
    if console:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        show_window = user32.ShowWindow
        show_window.argtypes = (wintypes.HWND, ctypes.c_int)
        show_window.restype = wintypes.BOOL
        show_window(console, 0)


def _force_close_ide():
    """Wired to QApplication.aboutToQuit: whatever is quitting Friday --
    the right-click Quit action, Ctrl+C, a hard process kill via the
    task manager, anything -- takes the IDE down with it too, since it's
    one of Friday's own windows now and not a separate process that could
    be left orphaned behind. No panel Friday ever creates should be able to
    outlive Friday itself.

    The IDE's Tk root now lives on its own dedicated thread (see
    agent_tools._TkThreadBridge) rather than sharing this one, so it
    can't be destroyed directly from here -- that has to be marshaled
    onto its own thread via Tk's thread-safe .after(0, ...) scheduling,
    same as every other cross-thread call into the IDE. Bounded wait so
    a wedged IDE thread can never hang Friday's own shutdown.
    """
    if not agent_tools._ide_apps:
        return
    try:
        # Fire-and-forget on the Tk-owning thread; close the full workspace
        # group before Friday's Qt process exits.
        agent_tools._post_on_main(agent_tools._close_workspace_group)
        deadline = time.time() + 2.0
        while time.time() < deadline and agent_tools._ide_apps:
            time.sleep(0.05)
    except Exception:  # noqa: BLE001
        pass


def main():
    _declare_dpi_awareness()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    # Built here, on the real Qt GUI thread, before anything else can
    # possibly need it -- see _on_qt_main_thread()'s docstring. Must be
    # constructed on this thread specifically (a QObject's thread
    # affinity is whichever thread creates it); it then lives for the
    # rest of the process.
    global _main_thread_invoker
    _main_thread_invoker = _MainThreadInvoker()

    overlay = CircleOverlay()

    # Lets the IDE's embedded "Friday AI Chat" panel (shown only while
    # agent-controlled) talk to the exact same AI brain this circle
    # uses. agent_tools' own IDE-lifecycle tools reach across onto
    # whichever thread the IDE's Tk mainloop is running on by
    # themselves now (see agent_mode_on()/_TkThreadBridge) -- nothing
    # to wire up here for that any more.
    agent_tools.set_chat_handler(_ide_chat_ask)

    # Lets the IDE's open/close "pop" animation (ide.py's
    # _play_open_animation/_play_close_animation) know where this circle
    # currently is on screen, so the window can visibly grow out of it /
    # shrink back into it -- purely cosmetic, see get_circle_rect()'s
    # docstring for the None-is-fine fallback.
    agent_tools.set_circle_rect_provider(overlay.get_circle_rect)

    # Lets the IDE's minimized-panel "pill" (shown near the circle while
    # the IDE is minimized) speak its own name aloud on hover, through
    # Friday's own voice -- purely cosmetic, see set_speak_label_handler's
    # docstring for the silent-no-op fallback.
    agent_tools.set_speak_label_handler(_ide_speak_label)

    app.aboutToQuit.connect(_force_close_ide)

    overlay.show()
    _hide_console_window()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

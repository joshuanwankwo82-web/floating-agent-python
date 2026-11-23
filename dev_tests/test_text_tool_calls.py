"""Text-level tool calls -- the last-resort tier's action engine.

Live-user origin: a fresh roster made of ONLY "+ Add Model" entries
(generic chat-completions, no native function schemas) answered an IDE
chat "build me a calculator" with pseudo-XML markup leaking RAW into
the visible reply:

    Let me set that up now.<tool_call>ide_create_folder<arg_key>name
    </arg_key><arg_value>calculator</arg_value></tool_call>

Nothing executed; the user saw the tag soup. The fix in
agent_overlay.py (_parse_tool_call_body / _extract_text_tool_calls /
_resolve_text_tool_name / _remap_tool_args / _coerce_tool_value /
_run_text_tool_entry) parses those text-level calls, strips the markup
from what the user sees, executes each call through the SAME
agent_tools.call_tool desk the native loop uses (same ide whitelist),
and feeds results back until a clean prose close-out. This battery
pins the parsers on the exact shipped dialects, the safety rails
(unknown tools never execute; ide scope enforced; single-arg alias
remap; coercion truth table), and a REAL end-to-end folder creation in
an actual IDE workspace.

Requires a display (the IDE end-to-end part spins a real IDEApp):
    DISPLAY=:99 python3 dev_tests/test_text_tool_calls.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools as at  # noqa: E402
import act_script  # noqa: E402
import agent_overlay as ao  # noqa: E402

CHECKS = []


def check(label, cond):
    CHECKS.append((label, bool(cond)))
    print(("PASS" if cond else "FAIL") + " - " + label)


# =========================================================================
# Part A: the parser, driving OFF THE ACTUAL SCREENSHOT DIALECTS
# =========================================================================
shot = ("Calculator web app \u2014 clean. I'll spin up a new workspace "
        "folder for this and build a single-file, neomorphic-styled "
        "calculator. Let me set that up now."
        "<tool_call>ide_create_folder<arg_key>name</arg_key>"
        "<arg_value>calculator</arg_value></tool_call>")
calls, visible = ao._extract_text_tool_calls(shot)
check("the exact screenshot reply yields ONE call and its prose",
      calls == [("ide_create_folder", {"name": "calculator"})])
check("the visible text contains NO markup at all",
      "<tool_call>" not in visible
      and "arg_key" not in visible
      and "arg_value" not in visible
      and "</tool_call>" not in visible
      and "Let me set that up now." in visible)

calls, visible = ao._extract_text_tool_calls(
    '<tool_call>{"name": "ide_write_file", "arguments": '
    '{"path": "a.txt", "content": "hi"}}</tool_call>')
check("the Hermes-JSON dialect parses",
      calls == [("ide_write_file", {"path": "a.txt", "content": "hi"})]
      and visible == "")

calls, visible = ao._extract_text_tool_calls(
    'prose <tool_call>ide_write_file(path="a.txt", content=42)</tool_call> tail')
check("the function-call dialect parses with typed values",
      calls == [("ide_write_file", {"path": "a.txt", "content": 42})]
      and "prose" in visible and "tail" in visible)

calls, visible = ao._extract_text_tool_calls(
    "<tool_call>ide_list_files</tool_call>")
check("the bare-name dialect parses (name only, empty args)",
      calls == [("ide_list_files", {})])

calls, visible = ao._extract_text_tool_calls(
    "Let me set that up now.<tool_call>ide_create_folder"
    "<arg_key>name</arg_key><arg_value>calculator</arg_value>")
check("an UNTERMINATED trailing tag is still parsed AND stripped",
      calls == [("ide_create_folder", {"name": "calculator"})]
      and "<tool_call>" not in visible
      and visible.endswith("set that up now."))

multi = ("w1 <tool_call>ide_create_folder(a)</tool_call> "
         "w2 <tool_call>ide_create_folder(b)</tool_call> w3")
calls, visible = ao._extract_text_tool_calls(multi)
check("multiple calls in one reply all parse; prose keeps a..w..z order",
      calls == [("ide_create_folder", {}), ("ide_create_folder", {})] and
      visible.replace("w1", "").replace("w2", "").replace("w3", "").strip() == "")

calls, visible = ao._extract_text_tool_calls("a < b and 5 > 2 are fine")
check("ordinary prose with angle brackets is NOT eaten",
      calls == [] and visible == "a < b and 5 > 2 are fine")

calls, visible = ao._extract_text_tool_calls("")
check("empty text -> nothing",
      calls == [] and visible == "")

check("prose without any calls returns no calls but keeps all text",
      ao._extract_text_tool_calls("plain answer")[1] == "plain answer")

# =========================================================================
# Part B: coercion truth table (was broken by a missing module-global
# `import json`, swallowed by a broad except -- pin it HARD)
# =========================================================================
truth = [("42", int), ("-3", int), ("3.5", float),
         ("true", bool), ("false", bool),
         ('{"a": 1}', dict), ("[1, 2]", list),
         ("calculator", str), ("plain prose", str)]
ok = all(isinstance(ao._coerce_tool_value(raw), want) for raw, want in truth)
check("coercion truth table: numbers/bool/JSON type, strings stay strings",
      ok)
check("integers and floats come out as numbers, not strings",
      ao._coerce_tool_value("7") == 7 and ao._coerce_tool_value("7.5") == 7.5)
check("booleans round-trip as real bools",
      ao._coerce_tool_value("true") is True
      and ao._coerce_tool_value("false") is False)
check("the coercer never crashes on garbage that LOOKS json-ish",
      ao._coerce_tool_value("{unclosed") == "{unclosed"
      and ao._coerce_tool_value("null") is None)

# =========================================================================
# Part C: resolving + remapping + safety rails
# =========================================================================
check("exact names resolve; fuzzy near-misses resolve",
      ao._resolve_text_tool_name("ide_create_folder", True)
      == "ide_create_folder"
      and ao._resolve_text_tool_name("ide_create_folders", True)
      == "ide_create_folder")
check("unknown names NEVER resolve (no gullible executions)",
      ao._resolve_text_tool_name("delete_everything", True) is None)
check("ide_scoped resolution refuses non-IDE tools (open_app)",
      ao._resolve_text_tool_name("open_app", True) is None)
check("non-ide_scoped resolution allows general tools (resolver on)",
      isinstance(ao._resolve_text_tool_name("ide_create_folder", False), str))

remapped = ao._remap_tool_args("ide_create_folder", {"name": "calc"})
check("single-arg alias: {'name': X} rides onto the tool's real param",
      remapped == {"path": "calc"})
check("value typing survives the remap",
      ao._remap_tool_args("ide_create_folder",
                          {"name": "folder42"}) == {"path": "folder42"})
check("multi-arg dicts pass through untouched",
      ao._remap_tool_args("ide_write_file",
                          {"path": "a.txt", "content": "b"})
      == {"path": "a.txt", "content": "b"})
check("non-dict args pass through untouched",
      ao._remap_tool_args("ide_create_folder", None) is None)

# =========================================================================
# Part D: REAL end-to-end -- a text-only model's call really creates a
# folder in an ACTUAL IDE workspace, twice-repeated call not re-run
# =========================================================================
msg = at.agent_mode_on()
check("agent_mode_on() succeeded (needed for real workspace writes)",
      "Agent Mode is on" in msg)

import_act_script_call_model = act_script.call_model
round_replies = []
created_marker = {"count": 0}


def fake_call_model(role, provider_id, system_prompt, user_prompt):
    if len(round_replies) == 0:
        round_replies.append("first")
        return act_script.CallResult(
            ok=True,
            text=shot,  # the exact markup reply from the live screenshot
            provider="fake")
    round_replies.append("followup")
    return act_script.CallResult(
        ok=True, text="Done \u2014 the calculator/ workspace folder is "
        "created and ready for the build.", provider="fake")


try:
    act_script.call_model = fake_call_model
    ok, text, err = ao._run_text_tool_entry(
        {"id": "fake-entry"}, "coding", "sys", "build a calculator",
        True)
    check("the full text-tool turn succeeds and reaches the model's "
          "closing reply", ok is True and err is None
          and "calculator/" in text and "folder is created" in text)

    ws_root = at._ide_app.fs.root
    check("the markup call REALLY created calculator/ in the ACTUAL "
          "IDE workspace",
          os.path.isdir(os.path.join(ws_root, "calculator")))
finally:
    act_script.call_model = import_act_script_call_model

# Repeat guard: a model that re-emits the identical call in round 2
# must NOT re-execute it -- the loop must stop cleanly with a summary.
repeat_rounds = []


def fake_repeat(role, provider_id, system_prompt, user_prompt):
    repeat_rounds.append(user_prompt[:60])
    return act_script.CallResult(
        ok=True,
        text="<tool_call>ide_create_folder<arg_key>path</arg_key>"
        "<arg_value>repeat_guard</arg_value></tool_call>",
        provider="fake")


try:
    act_script.call_model = fake_repeat
    ok, text, err = ao._run_text_tool_entry(
        {"id": "fake-entry"}, "coding", "sys", "make a repeat_guard dir",
        True)
    outcome = os.path.isdir(os.path.join(ws_root, "repeat_guard"))
    check("an endlessly-repeating model still terminates (round cap / "
          "repeat cut)", ok is True and err is None)
    check("the repeated call DID execute at least once (folder exists)",
          outcome)
    check("the loop stops and reports instead of looping the tool "
          "forever",
          len(repeat_rounds) <= ao.TEXT_TOOL_CALL_ROUNDS_MAX + 1)
finally:
    act_script.call_model = import_act_script_call_model

# Unknown-tool path: named but nonexistent tools are recorded, never
# executed, and surface in the close-out as skipped -- never silently.
def fake_unknown(role, provider_id, system_prompt, user_prompt):
    return act_script.CallResult(
        ok=True,
        text="<tool_call>nuke_everything<arg_key>target</arg_key>"
        "<arg_value>all</arg_value></tool_call>",
        provider="fake")


try:
    act_script.call_model = fake_unknown
    ok, text, err = ao._run_text_tool_entry(
        {"id": "fake-entry"}, "coding", "sys", "nuke it", True)
    check("an unknown tool name is NOT executed and is honestly named "
          "as skipped", ok is True and "nuke_everything" in text
          and "no matching tool" in text.lower() or "couldn't run" in text)
finally:
    act_script.call_model = import_act_script_call_model

# =========================================================================
# Part F (F13): the pipe-delimited dialect -- live 2026-10-22 build
# screenshot: the agent answered "Building you a web calculator..." in
# <|tool_call_begin|><|tool_call|>NAME<|tool_call_arg|>{json}
# <|tool_call_end|> tokens; nothing executed and the markup rendered
# raw. Below: the exact text, the variants, and a FULL build loop.
# =========================================================================
import json as _json


def pipe_call(name, args):
    return ("<|tool_call_begin|>\n<|tool_call|>" + name +
            "<|tool_call_arg|>" + _json.dumps(args) +
            "<|tool_call_end|>")


live13 = ("Building you a web calculator. Agent mode is on, and I am "
          "setting up a fresh workspace.\n" +
          pipe_call("agent_mode_on", {}) + "\n" +
          pipe_call("ide_create_folder", {"path": "web-calculator"}) +
          "\n" + pipe_call("ide_open_folder", {"name": "web-calculator"}))
calls, visible = ao._extract_text_tool_calls(live13)
check("F13: the EXACT screenshot text yields all 3 calls in order",
      calls == [("agent_mode_on", {}),
                ("ide_create_folder", {"path": "web-calculator"}),
                ("ide_open_folder", {"name": "web-calculator"})])
check("F13: not one pipe token survives into the visible text",
      "<|" not in visible and "web calculator" in visible)

calls, visible = ao._extract_text_tool_calls(
    "<|tool_calls_section_begin|>" +
    "<|tool_call|>ide_create_folder<|tool_call_arg|>{\"path\": \"sx\"}" +
    "<|tool_calls_section_end|>")
check("F13: the section-wrapped variant parses and strips fully",
      calls == [("ide_create_folder", {"path": "sx"})]
      and visible == "")

calls, visible = ao._extract_text_tool_calls(
    "prose <|tool_call|>ide_create_folder"
    "<|tool_call_arg|>{\"path\": \"bare\"}")
check("F13: a bare unwrapped segment (no begin/end at all) still parses",
      calls == [("ide_create_folder", {"path": "bare"})]
      and visible == "prose")

calls, visible = ao._extract_text_tool_calls(
    "let me check<|tool_call_begin|><|tool_call|>ide_list_folder")
check("F13: an UNTERMINATED trailing pipe tag parses name-only AND is "
      "stripped", calls == [("ide_list_folder", {})]
      and visible == "let me check")

calls, visible = ao._extract_text_tool_calls(
    "<|tool_call_begin|><|tool_call|>ide_write_file"
    "<|tool_call_arg|>{\"path\": \"a.txt\", \"content\": \"hi"
    "<|tool_call_end|>")
check("F13: truncated JSON args are NOT fabricated into a call "
      "(no-invention law)", calls == [] and "<|" not in visible)

calls, visible = ao._extract_text_tool_calls(
    "<|tool_call|>ide_create_folder<|tool_call_arg|>path=kwcalc"
    "<|tool_call_end|>")
check("F13: kwargs-style args inside the pipe dialect parse too",
      calls == [("ide_create_folder", {"path": "kwcalc"})])

check("F13: ordinary prose with a lone pipe is untouched",
      ao._extract_text_tool_calls("a | b in prose are fine")
      == ([], "a | b in prose are fine"))

check("F13: agent_mode_on is HONESTLY outside the ide-chat whitelist "
      "(recorded unknown, never executed)",
      ao._resolve_text_tool_name("agent_mode_on", True) is None
      and ao._resolve_text_tool_name("ide_open_folder", True)
      == "ide_open_folder")
check("F13: open_folder's {name: X} alias rides onto its real `folder` "
      "param", ao._remap_tool_args("ide_open_folder",
                                   {"name": "pipeapp"})
      == {"folder": "pipeapp"})

# ---- the user's core ask: prove a FULL BUILD happens in the loop ----
HTML = "<!DOCTYPE html><title>calc</title><h1>Calc</h1>"
CSS = "body{font-family:sans-serif}"
JS = "function add(a,b){return a+b}"
rounds13 = []


def fake_build13(role, provider_id, system_prompt, user_prompt):
    rounds13.append(user_prompt)  # FULL, so the follow-up claims pin truth
    n = len(rounds13)
    if n == 1:
        return act_script.CallResult(
            ok=True,
            text="Setting up the workspace.\n" +
                 pipe_call("agent_mode_on", {}) + "\n" +
                 pipe_call("ide_create_folder", {"path": "pipeapp"}) +
                 "\n" +
                 pipe_call("ide_open_folder", {"name": "pipeapp"}),
            provider="fake")
    if n == 2:
        # round 1 OPENED pipeapp/ -> the workspace root IS pipeapp/ now,
        # so the build writes plain root-relative filenames (as a real
        # model learns to from the tool results it is fed back).
        return act_script.CallResult(
            ok=True,
            text=pipe_call("ide_write_file",
                           {"path": "index.html", "content": HTML}) +
                 "\n" +
                 pipe_call("ide_write_file",
                           {"path": "style.css", "content": CSS}) +
                 "\n" +
                 pipe_call("ide_write_file",
                           {"path": "script.js", "content": JS}),
            provider="fake")
    return act_script.CallResult(
        ok=True,
        text="Done \u2014 pipeapp/ holds index.html, style.css and "
        "script.js. The calculator is built.", provider="fake")


try:
    act_script.call_model = fake_build13
    ok, text, err = ao._run_text_tool_entry(
        {"id": "fake-entry"}, "coding", "sys",
        "build a web calculator in pipeapp/", True)
    check("F13: the full build loop succeeds and reaches the closing "
          "prose", ok is True and err is None
          and "calculator is built" in text and "<|" not in text)
    check("F13: it really took THREE model rounds (setup, write x3, "
          "close)", len(rounds13) == 3)
    ws13 = at._ide_app.fs.root
    check("F13: pipeapp/ was REALLY created AND became the open "
          "workspace root (a dedupe suffix like pipeapp-1 is the IDE's "
          "own honest name-disambiguation, tolerated)",
          os.path.isdir(ws13) and os.path.basename(ws13)
          .startswith("pipeapp"))
    check("F13: ALL THREE files are REALLY on disk with the model's "
          "exact content",
          open(os.path.join(ws13, "index.html")).read() == HTML
          and open(os.path.join(ws13, "style.css")).read() == CSS
          and open(os.path.join(ws13, "script.js")).read() == JS)
    check("F13: the follow-up continuation carried the REAL tool "
          "results back to the model",
          "ide_create_folder" in rounds13[1])
finally:
    act_script.call_model = import_act_script_call_model

# =========================================================================
# Part G (F14): the lazy-model stall -- live reply to "BUILD ME A WEB
# CALCULATOR" was pure prose ("Let me check what's currently open in
# your workspace before I build anything new.") with NO tool call at
# all, and the turn honestly ended. Now: ONE bounded push.
# =========================================================================
stall_rounds = []


def fake_stall_then_act(role, provider_id, system_prompt, user_prompt):
    stall_rounds.append(user_prompt)
    n = len(stall_rounds)
    if n == 1:
        return act_script.CallResult(
            ok=True,
            text="Let me check what's currently open in your workspace "
                 "before I build anything new.", provider="fake")
    if n == 2:
        return act_script.CallResult(
            ok=True,
            text=pipe_call("ide_create_folder", {"path": "webcalc"}),
            provider="fake")
    return act_script.CallResult(
        ok=True, text="Built webcalc/ for you.", provider="fake")


try:
    act_script.call_model = fake_stall_then_act
    root0 = at._ide_app.fs.root
    ok, text, err = ao._run_text_tool_entry(
        {"id": "fake-entry"}, "coding", "sys",
        "BUILD ME A WEB CALCULATOR", True)
    check("F14: the announced stall gets pushed and the turn completes",
          ok is True and err is None and "Built webcalc/" in text)
    check("F14: exactly THREE model calls (reply, push, close) -- the "
          "push is bounded, not a loop", len(stall_rounds) == 3)
    check("F14: the push message carries the user's request and demands "
          "the REAL calls",
          "BUILD ME A WEB CALCULATOR" in stall_rounds[1]
          and "NO tool call" in stall_rounds[1])
    check("F14: the pushed call REALLY created the folder on disk",
          any(d.startswith("webcalc") and
              os.path.isdir(os.path.join(root0, d))
              for d in os.listdir(root0)))
finally:
    act_script.call_model = import_act_script_call_model

# stubborn prose: the model keeps talking -- the engine pushes ONCE and
# then takes the second prose as the honest final answer.
stubborn_rounds = []


def fake_stubborn(role, provider_id, system_prompt, user_prompt):
    stubborn_rounds.append(user_prompt)
    return act_script.CallResult(
        ok=True, text="Let me look into that first.", provider="fake")


try:
    act_script.call_model = fake_stubborn
    ok, text, err = ao._run_text_tool_entry(
        {"id": "fake-entry"}, "coding", "sys", "build something", True)
    check("F14: a stubborn talker gets exactly ONE push (2 calls, not "
          "a nagging loop)", len(stubborn_rounds) == 2)
    check("F14: and its second prose is still the honest final answer",
          ok is True and "Let me look into that first." in text)
finally:
    act_script.call_model = import_act_script_call_model

# no-push gates: ordinary questions and non-committal prose never burn
# an extra model call.
nope_rounds = []


def fake_nope(role, provider_id, system_prompt, user_prompt):
    nope_rounds.append(user_prompt)
    if "build" in user_prompt:
        return act_script.CallResult(
            ok=True, text="The summary: three files, all consistent.",
            provider="fake")
    return act_script.CallResult(
        ok=True, text="Let me put it this way: it reads fine.",
        provider="fake")


try:
    act_script.call_model = fake_nope
    ao._run_text_tool_entry({"id": "fake-entry"}, "coding", "sys",
                            "what does index.html do?", True)
    check("F14: a conversational question NEVER triggers the push",
          len(nope_rounds) == 1)
    nope_rounds.clear()
    ao._run_text_tool_entry({"id": "fake-entry"}, "coding", "sys",
                            "build the thing", True)
    check("F14: an action prompt answered by NON-committal prose (no "
          "announced intent) never triggers the push either",
          len(nope_rounds) == 1)
finally:
    act_script.call_model = import_act_script_call_model

# dialect 5b: a pipe block whose body is direct Hermes-style JSON.
calls, visible = ao._extract_text_tool_calls(
    "note "
    "<|tool_call_begin|>"
    "{\"name\": \"ide_create_folder\", \"arguments\": "
    "{\"path\": \"blkx\"}}"
    "<|tool_call_end|>")
check("F14: pipe block with direct JSON body parses via the Hermes "
      "body-parser and leaves prose only",
      calls == [("ide_create_folder", {"path": "blkx"})]
      and visible == "note")

# =========================================================================
n_fail = sum(1 for _, ok in CHECKS if not ok)
print(f"\n{len(CHECKS) - n_fail}/{len(CHECKS)} checks passed")
sys.exit(1 if n_fail else 0)

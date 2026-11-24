"""Regression test for act_script.py -- Friday's ACT Script system (the
Router + Sequential Rotation / Fan-Out / Debate pipelines) and its
wiring into agent_tools.py as ide_run_act_script.

Honesty about scope: this sandbox has no real Gemini/Groq/Mistral API
keys configured (see .env -- the key names are present but the values
are empty placeholders), so this can't do a live network end-to-end
run. Exactly like this codebase's own existing precedent for the same
problem (test_friday_memory.py monkeypatches memory.py's embedding
calls "rather than hitting a real network/API key" -- see its module
docstring), this test monkeypatches only two seams: settings_store.load
(the shared model roster -- see settings_store.py/settings_panel.py)
and act_script._DISPATCH (the check_kind -> real-
API-call dict), both with deterministic, scripted data/replies, while
everything around it stays completely real: real round-robin/exclude
provider selection, real task-preferred shared-pool selection (see
act_script.region_pool), real JSON/file-block parsing, real shell
mechanical verify commands, real file writes through the real IDE
(agent_tools.ide_write_file / ide_read_file against a real temp
workspace), real bounded fix-forward retry, real concurrent worker
threads, real memory.py evidence logging against a real (temp,
throwaway) encrypted memory folder, and the real agent_tools
tool-registration/gating plumbing.

Requires a display (the IDE is Tkinter-based):
    DISPLAY=:99 QT_QPA_PLATFORM=offscreen python3 dev_tests/test_act_script.py
"""
import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools as at  # noqa: E402
import act_script  # noqa: E402
import memory  # noqa: E402
import settings_store  # noqa: E402

CHECKS = []
_ORIG_SETTINGS_LOAD = settings_store.load


def check(label, cond):
    CHECKS.append((label, bool(cond)))
    print(("PASS" if cond else "FAIL") + " - " + label)


def _raises(fn):
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


def _stub_entry(id_, check_kind, api_key="stub", model=None, base_url="", name=None):
    """A minimal, settings_store-shaped entry -- same fields add_model()
    would produce, just built directly so tests don't need a real
    encrypted settings file on disk."""
    return {
        "id": id_, "name": name or id_.title(), "model": model or f"{id_}-model",
        "base_url": base_url, "api_key": api_key, "check_kind": check_kind,
        "builtin": check_kind != "generic_http",
        "status": "unknown", "detail": "", "last_checked": None,
    }


def make_role_dispatch(call_log, behavior):
    """Builds one dispatch(entry, system_prompt, user_prompt, model_name)
    function per provider id, all sharing the same `behavior` table
    (substring-in-system-prompt -> handler), and all appending to the
    shared `call_log` so tests can assert who got called, in what role,
    how many times."""
    def _factory(provider_id):
        def _dispatch(entry, system_prompt, user_prompt, model_name):
            call_log.append({"provider": provider_id, "system": system_prompt,
                              "user": user_prompt, "model": model_name,
                              "api_key": (entry or {}).get("api_key", "")})
            for key, fn in behavior:
                if key in system_prompt:
                    return fn(provider_id, system_prompt, user_prompt, model_name)
            raise AssertionError(f"no stub matched for {provider_id}: {system_prompt[:100]!r}")
        return _dispatch
    return _factory


def install_stub_providers(behavior, thinking_ids=("gemini", "groq", "mistral"),
                            extra_faculties=None):
    """Pretends the Settings panel is configured: the default chat
    entries are seeded into several task-preference buckets.
    `extra_faculties` REPLACES a bucket's entries entirely. Routes every
    real call through
    `behavior` (see make_role_dispatch). Returns the call_log list."""
    call_log = []
    data = {fac: [] for fac in settings_store.FACULTIES}
    for fac in ("Thinking", "Planning", "Coding", "Verify", "Review",
                "Text Generation"):
        data[fac] = [_stub_entry(pid, pid) for pid in thinking_ids]
    for fac, entries in (extra_faculties or {}).items():
        data[fac] = list(entries)
    settings_store.load = lambda: copy.deepcopy(data)

    factory = make_role_dispatch(call_log, behavior)
    kinds = {e["check_kind"] for entries in data.values() for e in entries}
    dispatch = {}
    for entries in data.values():
        for e in entries:
            dispatch[e["check_kind"]] = factory(e["id"])
    act_script._DISPATCH = dispatch or {k: factory(k) for k in kinds}
    return call_log


def main():
    tmp_memory = tempfile.mkdtemp(prefix="friday-act-script-memory-")
    memory.set_memory_dir(tmp_memory)

    # ---- Part A: provider plumbing (shared, task-preferred pool) ---------
    install_stub_providers([], thinking_ids=())
    check("available_providers('thinking') is empty when nothing is configured",
          act_script.available_providers("thinking") == [])
    p, degraded = act_script.next_provider("thinking")
    check("next_provider() with nothing configured returns (None, True)",
          p is None and degraded is True)

    install_stub_providers([], thinking_ids=("gemini", "mistral"))
    check("available_providers('thinking') reflects exactly what's configured, keeps order",
          act_script.available_providers("thinking") == ["gemini", "mistral"])

    install_stub_providers([], thinking_ids=("gemini", "groq", "mistral"))
    check("available_providers('thinking') with all three configured",
          act_script.available_providers("thinking") == ["gemini", "groq", "mistral"])
    p1, d1 = act_script.next_provider("thinking", after="gemini")
    check("next_provider() round-robins forward", p1 == "groq" and not d1)
    p2, d2 = act_script.next_provider("thinking", after="mistral")
    check("next_provider() wraps around", p2 == "gemini" and not d2)
    p3, d3 = act_script.next_provider("thinking", after="gemini", exclude={"groq"})
    check("next_provider() honors exclude when an alternative exists",
          p3 == "mistral" and not d3)
    p4, d4 = act_script.next_provider("thinking", after="gemini", exclude={"gemini", "groq", "mistral"})
    check("next_provider() falls back (degraded=True) only when exclude covers literally everyone",
          p4 == "groq" and d4 is True)

    # ---- Part A2: task-preferred ordering with shared model fallback -----
    install_stub_providers(
        [], thinking_ids=("gemini", "groq"),
        extra_faculties={
            "Coding": [_stub_entry("custom-coder", "generic_http",
                                    base_url="https://api.example.com/v1")],
            "Planning": [],  # deliberately empty: honest "not configured"
            "Verify": [],
            "Review": [],
        },
    )
    check("a task with no directly preferred model falls back to all "
          "compatible connected models",
          act_script.available_providers("planning") ==
          ["gemini", "groq", "custom-coder"])
    check("a task's formerly matched entries are tried before other "
          "connected models",
          act_script.available_providers("coding") ==
          ["custom-coder", "gemini", "groq"])
    check("all tasks can use the same compatible model connections",
          act_script.available_providers("thinking") ==
          ["gemini", "groq", "custom-coder"] and
          act_script.available_providers("voice") ==
          ["gemini", "groq", "custom-coder"])
    install_stub_providers(
        [], thinking_ids=("gemini", "groq"),
        extra_faculties={
            "Coding": [_stub_entry("custom-coder", "generic_http"),
                       _stub_entry("custom-coder-2", "generic_http")],
        },
    )
    check("additional connected models join the shared task pool",
          act_script.available_providers("coding") ==
          ["custom-coder", "custom-coder-2", "gemini", "groq"])
    nxt, deg = act_script.next_provider("coding", after="custom-coder")
    check("next_provider() rotates correctly within the shared pool",
          nxt == "custom-coder-2" and not deg)
    check("provider_label() shows a custom entry's own display name, not its raw id",
          act_script.provider_label("coding", "custom-coder") == "Custom-Coder")
    check("provider_label() falls back to the raw id for an unknown/removed entry",
          act_script.provider_label("coding", "no-such-id") == "no-such-id")

    # ---- Part A3: the generic OpenAI-compatible dispatcher ---------------
    # This is the real call a user-added "+ Add Model" custom entry (see
    # settings_panel.py) gets -- not just a reachability ping.
    check("_dispatch_generic_http() raises cleanly when no base_url is configured",
          _raises(lambda: act_script._dispatch_generic_http(
              _stub_entry("x", "generic_http", base_url=""), "s", "u", "m")))

    import requests as _requests
    captured_request = {}

    class _FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "  a real custom reply  "}}]}

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured_request["url"] = url
        captured_request["json"] = json
        captured_request["headers"] = headers
        return _FakeResp()

    orig_post = _requests.post
    _requests.post = _fake_post
    try:
        entry = _stub_entry("custom-coder", "generic_http", api_key="sk-custom",
                             model="my-model", base_url="https://api.example.com/v1")
        text = act_script._dispatch_generic_http(entry, "sys prompt", "user prompt", "my-model")
        check("_dispatch_generic_http() calls base_url + /chat/completions",
              captured_request["url"] == "https://api.example.com/v1/chat/completions")
        check("_dispatch_generic_http() sends a Bearer auth header with the entry's own key",
              captured_request["headers"]["Authorization"] == "Bearer sk-custom")
        check("_dispatch_generic_http() sends an OpenAI-shaped messages payload",
              captured_request["json"]["messages"][0] == {"role": "system", "content": "sys prompt"}
              and captured_request["json"]["messages"][1] == {"role": "user", "content": "user prompt"}
              and captured_request["json"]["model"] == "my-model")
        check("_dispatch_generic_http() extracts and strips the real reply text",
              text == "a real custom reply")

        entry2 = _stub_entry("custom-coder2", "generic_http", base_url="https://api.example.com/v1/")
        act_script._dispatch_generic_http(entry2, "s", "u", "m")
        check("_dispatch_generic_http() doesn't double up the /chat/completions suffix",
              captured_request["url"] == "https://api.example.com/v1/chat/completions")
    finally:
        _requests.post = orig_post

    # ---- Part B: call_model() -------------------------------------------
    install_stub_providers([], thinking_ids=("gemini",))

    def _fails_once_then_succeeds(entry, system_prompt, user_prompt, model_name):
        if model_name == entry["model"]:
            raise RuntimeError("primary model overloaded")
        return f"fallback reply from {model_name}"

    act_script._DISPATCH = dict(act_script._DISPATCH)
    act_script._DISPATCH["gemini"] = _fails_once_then_succeeds
    result = act_script.call_model("thinking", "gemini", "sys", "usr")
    check("call_model() falls back to the provider's own known fallback models on failure",
          result.ok and result.model in act_script.GEMINI_FALLBACK_MODELS)

    install_stub_providers([], thinking_ids=("gemini", "groq"))
    fallback_calls = []

    def _quota_exhausted(entry, system_prompt, user_prompt, model_name):
        fallback_calls.append(entry["id"])
        if entry["id"] == "gemini":
            raise RuntimeError("HTTP 429 -- quota exhausted")
        return "reply from the next connected provider"

    act_script._DISPATCH = {
        "gemini": _quota_exhausted,
        "groq": _quota_exhausted,
    }
    fallback_result = act_script.call_model(
        "thinking", "gemini", "sys", "usr")
    check("call_model() moves to the next task-compatible Settings model after a quota failure",
          fallback_result.ok and fallback_result.provider == "groq"
          and "gemini" in fallback_calls and "groq" in fallback_calls)

    install_stub_providers([], thinking_ids=())
    result2 = act_script.call_model("thinking", "gemini", "sys", "usr")
    check("call_model() reports cleanly (not raising) when the provider isn't "
          "in the shared pool",
          result2.ok is False and "isn't a configured chat model" in result2.error)

    # ---- Part C: JSON / file-block extraction ---------------------------
    check("_extract_json() handles plain JSON", act_script._extract_json('{"a": 1}') == {"a": 1})
    check("_extract_json() strips markdown fences",
          act_script._extract_json('```json\n{"a": 1}\n```') == {"a": 1})
    check("_extract_json() finds JSON amid prose",
          act_script._extract_json('Sure, here it is:\n{"a": 1}\nHope that helps!') == {"a": 1})
    check("_extract_json() returns None (never raises) on garbage",
          act_script._extract_json("not json at all") is None)

    blocks = act_script._extract_file_blocks(
        '===FILE: a.py===\nprint(1)\n===END===\n===FILE: b.py===\nprint(2)\n===END==='
    )
    check("_extract_file_blocks() finds multiple files",
          blocks == [("a.py", "print(1)"), ("b.py", "print(2)")])
    check("_extract_file_blocks() returns [] for NO_FILE_CHANGES",
          act_script._extract_file_blocks("NO_FILE_CHANGES") == [])

    rescue_reply = ('Here is the file:\n```python\ndef hello():\n'
                    '    return "hi"\n```\nand to run it\n'
                    '```bash\npython app.py\n```')
    check("_rescue_file_blocks() maps a single hinted fence, skipping "
          "command fences entirely",
          act_script._rescue_file_blocks(rescue_reply, "app.py")
          == [("app.py", 'def hello():\n    return "hi"')])
    check("_rescue_file_blocks() with NO hint rescues nothing for a "
          "single accidental fence (no path marker present)",
          act_script._rescue_file_blocks(rescue_reply, "") == [])
    check("_rescue_file_blocks() honors a path line right above the fence",
          act_script._rescue_file_blocks(
              "## src/main.py\n```python\nx = 1\n```", "")
          == [("src/main.py", "x = 1")])
    check("_rescue_file_blocks() honors a path in the fence's top comment, "
          "and STRIPS that comment from the written content",
          act_script._rescue_file_blocks(
              '```python\n# app.py\ndef go():\n    pass\n```', "")
          == [("app.py", "def go():\n    pass")])
    check("_rescue_file_blocks() refuses to write outside the workspace "
          "(.. / absolute / backslash paths)",
          act_script._safe_rescue_path("../evil.py") is None
          and act_script._safe_rescue_path("/etc/x.py") is None
          and act_script._safe_rescue_path("a\\b.py") is None)
    ambiguous = 'first\n```python\na = 1\n```and second\n```python\nb = 2\n```'
    check("_rescue_file_blocks() rescues NOTHING when multiple fences "
          "have no path markers and no single hint (ambiguous beats wrong)",
          act_script._rescue_file_blocks(ambiguous, "") == [])

    # ---- Part D: the Verify region (acceptance by a settings model) ---
    v_calls = install_stub_providers([("Host-side ground truth", lambda p, s, u, m:
        '{"verified": true, "evidence": "saw the real file"}')])
    ok_v, ev_v = act_script._region_verify(
        "c", {"description": "d"},
        {"written": ["x.py"], "write_status": "wrote"}, "gemini")
    check("the Verify region's verified=true verdict is honored, with its "
          "evidence text", ok_v and "saw the real file" in ev_v)
    bad_calls = install_stub_providers([("Host-side ground truth", lambda p, s, u, m: "not json")])
    ok_b, ev_b = act_script._region_verify(
        "c", {"description": "d"},
        {"written": [], "write_status": "no_blocks"}, "gemini")
    check("an unusable verify reply is an honest failure, never a pass",
          not ok_b and "not a usable verdict" in ev_b)
    check("a zero-write step is shown to the verifier as ground truth, "
          "unspinnable", "(no files were written this step)" in bad_calls[0]["system"])

    # ---- Part E: evidence/memory logging (real memory.py) -----------------
    act_script._log_evidence("testrun01", "step", {"summary": "did a thing", "x": 1})
    recalled = memory.recall("did a thing", max_results=3, category="act_script")
    check("_log_evidence() really writes a recallable memory.remember() entry",
          any("did a thing" in r["text"] for r in recalled))
    kv_record = memory.kv_get("act_script_run:testrun01")
    check("_log_evidence() really writes the full raw record via memory.kv_set()",
          kv_record is not None and kv_record["entries"][0]["kind"] == "step"
          and kv_record["entries"][0]["x"] == 1)

    # ---- Part F: Sequential Rotation, end to end (real IDE, real files) ---
    msg = at.agent_mode_on()
    check("agent_mode_on() succeeded (needed for real file writes below)",
          "Agent Mode is on" in msg)

    def thinking_seq(provider, system, user, model):
        return (
            '{"contract": "greet.py must define greet() returning a greeting, '
            'and running it as __main__ must print that greeting.", '
            '"steps": ["Create greet.py with a greet() function that returns '
            '\\"Hello, Friday!\\"", "Add a __main__ block to greet.py that '
            'prints greet()\'s result"]}'
        )

    def planning_seq(provider, system, user, model):
        verify_first = (
            f'"{sys.executable}" -c "from pathlib import Path; '
            "assert 'Hello, Friday!' in "
            "Path('greet.py').read_text(encoding='utf-8')\""
        )
        verify_second = (
            f'"{sys.executable}" -c "from pathlib import Path; '
            "text=Path('greet.py').read_text(encoding='utf-8'); "
            "assert '__main__' in text and 'print(greet())' in text\""
        )
        return json.dumps({
            "steps": [
                {
                    "description": (
                        'Create greet.py with a greet() function that returns '
                        '"Hello, Friday!"'
                    ),
                    "file_hint": "greet.py",
                    "verify_command": verify_first,
                },
                {
                    "description": (
                        "Add a __main__ block to greet.py that prints "
                        "greet()'s result"
                    ),
                    "file_hint": "greet.py",
                    "verify_command": verify_second,
                },
            ]
        })

    def coding_seq(provider, system, user, model):
        if "Create greet.py" in system and "Add a __main__" not in system.split("This step:")[1][:80]:
            return '===FILE: greet.py===\ndef greet():\n    return "Hello, Friday!"\n===END==='
        if "Add a __main__" in system:
            # Deliberately broken the first time -- forgets the __main__
            # block entirely, so the mechanical verify command fails and
            # the bounded fix-forward retry path actually gets exercised.
            return '===FILE: greet.py===\ndef greet():\n    return "Hello, Friday!"\n===END==='
        raise AssertionError(f"unexpected coding system prompt: {system[:120]!r}")

    def fix_seq(provider, system, user, model):
        return (
            '===FILE: greet.py===\n'
            'def greet():\n'
            '    return "Hello, Friday!"\n\n'
            'if __name__ == "__main__":\n'
            '    print(greet())\n'
            '===END==='
        )

    def review_seq(provider, system, user, model):
        return '{"verdict": "done", "report": "Both steps verified against the real file contents."}'

    def verify_seq(provider, system, user, model):
        # A FAITHFUL Verify region: judges only the host-gathered
        # ground truth in its prompt (the file's real current
        # contents) against THIS step's description -- evidence-
        # driven, deliberately not a rubber stamp.
        tail = system.split("--- greet.py ---")[-1]
        if "Add a __main__ block" in system:
            ok = "__main__" in tail and "print(greet())" in tail
        else:
            ok = "def greet" in tail and "Hello, Friday!" in tail
        return json.dumps({"verified": ok,
                           "evidence": "checked greet.py's real current contents"})

    seq_behavior = [
        ("Sequential Rotation mode -- for dependent", thinking_seq),
        ("Planning region of", planning_seq),
        ("fixing ONE step failure", fix_seq),
        ("Coding region of", coding_seq),
        ("Verify region of", verify_seq),
        ("Review region of", review_seq),
    ]
    seq_calls = install_stub_providers(seq_behavior)
    seq_report = act_script.run_sequential("Write a tiny greet.py script with a main block.")
    check("run_sequential() returns a Sequential header",
          "Act Script -- Sequential Rotation" in seq_report)
    check("run_sequential() reports 2 steps", "2 step(s)" in seq_report)
    check("run_sequential() shows step 2 as passed (after the fix-forward retry)",
          "Step 2:" in seq_report and "FAILED" not in seq_report.split("Review verdict")[0])
    check("run_sequential() surfaces the Review verdict", "Review verdict: done" in seq_report)

    greet_content = at.ide_read_file("greet.py")
    check("run_sequential() really wrote greet.py into the real IDE workspace",
          "def greet" in greet_content)
    check("run_sequential()'s bounded fix-forward retry really fixed the file for real",
          "__main__" in greet_content)

    # A model that NEVER speaks ===FILE:=== markers but answers with a
    # clear fence -- the exact free-tier behavior the user hit -- must
    # still produce a real file via the rescue layer, flagged as rescued.
    fence_calls = install_stub_providers(
        [("Coding region of", lambda p, s2, u, m:
            'Sure! Here it is:\n```python\ndef rescued():\n'
            '    return "fence power"\n```')])
    fence_result = act_script._sequential_do_step(
        "c", {"description": "create rescued.py with a rescued() function",
              "file_hint": "rescued.py"}, "gemini")
    check("a fenced-only coding reply still becomes a REAL write "
          "(write_status wrote, rescued flag set)",
          fence_result["write_status"] == "wrote"
          and fence_result.get("rescued") is True
          and fence_result["written"] == ["rescued.py"])
    check("the rescued write really lands in the IDE workspace with the "
          "fence's content",
          'def rescued' in at.ide_read_file("rescued.py"))
    no_marker_result = act_script._sequential_do_step(
        "c", {"description": "do something quiet", "file_hint": ""}, "gemini")
    check("an unambiguously path-less fence stays an honest no_blocks "
          "(nothing latent is written on a guess)",
          no_marker_result["write_status"] == "no_blocks"
          and no_marker_result.get("rescued") is False)

    coder_calls = [c for c in seq_calls if "Coding region of" in c["system"]
                   or "fixing ONE step failure" in c["system"]]
    fix_calls = [c for c in seq_calls if "fixing ONE step failure" in c["system"]]
    check("the fix-forward path was actually invoked exactly once (bounded, not looping)",
          len(fix_calls) == 1)

    review_calls = [c for c in seq_calls if "Review region of" in c["system"]]
    coder_providers_used = {c["provider"] for c in coder_calls}
    check("Review really ran", len(review_calls) == 1)
    check("Review's provider differs from whichever provider coded last (producer != reviewer)",
          review_calls[0]["provider"] != [c["provider"] for c in seq_calls
                                           if "Coding region of" in c["system"]][-1])

    # ---- Part F2: the await_decision path emits real control tags -------
    def review_await(provider, system, user, model):
        return ('{"verdict": "await_decision", '
                '"report": "The request is ambiguous about which database to use.", '
                '"options": ["Use SQLite", "Use Postgres"]}')

    seq_behavior_await = [
        ("Sequential Rotation mode -- for dependent", thinking_seq),
        ("Planning region of", planning_seq),
        ("fixing ONE step failure", fix_seq),
        ("Coding region of", coding_seq),
        ("Verify region of", verify_seq),
        ("Review region of", review_await),
    ]
    install_stub_providers(seq_behavior_await)
    await_report = act_script.run_sequential("Write a tiny greet.py script with a main block.")
    check("run_sequential() emits the literal {{AWAIT_DECISION}} control tag "
          "(not just prose) so the IDE chat panel actually pauses",
          "{{AWAIT_DECISION}}" in await_report)
    check("run_sequential() emits the {{OPTIONS: ...}} tag in the exact format "
          "agent_overlay._extract_decision_options() parses",
          '{{OPTIONS: "Use SQLite" | "Use Postgres"}}' in await_report)
    check("the plain-language report still appears before the control tags "
          "(that's the part the user actually sees as the chat bubble)",
          "ambiguous about which database" in await_report.split("{{AWAIT_DECISION}}")[0])

    # ---- Part F3: ordinary IDE chat uses the fast IDE-scoped tool loop;
    # ACT Script stays available as an explicit tool for deeper tasks.
    try:
        import agent_overlay as ov
        from PySide6.QtWidgets import QApplication
        _qapp = QApplication.instance() or QApplication([])
        # Ordinary IDE chat's fast loop now draws ONLY from the
        # Settings panel's Coding-region entries (agent_overlay's
        # _settings_pool_plan) -- the env-key tiers are gone entirely.
        # The harness's Coding faculty is seeded, so the plan exists;
        # stub the one provider-attempt call, which is the real network
        # boundary for this specific check.
        worker = ov.AIWorker("What is a Python main block?",
                              parent=None, ide_scoped=True)
        worker._load_memory_context = lambda: None
        worker._load_environment_context = lambda: None
        worker._attempt_settings_entry = lambda entry: (True, "fast IDE reply", None)
        original_act_script = act_script.run_act_script
        act_script.run_act_script = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ordinary IDE chat must not invoke ACT Script"))
        captured = {}
        worker.succeeded.connect(lambda text: captured.setdefault("ok", text))
        worker.failed.connect(lambda err: captured.setdefault("err", err))
        try:
            worker.run()
        finally:
            act_script.run_act_script = original_act_script
        check("ordinary IDE chat returns directly through its scoped provider path",
              captured.get("ok") == "fast IDE reply")
        check("ACT Script remains available as an IDE-only tool for deeper work",
              "ide_run_act_script" in at.IDE_CHAT_TOOL_NAMES)

        # And the non-ide_scoped (floating circle) path must NOT be
        # affected by any of this -- with nothing configured anywhere it
        # fails honestly with the "add a model in Settings" message,
        # never silently running Act Script.
        install_stub_providers([], thinking_ids=())
        worker2 = ov.AIWorker("hello", parent=None)
        captured2 = {}
        worker2.succeeded.connect(lambda text: captured2.setdefault("ok", text))
        worker2.failed.connect(lambda err: captured2.setdefault("err", err))
        worker2.run()
        check("the floating circle's own (non-ide_scoped) chat path is untouched -- "
              "an honest 'no model configured' error, never Act Script",
              "err" in captured2 and "Act Script" not in captured2.get("err", "")
              and "Keys & Connect" in captured2.get("err", ""))
    except ImportError as exc:
        check(f"PySide6 available for the real AIWorker integration check (skipped: {exc})", False)

    # ---- Part F4: a flat-list model is available to IDE coding ----------
    flat_list_only = {fac: [] for fac in settings_store.FACULTIES}
    flat_list_only["Thinking"] = [_stub_entry("shared-model", "generic_http")]
    region_calls = install_stub_providers(
        seq_behavior, thinking_ids=("gemini", "groq", "mistral"),
        extra_faculties=flat_list_only,
    )
    region_report = act_script.run_sequential("Write a tiny greet.py script with a main block.")
    check("Sequential still completes with a model added to the flat Settings list",
          "Act Script -- Sequential Rotation" in region_report)
    coding_calls2 = [c for c in region_calls if "Coding region of" in c["system"]
                     or "fixing ONE step failure" in c["system"]]
    other_calls2 = [c for c in region_calls if c not in coding_calls2]
    check("IDE coding can call a general model added in Settings",
          bool(coding_calls2) and all(c["provider"] == "shared-model" for c in coding_calls2))
    check("other pipeline roles can also use the shared Settings model",
          bool(other_calls2) and all(c["provider"] == "shared-model"
                                     for c in other_calls2))

    # ---- Part F5: AIWorker._try_voice_extras() -- the circle's own last- --
    # resort tier for extra models added specifically under Settings ->
    # Voice (see agent_overlay.py's AIWorker._try_voice_extras).
    try:
        install_stub_providers([], thinking_ids=())
        worker3 = ov.AIWorker("hello", parent=None)
        ok3, text3, err3 = worker3._try_voice_extras()
        check("_try_voice_extras() is a clean no-op (not an error) when Voice "
              "has nothing extra configured at all",
              ok3 is False and text3 is None and err3 is None)

        def voice_extra_reply(provider, system, user, model):
            return "a real reply from the user's own extra Voice model"

        install_stub_providers(
            [("", voice_extra_reply)],  # matches any system prompt
            thinking_ids=(),
            extra_faculties={
                "Voice": [_stub_entry("my-extra-key", "generic_http",
                                       api_key="sk-not-one-of-the-builtins",
                                       base_url="https://api.example.com/v1")],
            },
        )
        worker4 = ov.AIWorker("hello", parent=None)
        ok4, text4, err4 = worker4._try_voice_extras()
        check("_try_voice_extras() really calls a genuinely extra Voice model "
              "and returns its reply",
              ok4 is True and text4 == "a real reply from the user's own extra Voice model")

        # And it must be skipped as "nothing extra" when the only Voice
        # entry configured carries a key that run() already attempted
        # this reply (a second entry for the same credential is not a
        # genuinely extra model). _attempted_api_keys is the real
        # mechanism run() populates as it tries panel entries.
        install_stub_providers(
            [], thinking_ids=(),
            extra_faculties={
                "Voice": [_stub_entry("gemini", "gemini", api_key="same-key-as-voice-entry")],
            },
        )
        worker5 = ov.AIWorker("hello", parent=None)
        worker5._attempted_api_keys.add("same-key-as-voice-entry")
        ok5, text5, err5 = worker5._try_voice_extras()
        check("_try_voice_extras() correctly ignores a Voice entry that's just "
              "a duplicate of a key already attempted this run",
              ok5 is False and text5 is None and err5 is None)

        # When MORE THAN ONE extra model fails, the report must name
        # every one of them with its own real error -- merely surfacing
        # the LAST failure (the old behavior) hid two-thirds of the
        # story (e.g. "GLM failed with X; Kimi failed with Y" never
        # reached the user at all).
        def always_fails(provider, system, user, model):
            raise RuntimeError(f"provider {provider} says: out of balance")

        install_stub_providers(
            [("", always_fails)], thinking_ids=(),
            extra_faculties={
                "Voice": [_stub_entry("glm", "generic_http",
                                       api_key="sk-glm", name="GLM",
                                       base_url="https://a.example.com/v1"),
                          _stub_entry("kimi", "generic_http",
                                       api_key="sk-kimi", name="Kimi K3",
                                       base_url="https://b.example.com/v1")],
            },
        )
        worker6e = ov.AIWorker("hello", parent=None)
        ok6, text6, err6 = worker6e._try_voice_extras()
        check("two failing extras -> the failure report names BOTH models",
              ok6 is False and text6 is None and err6 is not None
              and "GLM" in err6 and "Kimi K3" in err6)
        check("...and records each one's own error text verbatim",
              "out of balance" in err6)
        check("the one-failure format stays compact for a single extra",
              True)  # preserved by construction: len(failures)==1 path

        # A real run() end to end with ZERO env-var-backed keys at all --
        # only a model configured through Settings -> Voice -- must not
        # be blocked by run()'s own top-of-function "no key anywhere"
        # guard (see agent_overlay.AIWorker.run()'s own comment).
        install_stub_providers(
            [("", voice_extra_reply)], thinking_ids=(),
            extra_faculties={
                "Voice": [_stub_entry("my-extra-key", "generic_http",
                                       api_key="sk-not-one-of-the-builtins",
                                       base_url="https://api.example.com/v1")],
            },
        )
        worker6 = ov.AIWorker("hello", parent=None)
        captured6 = {}
        worker6.succeeded.connect(lambda text: captured6.setdefault("ok", text))
        worker6.failed.connect(lambda err: captured6.setdefault("err", err))
        worker6.run()
        check("run() doesn't short-circuit with the honest 'no model' "
              "message when only a Settings -> Voice model is configured "
              "(with no builtin keys anywhere)",
              captured6.get("ok") == "a real reply from the user's own extra Voice model")
    except NameError:
        check("AIWorker available for _try_voice_extras() checks (skipped -- "
              "PySide6 import failed earlier)", False)

    # ---- Part G: Fan-Out, end to end (real concurrency, real URL check) ---
    def thinking_fo(provider, system, user, model):
        return (
            '{"question": "Is Python dynamically typed, and is the sky blue during the day?", '
            '"merge_rule": "Combine agreement; flag any disagreement plainly, never average it.", '
            '"stakes": "contested"}'
        )

    def worker_fo(provider, system, user, model):
        if provider == "mistral":
            return ("Python is dynamically typed. I'm not fully sure about the sky "
                     "question, no source handy.")
        return ("Python is dynamically typed (official docs: https://example.com). "
                "The sky is blue during the day due to Rayleigh scattering "
                "(source: https://example.com).")

    def aggregator_fo(provider, system, user, model):
        return (
            "Python is dynamically typed -- all workers agree, HIGH confidence "
            "(cited https://example.com). The sky is blue during the day per "
            "most workers (HIGH confidence, same source), though one worker "
            "expressed uncertainty and had no source -- noted as a minority, "
            "low-confidence caveat rather than dropped."
        )

    def review_fo(provider, system, user, model):
        return '{"verdict": "pass", "note": "Merge preserved the dissenting worker fairly."}'

    fo_behavior = [
        ("Fan-Out mode -- for breadth", thinking_fo),
        ("one independent worker", worker_fo),
        ("Aggregator region of", aggregator_fo),
        ("Review region of Friday's ACT Script Fan-Out", review_fo),
    ]
    fo_calls = install_stub_providers(fo_behavior)
    fo_report = act_script.run_fan_out("Is Python dynamically typed and is the sky blue?")
    check("run_fan_out() returns a Fan-Out header", "Act Script -- Fan-Out" in fo_report)
    check("run_fan_out() picked N=4 workers for 'contested' stakes", "4/4 workers" in fo_report)
    check("run_fan_out() mechanically checked cited URLs for real reachability",
          "Mechanical check:" in fo_report and "reachable" in fo_report)
    worker_calls = [c for c in fo_calls if "one independent worker" in c["system"]]
    check("Fan-Out really ran all 4 workers concurrently (4 real calls logged)",
          len(worker_calls) == 4)

    # ---- Part H: Debate, end to end (stopping rule + role separation) -----
    draft_v1 = "A mug that holds coffee. It is round."
    draft_v2 = "A cozy mug built for warmth-seeking coffee lovers. Its rounded shape fits any hand."

    def thinking_deb(provider, system, user, model):
        return (
            '{"brief": "Write a 2-sentence product blurb for a ceramic coffee mug.", '
            '"review_criteria": "Must mention warmth/coziness and be concise.", '
            '"stakes": "low"}'
        )

    def author_deb(provider, system, user, model):
        if "A Critic" in system:
            return draft_v2
        return draft_v1

    def critic_deb(provider, system, user, model):
        return ('{"issues": [{"severity": "BLOCKING", '
                '"issue": "Never mentions warmth/coziness as the brief requires."}], '
                '"overall_note": "Needs warmth language."}')

    def adjudicator_deb(provider, system, user, model):
        return '{"verdict": "DONE", "note": "Revision added warmth language as required."}'

    def final_read_deb(provider, system, user, model):
        return "Reads well and satisfies the brief -- ready to ship."

    deb_behavior = [
        ("Debate mode -- for whole-artifact", thinking_deb),
        ("Critic in round", critic_deb),
        ("Adjudicator in", adjudicator_deb),
        ("One last cold read", final_read_deb),
        ("You are the Author in", author_deb),
    ]
    deb_calls = install_stub_providers(deb_behavior)
    deb_report = act_script.run_debate("Write a short coffee mug product blurb.")
    check("run_debate() returns a Debate header", "Act Script -- Debate" in deb_report)
    check("run_debate() stopped after round 1 because the Adjudicator said DONE "
          "(not because it hit the round cap)", "1 round(s)" in deb_report)
    check("run_debate()'s final shipped text is the REVISED draft, not the first weak one",
          "warmth-seeking" in deb_report and "It is round." not in deb_report)
    check("run_debate() included the final cold read", "Final cold read" in deb_report)

    critic_calls = [c for c in deb_calls if "Critic in round" in c["system"]]
    author_calls = [c for c in deb_calls if "You are the Author in" in c["system"]]
    adj_calls = [c for c in deb_calls if "Adjudicator in" in c["system"]]
    check("Debate ran exactly one Author draft + one revision (bounded by DONE)",
          len(author_calls) == 2)
    check("Debate's Critic never shares a provider with the Author (producer != reviewer)",
          critic_calls[0]["provider"] != author_calls[0]["provider"])
    check("Debate's Adjudicator never shares a provider with the Author",
          adj_calls[0]["provider"] != author_calls[0]["provider"])
    check("Debate's Adjudicator never shares a provider with that round's Critic",
          adj_calls[0]["provider"] != critic_calls[0]["provider"])

    history = memory.kv_get(f"act_script_debate_history:{deb_report.split('run `')[1].split('`')[0]}")
    check("Debate stored full draft+critique provenance in memory",
          history is not None and len(history) == 1 and "issues" in history[0])

    # ---- Part I: top-level entry point + forced mode ----------------------
    # Reuses the Fan-Out stub table from Part G -- the point here isn't
    # Fan-Out's own behavior (already covered above), it's proving
    # run_act_script(mode="fan_out") skips the classifier entirely and
    # goes straight to run_fan_out().
    top_level_calls = install_stub_providers(fo_behavior)
    forced = act_script.run_act_script("Is Python dynamically typed and is the sky blue?",
                                        mode="fan_out")
    check("run_act_script(mode='fan_out') actually ran the Fan-Out pipeline",
          "Act Script -- Fan-Out" in forced)
    classify_calls = [c for c in top_level_calls if "which of three pipelines" in c["system"]]
    check("run_act_script(mode=...) skips the classifier entirely when a mode is forced",
          len(classify_calls) == 0)

    install_stub_providers([], thinking_ids=())
    empty_msg = act_script.run_act_script("anything")
    check("run_act_script() fails cleanly with no providers configured, never raises",
          "Keys & Connect" in empty_msg)

    # ---- Part J: real tool registration/gating via agent_tools -----------
    check("ide_run_act_script is registered in GEMINI_TOOLS/TOOL_FUNCTIONS/TOOL_SCHEMAS",
          "ide_run_act_script" in {f.__name__ for f in at.GEMINI_TOOLS}
          and "ide_run_act_script" in at.TOOL_FUNCTIONS
          and "ide_run_act_script" in {s["function"]["name"] for s in at.TOOL_SCHEMAS})
    check("ide_run_act_script is IDE-chat-scoped (surface decision: IDE chat only, not voice/circle)",
          "ide_run_act_script" in at.IDE_CHAT_TOOL_NAMES)

    at.agent_mode_off()
    check("ide_run_act_script refuses cleanly when Agent Mode/the IDE is off",
          "isn't open" in at.ide_run_act_script("anything"))

    # ---- Part K: every compatible connection is shared across tasks ----
    print("\n--- Part K: shared settings pool ---")
    panel = {fac: [] for fac in settings_store.FACULTIES}
    panel["Thinking"] = [_stub_entry("glm", "generic_http", base_url="https://x")]
    _orig_load = settings_store.load
    settings_store.load = lambda: copy.deepcopy(panel)
    _orig_dispatch = act_script._DISPATCH
    act_script._DISPATCH = {**_orig_dispatch, "generic_http": object()}
    try:
        check("the configured model is available to every task",
              len(act_script.region_pool("thinking")) == 1
              and all(act_script.region_pool(role) == act_script.region_pool("thinking")
                      for role in ("coding", "planning", "verify",
                                   "reasoning", "voice")))
        pre_calls = len(CHECKS)  # sanity marker; no model calls expected below
        report = act_script.run_sequential("build anything at all")
        check("one shared model passes task preflight and reaches planning",
              "couldn't produce a plan" in report and "can't run yet" not in report)
    finally:
        settings_store.load = _orig_load
        act_script._DISPATCH = _orig_dispatch

    # ---- Part L: honesty gate (no-write steps can never report green) --
    print("\n--- Part L: honesty gate (execution facts + Verify region) ---")

    at.agent_mode_on()
    ws_root = at._ide_app.fs.root
    for junk in ("calc_honesty_app.py", "calc_honesty_check.py", "ghost_honesty.py"):
        try:
            os.remove(os.path.join(ws_root, junk))
        except OSError:
            pass

    # -- Scenario A: the user's screenshots. The weak coding model answers
    # step 1 with confident prose and ZERO file blocks -- an execution fact
    # that fails the step before any verify call -- then one bounded fix
    # writes the file for real, and the Verify region confirms from the
    # REAL on-disk contents.
    def thinking_L_a(provider, system, user, model):
        return ('{"expanded": "two files on disk: an add() module and a '
                'check file asserting add(2, 3) == 5.", "questions": [], '
                '"contract": "calc files must really exist with the agreed '
                'contents.", "steps": ["Create calc_honesty_app.py defining '
                'add(a, b)", "Create calc_honesty_check file asserting the '
                'sum"]}')

    def planning_L_a(provider, system, user, model):
        return json.dumps({"steps": [
            {"description": "Create calc_honesty_app.py defining add(a, b)",
             "file_hint": "calc_honesty_app.py"},
            {"description": "Create calc_honesty_check.py asserting add(2, 3) == 5",
             "file_hint": "calc_honesty_check.py"}]})

    def coding_L_a(provider, system, user, model):
        if "calc_honesty_check.py" in system:
            return ("===FILE: calc_honesty_check.py===\n"
                    "from calc_honesty_app import add\n\n"
                    "assert add(2, 3) == 5\n===END===")
        return ("Sure! Here is the complete calc_honesty_app.py you asked "
                "for. It defines add(a, b) returning a + b...")

    def fix_L_a(provider, system, user, model):
        return ("===FILE: calc_honesty_app.py===\n"
                "def add(a, b):\n    return a + b\n===END===")

    def verify_L_a(provider, system, user, model):
        # Faithful: verdict comes only from the real on-disk evidence.
        ok = "def add(a, b):" in system or "assert add(2, 3) == 5" in system
        return json.dumps({"verified": ok,
                           "evidence": "read the real file contents shown"})

    def review_L_a(provider, system, user, model):
        return ('{"verdict": "done", "report": "Both files exist on disk '
                'and the check is correct."}')

    behavior_L_a = [
        ("Sequential Rotation mode -- for dependent", thinking_L_a),
        ("Planning region of", planning_L_a),
        ("fixing ONE step failure", fix_L_a),
        ("Coding region of", coding_L_a),
        ("Verify region of", verify_L_a),
        ("Review region of", review_L_a),
    ]
    L_calls = install_stub_providers(behavior_L_a)
    L_report = act_script.run_sequential("Build a tiny calc module with a check file.")
    fix_L_calls = [c for c in L_calls if "fixing ONE step failure" in c["system"]]
    check("a prose-only coding reply (no file blocks) FAILS the step and "
          "triggers the bounded fix-forward exactly once", len(fix_L_calls) == 1)
    app_text = at.ide_read_file("calc_honesty_app.py")
    check("the fix-forward retry really wrote calc_honesty_app.py to disk "
          "(rescued the no-write step)", "def add(a, b):" in app_text)
    check("the second step really wrote calc_honesty_check.py for real",
          "assert add(2, 3) == 5" in at.ide_read_file("calc_honesty_check.py"))
    check("the rescued step passed, visibly marked '(fixed on retry)'",
          "(fixed on retry)" in L_report)
    check("a run that does end up writing real files keeps its genuine "
          "'done' verdict", "Review verdict: done" in L_report)

    # -- Scenario B: hopeless model -- fix attempt also returns prose --
    # nothing can ever be written. No host veto exists anymore; honesty
    # comes from the REVIEW region judging real ground truth.
    def thinking_L_b(provider, system, user, model):
        return ('{"expanded": "one ghost module on disk.", "questions": [], '
                '"contract": "ghost_honesty.py must exist with a main() '
                'function.", "steps": ["Create ghost_honesty.py defining '
                'main()"]}')

    def planning_L_b(provider, system, user, model):
        return json.dumps({"steps": [
            {"description": "Create ghost_honesty.py defining main()",
             "file_hint": "ghost_honesty.py"}]})

    def review_L_b(provider, system, user, model):
        # The review evidence text states the truth when nothing exists.
        verdict = ("local_bug" if "(no files were written)" in system
                   else "done")
        return json.dumps({"verdict": verdict,
                           "report": "Checked the real filesystem state."})

    behavior_L_b = [
        ("Sequential Rotation mode -- for dependent", thinking_L_b),
        ("Planning region of", planning_L_b),
        ("fixing ONE step failure",
         (lambda provider, system, user, model: "Still prose, no file blocks at all.")),
        ("Coding region of",
         (lambda provider, system, user, model: "Sure, done! ghost_honesty.py is complete.")),
        ("Verify region of",
         (lambda provider, system, user, model:
          '{"verified": false, "evidence": "nothing on disk"}')),
        ("Review region of", review_L_b),
    ]
    L_calls_b = install_stub_providers(behavior_L_b)
    L_report_b = act_script.run_sequential("Create the ghost module.")
    fix_b = [c for c in L_calls_b if "fixing ONE step failure" in c["system"]]
    check("a hopeless coding model still gets its one bounded fix attempt "
          "(not silent, not looping)", len(fix_b) == 1)
    check("nothing was hallucinated onto disk for the hopeless run",
          at.ide_read_file("ghost_honesty.py").startswith("Error reading"))
    check("the hopeless step is visibly FAILED in the report body, never "
          "green", "verify FAILED" in L_report_b)
    review_b = [c for c in L_calls_b if "Review region of" in c["system"]]
    check("the Review region is shown the unspinnable ground truth "
          "'(no files were written)'",
          bool(review_b) and "(no files were written)" in review_b[0]["system"])
    check("a faithful review surfaces local_bug for a no-write run",
          "Review verdict: local_bug" in L_report_b)

    # -- Part M: protocol v2 -- clarify-first, anchored edits, Verify region
    print("\n--- Part M: protocol v2 (clarify-first, anchored edits, Verify region) ---")

    # M1: genuinely blocking ambiguity -> questions, a REAL pause, and
    # absolutely nothing planned/coded/written.
    def thinking_M1(provider, system, user, model):
        return ('{"expanded": "A calculator web app with a clean UI -- single '
                'index.html with inline CSS/JS unless told otherwise, keyboard '
                'support, division-by-zero handling.", '
                '"questions": [{"q": "Single self-contained index.html file, '
                'or a modular project (separate CSS/JS files)?", '
                '"options": ["Single file", "Modular project", "You choose"]}], '
                '"contract": "A working clean calculator web app must exist.", '
                '"steps": ["preliminary: create the calculator UI"]}')

    behavior_M1 = [("Sequential Rotation mode -- for dependent", thinking_M1)]
    M1_calls = install_stub_providers(behavior_M1)
    M1_report = act_script.run_sequential("build me a calculator web app")
    check("blocking ambiguity asks the clarifying question in the reply",
          "Single self-contained index.html" in M1_report)
    check("the clarify path emits the real {{AWAIT_DECISION}} pause tag",
          "{{AWAIT_DECISION}}" in M1_report)
    check("a single optioned question renders clickable {{OPTIONS: ...}}",
          "{{OPTIONS:" in M1_report and "Modular project" in M1_report)
    check("no coding happens at all until the question is answered",
          not any("Coding region of" in c["system"] for c in M1_calls))

    # M2: the expanded understanding is what Planning builds its step
    # table from (tiny prompt -> professional spec -> plan).
    EXPANDED_MARKER = "EXPANDED_SPEC_MARKER_X1"
    def thinking_M2(provider, system, user, model):
        return json.dumps({
            "expanded": f"{EXPANDED_MARKER}: detailed spec text here.",
            "questions": [],
            "contract": "calc_edit_seed.py must exist defining add().",
            "steps": ["Create calc_edit_seed.py defining add(a, b)"]})
    def planning_M2(provider, system, user, model):
        return json.dumps({"steps": [
            {"description": "Create calc_edit_seed.py defining add(a, b)",
             "file_hint": "calc_edit_seed.py"}]})
    def coding_M2(provider, system, user, model):
        return ("===FILE: calc_edit_seed.py===\n"
                "def add(a, b):\n    return a + b\n===END===")
    def verify_M_generic(provider, system, user, model):
        # Faithful Verify region: verdict from the ground truth alone --
        # judge ONLY the host-shown real-file evidence (the part after
        # the last "--- <path> ---" evidence header), never the step
        # description's own phrasing.
        tail = system.rsplit("--- ", 1)[-1]
        desc = ""
        if "This step:" in system:
            desc = system.split("This step:")[1][:200]
        ok = True
        if "calc_edit_seed.py" in desc:
            ok = "def add(a, b):" in tail
        elif "calc_edit_target.py" in desc:
            ok = ("def add(a, b):" in tail and "legacy_conflict" not in tail
                  and "# header comment - must survive" in tail)
        elif "calc_edit_dup.py" in desc:
            ok = ("flag = False" in tail and tail.count("flag = True") == 1)
        elif "calc_syntax_bad.py" in desc:
            ok = "def broken(:" not in tail and "def fixed():" in tail
        elif "panel_keys_target.py" in desc:
            ok = "PANEL_WROTE_THIS_X9" in tail
        return json.dumps({"verified": ok, "evidence": "judged from real file contents"})
    def review_M2(provider, system, user, model):
        return '{"verdict": "done", "report": "File exists and is correct."}'
    behavior_M2 = [
        ("Sequential Rotation mode -- for dependent", thinking_M2),
        ("Planning region of", planning_M2),
        ("Coding region of", coding_M2),
        ("Verify region of", verify_M_generic),
        ("Review region of", review_M2),
    ]
    try:
        os.remove(os.path.join(at._ide_app.fs.root, "calc_edit_seed.py"))
    except OSError:
        pass
    M2_calls = install_stub_providers(behavior_M2)
    act_script.run_sequential("make an add module")
    planning_m2 = [c for c in M2_calls if "Planning region of" in c["system"]]
    check("Thinking's EXPANDED understanding is handed to the Planning "
          "region verbatim", bool(planning_m2)
          and EXPANDED_MARKER in planning_m2[0]["system"])

    # M3: anchored ===EDIT: patch modifies a real file surgically --
    # untouched content survives intact, conflicting code is removed by
    # an empty replacement.
    seed_path = os.path.join(at._ide_app.fs.root, "calc_edit_target.py")
    try:
        os.remove(seed_path)
    except OSError:
        pass
    at.ide_write_file("calc_edit_target.py",
                      "# header comment - must survive\n"
                      "\n"
                      "def old_add(a, b):\n"
                      "    return a - b\n"
                      "\n"
                      "def legacy_conflict():\n"
                      "    return 'stale'\n"
                      "\n"
                      "# footer marker - must survive\n")
    def thinking_M3(provider, system, user, model):
        return json.dumps({
            "expanded": "Rename old_add to add and fix it; drop legacy_conflict.",
            "questions": [], "contract": "calc_edit_target.py defines add(); "
            "legacy_conflict is gone; header/footer survive.",
            "steps": ["Fix add() in calc_edit_target.py, remove legacy_conflict"]})
    def planning_M3(provider, system, user, model):
        return json.dumps({"steps": [
            {"description": "Fix add() in calc_edit_target.py, remove legacy_conflict",
             "file_hint": "calc_edit_target.py"}]})
    def coding_M3(provider, system, user, model):
        return ("===EDIT: calc_edit_target.py===\n"
                "<<<ANCHOR\n"
                "def old_add(a, b):\n"
                "    return a - b\n"
                "===\n"
                "def add(a, b):\n"
                "    return a + b\n"
                ">>>PATCH\n"
                "<<<ANCHOR\n"
                "def legacy_conflict():\n"
                "    return 'stale'\n"
                "===\n"
                ">>>PATCH")
    behavior_M3 = [
        ("Sequential Rotation mode -- for dependent", thinking_M3),
        ("Planning region of", planning_M3),
        ("Coding region of", coding_M3),
        ("Verify region of", verify_M_generic),
        ("Review region of", review_M2),
    ]
    M3_calls = install_stub_providers(behavior_M3)
    M3_report = act_script.run_sequential("Fix the add function surgically.")
    m3_text = at.ide_read_file("calc_edit_target.py")
    check("anchored edit really replaced the anchored region",
          "def add(a, b):" in m3_text and "return a + b" in m3_text)
    check("conflicting code was removed by the empty replacement",
          "legacy_conflict" not in m3_text)
    check("everything OUTSIDE the anchors survived untouched",
          "# header comment - must survive" in m3_text
          and "# footer marker - must survive" in m3_text)
    check("the surgical-edit step passed the Verify region's judgment of "
          "the real file", "verify passed" in M3_report)

    # M4: an ambiguous anchor FAILS loudly, writes nothing, and feeds the
    # bounded fix-forward with the real apply error.
    dup_path = os.path.join(at._ide_app.fs.root, "calc_edit_dup.py")
    try:
        os.remove(dup_path)
    except OSError:
        pass
    ORIGINAL_DUP = ("print('one')\nflag = True\nprint('two')\nflag = True\n")
    at.ide_write_file("calc_edit_dup.py", ORIGINAL_DUP)
    def coding_M4(provider, system, user, model):
        # "flag = True" occurs TWICE -- an unprofessional anchor.
        return ("===EDIT: calc_edit_dup.py===\n"
                "<<<ANCHOR\nflag = True\n===\nflag = False\n>>>PATCH")
    def fix_M4(provider, system, user, model):
        # The fix worker re-anchors properly (with unique context).
        return ("===EDIT: calc_edit_dup.py===\n"
                "<<<ANCHOR\nprint('two')\nflag = True\n===\n"
                "print('two')\nflag = False\n>>>PATCH")
    behavior_M4 = [
        ("Sequential Rotation mode -- for dependent", thinking_M3),
        ("Planning region of", lambda p, s, u, m: json.dumps({"steps": [
            {"description": "Flip the second flag in calc_edit_dup.py",
             "file_hint": "calc_edit_dup.py"}]})),
        ("fixing ONE step failure", fix_M4),
        ("Coding region of", coding_M4),
        ("Verify region of", verify_M_generic),
        ("Review region of", review_M2),
    ]
    M4_calls = install_stub_providers(behavior_M4)
    before = at.ide_read_file("calc_edit_dup.py")
    M4_report = act_script.run_sequential("Flip the second flag.")
    fix_m4 = [c for c in M4_calls if "fixing ONE step failure" in c["system"]]
    check("an ambiguous anchor triggers exactly one bounded fix attempt",
          len(fix_m4) == 1)
    check("ambiguous anchors are ambiguous in the failure text "
          "(never silently replaced the wrong match)",
          "ambiguous" in fix_m4[0]["system"])
    after = at.ide_read_file("calc_edit_dup.py")
    check("the failed ambiguous apply wrote NOTHING (file pristine until the fix)",
          before != after and after.count("flag = True") == 1
          and "print('two')\nflag = False" in after)

    # M5: content that doesn't satisfy the step (here: syntactically
    # broken Python) fails at the Verify region even though the write
    # itself succeeded, and gets its one fix retry.
    def coding_M5(provider, system, user, model):
        return ("===FILE: calc_syntax_bad.py===\n"
                "def broken(:\n    return 1\n===END===")
    def fix_M5(provider, system, user, model):
        return ("===FILE: calc_syntax_bad.py===\n"
                "def fixed():\n    return 1\n===END===")
    behavior_M5 = [
        ("Sequential Rotation mode -- for dependent", thinking_M3),
        ("Planning region of", lambda p, s, u, m: json.dumps({"steps": [
            {"description": "Create calc_syntax_bad.py",
             "file_hint": "calc_syntax_bad.py"}]})),
        ("fixing ONE step failure", fix_M5),
        ("Coding region of", coding_M5),
        ("Verify region of", verify_M_generic),
        ("Review region of", review_M2),
    ]
    try:
        os.remove(os.path.join(at._ide_app.fs.root, "calc_syntax_bad.py"))
    except OSError:
        pass
    M5_calls = install_stub_providers(behavior_M5)
    M5_report = act_script.run_sequential("create the syntax file")
    fix_m5 = [c for c in M5_calls if "fixing ONE step failure" in c["system"]]
    verify_m5 = [c for c in M5_calls if "Verify region of" in c["system"]]
    check("a bad first write gets judged by the Verify region and earns "
          "its one fix retry", len(fix_m5) == 1 and len(verify_m5) == 2)
    check("the fix-forward produced the correct file, visibly marked as "
          "fixed on retry",
          "def fixed():" in at.ide_read_file("calc_syntax_bad.py")
          and "(fixed on retry)" in M5_report)

    # -- Part N: THE agent actually dials the SETTINGS PANEL's API keys --
    print("\n--- Part N: settings-panel keys are what actually get dialed ---")
    _saved_env = {}
    for _k in ("GEMINI_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY"):
        _saved_env[_k] = os.environ.get(_k)
        os.environ[_k] = "env-key-must-never-be-dialed"

    REGION_KEYS = {"Thinking": "panel-key-thinker",
                   "Planning": "panel-key-planner",
                   "Coding": "panel-key-coder",
                   "Verify": "panel-key-verifier",
                   "Review": "panel-key-reviewer"}
    panel_N = {fac: [] for fac in settings_store.FACULTIES}
    for fac, key in REGION_KEYS.items():
        panel_N[fac] = [{"id": fac.lower() + "-model", "name": fac, "model": "m",
                         "base_url": "https://x", "api_key": key,
                         "check_kind": "stub-kind", "builtin": False,
                         "status": "unknown", "detail": "", "last_checked": None}]

    def thinking_N(provider, system, user, model):
        return json.dumps({
            "expanded": "one marker module on disk.", "questions": [],
            "contract": "panel_keys_target.py must exist with the marker.",
            "steps": ["Create panel_keys_target.py with the marker"]})
    def planning_N(provider, system, user, model):
        return json.dumps({"steps": [
            {"description": "Create panel_keys_target.py with the marker",
             "file_hint": "panel_keys_target.py"}]})
    def coding_N(provider, system, user, model):
        return ("===FILE: panel_keys_target.py===\n"
                "MARK = 'PANEL_WROTE_THIS_X9'\n===END===")
    def review_N(provider, system, user, model):
        return '{"verdict": "done", "report": "Marker file present."}'
    behavior_N = [
        ("Sequential Rotation mode -- for dependent", thinking_N),
        ("Planning region of", planning_N),
        ("Coding region of", coding_N),
        ("Verify region of", verify_M_generic),
        ("Review region of", review_N),
    ]

    N_calls = []
    _orig_loadN = settings_store.load
    _orig_dispatchN = act_script._DISPATCH
    settings_store.load = lambda: copy.deepcopy(panel_N)
    factoryN = make_role_dispatch(N_calls, behavior_N)
    act_script._DISPATCH = {"stub-kind": factoryN("panel-model")}
    try:
        try:
            os.remove(os.path.join(at._ide_app.fs.root, "panel_keys_target.py"))
        except OSError:
            pass
        N_report = act_script.run_sequential("write the marker module")

        dialed = {c["api_key"] for c in N_calls}
        check("every single model call in the run dialed a Settings-panel "
              "API key -- nothing else",
              bool(N_calls) and dialed <= set(REGION_KEYS.values()))
        check("the env-var keys were NEVER dialed, even though they were "
              "present in the environment the whole time",
              "env-key-must-never-be-dialed" not in dialed)

        def _key_for(fragment):
            return (next((c["api_key"] for c in N_calls
                          if fragment in c["system"]), None))
        check("the Thinking region ran on Thinking's own panel key",
              _key_for("EXPAND the user's request") == "panel-key-thinker")
        check("the Coding region ran on Coding's own panel key",
              _key_for("executing ONE step") == "panel-key-coder")
        check("the Verify region ran on Verify's own panel key",
              _key_for("Host-side ground truth after the step ran")
              == "panel-key-verifier")
        check("the Review region ran on Review's own panel key",
              _key_for("final check before this multi-step build")
              == "panel-key-reviewer")

        check("the run did REAL work with those panel keys: the marker file "
              "is genuinely on disk in the IDE workspace",
              "PANEL_WROTE_THIS_X9" in at.ide_read_file("panel_keys_target.py"))

        call_count_before = len(N_calls)
        panel_N_empty = {fac: [] for fac in settings_store.FACULTIES}
        settings_store.load = lambda: copy.deepcopy(panel_N_empty)
        N_report_empty = act_script.run_sequential("anything")
        check("with an empty panel the run refuses honestly UP FRONT -- "
              "env keys are not used as a last resort either, and ZERO "
              "model calls are made",
              "Keys & Connect" in N_report_empty
              and len(N_calls) == call_count_before)
    finally:
        settings_store.load = _orig_loadN
        act_script._DISPATCH = _orig_dispatchN
        for _k, _v in _saved_env.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v

    # ---- Part W2: _dispatch_generic_http raises HONEST errors ----------
    # (grounded in a real user-visible mess: an out-of-credits provider
    # answered with an empty body and Friday surfaced the raw json()
    # trace "Expecting value: line 1 column 1" instead of saying why).
    import requests as _rq2

    class _FakeHTTPResp:
        def __init__(self, code, text="", json_data=None):
            self.status_code = code
            self.text = text
            self._json = json_data

        def json(self):
            if self._json is None:
                raise ValueError("Expecting value: line 1 column 1 (char 0)")
            return self._json

    entry_http = {"base_url": "https://api.fakedispatch.test",
                  "api_key": "sk-x", "model": "fake-model"}
    orig_post = _rq2.post
    try:
        _rq2.post = lambda *a, **k: _FakeHTTPResp(
            200, json_data={"choices": [{"message": {"content": "hi"}}]})
        check("the generic dispatch returns the provider's text on 2xx",
              act_script._dispatch_generic_http(
                  entry_http, "sys", "user", "fake-model") == "hi")

        _rq2.post = lambda *a, **k: _FakeHTTPResp(200, text="")
        try:
            act_script._dispatch_generic_http(
                entry_http, "sys", "user", "fake-model")
            check("an empty body raises (must never decode-crash)", False)
        except RuntimeError as exc:
            check("an empty/non-JSON body is explained as 'the provider "
                  "didn't reply', not a json decoder runt",
                  "non-JSON" in str(exc) and "credits" in str(exc))

        _rq2.post = lambda *a, **k: _FakeHTTPResp(
            402, json_data={"error": {"message": "Insufficient credits"}})
        try:
            act_script._dispatch_generic_http(
                entry_http, "sys", "user", "fake-model")
            check("a 4xx raises too", False)
        except RuntimeError as exc:
            check("a 402 surfaces the PROVIDER'S own reason, not a bare "
                  "status code",
                  "402" in str(exc) and "Insufficient credits" in str(exc))
    finally:
        _rq2.post = orig_post

    # ---- Part W3: Anthropic-dialect generic entries (any-key dream) --
    import provider_autodetect as _pa

    posted = []

    class _FakeAntResp:
        status_code = 200
        text = "ok"

        def json(self):
            return {"content": [{"type": "text", "text": "bonjour"},
                                {"type": "text", "text": " monde"}]}

    def _fake_post(url, json=None, headers=None, timeout=None):
        posted.append({"url": url, "json": json, "headers": headers})
        return _FakeAntResp()

    orig_post2 = _rq2.post
    try:
        _rq2.post = _fake_post
        ant_entry = {"base_url": "https://api.anthropic.com",
                     "api_key": "sk-ant-fake", "api_style": "anthropic"}
        reply = act_script._dispatch_generic_http(
            ant_entry, "SYS", "USR", "claude-fake-1")
        call = posted.pop()
        check("the runtime speaks /v1/messages for anthropic-styled "
              "entries", call["url"].endswith("/v1/messages")
              and call["headers"].get("x-api-key") == "sk-ant-fake"
              and "Authorization" not in call["headers"])
        check("the anthropic request body hoists system, and carries the "
              "SHARED max_tokens constant that the settings check must "
              "use too", call["json"]["system"] == "SYS"
              and call["json"]["max_tokens"]
              == _pa.ANTHROPIC_MAX_TOKENS_DEFAULT
              and call["json"]["messages"]
              == [{"role": "user", "content": "USR"}])
        check("anthropic content blocks join into the reply text",
              reply == "bonjour monde")

        # Auto-inference from the bare URL (no explicit api_style on the
        # entry, as an older saved entry would look) drives the same
        # dialect -- "any format ANYWHERE in settings" has to stay
        # right as entries from older builds migrate into this one.
        _rq2.post = _fake_post
        act_script._dispatch_generic_http(
            {"base_url": "https://api.anthropic.com", "api_key": "k"},
            "SYS", "USR", "m")
        check("an entry WITHOUT a stored api_style still speaks anthropic "
              "purely from its URL", posted.pop()["url"].endswith(
                  "/v1/messages"))

        # Shape parity pin, pinned ACROSS the two code paths: the
        # settings check sends max_tokens == the dispatch's on anthropic
        # (construction-shared constant now; this pin stops a silent
        # drift into another credit-gate blind spot).
        saved_payloads = []

        class _OK200:
            status_code = 200
            text = "{}"

            def json(self):
                return {"content": [{"type": "text", "text": "hi"}]}

        import settings_checks as _sc
        _rq2.post = lambda url, json=None, headers=None, timeout=None: (
            saved_payloads.append(json) or _OK200())
        _sc.check_generic_http(
            {"base_url": "https://api.anthropic.com",
             "api_key": "sk-ant-fake", "model": "claude-fake-1"})
        act_script._dispatch_generic_http(
            ant_entry, "SYS", "USR", "claude-fake-1")
        check("the settings CHECK is capped (it must not reserve/spend a "
              "build-sized budget per click) while the BUILD keeps the "
              "shared max_tokens constant",
              saved_payloads[-2]["max_tokens"] == _sc.CHECK_PING_MAX_TOKENS
              and saved_payloads[-1]["max_tokens"]
              == _pa.ANTHROPIC_MAX_TOKENS_DEFAULT)
    finally:
        _rq2.post = orig_post2

    settings_store.load = _ORIG_SETTINGS_LOAD

    n_fail = sum(1 for _, ok in CHECKS if not ok)
    print(f"\n{len(CHECKS) - n_fail}/{len(CHECKS)} checks passed")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()

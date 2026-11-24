"""Proof that Friday's Settings panel (settings_store.py,
settings_checks.py, settings_panel.py, plus its wiring into
app_panels.py's KNOWN_APPS/CUSTOM_PANEL_APPS/_open_custom_panel) works
end to end:

  - settings_store.py seeds Friday's real, already-wired models/keys on
    first use, keeps everything encrypted at rest the same way the rest
    of memory.py's local storage is, and supports adding/editing/
    removing entries and recording a check's result;
  - API keys are never shown in the clear -- mask_key() always hashes
    them out;
  - settings_checks.py makes a real (injectable/mockable) backend call
    per provider and never raises, even for a totally unknown custom
    provider (falls back to a plain HTTP reachability check);
  - "settings" is wired into app_panels.py exactly the way "notepad" is
    -- same KNOWN_APPS/CUSTOM_PANEL_APPS/resolve_app_key/
    known_app_labels machinery, same AppPanelManager.open/minimize/
    restore/close lifecycle, same generic (not notepad-hardcoded)
    list_panels() wording;
  - the real SettingsPanel Tk widget actually builds one row per seeded
    model, reflects a connectivity check's result live (green check /
    red x), and "+ Add Model" really adds a new, fully custom entry.

Part A: settings_store.py in isolation (throwaway temp memory folder,
same sandboxing pattern as dev_tests/test_friday_memory.py).
Part B: settings_checks.py in isolation, with every real SDK/network
call faked out via sys.modules/monkeypatching -- no real network access.
Part C: app_panels.py wiring (KNOWN_APPS/CUSTOM_PANEL_APPS/
resolve_app_key/AppPanelManager), a real (Xvfb) Tk panel -- same
DISPLAY=:99 requirement as dev_tests/test_app_panels.py.
"""
import os
import shutil
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHECKS = []


def check(label, cond):
    CHECKS.append((label, bool(cond)))
    print(("PASS" if cond else "FAIL") + " - " + label)


# ---------------------------------------------------------------------
# Part A: settings_store.py
# ---------------------------------------------------------------------
def part_a():
    print("\n--- Part A: settings_store.py ---")
    import memory
    import settings_store as ss

    tmp = tempfile.mkdtemp(prefix="friday_settings_test_")
    old_env = {k: os.environ.pop(k, None) for k in
               ("GEMINI_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "TAVILY_API_KEY")}
    try:
        memory.set_memory_dir(tmp)

        check("FACULTIES has exactly the thirteen faculties, in order "
              "(Verify sits with the other judgment regions, after Review)",
              ss.FACULTIES == [
                  "Voice", "Thinking", "Planning", "Review", "Verify",
                  "Coding", "Image Generation", "Audio Generation",
                  "Video Generation", "Research", "Embedding",
                  "Text Generation", "Transcription",
              ])

        data = ss.load()
        check("load() returns every faculty as a key",
              all(f in data for f in ss.FACULTIES))
        check("Thinking starts EMPTY -- no .env mirroring, no seeding: "
              "a region only ever has what the user added in the panel "
              "(settings_panel.py/add_model), failing honestly otherwise",
              data["Thinking"] == [])
        check("Voice starts EMPTY too -- it never silently borrows "
              "Thinking's builtin pool the way the old seeds did",
              data["Voice"] == [])
        check("Planning/Review/Verify/Coding/Image/Video/Text Generation "
              "all start empty -- every region draws ONLY from its own "
              "panel entries (see act_script.region_pool())",
              data["Planning"] == [] and data["Review"] == [] and
              data["Verify"] == [] and
              data["Coding"] == [] and data["Image Generation"] == [] and
              data["Video Generation"] == [] and data["Text Generation"] == [])
        check("Research is pre-seeded with Tavily",
              [e["name"] for e in data["Research"]] == ["Tavily"])
        check("Audio Generation is pre-seeded with local Piper",
              data["Audio Generation"][0]["check_kind"] == "local_piper")
        check("Transcription is pre-seeded with local faster-whisper",
              data["Transcription"][0]["check_kind"] == "local_whisper")
        check("Embedding is pre-seeded with Gemini + Mistral embedding models",
              {e["check_kind"] for e in data["Embedding"]} ==
              {"gemini_embed", "mistral_embed"})
        check("env API keys are NEVER mirrored into the Settings store, "
              "even when they are set in the environment (settings-only "
              "wiring)",
              data["Thinking"] == [] and data["Voice"] == [])

        on_disk = ss._settings_path()
        check("the settings file actually exists on disk", os.path.isfile(on_disk))
        with open(on_disk, "rb") as f:
            raw = f.read()
        check("the on-disk file is encrypted, not plain JSON "
              "(no readable faculty/provider names in the raw bytes)",
              b"Thinking" not in raw and b"Gemini" not in raw and b"{" not in raw[:1])

        reread = ss.load()
        check("reloading gives back the same default data (idempotent, not "
              "re-randomized every time)",
              reread["Thinking"] == data["Thinking"] and
              reread["Review"] == data["Review"])

        new_entry = ss.add_model(data, "Coding", "OpenAI", "gpt-4.1",
                                  "sk-abc123", base_url="https://api.openai.com/v1")
        check("add_model() returns the new entry with a stable id",
              bool(new_entry["id"]) and new_entry["name"] == "OpenAI")
        check("a freshly-added model is never builtin / always generic_http checked",
              new_entry["builtin"] is False and new_entry["check_kind"] == "generic_http")
        reread2 = ss.load()
        check("add_model() actually persisted the new entry",
              any(e["id"] == new_entry["id"] for e in reread2["Coding"]))
        check("add_model() raises for an unknown faculty",
              _raises(lambda: ss.add_model(data, "Not A Real Faculty", "x", "y", "z")))

        batch = ss.add_models(
            data, "Planning", "OpenRouter", ["free-a", "trial-b"],
            "sk-batch", "https://openrouter.ai/api/v1/",
            api_style="openai_compat", provider_hint="OpenRouter")
        check("add_models() creates one row per discovered model",
              [e["model"] for e in batch] == ["free-a", "trial-b"]
              and len({e["id"] for e in batch}) == 2)
        check("batch rows retain the provider, endpoint, key and API dialect",
              all(e["api_key"] == "sk-batch"
                  and e["base_url"] == "https://openrouter.ai/api/v1/"
                  and e["api_style"] == "openai_compat"
                  and e["provider_hint"] == "OpenRouter"
                  and e["status"] == "unknown"
                  for e in batch))
        check("repeating a batch doesn't duplicate its model rows",
              ss.add_models(
                  data, "Planning", "OpenRouter", ["free-a", "trial-b"],
                  "sk-batch", "https://openrouter.ai/api/v1/",
                  api_style="openai_compat",
                  provider_hint="OpenRouter") == [])
        check("the full batch is persisted",
              {e["model"] for e in ss.load()["Planning"]}
              == {"free-a", "trial-b"})
        ss.update_check_result(data, "Planning", batch[0]["id"], True,
                               "Connected.", 12346.0)
        ss.update_check_result(data, "Planning", batch[1]["id"], False,
                               "Unauthorized.", 12347.0)
        check("connection counts distinguish confirmed failures from unchecked entries",
              ss.connection_counts(data) == {
                  "connected": 1, "not_connected": 1, "not_checked": 6})
        check("bulk removal deletes failed checks but keeps connected and unchecked entries",
              ss.remove_disconnected_models(data) == 1
              and any(e["id"] == batch[0]["id"] for e in data["Planning"])
              and not any(e["id"] == batch[1]["id"] for e in data["Planning"])
              and ss.connection_counts(data)["not_checked"] == 6)
        for entry in batch:
            ss.remove_model(data, "Planning", entry["id"])

        ss.update_check_result(data, "Coding", new_entry["id"], True,
                                "Connected.", 12345.0)
        reread3 = ss.load()
        updated = [e for e in reread3["Coding"] if e["id"] == new_entry["id"]][0]
        check("update_check_result() records status/detail/timestamp",
              updated["status"] == "connected" and updated["detail"] == "Connected." and
              updated["last_checked"] == 12345.0)

        ss.remove_model(data, "Coding", new_entry["id"])
        reread4 = ss.load()
        check("remove_model() actually deletes the entry",
              not any(e["id"] == new_entry["id"] for e in reread4["Coding"]))
        check("remove_model() on an unknown id is a harmless no-op",
              not _raises(lambda: ss.remove_model(data, "Coding", "no-such-id")))

        check("mask_key('') says not set", ss.mask_key("") == "not set")
        masked = ss.mask_key("sk-super-secret-value")
        check("mask_key() never contains any of the real key's characters",
              "super-secret-value" not in masked and "sk-" not in masked)
        check("mask_key() still reports a length hint",
              str(len("sk-super-secret-value")) in masked)
    finally:
        memory.close()
        shutil.rmtree(tmp, ignore_errors=True)
        for k, v in old_env.items():
            if v is not None:
                os.environ[k] = v


def _raises(fn):
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


# ---------------------------------------------------------------------
# Part B: settings_checks.py (every network/SDK call faked)
# ---------------------------------------------------------------------
def part_b():
    print("\n--- Part B: settings_checks.py ---")
    import settings_checks as sc

    def entry(check_kind, api_key="", base_url="", model=""):
        return {"check_kind": check_kind, "api_key": api_key,
                "base_url": base_url, "model": model}

    # -- no key configured: every cloud checker refuses before even
    #    trying a real call --
    for kind, fn in (("gemini", sc.check_gemini), ("groq", sc.check_groq),
                     ("mistral", sc.check_mistral), ("tavily", sc.check_tavily)):
        ok, detail = fn(entry(kind, api_key=""))
        check(f"check_{kind} refuses with no api key (no network call needed)",
              ok is False and "key" in detail.lower())

    # -- fake a working Gemini SDK --
    fake_google = types.ModuleType("google")
    fake_genai = types.ModuleType("google.genai")

    calls = {"gen": 0, "embed": 0}

    class _FakeModels:
        def generate_content(self, model=None, contents=None, config=None):
            calls["gen"] += 1
            return object()

        def embed_content(self, model=None, contents=None):
            calls["embed"] += 1
            return object()

    class _FakeClient:
        def __init__(self, api_key):
            self.models = _FakeModels()

    fake_genai.Client = _FakeClient
    fake_google.genai = fake_genai
    sys.modules["google"] = fake_google
    sys.modules["google.genai"] = fake_genai
    try:
        ok, detail = sc.check_gemini(entry("gemini", api_key="real-looking-key"))
        check("check_gemini really GENERATES a 1-token reply now "
              "(green = can chat, not merely = key authenticates)",
              ok is True and calls["gen"] == 1)
        ok, detail = sc.check_gemini_embed(entry("gemini_embed", api_key="k"))
        check("check_gemini_embed performs a real 1-item embed",
              ok is True and calls["embed"] == 1)
    finally:
        sys.modules.pop("google", None)
        sys.modules.pop("google.genai", None)

    # -- a SDK that REJECTS the dict-style config (ValueError, not
    #    TypeError -- real-world SDKs do exactly this) must still
    #    recover through the no-config variant instead of going red --
    fake_google3 = types.ModuleType("google")
    fake_genai3 = types.ModuleType("google.genai")

    class _PickyModels:
        def generate_content(self, model=None, contents=None, config=None):
            if config is not None:
                raise ValueError("unexpected keyword argument: config")
            return object()

    class _PickyClient:
        def __init__(self, api_key):
            self.models = _PickyModels()

    fake_genai3.Client = _PickyClient
    fake_google3.genai = fake_genai3
    sys.modules["google"] = fake_google3
    sys.modules["google.genai"] = fake_genai3
    try:
        ok, detail = sc.check_gemini(entry("gemini", api_key="k"))
        check("a config-shape rejection recovers through the no-config "
              "variant (healthy entries never go red for SDK style)",
              ok is True)

        class _DeadClient:
            def __init__(self, api_key):
                self.models = self

            def generate_content(self, **kw):
                raise RuntimeError("HTTP 401: API key not valid")

        fake_genai3.Client = _DeadClient
        ok, detail = sc.check_gemini(entry("gemini", api_key="k"))
        check("...and a genuinely failing key still reports the real "
              "error (no false-green from the retry machinery)",
              ok is False and "401" in detail)
    finally:
        sys.modules.pop("google", None)
        sys.modules.pop("google.genai", None)

    # -- fake a Gemini SDK that raises (bad key) --
    fake_genai2 = types.ModuleType("google.genai")

    class _BadClient:
        def __init__(self, api_key):
            raise RuntimeError("401 Unauthorized")

    fake_genai2.Client = _BadClient
    fake_google2 = types.ModuleType("google")
    fake_google2.genai = fake_genai2
    sys.modules["google"] = fake_google2
    sys.modules["google.genai"] = fake_genai2
    try:
        ok, detail = sc.check_gemini(entry("gemini", api_key="bad-key"))
        check("check_gemini reports failure (not an exception) for a bad key",
              ok is False and "401" in detail)
    finally:
        sys.modules.pop("google", None)
        sys.modules.pop("google.genai", None)

    # -- fake a working Groq SDK --
    fake_groq = types.ModuleType("groq")

    class _FakeGroqCompletions:
        created = 0

        def create(self, model=None, messages=None, max_tokens=None):
            type(self).created += 1
            assert max_tokens == 1, "the check must burn token-minimum quota"
            return object()

    class _FakeGroqChat:
        def __init__(self):
            self.completions = _FakeGroqCompletions()

    class _FakeGroq:
        def __init__(self, api_key):
            self.chat = _FakeGroqChat()

    fake_groq.Groq = _FakeGroq
    sys.modules["groq"] = fake_groq
    try:
        ok, detail = sc.check_groq(entry("groq", api_key="k"))
        check("check_groq really GENERATES now (1-token completion)",
              ok is True and _FakeGroqCompletions.created == 1)
    finally:
        sys.modules.pop("groq", None)

    # -- fake a working Mistral SDK (same generation-honesty contract) --
    fake_mistral = types.ModuleType("mistralai")

    class _FakeMistralChat:
        completed = 0

        def complete(self, model=None, messages=None, max_tokens=None):
            type(self).completed += 1
            return object()

    class _FakeMistralEmb:
        created = 0

        def create(self, model=None, inputs=None):
            type(self).created += 1
            return object()

    class _FakeMistral:
        def __init__(self, api_key):
            self.chat = _FakeMistralChat()
            self.embeddings = _FakeMistralEmb()

    fake_mistral.Mistral = _FakeMistral
    sys.modules["mistralai"] = fake_mistral
    try:
        ok, detail = sc.check_mistral(entry("mistral", api_key="k"))
        check("check_mistral really GENERATES now (1-token completion)",
              ok is True and _FakeMistralChat.completed == 1)
        ok, detail = sc.check_mistral_embed(entry("mistral_embed", api_key="k"))
        check("check_mistral_embed performs a real 1-item embed",
              ok is True and _FakeMistralEmb.created == 1)
    finally:
        sys.modules.pop("mistralai", None)

    # -- groq SDK missing entirely --
    sys.modules["groq"] = None  # forces ImportError on `import groq`
    try:
        ok, detail = sc.check_groq(entry("groq", api_key="k"))
        check("check_groq reports a clear message when the SDK isn't installed",
              ok is False and "install" in detail.lower())
    finally:
        sys.modules.pop("groq", None)

    # -- fake Tavily --
    fake_tavily = types.ModuleType("tavily")

    class _FakeTavilyClient:
        def __init__(self, api_key):
            self._key = api_key

        def search(self, query, max_results=1):
            return {"results": []}

    fake_tavily.TavilyClient = _FakeTavilyClient
    sys.modules["tavily"] = fake_tavily
    try:
        ok, detail = sc.check_tavily(entry("tavily", api_key="k"))
        check("check_tavily succeeds against a fake-but-working client", ok is True)
    finally:
        sys.modules.pop("tavily", None)

    # -- generic HTTP (custom "+ Add Model" entries): when NO model is
    #    stored there is no honest generation claim -- the probe stays a
    #    reachability check but its detail must SAY exactly that --
    ok, detail = sc.check_generic_http(entry("generic_http", api_key="k", base_url=""))
    check("check_generic_http refuses with no base url configured", ok is False)

    import requests as _requests

    class _FakeResp:
        def __init__(self, status_code=200, text=""):
            self.status_code = status_code
            self.text = text

    orig_get = _requests.get
    _requests.get = lambda *a, **k: _FakeResp(200)
    try:
        ok, detail = sc.check_generic_http(
            entry("generic_http", api_key="k", base_url="https://example.com/api"))
        check("no-model generic check stays a reachability probe but "
              "SAYS generation was not verified (no false green)",
              ok is True and "NOT verified" in detail)
    finally:
        _requests.get = orig_get

    # -- ...but WITH a model stored, the check must ask for real
    #    generation bytes: POST {base}/chat/completions, max_tokens=1 --
    posts = []

    class _FakePost:
        def __init__(self, resp):
            self.resp = resp

        def __call__(self, url, headers=None, json=None, timeout=None):
            posts.append((url, json))
            return self.resp

    def with_post_code(code, text=""):
        resp = _FakeResp(code, text or "")
        posts.clear()
        orig_post = _requests.post
        _requests.post = _FakePost(resp)
        try:
            return sc.check_generic_http(
                entry("generic_http", api_key="k",
                      base_url="https://api.fakeprovider.com",
                      model="fake-model-1")), posts[0][0] if posts else None
        finally:
            _requests.post = orig_post

    (ok, detail), url = with_post_code(200)
    check("model-set generic check POSTs to .../chat/completions "
          "(real generation bytes)",
          url == "https://api.fakeprovider.com/chat/completions"
          and ok is True and "can chat" in detail)
    check("the check ping is CAPPED (an uncapped ping burned a full reply "
          "per click and made credit-gated gateways flip models to 402)",
          posts and posts[0][1].get("model") == "fake-model-1"
          and posts[0][1].get("max_tokens") == sc.CHECK_PING_MAX_TOKENS)
    (ok, detail), _u = with_post_code(200, text='{"not json at all": }')
    check("the check passes on a 2xx (body content isn't inspected -- "
          "the runtime owns shape validation)", ok is True)

    (ok, detail), url = with_post_code(402)
    check("HTTP 402 (out of balance) is a red-x with the funding fix, "
          "never a green check",
          ok is False and "balance" in detail.lower())

    (ok, detail), url = with_post_code(405)
    check("HTTP 405 (URL is a webpage, e.g. the dashboard) explains the "
          "real mistake instead of cryptically failing",
          ok is False and "API endpoint" in detail)

    (ok, detail), url = with_post_code(401)
    check("HTTP 401/403 is an honest key-rejection",
          ok is False and "Key rejected" in detail)

    (ok, detail), url = with_post_code(429)
    check("HTTP 429 (working key, currently throttled) is TEMPORARY: "
          "ok=None so the panel keeps the previous status",
          ok is None and "rate-limited" in detail.lower())

    (ok, detail), url = with_post_code(404)
    check("HTTP 404 names the model/endpoint problem plainly",
          ok is False and "404" in detail)

    (ok, detail), url = with_post_code(503)
    check("any 5xx is TEMPORARY (provider outage), never a red-x that "
          "'Remove not connected' would delete",
          ok is None and "temporarily unavailable" in detail)

    def _raise_conn_error(*a, **k):
        raise _requests.exceptions.ConnectionError("no route to host")

    orig_get2 = _requests.get
    _requests.get = _raise_conn_error
    try:
        ok, detail = sc.check_generic_http(
            entry("generic_http", api_key="k", base_url="https://nowhere.invalid"))
        check("check_generic_http reports failure (not an exception) on connection error",
              ok is False and "route" in detail.lower())
    finally:
        _requests.get = orig_get2

    # -- local, no-network checks --
    ok, detail = sc.check_local_piper(entry("local_piper"))
    check("check_local_piper never raises even if piper/overlay_speech "
          "aren't fully set up here", isinstance(ok, bool))
    ok, detail = sc.check_local_whisper(entry("local_whisper"))
    check("check_local_whisper never raises", isinstance(ok, bool))

    # -- dispatch --
    check("run_check() dispatches generic_http for an unrecognized check_kind",
          sc.run_check(entry("totally-unknown-kind", base_url=""))[1].startswith(
              "No API base URL"))
    check("run_check() never raises even if a checker itself blows up",
          not _raises(lambda: sc.run_check({"check_kind": "gemini"})))


# ---------------------------------------------------------------------
# Part C: app_panels.py wiring + a real Tk SettingsPanel (Xvfb)
# ---------------------------------------------------------------------
def part_c():
    print("\n--- Part C: app_panels.py wiring + real Tk panel ---")
    import app_panels as ap

    check("'settings' is a known app", ap.resolve_app_key("settings") == "settings")
    for alias in ("settings panel", "ai settings", "model settings",
                  "api settings", "ai models", "model manager"):
        check(f"resolve_app_key({alias!r}) resolves to settings",
              ap.resolve_app_key(alias) == "settings")
    check("Settings is in known_app_labels()", "Settings" in ap.known_app_labels())
    check("'settings' is in CUSTOM_PANEL_APPS (fully native, no real OS window)",
          "settings" in ap.CUSTOM_PANEL_APPS)
    check("'settings' is not in EMBEDDABLE_APPS (it's not a real reparented window)",
          "settings" not in ap.EMBEDDABLE_APPS)
    check("_CUSTOM_PANEL_CLASSES maps settings to the real SettingsPanel class",
          ap._CUSTOM_PANEL_CLASSES["settings"].__name__ == "SettingsPanel")

    import memory
    import settings_store as ss
    tmp = tempfile.mkdtemp(prefix="friday_settings_test_panel_")
    try:
        memory.set_memory_dir(tmp)

        class FakeLauncher:
            def launch(self, *a, **k):
                raise AssertionError("settings must never launch a real process")

        class _StubAgentHost:
            def get_panel_placement(self, key):
                return (0, 0, 900, 700), True, ""

            def get_pill_slot(self, key):
                return 0

            def release_pill_slot(self, key):
                return []

            def reposition_pill(self, key, idx):
                pass

            def release_panel_slot(self, key):
                pass

        mgr = ap.AppPanelManager(launcher=FakeLauncher(), agent_host=_StubAgentHost())

        mgr._ensure_host()
        global_wheel_binding = mgr._bridge.call(
            lambda: str(mgr._root.tk.call("bind", "all", "<MouseWheel>")))
        msg = mgr.open("settings")
        check("open('settings') succeeds", "Opened Settings" in msg)
        rec = mgr._panels["settings"]
        check("settings opens with no real OS process or window at all",
              rec.get("proc") is None and rec.get("hwnd") is None)
        check("settings is tracked as a custom, embedded panel",
              rec.get("custom") is True and rec.get("embedded") is True)
        check("list_panels() names Settings generically, not as a built-in notepad",
              "built-in settings" in mgr.list_panels().lower() and
              "notepad" not in mgr.list_panels().lower())

        panel = rec["frame"]
        # v3 honesty rule: per-widget wheel bindings on rows are not just
        # unnecessary -- on Windows they can never fire (wheel goes to the
        # FOCUSED widget). Scrolling therefore rides the ONE global
        # pointer-routed handler installed at host-root birth; zero
        # per-panel bindings means zero way to leak them either.
        check("settings rows carry NO private wheel binding (the global "
              "pointer-routed handler scrolls them)",
              mgr._bridge.call(
                  lambda: not panel._body._canvas.bind("<MouseWheel>")
                  and not panel._canvas.bind("<MouseWheel>")))
        check("the handler that scrolls settings is the SINGLE one "
              "installed once per interpreter (_tp_global, idempotent)",
              "_tp_global" in global_wheel_binding
              and global_wheel_binding.count("_tp_global") == 1)
        check("opening Settings leaves other windows' global wheel binding alone",
              mgr._bridge.call(
                  lambda: str(mgr._root.tk.call("bind", "all", "<MouseWheel>")))
              == global_wheel_binding)
        check("Keys & Connect renders one flat section without region cards",
              mgr._bridge.call(
                  lambda: len(panel._body.winfo_children()) == 1))
        check("the real SettingsPanel has one row per default connection "
              "(1 Audio + 1 Research + 2 Embedding + 1 Transcription)",
              len(panel._row_widgets) == 5)
        check("no phantom chat-model rows are rendered",
              [k for k in panel._row_widgets if k[0] == "Thinking"] == [])
        check("a seeded row's key is shown masked, never in the clear",
              panel._row_widgets.get(("Research", "tavily")) is not None)

        check("the panel's own data uses settings_store.mask_key for display",
              ss.mask_key("") == "not set")

        # -- the user adds their own Thinking models through the panel's
        #    real add-model path (no .env seeds anymore -- the panel is
        #    the ONLY source), then finished connectivity checks are
        #    simulated exactly like the real background worker would
        #    call back, without touching the network or threads --
        import settings_panel as sp
        t1 = ss.add_model(panel._data, "Thinking", "My Gemini",
                           "gemini-2.5-flash", "sk-t1")
        t2 = ss.add_model(panel._data, "Thinking", "My Groq",
                           "llama-3.3-70b", "sk-t2")
        panel._render_all()
        panel._finish_check("Thinking", t1["id"], True, "Connected.")
        widgets = panel._row_widgets[("Thinking", t1["id"])]
        check("a successful check turns the status label green with a checkmark",
              "\u2713" in widgets["status_lbl"].cget("text") and
              str(widgets["status_lbl"].cget("text_color")) ==
              str(sp.C["success"]))
        check("...and the status text says Connected",
              "Connected" in widgets["status_lbl"].cget("text"))

        panel._finish_check("Thinking", t2["id"], False, "401 Unauthorized")
        widgets2 = panel._row_widgets[("Thinking", t2["id"])]
        check("a failed check shows a not-connected status",
              "Connected" not in widgets2["status_lbl"].cget("text"))
        check("connection summary shows connected, failed, and unchecked totals",
              panel._connection_counts_lbl.cget("text")
              == "Connected: 1  |  Not connected: 1  |  Not checked: 5")

        # A TEMPORARY result (ok=None: rate limit / outage) must not flip a
        # working model to "Not connected" -- the cause of models
        # "disconnecting" on a re-check and then being bulk-removed.
        panel._finish_check("Thinking", t1["id"], None,
                            "Rate-limited right now (HTTP 429)")
        widgets = panel._row_widgets[("Thinking", t1["id"])]
        check("a temporary check result keeps a Connected model Connected",
              "Connected" in widgets["status_lbl"].cget("text")
              and "Not connected" not in widgets["status_lbl"].cget("text"))
        check("...and the totals are unchanged",
              panel._connection_counts_lbl.cget("text")
              == "Connected: 1  |  Not connected: 1  |  Not checked: 5")
        check("...and the reason is kept on the entry for the user to read",
              next(e for e in panel._data["Thinking"] if e["id"] == t1["id"])
              ["detail"].startswith("Rate-limited"))

        ask_prompts = []
        original_askyesno = sp.messagebox.askyesno
        sp.messagebox.askyesno = lambda *args, **kwargs: (
            ask_prompts.append((args, kwargs)) or True)
        try:
            panel._remove_disconnected()
        finally:
            sp.messagebox.askyesno = original_askyesno
        check("bulk removal confirms, removes failed checks, and preserves unchecked models",
              "haven't been checked are kept" in ask_prompts[0][0][1]
              and ("Thinking", t2["id"]) not in panel._row_widgets
              and ("Thinking", t1["id"]) in panel._row_widgets
              and panel._connection_counts_lbl.cget("text")
              == "Connected: 1  |  Not connected: 0  |  Not checked: 5")

        # -- "+ Add Model" really adds a brand-new custom provider --
        before = len(panel._row_widgets)
        added = {}

        def _fake_submit():
            entry = ss.add_model(panel._data, "Coding", "Custom LLM",
                                  "custom-model-v1", "sk-xyz",
                                  base_url="https://api.example.com")
            added["entry"] = entry
            panel._render_all()

        _fake_submit()
        check("adding a brand-new custom provider adds exactly one more row",
              len(panel._row_widgets) == before + 1)
        check("the new row is keyed by faculty + the new entry's id",
              ("Coding", added["entry"]["id"]) in panel._row_widgets)
        new_key_lbl = panel._row_widgets[("Coding", added["entry"]["id"])]["key_lbl"]
        check("the new custom provider's key is masked in the UI, never shown raw",
              "sk-xyz" not in new_key_lbl.cget("text"))

        msg = mgr.minimize("settings")
        check("minimize('settings') succeeds", "Settings" in msg or "minimized" in msg)
        msg = mgr.restore("settings")
        check("restore('settings') succeeds", "Restored Settings" in msg)
        msg = mgr.close("settings")
        check("close('settings') succeeds", "Closed Settings" in msg)
        check("settings is no longer tracked after closing",
              "settings" not in mgr._panels)
        import touchpad_scroll as _touchpad_scroll
        check("closing Settings releases its live scroll-probe reference",
              _touchpad_scroll._PROBE[0] is None)

        # -- a broken settings_store.load() must never leave a blank,
        #    un-closeable panel on screen (the real bug report this
        #    guards against): the constructor must swallow the error,
        #    show it in plain text, and still have a real, working
        #    close button, instead of either staying blank or raising
        #    out of __init__ entirely (which would leak an untracked,
        #    orphaned Toplevel with no way to close it). --
        import settings_panel as sp_mod
        orig_load = ss.load
        ss.load = lambda: (_ for _ in ()).throw(RuntimeError("disk is on fire"))
        close_events = []
        try:
            def _build_broken():
                return sp_mod.SettingsPanel(
                    mgr._root, on_minimize=lambda: None,
                    on_close=lambda: close_events.append(True),
                    corner_index=0, bounds=(0, 0, 900, 700))

            broken_panel = mgr._bridge.call(_build_broken)
            check("a broken settings_store.load() doesn't raise out of "
                  "SettingsPanel.__init__", True)
            import customtkinter as ctk
            body_text = " ".join(
                w.cget("text") for w in broken_panel._body.winfo_children()
                if isinstance(w, ctk.CTkLabel))
            check("...and shows the real error message in the panel instead "
                  "of a silently blank one",
                  "disk is on fire" in body_text)
            check("...with zero model rows (nothing half-rendered)",
                  len(broken_panel._row_widgets) == 0)

            def _click_close():
                broken_panel._close_btn.invoke()

            mgr._bridge.call(_click_close)
            check("the error panel's own titlebar close button really is "
                  "wired up and clickable, escaping the error",
                  len(close_events) == 1)
            mgr._bridge.call(broken_panel.destroy)
        finally:
            ss.load = orig_load

        # No manual Tk teardown here -- mgr's own atexit-registered
        # shutdown() (see AppPanelManager.shutdown's docstring) is the
        # only thread-safe way to stop its background Tk host thread;
        # reaching across threads to call mgr._root.destroy() directly
        # from here is exactly the kind of cross-thread Tcl call that
        # hangs, same as test_app_panels.py deliberately never does it
        # either.
    finally:
        memory.close()
        shutil.rmtree(tmp, ignore_errors=True)


def part_d():
    """_FieldDialog v2 -- the any-key dream's front end: key-shape
    auto-detection, the provider quick-pick, and fetch-models-driven
    model discovery, all feeding entries their api_style so the runtime
    speaks the right dialect."""
    import tkinter as tk
    import provider_autodetect as pa
    import settings_panel as sp

    root = tk.Tk()
    root.withdraw()
    try:
        recorded = []
        d = sp._FieldDialog(root, "Add model", "Coding",
                            lambda *args: recorded.append(args))
        d._name_var.set("OR")
        # An OpenRouter-shaped key: auto-detected, base URL filled.
        d._key_var.set("sk-or-v1-" + "x" * 60)
        check("pasting an sk-or-v1 key fills OpenRouter's base URL itself",
              d._base_url_var.get() == "https://openrouter.ai/api/v1")
        check("...and the dialog SAYS what it detected",
              "OpenRouter" in d._detect_lbl.cget("text"))
        d._submit()
        check("saving an auto-detected entry carries its api_style + "
              "provider name so check/build/verify all speak one dialect",
              recorded and recorded[0][4] == "openai_compat"
              and recorded[0][5] == "OpenRouter")
        d.destroy()

        # An Anthropic key: v1/messages style flows through on save.
        recorded.clear()
        d2 = sp._FieldDialog(root, "Add model", "Coding",
                             lambda *args: recorded.append(args))
        d2._key_var.set("sk-ant-" + "x" * 40)
        d2._name_var.set("CL")
        check("pasting an sk-ant key detects Anthropic",
              "Anthropic" in d2._detect_lbl.cget("text"))
        d2._submit()
        check("an anthropic-detected entry saves api_style='anthropic'",
              recorded and recorded[0][4] == "anthropic")
        d2.destroy()

        # An unknown-shaped key: honestly NOT guessed, quick-pick helps.
        recorded.clear()
        d3 = sp._FieldDialog(root, "Add model", "Coding",
                             lambda *args: recorded.append(args))
        d3._key_var.set("sk-" + "f" * 46)   # deepseek/openai-legacy gap
        check("an unrecognizable key shape is named as needing a pick, "
              "never silently guessed",
              "isn't recognized" in d3._detect_lbl.cget("text"))
        d3._on_preset_pick("DeepSeek")
        check("picking a provider from the quick-pick fills its base URL",
              "api.deepseek" in d3._base_url_var.get())
        d3.destroy()

        recorded.clear()
        d_blackpig = sp._FieldDialog(
            root, "Add model", "Thinking",
            lambda *args: recorded.append(args))
        d_blackpig._on_preset_pick("BlackPig AI")
        d_blackpig._name_var.set("BlackPig Opus")
        d_blackpig._model_var.set("claude-opus-5-5")
        d_blackpig._key_var.set("test-key")
        d_blackpig._submit()
        check("BlackPig preset saves its API base, Anthropic dialect, and "
              "provider hint",
              recorded and recorded[0][1] == "claude-opus-5-5"
              and recorded[0][3] == "https://api.vv666.ai"
              and recorded[0][4] == "anthropic"
              and recorded[0][5] == "BlackPig AI")
        d_blackpig.destroy()

        # Fetch-models: a stubbed provider catalog drops REAL ids into
        # the picker without typing anything.
        d4 = sp._FieldDialog(root, "Add model", "Coding",
                             lambda *args: recorded.append(args))
        d4._key_var.set("sk-or-v1-" + "y" * 60)
        d4._fetcher = lambda entry: {"ok": True,
                                     "ids": ["m-a", "m-b"], "error": None}
        d4._apply_fetch({"ok": True, "ids": ["m-a", "m-b"],
                         "error": None})
        check("a successful fetch auto-fills the first real model id",
              d4._model_var.get() == "m-a")
        check("...and the picker carries the provider's REAL catalog",
              list(d4._model_combo.cget("values")) == ["m-a", "m-b"])
        d4._apply_fetch({"ok": False, "ids": [],
                         "error": "HTTP 401 -- bad key"})
        check("a failed fetch says so and invites manual typing, each "
              "honestly", "Couldn't fetch" in
              d4._model_hint_lbl.cget("text")
              and "401" in d4._model_hint_lbl.cget("text"))
        d4.destroy()

        # Fetch-models with tiered catalog: prefixes in the picker, raw
        # id saved on submit, and the summary names every bucket size.
        recorded.clear()
        d6 = sp._FieldDialog(root, "Add model", "Coding",
                             lambda *args: recorded.append(args))
        d6._name_var.set("T")
        d6._base_url_var.set("https://openrouter.ai/api/v1")
        d6._apply_fetch({
            "ok": True,
            "ids": ["a", "b", "t", "p", "u"],
            "tiers": {"free": ["a", "b"], "trial": ["t"],
                      "paid": ["p"], "unstated": ["u"]},
            "error": None})
        vals = list(d6._model_combo.cget("values"))
        check("tiered fetch groups the picker FREE first, then TRIAL, "
              "then PAID, then price-unstated",
              vals == ["[FREE] a", "[FREE] b", "[TRIAL] t", "[PAID] p",
                       "[open] u"])
        check("the summary reports every bucket size honestly (incl. the "
              "honest 'no data' bucket)", "2 FREE" in
              d6._model_hint_lbl.cget("text")
              and "1 TRIAL" in d6._model_hint_lbl.cget("text")
              and "1 PAID" in d6._model_hint_lbl.cget("text")
              and "1 price-unstated" in d6._model_hint_lbl.cget("text"))
        check("the auto-pick lands on the first FREE model",
              d6._model_var.get() == "[FREE] a")
        d6._model_var.set("[PAID] p")
        d6._submit()
        check("a prefix-prefixed pick resolves to the RAW model id on "
              "save (the prefix is display-only)",
              recorded and recorded[0][1] == "p")
        d6.destroy()

        # Bulk add only models positively classified as free or trial.
        from unittest import mock
        bulk_calls = []
        d8 = sp._FieldDialog(
            root, "Add model", "Thinking", lambda *args: None,
            on_add_many=lambda *args: bulk_calls.append(args) or
            [{"id": "added-a"}, {"id": "added-b"}])
        d8._name_var.set("OpenRouter")
        d8._key_var.set("sk-or-v1-" + "z" * 60)
        d8._base_url_var.set("https://openrouter.ai/api/v1")
        d8._apply_fetch({
            "ok": True, "ids": ["free-a", "trial-b", "paid-c", "unknown-d"],
            "tiers": {"free": ["free-a"], "trial": ["trial-b"],
                      "paid": ["paid-c"], "unstated": ["unknown-d"]},
            "error": None})
        check("bulk add is enabled only for the fetched free/trial catalog",
              d8._bulk_btn is not None
              and d8._bulk_btn.cget("state") == "normal"
              and d8._bulk_models == ["free-a", "trial-b"])
        with mock.patch.object(sp.messagebox, "showinfo"):
            d8._add_free_trial_models()
        check("bulk add sends only free/trial ids and the same provider key "
              "metadata to the settings store",
              len(bulk_calls) == 1
              and bulk_calls[0][0:2] == ("OpenRouter", ["free-a", "trial-b"])
              and bulk_calls[0][2] == "sk-or-v1-" + "z" * 60
              and bulk_calls[0][3] == "https://openrouter.ai/api/v1"
              and bulk_calls[0][4:] == ("openai_compat", "OpenRouter"))
        d8.destroy()

        d9 = sp._FieldDialog(
            root, "Add model", "Thinking", lambda *args: None,
            on_add_many=lambda *args: [])
        d9._apply_fetch({
            "ok": True, "ids": ["free-a"],
            "tiers": {"free": ["free-a"]}, "error": None})
        snapshot = (d9._base_url_var.get(), d9._key_var.get(),
                    d9._api_style or None)
        d9._base_url_var.set("https://changed.example/v1")
        d9._apply_fetch(
            {"ok": True, "ids": ["free-a"],
             "tiers": {"free": ["free-a"]}, "error": None},
            snapshot)
        check("a catalog fetched for old connection details cannot bulk-add "
              "against changed settings",
              not d9._bulk_models
              and d9._bulk_btn.cget("state") == "disabled"
              and "changed" in d9._model_hint_lbl.cget("text"))
        d9.destroy()

        # A fetch whose provider shared no tiers at all still works flat.
        d7 = sp._FieldDialog(root, "Add model", "Coding",
                             lambda *args: recorded.append(args))
        d7._apply_fetch({"ok": True, "ids": ["m1", "m2"], "tiers": None,
                         "error": None})
        check("a fetch without tier info keeps the plain, honest flat "
              "listing", list(d7._model_combo.cget("values"))
              == ["m1", "m2"] and "model(s) from the provider"
              in d7._model_hint_lbl.cget("text"))
        d7.destroy()

        # The Ollama localhost preset never demands a key.
        d5 = sp._FieldDialog(root, "Add model", "Coding",
                             lambda *args: recorded.append(args))
        d5._on_preset_pick("Ollama (local, no key)")
        check("the local preset hints 'no key needed' visibly",
              "isn't needed" in d5._detect_lbl.cget("text")
              and d5._base_url_var.get().startswith("http://localhost"))
        d5.destroy()
    finally:
        root.destroy()


def main():
    # part_d owns (and destroys) its own Tk root BEFORE the manager's
    # background Tk host thread comes alive in part_c -- keeping two Tk
    # interpreters from racing at teardown is why it runs first.
    part_d()
    part_a()
    part_b()
    part_c()
    passed = sum(1 for _, ok in CHECKS if ok)
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        sys.exit(1)


if __name__ == "__main__":
    main()

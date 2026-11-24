#!/usr/bin/env python3
"""Fix: "Check all drains my tokens and some models disconnect on re-check".

Run from the floating-agent-python folder:

    python apply_check_fix.py           # apply
    python apply_check_fix.py --dry-run # only report what would change
    python apply_check_fix.py --undo    # restore the .bak-checkfix backups

Works on edited/"cleaned" copies: functions are located BY NAME (Python's
own parser), not by matching comments or line numbers. Every touched file
is backed up as <file>.bak-checkfix, and the result must compile or all
files are restored.

What changes
  settings_checks.py : capped single-row ping (16 tokens), temporary errors
                       (429 / 5xx / timeouts) return ok=None, new check_many()
                       = token-free "Check all" (one catalog request per key),
                       OpenRouter credit via its free /key endpoint.
  settings_panel.py  : _finish_check keeps the previous status on ok=None;
                       _check_all uses check_many and ignores double clicks.
  dev_tests/         : new test_settings_check_cost.py; three old assertions
                       that pinned the uncapped/429-is-green behaviour updated.
"""
import ast
import os
import py_compile
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BAK = ".bak-checkfix"
MARK = "CHECK_PING_MAX_TOKENS"

CONSTS_BLOCK = '# -------------------------------------------------------------------------\n# SPEND-SAFE CHECKING (fixes "Check all drains my tokens and flips models\n# between Connected / Not connected"):\n#\n# 1. The single-row ping is CAPPED (CHECK_PING_MAX_TOKENS). The old ping\n#    sent NO max_tokens (8192 on Anthropic) so every click could generate a\n#    full reply -- reasoning models think for hundreds of tokens -- and\n#    credit-gated gateways (OpenRouter style) reserved each model\'s FULL\n#    max output against the balance, so as the checks themselves spent the\n#    balance, the pricier models started answering 402 -> red.\n# 2. "Check all" spends NO tokens: one free GET of the provider\'s model\n#    catalog per (base URL, key) proves the key and that each model is\n#    offered; OpenRouter\'s free /key endpoint reports remaining credit.\n#    Only a model the catalog can\'t vouch for gets a capped ping.\n# 3. Requests per provider are paced (sequential within a key, a short gap\n#    between pings) instead of a burst that trips free-tier rate limits.\n# 4. TEMPORARY failures (429, 5xx, timeouts, connection drops) return\n#    ok=None == "couldn\'t tell right now": the panel keeps the model\'s\n#    previous status instead of turning a working model red (and "Remove\n#    not connected" can no longer delete it).\n# -------------------------------------------------------------------------\nCHECK_PING_MAX_TOKENS = 16          # enough for "OK" on every provider\'s minimum\nCHECK_PING_PROMPT = "Reply with the single word OK."\nCHECK_ALL_PARALLEL_PROVIDERS = 4    # different providers checked side by side\nCHECK_ALL_PING_SPACING_S = 1.2      # gap between pings on the SAME key\nTRANSIENT_HTTP = {408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522,\n                  523, 524, 529}\n_TRANSIENT_TEXT = re.compile(\n    r"\\b(429|500|502|503|504|529)\\b|rate.?limit|too many requests|timed? ?out|"\n    r"timeout|temporar|overloaded|unavailable|try again|connection (reset|"\n    r"aborted|refused)|max retries", re.IGNORECASE)\n'
HELPERS_SRC = 'def _body_hint(resp):\n    try:\n        return (resp.text or "").strip().replace("\\n", " ")[:120]\n    except Exception:  # noqa: BLE001\n        return ""\n\n\ndef _classify_http_failure(code, model, resp=None):\n    """Non-2xx -> (ok, detail). ok=None means TEMPORARY: keep the old status."""\n    if code == 401:\n        return False, ("Key rejected (HTTP 401) -- generate a fresh API "\n                       "key and update this entry.")\n    if code == 403:\n        hint = _body_hint(resp) if resp is not None else ""\n        return False, ("Access refused (HTTP 403) -- the key is invalid, or a "\n                       "spend/budget limit on it is used up"\n                       + (f": {hint}" if hint else "."))\n    if code == 402:\n        return False, ("No usable balance on this account (HTTP 402) -- "\n                       "top up at the provider\'s billing page, or swap "\n                       "this entry for a funded one.")\n    if code == 404:\n        return False, (f"Not found (HTTP 404) -- the model \'{model}\' or the "\n                       "chat endpoint doesn\'t exist at this base URL.")\n    if code == 405:\n        return False, ("Method Not Allowed (HTTP 405) -- this address looks "\n                       "like a PAGE (e.g. the provider\'s dashboard/API-keys "\n                       "screen), not its API endpoint. Put the API base in "\n                       "the URL field, for example "\n                       "https://api.deepseek.com for DeepSeek.")\n    if code == 429:\n        return None, (f"Rate-limited right now (HTTP 429) -- the key works but "\n                      f"the provider is throttling requests. Status left "\n                      "unchanged; it clears when the limit window resets.")\n    if code in TRANSIENT_HTTP or code >= 500:\n        hint = _body_hint(resp) if resp is not None else ""\n        return None, (f"Provider temporarily unavailable (HTTP {code}) -- "\n                      "status left unchanged, try again shortly."\n                      + (f" Server said: {hint}" if hint else ""))\n    hint = _body_hint(resp) if resp is not None else ""\n    return False, (f"Rejected (HTTP {code})"\n                   + (f" -- server says: {hint}" if hint else ""))\n\n\n# -------------------------------------------------------------------------\n# Free account lookups (no generation, no tokens)\n# -------------------------------------------------------------------------\nOPENROUTER_HOSTS = ["openrouter.ai"]   # hosts that serve OpenRouter\'s /key endpoint\n\n\ndef openrouter_key_info(base_url, api_key):\n    """OpenRouter\'s free GET /api/v1/key: remaining credit on this key and\n    free-model requests left today. None for other providers or on error."""\n    if not api_key or not any(h in (base_url or "").lower() for h in OPENROUTER_HOSTS):\n        return None\n    try:\n        import requests\n        resp = requests.get(base_url.rstrip("/") + "/key", timeout=8,\n                            headers={"Authorization": f"Bearer {api_key}"})\n        if resp.status_code != 200:\n            return None\n        data = resp.json().get("data")\n        return data if isinstance(data, dict) else None\n    except Exception:  # noqa: BLE001\n        return None\n\n\ndef _is_free_model_id(model):\n    return str(model or "").lower().endswith(":free")\n\n\ndef _credit_verdict(info, model):\n    """(ok, note) from OpenRouter key info, or None when nothing to say.\n    ok False only when a PAID model can\'t run (no credit left / budget spent)."""\n    if not info:\n        return None\n    notes = []\n    free_req = info.get("free_model_daily_requests") or {}\n    remaining = info.get("limit_remaining")\n    budget = info.get("effective_budget") or {}\n    if _is_free_model_id(model):\n        if isinstance(free_req.get("remaining"), (int, float)):\n            notes.append(f"{int(free_req[\'remaining\'])} free requests left today.")\n        return (True, " ".join(notes)) if notes else None\n    if isinstance(remaining, (int, float)) and remaining <= 0:\n        return False, ("This key\'s credit limit is used up -- paid models will "\n                       "fail with HTTP 402. Top up or raise the key\'s limit on "\n                       "openrouter.ai, or use a \':free\' model.")\n    if isinstance(budget.get("remaining_usd"), (int, float)) and budget["remaining_usd"] <= 0:\n        return False, ("This key\'s spending budget is used up -- requests will be "\n                       "refused until it resets or is raised on openrouter.ai.")\n    if isinstance(remaining, (int, float)):\n        notes.append(f"${remaining:,.2f} credit left on this key.")\n    return (True, " ".join(notes)) if notes else None\n\n\n# -------------------------------------------------------------------------\n# "Check all": grouped, paced, deduplicated, and token-free where possible\n# -------------------------------------------------------------------------\ndef _catalog(entry):\n    """One free GET of the provider\'s model list.\n    -> ("ok", set_of_ids) | ("rejected", detail) | ("transient", detail) | ("none", detail)"""\n    import requests\n    import provider_autodetect\n    base_url = (entry.get("base_url") or "").strip()\n    style = provider_autodetect.entry_api_style(entry)\n    hint = provider_autodetect.preset_for_base_url(base_url) or {}\n    url = provider_autodetect.models_url(\n        base_url, style, override=hint.get("models_url_override", ""))\n    if not url:\n        return "none", "no model-listing URL for this base"\n    try:\n        resp = requests.get(url, timeout=10, headers=provider_autodetect.headers_for(\n            style, entry.get("api_key", "")))\n    except requests.exceptions.RequestException as exc:\n        return "transient", (f"Couldn\'t reach {base_url} right now ({_short(exc)}). "\n                             "Status left unchanged.")\n    code = resp.status_code\n    if code in (401, 403):\n        return "rejected", _classify_http_failure(code, "", resp)[1]\n    if code in TRANSIENT_HTTP or code >= 500:\n        return "transient", _classify_http_failure(code, "", resp)[1]\n    if code >= 400:\n        return "none", f"HTTP {code}"\n    try:\n        data = resp.json()\n    except ValueError:\n        return "none", "catalog wasn\'t JSON"\n    items = (data.get("data") or data.get("models") or []) if isinstance(data, dict) else data\n    ids = set()\n    for item in items or []:\n        mid = (item.get("id") or item.get("name") or item.get("model")) if isinstance(item, dict) else item\n        if mid:\n            ids.add(str(mid).removeprefix("models/").lower())\n    return ("ok", ids) if ids else ("none", "empty catalog")\n\n\ndef _check_provider_group(group, on_result, should_stop, spacing):\n    """All entries sharing one (base URL, key): ONE catalog call, one\n    credit lookup, then a capped ping only for models the catalog can\'t\n    vouch for. Duplicate models are checked once."""\n    first = group[0][1]\n    state, payload = _catalog(first)\n    if state in ("rejected", "transient"):\n        ok = False if state == "rejected" else None\n        for faculty, entry in group:\n            on_result(faculty, entry["id"], ok, payload)\n        return\n    info = openrouter_key_info(first.get("base_url", ""), first.get("api_key", ""))\n    by_model = {}\n    for faculty, entry in group:\n        by_model.setdefault((entry.get("model") or "").strip(), []).append((faculty, entry))\n    pinged = False\n    for model, members in by_model.items():\n        if should_stop():\n            return\n        if state == "ok" and model.lower() in payload:\n            result = (True, f"Key works and the provider offers {model} -- "\n                            "checked without spending any tokens.")\n            verdict = _credit_verdict(info, model)\n            if verdict is not None:\n                result = verdict if verdict[0] is False else (True, result[1] + " " + verdict[1])\n        else:\n            if pinged and spacing:\n                time.sleep(spacing)\n            result = run_check(dict(members[0][1]))\n            pinged = True\n        for faculty, entry in members:\n            on_result(faculty, entry["id"], result[0], result[1])\n\n\ndef check_many(items, on_result, should_stop=lambda: False,\n               max_parallel=CHECK_ALL_PARALLEL_PROVIDERS,\n               spacing=CHECK_ALL_PING_SPACING_S):\n    """The "Check all" engine. `items` is [(faculty, entry_dict), ...];\n    on_result(faculty, entry_id, ok, detail) is called once per entry from\n    worker threads (ok may be None = temporary, keep the old status).\n    Blocks until every entry has a result. Never raises."""\n    groups, solo = {}, []\n    for faculty, entry in items:\n        kind = entry.get("check_kind") or "generic_http"\n        if kind == "generic_http" and (entry.get("model") or "").strip() \\\n                and (entry.get("base_url") or "").strip():\n            key = ((entry.get("base_url") or "").strip().rstrip("/").lower(),\n                   entry.get("api_key") or "", entry.get("api_style") or "")\n            groups.setdefault(key, []).append((faculty, entry))\n        else:\n            solo.append((faculty, entry))\n    lock = threading.Lock()\n    done = set()\n\n    def _safe_result(faculty, entry_id, ok, detail):\n        with lock:\n            if (faculty, entry_id) in done:\n                return\n            done.add((faculty, entry_id))\n        try:\n            on_result(faculty, entry_id, ok, detail)\n        except Exception:  # noqa: BLE001\n            pass\n\n    def _run_group(group):\n        try:\n            _check_provider_group(group, _safe_result, should_stop, spacing)\n        except Exception as exc:  # noqa: BLE001\n            for faculty, entry in group:\n                _safe_result(faculty, entry["id"], None,\n                             f"Check failed unexpectedly: {_short(exc)}")\n\n    def _run_solo(pair):\n        faculty, entry = pair\n        if should_stop():\n            return\n        ok, detail = run_check(dict(entry))\n        _safe_result(faculty, entry["id"], ok, detail)\n\n    with ThreadPoolExecutor(max_workers=max(1, max_parallel),\n                            thread_name_prefix="FridayCheckAll") as pool:\n        futures = [pool.submit(_run_group, g) for g in groups.values()]\n        futures += [pool.submit(_run_solo, p) for p in solo]\n        for f in futures:\n            try:\n                f.result()\n            except Exception:  # noqa: BLE001\n                pass\n\n\n'
GENERIC_SRC = 'def check_generic_http(entry):\n    """Real generation check for a custom entry (everything added\n    through the panel\'s "+ Add Model" button), in THAT ENTRY\'S OWN\n    provider dialect (OpenAI-compatible or Anthropic -- see\n    provider_autodetect). When a model name is stored, the check POSTS\n    one CAPPED ping (CHECK_PING_MAX_TOKENS) to the same URL with the same\n    headers the build pipeline uses, so green still means "this model\n    really generated", at a cost of a few dozen tokens instead of a full\n    reply. Credit is checked through the provider\'s free account\n    endpoint where one exists (OpenRouter /key) rather than by making a\n    max-size reservation. Temporary failures (429, 5xx, timeouts,\n    dropped connections) return ok=None so the panel keeps the previous\n    status. With NO model name stored the honest reachability claim is\n    kept and labelled as exactly that -- generation explicitly NOT\n    verified."""\n    base_url = (entry.get("base_url") or "").strip()\n    if not base_url:\n        return False, "No API base URL configured to check."\n    model = (entry.get("model") or "").strip()\n    try:\n        import requests\n    except Exception as exc:  # noqa: BLE001\n        return False, f"The \'requests\' package isn\'t installed: {exc}"\n    import provider_autodetect\n    style = provider_autodetect.entry_api_style(entry)\n    headers = provider_autodetect.headers_for(style, entry.get("api_key", ""))\n\n    if not model:\n        # No model name -> no honest generation claim possible. Keep the\n        # bare reachability probe but SAY it honestly.\n        try:\n            resp = requests.get(base_url, headers=headers, timeout=6)\n            if resp.status_code < 500:\n                return True, (f"Server responded (HTTP {resp.status_code}) -- "\n                              "reachable. NOTE: no model name is set, so "\n                              "generation was NOT verified -- add the model "\n                              "and recheck to prove this entry can answer.")\n            return False, f"Server error (HTTP {resp.status_code})."\n        except Exception as exc:  # noqa: BLE001\n            return False, _short(exc)\n\n    # URL/headers come from the SAME builders act_script\'s runtime uses\n    # (dialect parity). The body is a CAPPED ping -- see SPEND-SAFE\n    # CHECKING at the top: an uncapped ping burned real tokens on every\n    # click and made credit-gated gateways flip models to 402.\n    url = provider_autodetect.chat_url(base_url, style)\n    payload = provider_autodetect.chat_payload(\n        style, model, "", CHECK_PING_PROMPT, max_tokens=CHECK_PING_MAX_TOKENS)\n    try:\n        resp = requests.post(url, headers=headers, json=payload, timeout=20)\n        if (resp.status_code == 400 and style != provider_autodetect.ANTHROPIC_API_STYLE\n                and "max_tokens" in (resp.text or "")):\n            # Newer OpenAI-family models refuse `max_tokens` and want\n            # `max_completion_tokens`; keep the cap, just rename it.\n            payload.pop("max_tokens", None)\n            payload["max_completion_tokens"] = CHECK_PING_MAX_TOKENS\n            resp = requests.post(url, headers=headers, json=payload, timeout=20)\n    except requests.exceptions.Timeout:\n        return None, ("The provider didn\'t answer within 20 s -- probably busy. "\n                      "Status left unchanged; try again in a minute.")\n    except requests.exceptions.ConnectionError as exc:\n        return None, (f"Couldn\'t reach {base_url} right now ({_short(exc)}). "\n                      "Status left unchanged -- check your internet or the base URL.")\n    except Exception as exc:  # noqa: BLE001\n        return False, _short(exc)\n    code = resp.status_code\n    if 200 <= code < 300:\n        detail = f"Generated a reply with {model} -- this entry can chat."\n        info = openrouter_key_info(base_url, entry.get("api_key", ""))\n        verdict = _credit_verdict(info, model)\n        if verdict is not None and verdict[0] is False:\n            return verdict\n        if verdict is not None:\n            detail += " " + verdict[1]\n        return True, detail\n    return _classify_http_failure(code, model, resp)\n'
RUN_CHECK_SRC = 'def run_check(entry):\n    """Dispatches to the right checker for `entry` based on its\n    check_kind, falling back to the generic HTTP reachability check for\n    anything unrecognized. Never raises. Returns (ok, detail) where ok is\n    True / False / None (None = temporary problem, keep previous status)."""\n    kind = entry.get("check_kind") or "generic_http"\n    fn = CHECKERS.get(kind, check_generic_http)\n    try:\n        ok, detail = fn(entry)\n    except Exception as exc:  # noqa: BLE001\n        ok, detail = False, f"Check failed unexpectedly: {_short(exc)}"\n    # SDK-based checkers (Gemini/Groq/Mistral...) surface throttling and\n    # outages as exception text; those are temporary, not "disconnected".\n    if ok is False and kind != "generic_http" and _TRANSIENT_TEXT.search(detail or ""):\n        return None, (f"{detail} -- looks temporary (rate limit / outage); "\n                      "status left unchanged.")\n    return ok, detail\n'
FINISH_SRC = '    def _finish_check(self, faculty, model_id, ok, detail):\n        if self._closing:\n            return\n        if ok is None:\n            # TEMPORARY problem (rate limit, provider outage, timeout): we\n            # learned nothing new, so the row keeps its previous status\n            # instead of a working model flipping to "Not connected" (and\n            # becoming a target for "Remove not connected").\n            entry = next((e for e in self._data.get(faculty, [])\n                          if e.get("id") == model_id), None)\n            previous = (entry or {}).get("status")\n            if previous not in ("connected", "error"):\n                previous = "unknown"\n            if entry is not None:\n                entry["detail"] = detail\n                entry["last_attempt"] = time.time()\n                settings_store.save(self._data)\n            self._apply_status(faculty, model_id, previous, detail)\n        else:\n            settings_store.update_check_result(self._data, faculty, model_id, ok,\n                                                detail, time.time())\n            self._apply_status(faculty, model_id, "connected" if ok else "error",\n                               detail)\n        self._update_connection_summary()\n        widgets = self._row_widgets.get((faculty, model_id))\n        if widgets:\n            try:\n                widgets["check_btn"].configure(state="normal")\n            except tk.TclError:\n                pass\n'
CHECKALL_SRC = '    def _check_all(self):\n        """Token-free where possible (see settings_checks.check_many): one\n        catalog lookup per provider key instead of a generation per model,\n        paced per provider, temporary failures keep the old status. A\n        second click while a run is in progress is ignored."""\n        if getattr(self, "_check_all_running", False) or self._closing:\n            return\n        items = [(faculty, entry) for faculty, entries in self._data.items()\n                 for entry in entries\n                 if entry.get("api_key") or entry.get("base_url")\n                 or entry.get("check_kind", "").startswith("local")]\n        if not items:\n            return\n        self._check_all_running = True\n        try:\n            self._check_all_btn.configure(state="disabled", text="Checking\\u2026")\n        except (tk.TclError, AttributeError):\n            pass\n        for faculty, entry in items:\n            self._apply_status(faculty, entry["id"], "checking", "")\n            widgets = self._row_widgets.get((faculty, entry["id"]))\n            if widgets:\n                try:\n                    widgets["check_btn"].configure(state="disabled")\n                except tk.TclError:\n                    pass\n\n        def _post(faculty, entry_id, ok, detail):\n            if self._closing:\n                return\n            try:\n                self.after(0, lambda: self._finish_check(faculty, entry_id, ok, detail))\n            except (tk.TclError, RuntimeError):\n                pass\n\n        def _worker():\n            try:\n                settings_checks.check_many(\n                    [(faculty, dict(entry)) for faculty, entry in items], _post,\n                    should_stop=lambda: self._closing)\n            finally:\n                try:\n                    self.after(0, self._check_all_finished)\n                except (tk.TclError, RuntimeError):\n                    pass\n\n        threading.Thread(target=_worker, name="FridaySettingsCheckAll",\n                         daemon=True).start()\n'
FINISHED_SRC = '    def _check_all_finished(self):\n        self._check_all_running = False\n        if self._closing:\n            return\n        try:\n            self._check_all_btn.configure(state="normal", text="Check all")\n        except (tk.TclError, AttributeError):\n            pass\n'
NEW_TEST = '"""Regression: "Check all" drained tokens and flipped models between\nConnected and Not connected on every re-check.\n\nA fake OpenRouter-style gateway reproduces the real mechanics:\n  * credit-gated: a chat call with NO max_tokens reserves the model\'s full\n    max output against the balance and answers 402 when it can\'t cover it;\n  * every generation spends credit;\n  * a burst of chat calls on one key gets 429; the catalog is free.\nThe old check (uncapped ping per model, 4 at a time) spent credit on every\nclick and turned pricier models red as the balance fell. The new one must:\nspend nothing for catalogued models, give identical results run after run,\nkeep previous status on temporary errors, and send one catalog GET per key.\n"""\nimport json\nimport os\nimport sys\nimport threading\nimport time\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\n\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n\nimport settings_checks as sc  # noqa: E402\n\nCHECKS = []\n\n\ndef check(label, cond):\n    CHECKS.append((label, bool(cond)))\n    print(("PASS" if cond else "FAIL") + " - " + label)\n\n\nPRICE_PER_TOKEN = {"cheap/model-a": 0.000001, "mid/model-b": 0.00001,\n                   "pricey/model-c": 0.0001, "free/model-d:free": 0.0}\nMAX_OUTPUT = 32000            # what an uncapped request reserves\n\n\nclass Gateway(BaseHTTPRequestHandler):\n    balance = 1.0\n    catalog_gets = 0\n    chat_posts = []\n    recent_chat = []\n    fail_catalog_with = None\n\n    def log_message(self, *a):\n        pass\n\n    def _send(self, code, obj):\n        body = json.dumps(obj).encode()\n        self.send_response(code)\n        self.send_header("Content-Type", "application/json")\n        self.send_header("Content-Length", str(len(body)))\n        self.end_headers()\n        self.wfile.write(body)\n\n    def do_GET(self):\n        if self.headers.get("Authorization") != "Bearer good-key":\n            return self._send(401, {"error": {"message": "bad key"}})\n        if self.path.endswith("/models"):\n            Gateway.catalog_gets += 1\n            if Gateway.fail_catalog_with:\n                return self._send(Gateway.fail_catalog_with, {"error": {"message": "busy"}})\n            return self._send(200, {"data": [{"id": m} for m in PRICE_PER_TOKEN]})\n        if self.path.endswith("/key"):\n            return self._send(200, {"data": {"limit_remaining": Gateway.balance,\n                                             "free_model_daily_requests": {"remaining": 42}}})\n        self._send(404, {})\n\n    def do_POST(self):\n        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))\n        Gateway.chat_posts.append(body)\n        now = time.time()\n        Gateway.recent_chat = [t for t in Gateway.recent_chat if now - t < 0.5] + [now]\n        if len(Gateway.recent_chat) > 2:\n            return self._send(429, {"error": {"message": "slow down"}})\n        model = body["model"]\n        if model not in PRICE_PER_TOKEN:\n            return self._send(404, {"error": {"message": "no such model"}})\n        cap = body.get("max_tokens") or body.get("max_completion_tokens") or MAX_OUTPUT\n        if cap * PRICE_PER_TOKEN[model] > Gateway.balance:\n            return self._send(402, {"error": {"message": "insufficient credits"}})\n        spent_tokens = min(cap, 600)   # reasoning model "thinks" a lot on a ping\n        Gateway.balance -= spent_tokens * PRICE_PER_TOKEN[model]\n        self._send(200, {"choices": [{"message": {"content": "OK"}}]})\n\n\nsrv = HTTPServer(("127.0.0.1", 0), Gateway)\nthreading.Thread(target=srv.serve_forever, daemon=True).start()\nBASE = f"http://127.0.0.1:{srv.server_address[1]}/api/v1"\nsc.OPENROUTER_HOSTS.append(f"127.0.0.1:{srv.server_address[1]}")   # fake gateway speaks /key\n\n\ndef entry(i, model, key="good-key"):\n    return {"id": f"e{i}", "name": f"OR - {model}", "model": model, "api_key": key,\n            "base_url": BASE, "check_kind": "generic_http"}\n\n\ndef run_all(items):\n    results = {}\n    sc.check_many(items, lambda f, eid, ok, d: results.__setitem__(eid, (ok, d)), spacing=0.6)\n    return results\n\n\nmodels = list(PRICE_PER_TOKEN) + ["retired/model-x"]          # x is NOT in the catalog\nitems = [("Thinking", entry(i, m)) for i, m in enumerate(models)]\nitems.append(("Voice", entry(99, "mid/model-b")))              # same model in a 2nd faculty\n\n# ---- run 1\nstart_balance = Gateway.balance\nr1 = run_all(items)\nspent_run1 = start_balance - Gateway.balance\ncheck("every entry gets exactly one result", set(r1) == {e["id"] for _, e in items})\ncheck("catalogued models are green WITHOUT any generation",\n      all(r1[f"e{i}"][0] is True for i in range(4))\n      and not any(p["model"] in PRICE_PER_TOKEN for p in Gateway.chat_posts))\ncheck("one catalog GET for the whole key (not one call per model)", Gateway.catalog_gets == 1)\ncheck("a model missing from the catalog falls back to ONE capped ping and is red (404)",\n      r1["e4"][0] is False and len(Gateway.chat_posts) == 1\n      and Gateway.chat_posts[0].get("max_tokens") == sc.CHECK_PING_MAX_TOKENS)\ncheck("the duplicate model in another faculty shares the result",\n      r1["e99"][0] is True)\ncheck("a check-all spends no credit on catalogued models", spent_run1 == 0)\ncheck("free models report the free requests left today",\n      "42 free requests" in r1["e3"][1])\n\n# ---- user removes the not-connected one, runs check all again (twice)\nitems2 = [(f, e) for f, e in items if r1[e["id"]][0] is not False]\nr2 = run_all(items2)\nr3 = run_all(items2)\ncheck("re-checking gives the SAME result every time (no flip-flop)",\n      {k: v[0] for k, v in r2.items()} == {k: v[0] for k, v in r3.items()}\n      == {k: r1[k][0] for k in r2})\ncheck("re-checking spends nothing", Gateway.balance == start_balance)\n\n# ---- temporary provider trouble keeps status (ok=None), not red\nGateway.fail_catalog_with = 503\nr4 = run_all(items2)\nGateway.fail_catalog_with = None\ncheck("a provider outage is reported as temporary (ok=None) for every entry",\n      all(v[0] is None for v in r4.values()))\n\n# ---- bad key is still a definite red\nr5 = run_all([("Thinking", entry(7, "cheap/model-a", key="wrong"))])\ncheck("a rejected key is still a definite red", r5["e7"][0] is False and "401" in r5["e7"][1])\n\n# ---- single-row Check: capped real ping, and paid model with no credit is red\nposts_before = len(Gateway.chat_posts)\ntime.sleep(0.6)\nok, detail = sc.check_generic_http(entry(8, "cheap/model-a"))\ncheck("single-row Check generates with a capped request",\n      ok is True and Gateway.chat_posts[posts_before].get("max_tokens") == sc.CHECK_PING_MAX_TOKENS)\ncheck("...and costs only the capped amount",\n      start_balance - Gateway.balance <= sc.CHECK_PING_MAX_TOKENS * PRICE_PER_TOKEN["cheap/model-a"] + 1e-12)\n\nGateway.balance = 0.0\nr6 = run_all([("Thinking", entry(9, "pricey/model-c")), ("Thinking", entry(10, "free/model-d:free"))])\ncheck("with no credit left, paid models are red (they\'d fail with 402) ...",\n      r6["e9"][0] is False and "402" in r6["e9"][1])\ncheck("... while free models stay green", r6["e10"][0] is True)\n\n# ---- the OLD behaviour, for the record: an uncapped ping per click\nGateway.balance = 1.0\ntime.sleep(0.6)\nimport requests  # noqa: E402\ncodes = []\nfor m in ("cheap/model-a", "mid/model-b", "pricey/model-c"):\n    time.sleep(0.6)\n    codes.append(requests.post(BASE + "/chat/completions",\n                               json={"model": m, "messages": [{"role": "user", "content": "ping"}]}).status_code)\ncheck("(reproduction) an UNCAPPED ping is refused 402 on the pricier models even with "\n      "credit left -- the old check\'s \'disconnects\'", codes[2] == 402)\n\nsrv.shutdown()\nn_fail = sum(1 for _, ok in CHECKS if not ok)\nprint(f"\\n{len(CHECKS) - n_fail}/{len(CHECKS)} checks passed")\nsys.exit(1 if n_fail else 0)\n'
TEST_EDITS = [('dev_tests/test_settings_panel.py', '    check("the check sends the SAME shape a build sends (no max_tokens "\n          "squeeze -- a 1-token ping can slip past OpenRouter-style "\n          "credit gates that block every real build call)",\n          posts and posts[0][1].get("model") == "fake-model-1"\n          and "max_tokens" not in posts[0][1])', '    check("the check ping is CAPPED (an uncapped ping burned a full reply "\n          "per click and made credit-gated gateways flip models to 402)",\n          posts and posts[0][1].get("model") == "fake-model-1"\n          and posts[0][1].get("max_tokens") == sc.CHECK_PING_MAX_TOKENS)'), ('dev_tests/test_settings_panel.py', '    check("HTTP 429 (working key, currently throttled) is green-with-a-"\n          "note, the truthful middle ground",\n          ok is True and "rate-limited" in detail.lower())', '    check("HTTP 429 (working key, currently throttled) is TEMPORARY: "\n          "ok=None so the panel keeps the previous status",\n          ok is None and "rate-limited" in detail.lower())'), ('dev_tests/test_settings_panel.py', '    check("any 5xx is an honest server-error red-x",\n          ok is False and "Server error" in detail)', '    check("any 5xx is TEMPORARY (provider outage), never a red-x that "\n          "\'Remove not connected\' would delete",\n          ok is None and "temporarily unavailable" in detail)'), ('dev_tests/test_act_script.py', '        check("CHECK and BUILD send the IDENTICAL max_tokens on "\n              "anthropic entries (parity, not a repeat of the blind "\n              "spot)",\n              [p["max_tokens"] for p in saved_payloads[-2:]].count(\n                  _pa.ANTHROPIC_MAX_TOKENS_DEFAULT) == 2)', '        check("the settings CHECK is capped (it must not reserve/spend a "\n              "build-sized budget per click) while the BUILD keeps the "\n              "shared max_tokens constant",\n              saved_payloads[-2]["max_tokens"] == _sc.CHECK_PING_MAX_TOKENS\n              and saved_payloads[-1]["max_tokens"]\n              == _pa.ANTHROPIC_MAX_TOKENS_DEFAULT)')]
TEST_EDITS.append(("dev_tests/test_settings_panel.py", '        check("connection summary shows connected, failed, and unchecked totals",\n              panel._connection_counts_lbl.cget("text")\n              == "Connected: 1  |  Not connected: 1  |  Not checked: 5")\n', '        check("connection summary shows connected, failed, and unchecked totals",\n              panel._connection_counts_lbl.cget("text")\n              == "Connected: 1  |  Not connected: 1  |  Not checked: 5")\n\n        # A TEMPORARY result (ok=None: rate limit / outage) must not flip a\n        # working model to "Not connected" -- the cause of models\n        # "disconnecting" on a re-check and then being bulk-removed.\n        panel._finish_check("Thinking", t1["id"], None,\n                            "Rate-limited right now (HTTP 429)")\n        widgets = panel._row_widgets[("Thinking", t1["id"])]\n        check("a temporary check result keeps a Connected model Connected",\n              "Connected" in widgets["status_lbl"].cget("text")\n              and "Not connected" not in widgets["status_lbl"].cget("text"))\n        check("...and the totals are unchanged",\n              panel._connection_counts_lbl.cget("text")\n              == "Connected: 1  |  Not connected: 1  |  Not checked: 5")\n        check("...and the reason is kept on the entry for the user to read",\n              next(e for e in panel._data["Thinking"] if e["id"] == t1["id"])\n              ["detail"].startswith("Rate-limited"))\n'))
NEEDED_IMPORTS = ["import re", "import threading", "import time",
                  "from concurrent.futures import ThreadPoolExecutor"]

notes, touched = [], []


def path(rel):
    return os.path.join(HERE, rel)


def read(rel):
    # newline="" keeps Windows CRLF line endings exactly as they are
    with open(path(rel), encoding="utf-8", newline="") as fh:
        return fh.read()


def write(rel, text, dry):
    if dry:
        return
    p = path(rel)
    if os.path.exists(p) and not os.path.exists(p + BAK):
        shutil.copy2(p, p + BAK)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    touched.append(rel)


def node_span(lines, node):
    """[start, end) line indexes of a top-level/def node incl. decorators
    and the comment lines directly above it."""
    start = (node.decorator_list[0].lineno if getattr(node, "decorator_list", None)
             else node.lineno) - 1
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    return start, node.end_lineno


def detect_newline(text):
    return "\r\n" if "\r\n" in text else "\n"


def fix_checks(dry):
    rel = "settings_checks.py"
    text = read(rel)
    nl = detect_newline(text)
    text = text.replace("\r\n", "\n")
    if MARK in text:
        notes.append(f"{rel}: already fixed -- skipped")
        return
    # The new check code calls these provider_autodetect helpers; make sure
    # this copy has them (checked by reading the file, nothing is imported).
    pa_names = set()
    for n in ast.parse(read("provider_autodetect.py")).body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            pa_names.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Name):
                    pa_names.add(t.id)
    missing = [x for x in ("chat_payload", "chat_url", "entry_api_style",
                           "preset_for_base_url", "models_url", "headers_for",
                           "ANTHROPIC_API_STYLE") if x not in pa_names]
    if missing:
        raise SystemExit("provider_autodetect.py is missing " + ", ".join(missing)
                         + " -- send me provider_autodetect.py and settings_checks.py.")
    tree = ast.parse(text)
    top = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for need in ("check_generic_http", "run_check", "_short"):
        if need not in top:
            raise SystemExit(f"{rel}: can't find function {need}() -- send me this file and I'll adapt the fix.")
    lines = text.splitlines(keepends=True)

    # replace run_check (later in file first, so earlier spans stay valid)
    spans = sorted([("run", top["run_check"]), ("generic", top["check_generic_http"])],
                   key=lambda kv: kv[1].lineno, reverse=True)
    for kind, node in spans:
        s, e = node_span(lines, node)
        repl = RUN_CHECK_SRC if kind == "run" else (GENERIC_SRC.rstrip("\n") + "\n\n\n" + HELPERS_SRC)
        if not repl.endswith("\n"):
            repl += "\n"
        lines[s:e] = [repl]
    text = "".join(lines)

    # imports + constants after the last top-level import
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    present = {ast.get_source_segment(text, n).strip() for n in imports}
    add = [imp for imp in NEEDED_IMPORTS if imp not in present]
    if imports:
        at = imports[-1].end_lineno
    else:
        at = tree.body[0].end_lineno if (tree.body and isinstance(tree.body[0], ast.Expr)) else 0
    block = "".join(i + "\n" for i in add) + "\n" + CONSTS_BLOCK.rstrip("\n") + "\n\n"
    lines[at:at] = [block]
    text = "".join(lines)
    ast.parse(text)
    write(rel, text.replace("\n", nl), dry)
    notes.append(f"{rel}: replaced check_generic_http() and run_check(), added check_many() + helpers"
                 + (f", added imports: {', '.join(add)}" if add else ""))


def fix_panel(dry):
    rel = "settings_panel.py"
    text = read(rel)
    nl = detect_newline(text)
    text = text.replace("\r\n", "\n")
    if "check_many" in text:
        notes.append(f"{rel}: already fixed -- skipped")
        return
    tree = ast.parse(text)
    cls = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
                and any(isinstance(f, ast.FunctionDef) and f.name == "_check_all" for f in n.body)), None)
    if cls is None:
        raise SystemExit(f"{rel}: can't find the class with _check_all() -- send me this file.")
    methods = {f.name: f for f in cls.body if isinstance(f, ast.FunctionDef)}
    for need in ("_finish_check", "_check_all"):
        if need not in methods:
            raise SystemExit(f"{rel}: can't find method {need}() -- send me this file.")
    lines = text.splitlines(keepends=True)
    indent = lines[methods["_check_all"].lineno - 1][:len(lines[methods["_check_all"].lineno - 1])
                                                    - len(lines[methods["_check_all"].lineno - 1].lstrip())]

    def reindent(src):
        return "".join((indent + l[4:]) if l.startswith("    ") else l for l in src.splitlines(keepends=True))

    for name in sorted(("_finish_check", "_check_all"), key=lambda n: methods[n].lineno, reverse=True):
        node = methods[name]
        s, e = node.lineno - 1, node.end_lineno
        while s > 0 and lines[s - 1].strip().startswith("@"):
            s -= 1
        repl = reindent(FINISH_SRC) if name == "_finish_check" else (
            reindent(CHECKALL_SRC).rstrip("\n") + "\n\n" + reindent(FINISHED_SRC))
        if not repl.endswith("\n"):
            repl += "\n"
        lines[s:e] = [repl]
    text = "".join(lines)
    for mod in ("import time", "import threading"):
        if not any(l.strip() == mod for l in text.splitlines()):
            text = mod + "\n" + text
            notes.append(f"{rel}: added '{mod}'")
    ast.parse(text)
    if "settings_checks" not in text or "settings_store" not in text:
        notes.append(f"{rel}: WARNING settings_checks/settings_store import not found -- check imports")
    write(rel, text.replace("\n", nl), dry)
    notes.append(f"{rel}: replaced _finish_check() and _check_all(), added _check_all_finished()")


def fix_tests(dry):
    if not os.path.isdir(path("dev_tests")):
        notes.append("dev_tests/ not found -- tests skipped")
        return
    rel = "dev_tests/test_settings_check_cost.py"
    if not os.path.exists(path(rel)):
        write(rel, NEW_TEST, dry)
        notes.append(f"{rel}: added")
    for rel, old, new in TEST_EDITS:
        if not os.path.exists(path(rel)):
            continue
        text = read(rel)
        nl = detect_newline(text)
        t = text.replace("\r\n", "\n")
        if new in t:
            continue
        if old in t:
            write(rel, t.replace(old, new).replace("\n", nl), dry)
            notes.append(f"{rel}: updated one assertion to the new behaviour")
        else:
            notes.append(f"{rel}: an old assertion wasn't found (fine if you edited/removed it)")


def undo():
    n = 0
    for root, _d, files in os.walk(HERE):
        for f in files:
            if f.endswith(BAK):
                p = os.path.join(root, f)
                shutil.move(p, p[:-len(BAK)])
                n += 1
    new_test = path("dev_tests/test_settings_check_cost.py")
    if os.path.exists(new_test):
        os.remove(new_test)
    print(f"Restored {n} file(s).")


def main():
    if "--undo" in sys.argv:
        return undo()
    dry = "--dry-run" in sys.argv
    if not os.path.exists(path("settings_checks.py")):
        raise SystemExit("Run this from the floating-agent-python folder (settings_checks.py not found).")
    try:
        fix_checks(dry)
        fix_panel(dry)
        fix_tests(dry)
        for rel in touched:
            if rel.endswith(".py"):
                py_compile.compile(path(rel), doraise=True)
    except BaseException as exc:
        for rel in touched:
            p = path(rel)
            if os.path.exists(p + BAK):
                shutil.move(p + BAK, p)
            elif rel.endswith("test_settings_check_cost.py") and os.path.exists(p):
                os.remove(p)
        print("Nothing was changed (all files restored).")
        raise SystemExit(f"Error: {exc}")
    print(("DRY RUN -- would do:" if dry else "Done:"))
    for n in notes:
        print("  -", n)
    if not dry:
        print("\nBackups: *.bak-checkfix   (undo with: python apply_check_fix.py --undo)")
        print("Verify:   python dev_tests/test_settings_check_cost.py")


if __name__ == "__main__":
    main()

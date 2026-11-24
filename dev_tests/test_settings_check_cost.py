"""Regression: "Check all" drained tokens and flipped models between
Connected and Not connected on every re-check.

A fake OpenRouter-style gateway reproduces the real mechanics:
  * credit-gated: a chat call with NO max_tokens reserves the model's full
    max output against the balance and answers 402 when it can't cover it;
  * every generation spends credit;
  * a burst of chat calls on one key gets 429; the catalog is free.
The old check (uncapped ping per model, 4 at a time) spent credit on every
click and turned pricier models red as the balance fell. The new one must:
spend nothing for catalogued models, give identical results run after run,
keep previous status on temporary errors, and send one catalog GET per key.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings_checks as sc  # noqa: E402

CHECKS = []


def check(label, cond):
    CHECKS.append((label, bool(cond)))
    print(("PASS" if cond else "FAIL") + " - " + label)


PRICE_PER_TOKEN = {"cheap/model-a": 0.000001, "mid/model-b": 0.00001,
                   "pricey/model-c": 0.0001, "free/model-d:free": 0.0}
MAX_OUTPUT = 32000            # what an uncapped request reserves


class Gateway(BaseHTTPRequestHandler):
    balance = 1.0
    catalog_gets = 0
    chat_posts = []
    recent_chat = []
    fail_catalog_with = None

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer good-key":
            return self._send(401, {"error": {"message": "bad key"}})
        if self.path.endswith("/models"):
            Gateway.catalog_gets += 1
            if Gateway.fail_catalog_with:
                return self._send(Gateway.fail_catalog_with, {"error": {"message": "busy"}})
            return self._send(200, {"data": [{"id": m} for m in PRICE_PER_TOKEN]})
        if self.path.endswith("/key"):
            return self._send(200, {"data": {"limit_remaining": Gateway.balance,
                                             "free_model_daily_requests": {"remaining": 42}}})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Gateway.chat_posts.append(body)
        now = time.time()
        Gateway.recent_chat = [t for t in Gateway.recent_chat if now - t < 0.5] + [now]
        if len(Gateway.recent_chat) > 2:
            return self._send(429, {"error": {"message": "slow down"}})
        model = body["model"]
        if model not in PRICE_PER_TOKEN:
            return self._send(404, {"error": {"message": "no such model"}})
        cap = body.get("max_tokens") or body.get("max_completion_tokens") or MAX_OUTPUT
        if cap * PRICE_PER_TOKEN[model] > Gateway.balance:
            return self._send(402, {"error": {"message": "insufficient credits"}})
        spent_tokens = min(cap, 600)   # reasoning model "thinks" a lot on a ping
        Gateway.balance -= spent_tokens * PRICE_PER_TOKEN[model]
        self._send(200, {"choices": [{"message": {"content": "OK"}}]})


srv = HTTPServer(("127.0.0.1", 0), Gateway)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}/api/v1"
sc.OPENROUTER_HOSTS.append(f"127.0.0.1:{srv.server_address[1]}")   # fake gateway speaks /key


def entry(i, model, key="good-key"):
    return {"id": f"e{i}", "name": f"OR - {model}", "model": model, "api_key": key,
            "base_url": BASE, "check_kind": "generic_http"}


def run_all(items):
    results = {}
    sc.check_many(items, lambda f, eid, ok, d: results.__setitem__(eid, (ok, d)), spacing=0.6)
    return results


models = list(PRICE_PER_TOKEN) + ["retired/model-x"]          # x is NOT in the catalog
items = [("Thinking", entry(i, m)) for i, m in enumerate(models)]
items.append(("Voice", entry(99, "mid/model-b")))              # same model in a 2nd faculty

# ---- run 1
start_balance = Gateway.balance
r1 = run_all(items)
spent_run1 = start_balance - Gateway.balance
check("every entry gets exactly one result", set(r1) == {e["id"] for _, e in items})
check("catalogued models are green WITHOUT any generation",
      all(r1[f"e{i}"][0] is True for i in range(4))
      and not any(p["model"] in PRICE_PER_TOKEN for p in Gateway.chat_posts))
check("one catalog GET for the whole key (not one call per model)", Gateway.catalog_gets == 1)
check("a model missing from the catalog falls back to ONE capped ping and is red (404)",
      r1["e4"][0] is False and len(Gateway.chat_posts) == 1
      and Gateway.chat_posts[0].get("max_tokens") == sc.CHECK_PING_MAX_TOKENS)
check("the duplicate model in another faculty shares the result",
      r1["e99"][0] is True)
check("a check-all spends no credit on catalogued models", spent_run1 == 0)
check("free models report the free requests left today",
      "42 free requests" in r1["e3"][1])

# ---- user removes the not-connected one, runs check all again (twice)
items2 = [(f, e) for f, e in items if r1[e["id"]][0] is not False]
r2 = run_all(items2)
r3 = run_all(items2)
check("re-checking gives the SAME result every time (no flip-flop)",
      {k: v[0] for k, v in r2.items()} == {k: v[0] for k, v in r3.items()}
      == {k: r1[k][0] for k in r2})
check("re-checking spends nothing", Gateway.balance == start_balance)

# ---- temporary provider trouble keeps status (ok=None), not red
Gateway.fail_catalog_with = 503
r4 = run_all(items2)
Gateway.fail_catalog_with = None
check("a provider outage is reported as temporary (ok=None) for every entry",
      all(v[0] is None for v in r4.values()))

# ---- bad key is still a definite red
r5 = run_all([("Thinking", entry(7, "cheap/model-a", key="wrong"))])
check("a rejected key is still a definite red", r5["e7"][0] is False and "401" in r5["e7"][1])

# ---- single-row Check: capped real ping, and paid model with no credit is red
posts_before = len(Gateway.chat_posts)
time.sleep(0.6)
ok, detail = sc.check_generic_http(entry(8, "cheap/model-a"))
check("single-row Check generates with a capped request",
      ok is True and Gateway.chat_posts[posts_before].get("max_tokens") == sc.CHECK_PING_MAX_TOKENS)
check("...and costs only the capped amount",
      start_balance - Gateway.balance <= sc.CHECK_PING_MAX_TOKENS * PRICE_PER_TOKEN["cheap/model-a"] + 1e-12)

Gateway.balance = 0.0
r6 = run_all([("Thinking", entry(9, "pricey/model-c")), ("Thinking", entry(10, "free/model-d:free"))])
check("with no credit left, paid models are red (they'd fail with 402) ...",
      r6["e9"][0] is False and "402" in r6["e9"][1])
check("... while free models stay green", r6["e10"][0] is True)

# ---- the OLD behaviour, for the record: an uncapped ping per click
Gateway.balance = 1.0
time.sleep(0.6)
import requests  # noqa: E402
codes = []
for m in ("cheap/model-a", "mid/model-b", "pricey/model-c"):
    time.sleep(0.6)
    codes.append(requests.post(BASE + "/chat/completions",
                               json={"model": m, "messages": [{"role": "user", "content": "ping"}]}).status_code)
check("(reproduction) an UNCAPPED ping is refused 402 on the pricier models even with "
      "credit left -- the old check's 'disconnects'", codes[2] == 402)

srv.shutdown()
n_fail = sum(1 for _, ok in CHECKS if not ok)
print(f"\n{len(CHECKS) - n_fail}/{len(CHECKS)} checks passed")
sys.exit(1 if n_fail else 0)

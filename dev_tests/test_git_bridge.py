"""F42 battery: the git bridge between the IDE and the Settings panel.

Runtime parts drive REAL git through file:// remotes (offline,
deterministic): clone -> commit-as-chosen-author -> push -> second
clone -> commit-as-other-author -> push -> pull, asserting every commit
carries exactly the identity the user picked (the F41 protocol), and
that the bridge reads the SAME ~/.friday store the Settings panel
writes. Token rules are proven with a monkeypatched subprocess.run
(token rides a github.com-scoped env header, never argv) and a local
HTTP stub (create-repo Bearer POST, error paths sanitized). Static
pins lock the IDE seams: the FRIDAY GITHUB action block, the author
picker, and the rule that a token NEVER enters run_terminal_command's
visible command line.
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import git_bridge  # noqa: E402
import memory  # noqa: E402
import user_profile  # noqa: E402

IDE_SRC = os.path.join(ROOT, "ide", "ide.py")

AUTHOR_A = {"username": "okafor-dev", "email": "okafor.dev@example.com"}
AUTHOR_B = {"username": "okafor-alt", "email": "okafor.alt@example.com"}


def _src(path):
    with open(path) as fh:
        return fh.read()


def _git(path, *args):
    return subprocess.run(["git"] + list(args), cwd=path,
                          capture_output=True, text=True)


class BridgeRuntime(unittest.TestCase):
    def setUp(self):
        if not git_bridge.git_available():
            self.skipTest("git not installed")
        self.base = tempfile.mkdtemp(prefix="f42_batt_")

    def _seed_remote(self):
        """bare origin + a baseline commit authored as AUTHOR_A."""
        origin = os.path.join(self.base, "origin.git")
        subprocess.run(["git", "init", "--bare", "-q", origin],
                       check=True)
        seed = os.path.join(self.base, "seed")
        self.assertTrue(git_bridge.clone(origin, seed)["ok"])
        with open(os.path.join(seed, "app.py"), "w") as fh:
            fh.write("print('v1')\n")
        res = git_bridge.commit_all(seed, "baseline", AUTHOR_A)
        self.assertTrue(res["ok"], res["out"])
        self.assertTrue(git_bridge.push(seed)["ok"])
        return origin

    def test_full_clone_commit_push_pull_cycle(self):
        origin = self._seed_remote()
        laptop = os.path.join(self.base, "laptop")
        desktop = os.path.join(self.base, "desktop")

        self.assertTrue(git_bridge.clone(origin, laptop)["ok"])
        self.assertTrue(os.path.isfile(os.path.join(laptop, "app.py")))
        self.assertTrue(git_bridge.has_remote(laptop))
        self.assertNotEqual(git_bridge.current_branch(laptop), "")

        # laptop commits + pushes as AUTHOR_A
        with open(os.path.join(laptop, "feature.py"), "w") as fh:
            fh.write("FEATURE = 1\n")
        res = git_bridge.commit_all(laptop, "add feature", AUTHOR_A)
        self.assertTrue(res["ok"], res["out"])
        self.assertTrue(git_bridge.push(laptop)["ok"])

        # desktop clones -> sees the pushed file; commits + pushes as
        # AUTHOR_B (the user picking a DIFFERENT author mid-stream --
        # the protocol's redirect path)
        self.assertTrue(git_bridge.clone(origin, desktop)["ok"])
        self.assertTrue(os.path.isfile(os.path.join(desktop,
                                                    "feature.py")))
        with open(os.path.join(desktop, "feature.py"), "w") as fh:
            fh.write("FEATURE = 2\n")
        self.assertTrue(git_bridge.commit_all(desktop, "tune feature",
                                              AUTHOR_B)["ok"])
        self.assertTrue(git_bridge.push(desktop)["ok"])

        # laptop pulls the change back (fast-forward path)
        pulled = git_bridge.pull(laptop)
        self.assertTrue(pulled["ok"], pulled["out"])
        with open(os.path.join(laptop, "feature.py")) as fh:
            self.assertIn("FEATURE = 2", fh.read())

        # every commit carries EXACTLY the identity chosen for it
        log = _git(laptop, "log", "--format=%an|%ae|%s").stdout
        self.assertIn("okafor-dev|okafor.dev@example.com|baseline", log)
        self.assertIn("okafor-dev|okafor.dev@example.com|add feature",
                      log)
        self.assertIn("okafor-alt|okafor.alt@example.com|tune feature",
                      log)
        head = _git(laptop, "log", "-1", "--format=%an|%ae").stdout
        self.assertIn("okafor-alt|okafor.alt@example.com", head)

        # push -u wired the upstream for tracked pulls
        upstream = _git(laptop, "rev-parse", "--abbrev-ref", "@{u}")
        self.assertEqual(upstream.returncode, 0)

    def test_nothing_to_commit_is_a_friendly_noop(self):
        origin = self._seed_remote()
        repo = os.path.join(self.base, "clean")
        self.assertTrue(git_bridge.clone(origin, repo)["ok"])
        res = git_bridge.commit_all(repo, "no-op", AUTHOR_A)
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("noop"))
        self.assertIn("Nothing to commit", res["out"])

    def test_commit_requires_an_author_choice(self):
        origin = self._seed_remote()
        repo = os.path.join(self.base, "pickme")
        self.assertTrue(git_bridge.clone(origin, repo)["ok"])
        res = git_bridge.commit_all(repo, "x", None)
        self.assertFalse(res["ok"])
        self.assertIn("No GitHub author chosen", res["out"])


class BridgeHelpers(unittest.TestCase):
    def test_parse_github_url_all_schemes(self):
        self.assertEqual(
            git_bridge.parse_github_url(
                "https://github.com/okafor/friday.git"),
            {"owner": "okafor", "repo": "friday"})
        self.assertEqual(
            git_bridge.parse_github_url("https://github.com/okafor/f/"),
            {"owner": "okafor", "repo": "f"})
        self.assertEqual(
            git_bridge.parse_github_url("git@github.com:Okafor/two.git"),
            {"owner": "Okafor", "repo": "two"})
        self.assertEqual(
            git_bridge.parse_github_url(
                "ssh://git@github.com/o/three.git"),
            {"owner": "o", "repo": "three"})
        self.assertIsNone(git_bridge.parse_github_url(
            "/home/user/bare.git"))
        self.assertIsNone(git_bridge.parse_github_url(
            "https://gitlab.com/o/r.git"))
        self.assertIsNone(git_bridge.parse_github_url(""))

    def test_suggested_author_clone_owner_default(self):
        authors = [AUTHOR_A, AUTHOR_B]
        # cloned FROM okafor-alt's account -> that author pre-selects
        # (case-insensitive owner match)
        pick = git_bridge.suggested_author(
            "git@github.com:OKAFOR-ALT/repo.git", authors)
        self.assertEqual(pick, AUTHOR_B)
        # a stranger's repo -> no pre-selection, the user decides
        self.assertIsNone(git_bridge.suggested_author(
            "https://github.com/torvalds/linux.git", authors))
        # file:// test/local remotes -> never token/authors logic
        self.assertIsNone(git_bridge.suggested_author(
            "/tmp/origin.git", authors))
        self.assertIsNone(git_bridge.suggested_author(
            "https://github.com/okafor-dev/x.git", []))

    def test_resolve_token_uses_selected_author_row_only(self):
        """GitHub commit/push/API actions use only the selected author's
        token; Web passwords is never an implicit fallback."""
        profile = {"github_authors": [
            {"username": "kappa16", "email": "a@x.io",
             "token": "ghp_KAPPA"},
            {"username": "veloura-dev", "email": "v@x.io",
             "token": "ghp_VELOURA"}],
            "credentials": [{"site": "github.com", "username": "kappa16",
                             "email": "", "password": "ghp_WEBROW"}]}
        self.assertEqual(
            git_bridge.resolve_token({"username": "veloura-dev"},
                                     profile), "ghp_VELOURA")
        self.assertEqual(
            git_bridge.resolve_token({"username": "KAPPA16"}, profile),
            "ghp_KAPPA")
        bare = {"github_authors": [{"username": "kappa16", "email": "",
                                    "token": ""}],
                "credentials": profile["credentials"]}
        self.assertIsNone(
            git_bridge.resolve_token({"username": "kappa16"}, bare))
        self.assertIsNone(git_bridge.resolve_token(None, profile))
        self.assertIsNone(git_bridge.resolve_token(
            {"username": "nope"}, {"github_authors": [],
                                   "credentials": profile["credentials"]}))

    def test_authors_list_is_secrets_free_but_full_rows_carry_tokens(self):
        profile = {"github_authors": [
            {"username": "kappa16", "email": "k@x.io",
             "token": "ghp_SECRET"}]}
        self.assertEqual(git_bridge.authors(profile),
                         [{"username": "kappa16", "email": "k@x.io"}])
        self.assertNotIn("ghp_SECRET", repr(git_bridge.authors(profile)))
        full = git_bridge._authors_full(profile)
        self.assertEqual(full[0]["token"], "ghp_SECRET")

    def test_github_credential_reads_web_passwords_row(self):
        profile = {"credentials": [
            {"site": "example.com", "username": "u", "password": "p",
             "email": ""},
            {"site": "https://github.com/", "username": "okafor",
             "password": "ghp_SECRET", "email": "o@x.dev"}]}
        cred = git_bridge.github_credential(profile)
        self.assertEqual(cred["token"], "ghp_SECRET")
        self.assertEqual(cred["username"], "okafor")
        self.assertEqual(cred["email"], "o@x.dev")
        # no github row -> None; no profile -> None; authors sanitize
        self.assertIsNone(git_bridge.github_credential(
            {"credentials": []}))
        self.assertIsNone(git_bridge.github_credential(None))

    def test_authors_reads_the_profile_store_shape(self):
        messy = {"github_authors": [
            AUTHOR_A, {"username": "  ", "email": "x"},
            {"email": "no-name@x.dev"}, "garbage", None]}
        self.assertEqual(git_bridge.authors(messy), [AUTHOR_A])
        self.assertEqual(git_bridge.authors({}), [])
        self.assertEqual(git_bridge.authors(None), [])

    def test_sanitize_scrubs_every_secret(self):
        out = git_bridge.sanitize(
            "fatal: https://ghp_ABC@github.com/o/r rejected ghp_ABC",
            "ghp_ABC")
        self.assertNotIn("ghp_ABC", out)
        self.assertIn("***", out)

    def test_token_rides_env_never_argv_and_output_is_scrubbed(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = dict(kwargs.get("env") or {})
            return SimpleNamespace(
                returncode=0,
                stdout="To https://x-access-token:ghp_LEAK@github.com"
                       "/o/r.git",
                stderr="")

        with mock.patch.object(git_bridge.subprocess, "run", fake_run):
            res = git_bridge.run_git(["push", "-u", "origin", "main"],
                                     cwd="/tmp", token="ghp_LEAK",
                                     username="veloura-dev")
        # token appears in NO argv element
        joined = " ".join(captured["cmd"])
        self.assertNotIn("ghp_LEAK", joined)
        # token rides the github.com-scoped http header via ENVIRONMENT
        env = captured["env"]
        self.assertEqual(env.get("GIT_CONFIG_COUNT"), "1")
        self.assertEqual(env.get("GIT_CONFIG_KEY_0"),
                         "http.https://github.com/.extraheader")
        import base64
        encoded = base64.b64encode(b"veloura-dev:ghp_LEAK").decode()
        self.assertEqual(env.get("GIT_CONFIG_VALUE_0"),
                         "Authorization: Basic " + encoded)
        # credential prompts can never silently hang the UI
        self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0")
        # captured output is scrubbed before any surface sees it
        self.assertTrue(res["ok"])
        self.assertNotIn("ghp_LEAK", res["out"])
        self.assertIn("***", res["out"])


class BridgeAuthResilience(unittest.TestCase):
    """F45: a bad stored token may never kill a public clone, and auth
    failures always end in actionable guidance."""

    def test_is_auth_failure_table(self):
        yes = ["fatal: Authentication failed for 'https://github.com/'",
               "remote: invalid credentials",
               "fatal: could not read Username for 'https://github.com'",
               "The requested URL returned error: 401",
               "The requested URL returned error: 403"]
        no = ["Repository not found.", "Could not resolve host: github.com",
              "Everything up-to-date", "Connection timed out", ""]
        for text in yes:
            self.assertTrue(git_bridge.is_auth_failure(text), text)
        for text in no:
            self.assertFalse(git_bridge.is_auth_failure(text), text)

    def test_note_bad_token_appends_guidance_only_on_auth_failures(self):
        bad = {"ok": False, "out": "fatal: Authentication failed"}
        out = git_bridge.note_bad_token(dict(bad), "ghp_BAD")
        self.assertIn("GitHub rejected the token Friday selected", out["out"])
        self.assertIn("GitHub authors", out["out"])
        self.assertNotIn("Web passwords", out["out"])
        net = {"ok": False, "out": "Could not resolve host"}
        self.assertEqual(git_bridge.note_bad_token(
            dict(net), "ghp_BAD")["out"], net["out"])   # untouched
        ok = {"ok": True, "out": "done"}
        self.assertEqual(git_bridge.note_bad_token(
            dict(ok), "ghp_BAD")["out"], "done")        # success untouched
        no_token = {"ok": False, "out": "fatal: Authentication failed"}
        self.assertEqual(git_bridge.note_bad_token(
            dict(no_token), "")["out"], no_token["out"])  # no token, no note

    def test_clone_retries_public_anonymously_when_token_rejected(self):
        calls = []
        dest = os.path.join(tempfile.mkdtemp(prefix="f45_"), "repo")

        def fake_run_git(args, cwd=None, token=None, timeout=120, username=None):
            calls.append({"args": list(args), "token": token})
            if token:
                return {"ok": False, "code": 128,
                        "out": "remote: invalid credentials\n"
                               "fatal: Authentication failed"}
            os.makedirs(os.path.join(str(args[-1]), ".git"))
            return {"ok": True, "code": 0, "out": "done."}

        with mock.patch.object(git_bridge, "run_git", fake_run_git):
            res = git_bridge.clone("https://github.com/Kappa16/"
                                   "frontend-madness.git", dest,
                                   token="ghp_BAD")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["token"], "ghp_BAD")
        self.assertIsNone(calls[1]["token"])            # anonymous retry
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("retried_anonymous"))
        self.assertIn("cloned anonymously", res["out"])

    def test_clone_retry_still_failing_explains_both_halves(self):
        dest = os.path.join(tempfile.mkdtemp(prefix="f45_"), "repo")

        def always_fail(args, cwd=None, token=None, timeout=120, username=None):
            return {"ok": False, "code": 128,
                    "out": "fatal: Authentication failed"}

        with mock.patch.object(git_bridge, "run_git", always_fail):
            res = git_bridge.clone("https://github.com/o/private.git",
                                   dest, token="ghp_BAD")
        self.assertFalse(res["ok"])
        self.assertNotIn("retried_anonymous", res)
        self.assertIn("invalid/expired", res["out"])
        self.assertIn("Settings > Profile > Web passwords", res["out"])

    def test_bad_token_does_not_poison_good_clones(self):
        """No token -> no retry logic at all (public path untouched)."""
        calls = []
        dest = os.path.join(tempfile.mkdtemp(prefix="f45_"), "repo")

        def fake_run_git(args, cwd=None, token=None, timeout=120, username=None):
            calls.append(token)
            os.makedirs(os.path.join(str(args[-1]), ".git"))
            return {"ok": True, "code": 0, "out": "done."}

        with mock.patch.object(git_bridge, "run_git", fake_run_git):
            res = git_bridge.clone("https://github.com/o/r.git", dest)
        self.assertTrue(res["ok"])
        self.assertEqual(calls, [None])                 # exactly one try


class _StubHandler(http.server.BaseHTTPRequestHandler):
    captured = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        _StubHandler.captured = {
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": self.rfile.read(length).decode("utf-8")}
        status = getattr(self.server, "_status", 201)
        payload = getattr(self.server, "_payload",
                          {"clone_url": "https://github.com/okafor/d.git",
                           "html_url": "https://github.com/okafor/d",
                           "full_name": "okafor/d"})
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args, **_kwargs):
        pass


class BridgeCreateRepo(unittest.TestCase):
    def _stub(self, status=201, payload=None):
        server = http.server.HTTPServer(("127.0.0.1", 0), _StubHandler)
        server._status = status
        if payload is not None:
            server._payload = payload
        threading.Thread(target=server.serve_forever, daemon=True
                         ).start()
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % server.server_address[1]

    def test_create_repo_posts_bearer_and_parses(self):
        base = self._stub()
        res = git_bridge.create_repo("demo", "ghp_SECRET", private=True,
                                     api_base=base)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["clone_url"], "https://github.com/okafor/d.git")
        self.assertEqual(res["full_name"], "okafor/d")
        seen = _StubHandler.captured
        self.assertEqual(seen["path"], "/user/repos")
        self.assertEqual(seen["authorization"], "Bearer ghp_SECRET")
        body = json.loads(seen["body"])
        self.assertEqual(body["name"], "demo")
        self.assertTrue(body["private"])

    def test_create_repo_error_never_leaks_the_token(self):
        base = self._stub(status=422, payload={
            "message": "Repository creation failed for token ghp_SHH"})
        res = git_bridge.create_repo("demo", "ghp_SHH", api_base=base)
        self.assertFalse(res["ok"])
        self.assertNotIn("ghp_SHH", res["error"])
        self.assertIn("***", res["error"])
        self.assertEqual(res.get("code"), 422)

    def test_create_repo_requires_token(self):
        res = git_bridge.create_repo("demo", "")
        self.assertFalse(res["ok"])
        self.assertIn("token", res["error"])


class BridgeStoreSharing(unittest.TestCase):
    """What the Settings panel writes, the IDE bridge reads: one ~/.friday
    store, two panels."""

    def test_bridge_reads_the_same_store_settings_writes(self):
        prev = memory._memory_dir
        tmp = tempfile.mkdtemp(prefix="f42_store_")
        memory.set_memory_dir(tmp)
        try:
            profile = user_profile.default_profile()
            profile["github_authors"] = [dict(AUTHOR_A), dict(AUTHOR_B)]
            profile["credentials"] = [
                {"site": "github.com", "email": "o@x.dev",
                 "username": "okafor-dev", "password": "ghp_SHARED"}]
            user_profile.save(profile)
            self.assertEqual(git_bridge.authors(), [AUTHOR_A, AUTHOR_B])
            cred = git_bridge.github_credential()
            self.assertEqual(cred["token"], "ghp_SHARED")
            self.assertEqual(cred["username"], "okafor-dev")
        finally:
            memory.set_memory_dir(prev)


class BridgeSourcePins(unittest.TestCase):
    def test_pins_bridge_file(self):
        src = _src(os.path.join(ROOT, "git_bridge.py"))
        self.assertIn("def run_git(args, cwd=None, token=None", src)
        self.assertIn('env["GIT_CONFIG_KEY_0"] = '
                      '"http.https://github.com/.extraheader"', src)
        self.assertIn('"GIT_TERMINAL_PROMPT"] = "0"', src)
        self.assertIn("def suggested_author(", src)
        self.assertIn("def sanitize(text, *secrets):", src)
        self.assertIn('"pull", "--ff-only", remote, branch', src)
        self.assertIn('"/user/repos"', src)
        self.assertIn('if "github.com" not in site:', src)
        self.assertIn("import user_profile", src)
        self.assertIn('"user.name=%s" % author["username"]', src)
        # the protocol stays pinned in the module docstring
        self.assertIn("CLONED", src)
        self.assertIn("no dialog (VSCode-like, no asking)", src)
        self.assertIn("goes out under exactly that", src)
        self.assertIn("the user picks\n  WHICH author", src)
        self.assertIn("fork's upstream parent", src)

    def test_pins_ide_wiring(self):
        src = _src(IDE_SRC)
        self.assertIn("import git_bridge", src)
        self.assertIn("def _git_actions_block(self, parent, repo):", src)
        self.assertIn('"FRIDAY GITHUB"', src)
        self.assertIn("self.app._git_actions_block(f, repo=True)", src)
        self.assertIn("self.app._git_actions_block(f, repo=False)", src)
        self.assertIn("def _ask_github_author(", src)
        self.assertIn("def _build_author_picker(", src)
        self.assertIn('self._dialog("Choose GitHub Author", 470, height)',
                      src)
        self.assertIn("def github_commit_push_dialog(self):", src)
        self.assertIn("def github_push(self):", src)
        self.assertIn("def github_pull(self):", src)
        self.assertIn("def github_publish_dialog(self):", src)
        self.assertIn("def github_pr_dialog(self):", src)
        self.assertIn("def _pick_repo_author(", src)
        self.assertIn('"Open Pull Request..."', src)
        self.assertIn("fork -> upstream", src)
        self.assertIn('"Commit & Push..."', src)
        self.assertIn('"Publish to GitHub..."', src)
        self.assertIn("Profile > GitHub authors", src)
        self.assertIn('site: github.com', src)
        # both panels referenced by the actions block hint
        self.assertIn("Web passwords", src)

    def test_pins_quiet_skin_bridge(self):
        """The quiet redesign swaps the sidebar builders wholesale -- the
        FRIDAY GITHUB block must exist THERE too (that's the skin the app
        actually shows)."""
        src = _src(os.path.join(ROOT, "ide", "quiet", "panels.py"))
        self.assertIn(
            "def _quiet_friday_github(self,app,core,frame,repo):", src)
        self.assertIn("text='FRIDAY GITHUB'", src)
        self.assertIn(
            "self._quiet_friday_github(app,core,frame,repo=True)", src)
        self.assertIn(
            "self._quiet_friday_github(app,core,frame,repo=False)", src)
        self.assertIn("app.github_commit_push_dialog", src)
        self.assertIn("app.github_commit_dialog", src)
        self.assertIn("app.github_push", src)
        self.assertIn("app.github_pull", src)
        self.assertIn("app.github_publish_dialog", src)
        self.assertIn("app.clone_repository_dialog", src)
        self.assertIn("'Commit & Push'+chr(8230)", src)
        # F46: own-account/fork clones auto-commit as that author
        self.assertIn("automatically (this clone is yours -- no asking)",
                      src)
        self.assertIn("'Open Pull Request'+chr(8230)", src)
        self.assertIn("app.github_pr_dialog", src)
        self.assertIn("Settings > Profile > Web passwords", src)

    def test_token_never_enters_the_visible_terminal_command(self):
        src = _src(IDE_SRC)
        for line in src.splitlines():
            if "run_terminal_command(" in line:
                self.assertNotIn("token", line.lower(),
                                 "a token may never ride the visible "
                                 "terminal: " + line.strip())
        # the bridge auth is explicitly env-scoped (pinned literals)
        self.assertIn("[clone] github.com URL -- authenticating with ",
                      src)
        self.assertIn("your stored token (never shown)", src)

    def test_pins_clone_ux_f43(self):
        """F43 regression pins: the native folder picker must never stack
        behind the frameless/topmost clone dialog (the 'clicked Clone and
        nothing happened' bug)."""
        src = _src(IDE_SRC)
        self.assertIn('top.attributes("-topmost", False)', src)
        self.assertIn('parent=top, title="Choose where to clone into"',
                      src)
        self.assertIn("top.lift()", src)
        self.assertIn('"Choose where to clone into..."', src)
        self.assertIn("Git isn't installed on this PC", src)
        self.assertIn("If that repo is PRIVATE", src)
        # F44: the picker can never die silently -- parented call,
        # unparented retry, and any crash narrated into dialog+terminal
        self.assertIn("Folder picker error:", src)
        self.assertIn("traceback.format_exc()", src)
        clone_seg = src.split("def do_clone():")[1].split(
            'clone_btn.configure(state="disabled"', 1)[0]
        self.assertEqual(clone_seg.count("filedialog.askdirectory("), 2)

    def test_pins_f45_auth_resilience(self):
        src = _src(os.path.join(ROOT, "git_bridge.py"))
        self.assertIn("def is_auth_failure(out):", src)
        self.assertIn("def note_bad_token(res, token):", src)
        self.assertIn('"authentication failed"', src)
        self.assertIn('retry["retried_anonymous"] = True', src)
        self.assertIn("cloned anonymously instead.", src)
        ide = _src(IDE_SRC)
        self.assertIn("bridge.note_bad_token(", ide)
        # all three push paths covered
        self.assertGreaterEqual(ide.count("bridge.note_bad_token("), 3)
        self.assertIn('res.get("retried_anonymous")', ide)
        self.assertIn("succeeded WITHOUT the rejected", ide)
        # F47: per-author token resolution wired into push/PR/publish
        self.assertIn("def resolve_token(", src)
        self.assertIn("def _authors_full(", src)
        self.assertGreaterEqual(ide.count("bridge.resolve_token("), 3)
        self.assertIn("set it on the author's row in", ide)
        self.assertIn("self._git_token_or_warn(bridge, remote, author)",
                      ide)


class _FlexHandler(http.server.BaseHTTPRequestHandler):
    """Routes-driven mini GitHub API for F46 runtime tests."""
    routes = {}
    captured = []

    def _go(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        type(self).captured.append({
            "method": self.command, "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": body})
        status, payload = type(self).routes.get(
            (self.command, self.path), (404, {"message": "not found"}))
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._go()

    def do_POST(self):
        self._go()

    def log_message(self, *_args, **_kwargs):
        pass


class BridgePullRequests(unittest.TestCase):
    def setUp(self):
        _FlexHandler.routes = {}
        _FlexHandler.captured = []
        server = http.server.HTTPServer(("127.0.0.1", 0), _FlexHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.api = "http://127.0.0.1:%d" % server.server_address[1]

    def test_repo_info_reads_fork_parent_and_default_branch(self):
        _FlexHandler.routes[("GET", "/repos/kappa16/frontend-madness")] = (
            200, {"full_name": "kappa16/frontend-madness", "fork": True,
                  "parent": {"full_name": "acme/frontend-madness"},
                  "default_branch": "dev"})
        res = git_bridge.repo_info("kappa16", "frontend-madness",
                                   token="ghp_TOK", api_base=self.api)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertTrue(res["fork"])
        self.assertEqual(res["parent_full_name"], "acme/frontend-madness")
        self.assertEqual(res["default_branch"], "dev")
        seen = _FlexHandler.captured[0]
        self.assertEqual(seen["authorization"], "Bearer ghp_TOK")

    def test_repo_info_anonymous_for_public_repos(self):
        _FlexHandler.routes[("GET", "/repos/octocat/Hello-World")] = (
            200, {"full_name": "octocat/Hello-World", "fork": False,
                  "default_branch": "master"})
        res = git_bridge.repo_info("octocat", "Hello-World",
                                   api_base=self.api)
        self.assertTrue(res["ok"])
        self.assertFalse(res["fork"])
        self.assertEqual(res["default_branch"], "master")
        self.assertEqual(res["parent_full_name"], "")
        self.assertIsNone(_FlexHandler.captured[0]["authorization"])

    def test_create_pull_posts_cross_repo_head(self):
        _FlexHandler.routes[("POST", "/repos/acme/frontend-madness/"
                                    "pulls")] = (
            201, {"number": 42,
                  "html_url": "https://github.com/acme/frontend-madness"
                              "/pull/42"})
        res = git_bridge.create_pull(
            "acme/frontend-madness", "main", "kappa16:feature-login",
            "Add login page", body="", token="ghp_TOK", api_base=self.api)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["number"], 42)
        self.assertIn("/pull/42", res["html_url"])
        seen = _FlexHandler.captured[0]
        self.assertEqual(seen["path"],
                         "/repos/acme/frontend-madness/pulls")
        self.assertEqual(seen["authorization"], "Bearer ghp_TOK")
        body = json.loads(seen["body"])
        self.assertEqual(body["head"], "kappa16:feature-login")
        self.assertEqual(body["base"], "main")
        self.assertEqual(body["title"], "Add login page")

    def test_create_pull_422_already_exists_is_plain_text(self):
        _FlexHandler.routes[("POST", "/repos/acme/x/pulls")] = (
            422, {"message": "Validation Failed",
                  "errors": [{"message": "A pull request already exists "
                                         "for acme:main ghp_ECHO"}]})
        res = git_bridge.create_pull("acme/x", "main", "acme:main",
                                     "again", token="ghp_ECHO",
                                     api_base=self.api)
        self.assertFalse(res["ok"])
        self.assertIn("A pull request already exists", res["error"])
        self.assertNotIn("ghp_ECHO", res["error"])   # scrubbed

    def test_create_pull_requires_title_and_token(self):
        self.assertFalse(git_bridge.create_pull("o/r", "main", "b", "  ",
                                                token="t",
                                                api_base=self.api)["ok"])
        res = git_bridge.create_pull("o/r", "main", "b", "t", token="",
                                     api_base=self.api)
        self.assertFalse(res["ok"])
        self.assertIn("token", res["error"])


class AutoAuthorRule(unittest.TestCase):
    """F46: own-account/fork clones commit as the clone owner with NO
    picker (that's the whole 'like VSCode instead of asking' ask)."""

    def setUp(self):
        import tkinter as _tk
        try:
            probe = _tk.Tk()
            probe.destroy()
        except _tk.TclError:
            self.skipTest("no display")

    def test_owned_clone_never_asks_and_foreign_clone_does(self):
        import tkinter as tk  # noqa: F401
        sys.path.insert(0, os.path.join(ROOT, "ide"))
        from ide import IDEApp  # noqa: E402

        app = IDEApp(tempfile.mkdtemp(prefix="f46_"), show_chat=False)
        try:
            app.update()
            calls = []

            def explode_picker(*_args, **_kwargs):
                calls.append(_args)
                return AUTHOR_B   # stands in for a modal pick

            app._ask_github_author = explode_picker
            # clone owner matched -> NO picker, straight-through author
            got = app._pick_repo_author("Commit as which author?",
                                        [AUTHOR_A, AUTHOR_B], AUTHOR_A)
            self.assertEqual(got, AUTHOR_A)
            self.assertEqual(calls, [], "the picker must NOT open for an "
                                        "own-account clone")
            # no owner match -> picker opens (his choice across accounts)
            got = app._pick_repo_author("Commit as which author?",
                                        [AUTHOR_A, AUTHOR_B], None)
            self.assertEqual(got, AUTHOR_B)
            self.assertEqual(len(calls), 1)
        finally:
            try:
                app.destroy()
            except Exception:  # noqa: BLE001
                pass


class CloneDialogRuntime(unittest.TestCase):
    """Drive the REAL clone dialog offscreen: clicking 'Clone' must drop
    the dialog's topmost and parent the native picker to it, and a
    cancelled pick must say so in the dialog (never a dead click)."""

    def setUp(self):
        if not git_bridge.git_available():
            self.skipTest("git not installed")
        import tkinter as _tk
        try:
            probe = _tk.Tk()
            probe.destroy()
        except _tk.TclError:
            self.skipTest("no display")

    def test_clone_click_drops_topmost_and_parents_the_picker(self):
        import tkinter as tk
        from tkinter import filedialog as fd
        sys.path.insert(0, os.path.join(ROOT, "ide"))
        from ide import IDEApp  # noqa: E402

        ws = tempfile.mkdtemp(prefix="f43_clone_")
        app = IDEApp(ws, show_chat=False)
        try:
            app.update()
            app.clone_repository_dialog()
            app.update()
            dialog = None
            for win in app.winfo_children():
                if isinstance(win, tk.Toplevel) and \
                        win.title() == "Clone Repository":
                    dialog = win
                    break
            self.assertIsNotNone(dialog, "clone dialog did not open")

            captured = {}
            real_ask = fd.askdirectory

            def spy(**kwargs):
                captured.update(kwargs)
                parent = kwargs.get("parent")
                captured["topmost_at_call"] = (
                    parent.attributes("-topmost") if parent else None)
                return ""   # user cancelled the folder pick

            # the URL entry must be filled or do_clone exits at its guard
            def fill(widget):
                for child in widget.winfo_children():
                    if isinstance(child, tk.Entry):
                        child.delete(0, "end")
                        child.insert(0, "https://github.com/octocat/"
                                        "Hello-World.git")
                        return True
                    if fill(child):
                        return True
                return False
            self.assertTrue(fill(dialog), "URL entry not found")

            fd.askdirectory = spy
            try:
                clone_btn = None

                def walk(widget):
                    nonlocal clone_btn
                    for child in widget.winfo_children():
                        try:
                            if child.cget("text") == "Clone":
                                clone_btn = child
                                return
                        except Exception:  # noqa: BLE001
                            pass
                        walk(child)
                walk(dialog)
                self.assertIsNotNone(clone_btn, "Clone button not found")
                if hasattr(clone_btn, "invoke"):
                    clone_btn.invoke()      # quiet-skin CTk button
                else:
                    clone_btn.event_generate("<Button-1>", x=3, y=3)
                app.update()
            finally:
                fd.askdirectory = real_ask

            self.assertIs(captured.get("parent"), dialog,
                          "native picker must be parented to the dialog")
            self.assertIn(captured.get("topmost_at_call"), ("0", 0, False),
                          "dialog must drop -topmost before the picker")

            texts = []

            def collect(widget):
                for child in widget.winfo_children():
                    try:
                        texts.append(child.cget("text"))
                    except Exception:  # noqa: BLE001
                        pass
                    collect(child)
            collect(dialog)
            self.assertTrue(
                any("Pick a folder to clone into" in str(t)
                    for t in texts),
                "a cancelled pick must explain itself, not go silent")
            dialog.destroy()
        finally:
            try:
                app.destroy()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    unittest.main()

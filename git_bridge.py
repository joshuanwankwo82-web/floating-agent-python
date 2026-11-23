"""F42 -- Git bridge: the IDE's Source Control actions wired to the
Friday profile the Settings panel owns. THE BRIDGE BETWEEN THE TWO
PANELS: Settings writes, the IDE spends.

The store halves (unchanged, F39/F40/F41):

- Settings > Profile > GitHub authors  -> profile["github_authors"]:
  [{username, email}] public commit identities (NO secrets).
- Settings > Profile > Web passwords   -> profile["credentials"] row
  whose site contains "github.com": its `password` field IS the
  personal ACCESS TOKEN that authenticates HTTPS pushes and the
  create-repo API call. Its `username` is the GitHub login (display).

The operating protocol (user's spec, F41 -> F46, binding):

- The authors list is the user's OWN GitHub accounts. A repo CLONED
  from one of the user's GitHub accounts keeps that author and the
  IDE just uses it -- every commit goes out under exactly that
  identity with no dialog (VSCode-like, no asking), unless the user
  explicitly redirects it.
- Workspaces with no owner match (blank canvas, someone else's repo,
  Publish-to-a-brand-new-repo) still get the picker: the user picks
  WHICH author from any of their accounts.
- "Open Pull Request" (F46): a clone of a FORK aims the PR at the
  fork's upstream parent (head = youruser:branch); any other github
  clone opens a same-repo PR. The PR posts ride the token.
- The token AUTHENTICATES pushes/PRs; the author IDENTIFIES commits.
  The two never mix, and the token is never shown.

Token discipline (also user-pinned): the token travels ONLY as

- a GIT_CONFIG_KEY_0=http.https://github.com/.extraheader /
  GIT_CONFIG_VALUE_0=Authorization: Basic <base64(username:token)> environment
  override for the one subprocess call (never on the argv list,
  never written into .git/config or any remote URL, never echoed
  into the IDE's integrated terminal), and
- an Authorization: Bearer header on the create-repo HTTPS call.

Anything returned here has sanitize() applied to the token, so
surfaces (terminal panel, error dialogs, logs) can print results
raw without review.

Every function is sync + headless (no Tk): the IDE marshals dialogs
and threads; the battery drives this file directly. git's own plumb
answers (rev-parse, remote get-url, branch --show-current) decide
state, never guesses from folder contents.
"""
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"

# github.com owner/repo out of https / ssh / scp-style remotes.
_GH_PATTERNS = (
    re.compile(r"^https?://(?:[^@/]+@)?github\.com/"
               r"(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?/?$",
               re.IGNORECASE),
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/"
               r"(?P<repo>[^/.]+?)(?:\.git)?$", re.IGNORECASE),
    re.compile(r"^ssh://git@github\.com/(?P<owner>[^/]+)/"
               r"(?P<repo>[^/.]+?)(?:\.git)?$", re.IGNORECASE),
)


def git_available():
    """F42: everything here degrades to a friendly error without git."""
    return shutil.which("git") is not None


def _load_profile():
    """Late import: a standalone ide/ checkout may not have the Friday
    core beside it -> the bridge reports 'no profile' instead of
    crashing the IDE at import time."""
    try:
        import user_profile
        return user_profile.load()
    except Exception:  # noqa: BLE001 -- store missing/unreadable
        return None


def authors(profile=None):
    """F42: [{username, email}] commit identities, straight from the
    Settings-profile store (the bridge read). Empty when absent.
    Secrets-free by construction -- per-author tokens (F47) stay in
    _authors_full; prompts/UI lists must never see them."""
    return [{k: row[k] for k in ("username", "email")}
            for row in _authors_full(profile)]


def _authors_full(profile=None):
    """F47: internal readers only -- full author rows INCLUDING the
    per-account token. Never return this to any prompt/UI list."""
    profile = profile if profile is not None else _load_profile()
    if not isinstance(profile, dict):
        return []
    out = []
    for row in profile.get("github_authors") or []:
        if not isinstance(row, dict):
            continue
        username = str(row.get("username", "")).strip()
        email = str(row.get("email", "")).strip()
        if username:
            out.append({"username": username, "email": email,
                        "token": str(row.get("token", "")).strip()})
    return out


def resolve_token(author=None, profile=None):
    """F47: GitHub git/API actions use only the selected account's token
    from its GitHub authors row. Web-password credentials are deliberately
    NOT a fallback. Returns None if the selected row has no token.
    NEVER print/log the return value."""
    profile = profile if profile is not None else _load_profile()
    if not isinstance(profile, dict) or not isinstance(author, dict):
        return None
    wanted = str(author.get("username", "")).lower()
    for row in _authors_full(profile):
        if row["username"].lower() == wanted:
            return row.get("token") or None
    return None


def github_credential(profile=None):
    """F42: the github.com Web-passwords vault row ->
    {"username", "email", "token"} or None. The token is the row's
    password. NEVER print/log the returned token as-is."""
    profile = profile if profile is not None else _load_profile()
    if not isinstance(profile, dict):
        return None
    for row in profile.get("credentials") or []:
        if not isinstance(row, dict):
            continue
        site = str(row.get("site", "")).lower()
        if "github.com" not in site:
            continue
        token = str(row.get("password", ""))
        if token:
            return {"username": str(row.get("username", "")),
                    "email": str(row.get("email", "")),
                    "token": token}
    return None


def parse_github_url(url):
    """F42: {"owner", "repo"} for github.com remotes of any scheme, or
    None for anything else (file:// test remotes included -- those need
    no token and no owner matching)."""
    text = str(url or "").strip()
    for pattern in _GH_PATTERNS:
        match = pattern.match(text)
        if match:
            return {"owner": match.group("owner"),
                    "repo": match.group("repo")}
    return None


def suggested_author(remote_url, author_list):
    """F42/F41 protocol: a repo CLONED from one of the user's accounts
    pre-selects that account's author (owner == username, case-insens);
    anything else -> None and the picker has no pre-selection, so the
    user always steers."""
    parsed = parse_github_url(remote_url)
    if not parsed:
        return None
    owner = parsed["owner"].lower()
    for author in author_list or []:
        if author.get("username", "").lower() == owner:
            return author
    return None


def sanitize(text, *secrets):
    """F42: scrub secrets from anything headed for a screen/log."""
    out = str(text if text is not None else "")
    for secret in secrets:
        if secret:
            out = out.replace(str(secret), "***")
    return out


_AUTH_FAILURE_MARKERS = (   # F45: git/github's ways of saying "bad token"
    "authentication failed", "invalid credentials",
    "could not read username", "returned error: 401",
    "returned error: 403")


def is_auth_failure(out):
    """F45: True when git output says authentication flopped (invalid,
    expired, revoked or under-scoped token) as opposed to network/repo
    problems -- the two need VERY different user guidance."""
    lowered = str(out or "").lower()
    return any(marker in lowered for marker in _AUTH_FAILURE_MARKERS)


def note_bad_token(res, token):
    """F45: after a failed authenticated git op, append actionable
    guidance when -- and only when -- the failure was auth (pointing at
    the Settings row that owns the token)."""
    if (token and isinstance(res, dict) and not res.get("ok")
            and is_auth_failure(res.get("out", ""))):
        res["out"] = (str(res.get("out", ""))
                      + "\nGitHub rejected the token Friday selected. "
                        "Replace the token on the selected account's row "
                        "in Settings > Profile > GitHub authors.")
    return res


def run_git(args, cwd=None, token=None, timeout=120, username=None):
    """F42: the one git runner for all bridge ops. Returns a plain dict
    {ok, code, out}. The TOKEN, when given, rides a github.com-scoped
    http.extraheader passed through ENVIRONMENT (GIT_CONFIG_*) -- never
    argv, never .git/config, never the remote URL -- and the captured
    output is sanitized before anyone can show it. GIT_TERMINAL_PROMPT
    is off so a bad/expired token fails fast instead of hanging the UI
    on an invisible password prompt."""
    if not git_available():
        return {"ok": False, "code": -1,
                "out": "git is not installed or not on PATH."}
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"   # F42: fail, never silently prompt
    encoded_credential = ""
    if token:
        # GitHub's Git-over-HTTPS transport expects PAT credentials via
        # HTTP Basic (username:token). Bearer remains correct for REST API
        # calls, but Git smart-HTTP rejects a Bearer PAT as invalid creds.
        import base64
        user = str(username or "x-access-token")
        encoded_credential = base64.b64encode(
            (user + ":" + str(token)).encode("utf-8")).decode("ascii")
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = "Authorization: Basic " + encoded_credential
    try:
        proc = subprocess.run(
            ["git"] + [str(a) for a in args], cwd=cwd,
            capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1,
                "out": sanitize("git timed out after %ss" % timeout,
                                token, encoded_credential)}
    except Exception as exc:  # noqa: BLE001 -- surfaced to the user
        return {"ok": False, "code": -1,
                "out": sanitize("git could not run: %s" % exc,
                                token, encoded_credential)}
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {"ok": proc.returncode == 0, "code": proc.returncode,
            "out": sanitize(out or "(no output)", token,
                            encoded_credential)}


# ------------------------------------------------------------------ #
#  Repo facts (all read-through-git; empty strings on non-repos)      #
# ------------------------------------------------------------------ #
def is_repo(path):
    res = run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return res["ok"] and res["out"].startswith("true")


def current_branch(path):
    res = run_git(["branch", "--show-current"], cwd=path)
    return res["out"].splitlines()[0].strip() if res["ok"] and res["out"] \
        else ""


def remote_url(path, remote="origin"):
    res = run_git(["remote", "get-url", remote], cwd=path)
    return res["out"].splitlines()[0].strip() if res["ok"] and res["out"] \
        else ""


def has_remote(path, remote="origin"):
    return bool(remote_url(path, remote))


def add_remote(path, url, remote="origin"):
    return run_git(["remote", "add", remote, url], cwd=path)


# ------------------------------------------------------------------ #
#  The four user-facing verbs + create                                 #
# ------------------------------------------------------------------ #
def clone(url, dest, token=None, timeout=600, username=None):
    """F42: `git clone <url> <dest>`; the token (private repos) rides
    the env header, never the visible URL. ok also requires the .git
    landing zone to exist.

    F45 hardening: a stored token that GitHub REJECTS must never kill a
    public clone -- on a clear auth failure we retry anonymously and
    mark the result (`retried_anonymous`), and if that retry also fails
    the user is told the token is bad AND the repo still refused
    (private repo, wrong token)."""
    res = run_git(["clone", "--", str(url), str(dest)],
                  token=token, timeout=timeout, username=username)
    if res["ok"] and not os.path.isdir(os.path.join(dest, ".git")):
        return {"ok": False, "code": res["code"],
                "out": res["out"] + "\n(but the clone folder is missing)"
                }
    if not res["ok"] and token and is_auth_failure(res["out"]):
        # git cleans up the partial destination on failed clones, so a
        # retry into the same path is safe.
        retry = run_git(["clone", "--", str(url), str(dest)],
                        timeout=timeout)
        landed = os.path.isdir(os.path.join(dest, ".git"))
        if retry["ok"] and landed:
            retry["retried_anonymous"] = True          # F45
            retry["out"] = ("GitHub rejected the stored token, so this "
                            "public repo cloned anonymously instead.\n"
                            + retry["out"])
            return retry
        retry["out"] += ("\n(also retried anonymously -- no luck: the "
                         "stored github.com token is invalid/expired AND "
                         "the repo still refuses; refresh the token in "
                         "Settings > Profile > Web passwords)")
        return retry
    return res


def commit_all(path, message, author, timeout=120):
    """F42: stage everything, then commit AS the user-CHOSEN author
    (`git -c user.name=... -c user.email=... commit`): the identity is
    public info and CLI-scoped -- repo config is never mutated. A clean
    tree is a friendly no-op, not an error."""
    message = str(message or "").strip()
    if not message:
        return {"ok": False, "code": -1, "out": "Empty commit message."}
    if not isinstance(author, dict) or not author.get("username"):
        return {"ok": False, "code": -1,
                "out": "No GitHub author chosen -- pick one in the IDE "
                       "or add one in Settings > Profile."}
    staged = run_git(["add", "-A"], cwd=path, timeout=timeout)
    if not staged["ok"]:
        return staged
    res = run_git(["-c", "user.name=%s" % author["username"],
                   "-c", "user.email=%s" % author.get("email", ""),
                   "commit", "-m", message], cwd=path, timeout=timeout)
    lowered = res["out"].lower()
    if not res["ok"] and ("nothing to commit" in lowered or
                          "no changes added" in lowered):
        return {"ok": True, "noop": True, "code": 0,
                "out": "Nothing to commit -- the working tree is clean."}
    return res


def push(path, token=None, remote="origin", branch=None, timeout=300,
         username=None):
    """F42: push the current branch, `-u` so the clone-linked upstream
    is set for later pulls. AUTHENTICATED by the token; the commit
    identity was already burned in at commit time by the chosen
    author."""
    branch = branch or current_branch(path)
    if not branch:
        return {"ok": False, "code": -1,
                "out": "Detached HEAD -- check out a branch first."}
    return run_git(["push", "-u", remote, branch], cwd=path,
                   token=token, timeout=timeout, username=username)


def pull(path, token=None, remote="origin", branch=None, timeout=300,
         username=None):
    """F42: fast-forward-only pull. A merging pull could demand a merge
    commit identity we never configured; conflicts surface as an honest
    error the user resolves instead of a surprise merge."""
    branch = branch or current_branch(path)
    if not branch:
        return {"ok": False, "code": -1,
                "out": "Detached HEAD -- check out a branch first."}
    return run_git(["pull", "--ff-only", remote, branch],   # F42: ff-only
                   cwd=path, token=token, timeout=timeout,
                   username=username)


def create_repo(name, token, private=True, api_base=None, timeout=20):
    """F42: POST /user/repos on GitHub with Bearer auth (the Web-
    passwords token). `api_base` is injectable ONLY so the battery can
    run a local stub -- real calls always hit GITHUB_API over https.
    Errors are sanitized before they reach any panel."""
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "error": "Empty repository name."}
    if not token:
        return {"ok": False,
                "error": "No github.com token in Web passwords (Settings "
                         "> Profile) -- create-repo needs it."}
    api_base = api_base or GITHUB_API
    body = json.dumps({"name": name, "private": bool(private),
                       "auto_init": False}).encode("utf-8")
    request = urllib.request.Request(
        api_base.rstrip("/") + "/user/repos", data=body, method="POST",
        headers={"Authorization": "Bearer " + str(token),
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "Friday-IDE"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8",
                                                    errors="replace"))
        return {"ok": True,
                "clone_url": str(payload.get("clone_url", "")),
                "html_url": str(payload.get("html_url", "")),
                "full_name": str(payload.get("full_name", ""))}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        message = raw[:300]
        try:
            message = json.loads(raw).get("message", message)
        except Exception:  # noqa: BLE001 -- non-JSON error page
            pass
        return {"ok": False, "code": exc.code,
                "error": sanitize("GitHub said %s: %s" % (exc.code,
                                                          message),
                                  token)}
    except Exception as exc:  # noqa: BLE001 -- offline, DNS, timeout
        return {"ok": False,
                "error": sanitize("create-repo failed: %s" % exc, token)}


# ------------------------------------------------------------------ #
#  Pull requests (F46)                                                 #
# ------------------------------------------------------------------ #
def _api_headers(token):
    headers = {"Accept": "application/vnd.github+json",
               "Content-Type": "application/json",
               "User-Agent": "Friday-IDE"}
    if token:
        headers["Authorization"] = "Bearer " + str(token)
    return headers


def repo_info(owner, repo, token=None, api_base=None, timeout=15):
    """F46: GET /repos/{owner}/{repo} -> {ok, full_name, fork,
    parent_full_name, default_branch}. Anonymous reads work for public
    repos; a stored token rides Bearer when present (private + forks)."""
    api_base = (api_base or GITHUB_API).rstrip("/")
    request = urllib.request.Request(
        api_base + "/repos/%s/%s" % (owner, repo),
        headers=_api_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8",
                                                    errors="replace"))
        parent = payload.get("parent") or {}
        return {"ok": True,
                "full_name": str(payload.get("full_name",
                                             "%s/%s" % (owner, repo))),
                "fork": bool(payload.get("fork")),
                "parent_full_name": str(parent.get("full_name", "")),
                "default_branch": str(payload.get("default_branch")
                                      or "main")}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "code": exc.code,
                "error": sanitize("GitHub said %s: %s"
                                  % (exc.code, raw[:300]), token)}
    except Exception as exc:  # noqa: BLE001 -- offline/DNS/timeout
        return {"ok": False,
                "error": sanitize("repo lookup failed: %s" % exc, token)}


def create_pull(base_repo, base_branch, head, title, body="",
                token=None, api_base=None, timeout=20):
    """F46: POST /repos/{base_repo}/pulls. `head` is the branch for a
    same-repo PR, or 'username:branch' for the fork -> upstream case --
    exactly the shape VSCode/GitHub expect across repositories. A 422
    'already exists' is surfaced as plain text, not a crash."""
    title = str(title or "").strip()
    if not title:
        return {"ok": False, "error": "A pull request needs a title."}
    if not token:
        return {"ok": False,
                "error": "No github.com token in Web passwords (Settings "
                         "> Profile) -- opening a PR needs it."}
    api_base = (api_base or GITHUB_API).rstrip("/")
    payload = {"title": title, "head": str(head),
               "base": str(base_branch or "main"), "body": str(body or "")}
    request = urllib.request.Request(
        api_base + "/repos/%s/pulls" % base_repo,
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers=_api_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8",
                                                    errors="replace"))
        return {"ok": True, "number": payload.get("number"),
                "html_url": str(payload.get("html_url", ""))}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        message = raw[:400]
        try:
            parsed = json.loads(raw)
            if parsed.get("errors"):
                message = "; ".join(str(e.get("message", e))
                                    for e in parsed["errors"])
            elif parsed.get("message"):
                message = str(parsed["message"])
        except Exception:  # noqa: BLE001 -- non-JSON error page
            pass
        return {"ok": False, "code": exc.code,
                "error": sanitize("GitHub said %s: %s" % (exc.code,
                                                          message),
                                  token)}
    except Exception as exc:  # noqa: BLE001 -- offline, DNS, timeout
        return {"ok": False,
                "error": sanitize("create-PR failed: %s" % exc, token)}

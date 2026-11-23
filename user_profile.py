"""Friday's user profile: WHO the user is, as the user described themself.

Stored in one small JSON blob, Fernet-encrypted at rest like the
settings store (same memory folder "settings", sibling file), never in
the project folder. The Settings panel edits it; Friday reads it:

- `prompt_summary(profile)` is text injected into every system prompt,
  so development answers are styled in the user's own context -- their
  name, their craft, their links. Credentials are NEVER part of it.
- `display_name`/`avatar` feed the chat header presence badge, and
  Friday addresses the user by name (voice included, since replies are
  spoken).

Field map (and the decisions above them, all user-picked):
- name (real), dev_names (comma list they also answer to), bio
  (their description of themself), photo (local path OR URL -- both
  work, preview degrades gracefully).
- interests / goals: what they care about and what they're working
  toward -- the personalization half of "know them while helping",
  user-added (F37 discussion: profile direction staged).
- links: website, github, twitter, linkedin, telegram, portfolio.
- connected_apps: freeform names (plain list).
- credentials: [{site, email, username, password}] -- encrypted at rest
  the same Fernet blob, shown ONLY as masks in the UI, never injected
  into any prompt.

  The operating model for these (user spec, F39):
  - "Log in to <site> I've added" -> Friday drives the login with the
    stored username/password (aloud-confirmed first, always).
  - "Sign up" (or a task that needs an account) -> Friday uses the
    Email section's address and a generated password suggestion
    (suggest_password), and ONCE the account exists, auto-upserts the
    new credential HERE via add_web_credential() -- same encrypted
    blob, masked-only in the UI, visible to and removable by ONLY the
    user, exactly like the Keys & Connect list's rows.
- github_authors: [{username, email, token?}] -- the user's OWN
  GitHub accounts as commit identities (F41, F46-refined, F47 tokens).
  A repo CLONED from one of these accounts keeps that author
  FOREVER: the IDE commits as it, automatically, no asking. Workspaces with NO owner match (blank
  canvas, third-party repos, Publish-to-a-new-repo) still get the
  picker across the accounts. The optional `token` field is THAT
  account's own access key: masked-only in the UI, never prompted,
  never logged. A bare github.com row in Web passwords remains the
  fallback when an author row carries no token. (The ACCESS TOKEN
  never reaches prompts; identity and secret stay unmixed.)
- emails: [{address, password}] -- one or MORE mailboxes Friday can
  work for the user (F40: the provider field was dropped -- the
  address's own domain decides routing). With 2+ addresses and a
  task needing mail/signup, Friday asks WHICH email to use
  first. Setup-only for now (staged E1-E3); passwords masked-
  only in the UI and never prompted.
"""
import os

import memory

_PROFILE_FILENAME = "user_profile.json.enc"

_LINK_KEYS = ("website", "github", "twitter", "linkedin", "telegram",
              "portfolio")


def _profile_path():
    folder = memory._ensure_folder("settings")
    return os.path.join(folder, _PROFILE_FILENAME)


def default_profile():
    return {
        "name": "",
        "dev_names": "",
        "bio": "",
        "photo": "",
        "interests": "",
        "goals": "",
        "links": {key: "" for key in _LINK_KEYS},
        "connected_apps": [],
        "credentials": [],
        "emails": [],
        "github_authors": [],
    }


def _normalize(data):
    """Load-time cleanup: unknown keys dropped, missing keys defaulted,
    credential rows coerced to {site, username, password} strings only.
    Storage can be hand-edited or from older Friday builds; what reaches
    the UI and prompts is always exactly the documented shape."""
    out = default_profile()
    if not isinstance(data, dict):
        return out
    for key in ("name", "dev_names", "bio", "photo", "interests",
                "goals"):
        value = data.get(key, "")
        out[key] = value if isinstance(value, str) else str(value)
    links = data.get("links", {})
    if isinstance(links, dict):
        for key in _LINK_KEYS:
            value = links.get(key, "")
            out["links"][key] = value if isinstance(value, str) \
                else str(value)
    apps = data.get("connected_apps", [])
    if isinstance(apps, list):
        out["connected_apps"] = [a for a in apps if isinstance(a, str)
                                 and a.strip()]
    creds = data.get("credentials", [])
    if isinstance(creds, list):
        clean = []
        for row in creds:
            if not isinstance(row, dict):
                continue
            site = str(row.get("site", "")).strip()
            username = str(row.get("username", ""))
            email = str(row.get("email", ""))          # F40 format
            password = str(row.get("password", ""))
            if site:
                clean.append({"site": site, "email": email,
                              "username": username, "password": password})
        out["credentials"] = clean
    # canonical: list of {address, password}. A legacy (<=F39) single
    # dict {address, provider, app_password} migrates in place; the
    # provider is DROPPED -- the address's own domain decides routing.
    emails = data.get("emails")
    migrated = []
    if isinstance(emails, list):
        for row in emails:
            if not isinstance(row, dict):
                continue
            address = str(row.get("address", "")).strip()
            password = str(row.get("password", ""))
            if address:
                migrated.append({"address": address,
                                 "password": password})
    legacy = data.get("email")
    if isinstance(legacy, dict):
        address = str(legacy.get("address", "")).strip()
        password = str(legacy.get("app_password",
                                  legacy.get("password", "")))
        if address:
            migrated.append({"address": address, "password": password})
    out["emails"] = migrated
    authors = data.get("github_authors", [])
    if isinstance(authors, list):
        out["github_authors"] = [
            {"username": str(a.get("username", "")).strip(),
             "email": str(a.get("email", "")).strip(),
             "token": str(a.get("token", "")).strip()}   # F47: no stray paste whitespace
            for a in authors
            if isinstance(a, dict) and str(a.get("username", "")).strip()]
    return out


def load():
    """Encrypted-at-rest read; any corruption or old format returns
    defaults instead of breaking the Settings panel (same posture as
    settings_store's own error handling)."""
    try:
        data = memory._read_encrypted(_profile_path())
    except Exception:  # noqa: BLE001 -- missing file: first run
        return default_profile()
    return _normalize(data)


def save(profile):
    """Encrypted-at-rest write of the normalized shape only -- the UI
    can never accidentally ship widgets or PIL objects to disk."""
    memory._write_encrypted(_profile_path(), _normalize(profile))
    return True


def add_web_credential(site, username, password, email=""):
    """Auto-capture hook for browser sign-ups/logins (F39): upsert one
    web credential into the encrypted vault. Same site => the stored
    username/password are REPLACED (signing up again on the same site
    with the final password). Returns the saved profile. Encrypted at
    rest via save()'s normal path; nothing here ever logs or returns
    the plaintext beyond the caller who supplied it."""
    site = str(site or "").strip()
    if not site:
        raise ValueError("web credentials need a site")
    profile = load()
    live = [c for c in profile["credentials"]
            if c.get("site", "").lower() != site.lower()]
    live.append({"site": site, "email": str(email or ""),
                 "username": str(username or ""),
                 "password": str(password or "")})
    profile["credentials"] = live
    save(profile)
    return profile


def remove_web_credential(site):
    """User-facing twin of the vault's X button; safe no-op on absent
    sites."""
    site = str(site or "").strip().lower()
    profile = load()
    live = [c for c in profile["credentials"]
            if c.get("site", "").lower() != site]
    profile["credentials"] = live
    save(profile)
    return profile


def suggest_password(length=16):
    """A generated signup password suggestion (user spec, F39): secure
    bits from os.urandom via `secrets`, guaranteed to contain at least
    one lowercase, one uppercase, one digit and one symbol so provider
    password rules pass."""
    import secrets
    import string
    length = max(8, int(length))
    pools = [string.ascii_lowercase, string.ascii_uppercase,
             string.digits, "!@#$%^&*()-_=+"]
    alnum_syms = "".join(pools)
    out = [secrets.choice(pool) for pool in pools]
    out += [secrets.choice(alnum_syms)
            for _ in range(length - len(out))]
    secrets.SystemRandom().shuffle(out)
    return "".join(out)


def display_name(profile=None):
    """What Friday calls the user. Empty if never set."""
    profile = profile if profile is not None else load()
    return (profile.get("name") or "").strip()


def mask(_password):
    """Credentials are displayed ONLY masked. Fixed length on purpose:
    the mask must not leak the password's length."""
    return "\u2022" * 10


def prompt_summary(profile=None, max_chars=600):
    """Short system-prompt block: everything that helps Friday style its
    answers in the user's context, and precisely one thing more --
    nothing sensitive. Credentials are structurally absent. Empty when
    the profile is empty (no noise injected)."""
    profile = profile if profile is not None else load()
    name = (profile.get("name") or "").strip()
    dev_names = (profile.get("dev_names") or "").strip()
    bio = (profile.get("bio") or "").strip()
    interests = (profile.get("interests") or "").strip()
    goals = (profile.get("goals") or "").strip()
    links = {k: v.strip() for k, v in (profile.get("links") or {}).items()
             if isinstance(v, str) and v.strip()}
    profile = _normalize(profile)   # legacy shapes migrate on the fly
    addrs = [e["address"] for e in profile.get("emails", [])]
    # mailbox PASSWORDS are structurally absent: they never reach prompts
    if not (name or dev_names or bio or interests or goals or links
            or addrs):
        return ""
    lines = ["User profile (their own words; know them while helping "
             "them -- and style code/answers in their voice):"]
    if name:
        lines.append("- Name the user answers to: " + name + ".")
    if dev_names:
        lines.append("- Also known as / dev names: " + dev_names + ".")
    if bio:
        lines.append("- About them: " + bio + ".")
    if interests:
        lines.append("- What they care about: " + interests + ".")
    if goals:
        lines.append("- What they're working toward: " + goals + ".")
    if links:
        formatted = ", ".join(f"{key}={value}" for key, value in
                              links.items())
        lines.append("- Their links: " + formatted + ".")
    if addrs:
        suffix = "s" if len(addrs) != 1 else ""
        lines.append("- Their email" + suffix + ": "
                     + ", ".join(addrs) + ".")
    authors = [a["username"] for a in profile.get("github_authors", [])]
    if authors:
        lines.append("- Their GitHub commit identities (chose at push "
                     "time): " + ", ".join(authors) + ".")
    text = "\n".join(lines)
    return text[:max_chars]


def avatar_photoimage(profile=None, size=48, master=None):
    """A tk.PhotoImage of the user's photo, circular-cropped, or None.

    Sources, in order: local file path (PIL opens), or an http(s) URL
    (requests, 4 s timeout). ANY failure -- missing PIL, bad path,
    offline, corrupted bytes -- returns None so callers show the
    silhouette placeholder instead of crashing. The returned image must
    be held by the caller (Tk garbage-collects unreferenced images).
    `master` MUST be a widget in the SAME interpreter as where the image
    will be shown (Friday keeps panels in a second Tk interpreter --
    defaulting to the process-wide default root detonates there)."""
    profile = profile if profile is not None else load()
    source = (profile.get("photo") or "").strip()
    if not source:
        return None
    try:
        from PIL import Image, ImageDraw, ImageOps  # noqa: F401
        import io

        if source.lower().startswith(("http://", "https://")):
            import requests
            data = requests.get(source, timeout=4).content
            img = Image.open(io.BytesIO(data))
        else:
            img = Image.open(os.path.expanduser(source))
        img = img.convert("RGBA")
        img = ImageOps.fit(img, (size, size))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
        img.putalpha(mask)
        import tkinter as _tk  # noqa: F401
        from PIL import ImageTk
        return ImageTk.PhotoImage(img, master=master)
    except Exception:  # noqa: BLE001 -- everything degrades to None
        return None

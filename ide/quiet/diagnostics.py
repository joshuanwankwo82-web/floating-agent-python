"""Real diagnostics for the Problems tab of the quiet IDE.

Two layers, both genuine, combined in `analyze()`:

- **syntax (always available):** the file is compiled; a SyntaxError
  becomes an ``Error`` finding with the exact line/col CPython reports.
- **pyflakes (if importable) or ruff (if a `ruff` binary is found):**
  warnings for the problems those linters prove (unused imports/
  variables, undefined names, redefinitions...). Layer skipped cleanly
  -- neither faked nor guessed -- when neither backend is installed, and
  the returned `BackendInfo` says which one ran so the panel can label
  results honestly.

All functions are pure/synchronous; the GUI keeps its own debounce and
calls them off the EDT (they only touch files under the workspace).
"""

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    file: str            # absolute path
    line: int
    col: int
    severity: str        # "error" | "warning"
    code: str            # short machine code, e.g. "syntax", "F401"
    message: str
    source: str          # "python" | "pyflakes" | "ruff"


@dataclass(frozen=True)
class BackendInfo:
    syntax: str          # always "python (built-in compile)"
    linter: str          # "pyflakes" | "ruff" | "" (none available)

    @property
    def label(self):
        if self.linter:
            return f"python + {self.linter}"
        return "python (syntax only)"


_RUFF_SEVERITY = {
    "F": "warning",   # pyflakes-equivalents
    "E": "warning",
    "W": "warning",
    "I": "warning",
    "N": "warning",
    "D": "info",
    "UP": "info",
    "SIM": "info",
    "B": "info",
}


def _syntax_findings(path, text):
    try:
        compile(text, path, "exec")
    except SyntaxError as exc:
        line = exc.lineno or 1
        col = (exc.offset - 1) if exc.offset else 0
        return [Finding(os.path.abspath(path), line, max(0, col),
                        "error", "syntax", exc.msg.strip() or "syntax error",
                        "python")]
    except (ValueError, OverflowError):
        return [Finding(os.path.abspath(path), 1, 0, "error", "compile",
                        "file could not be compiled", "python")]
    return []


_PYFLAKES_LINE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<msg>.+)$")


def _pyflakes_findings(path):
    """Real pyflakes run against the file itself (not its on-disk copy --
    pyflakes reads the file itself, so callers save first)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pyflakes", path],
            capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001
        return None
    out = []
    for line in proc.stdout.splitlines():
        m = _PYFLAKES_LINE.match(line.strip())
        if not m:
            continue
        msg = m.group("msg").strip()
        code = msg.split(":", 1)[0].strip()
        code = code if code.startswith(("F", "E", "W")) else "F999"
        out.append(Finding(os.path.abspath(path), int(m.group("line")),
                           int(m.group("col") or 1) - 1,
                           "warning", code, msg, "pyflakes"))
    return out


_RUFF_LINE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s*(?P<code>[A-Z]+\d+[A-Z]?)\s*(?P<msg>.+)$")


def _ruff_findings(path, ruff_cmd):
    try:
        proc = subprocess.run(
            [ruff_cmd, "check", "--output-format", "concise",
             "--no-cache", path],
            capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001
        return None
    out = []
    for line in proc.stdout.splitlines():
        m = _RUFF_LINE.match(line.strip())
        if not m:
            continue
        code = m.group("code")
        out.append(Finding(os.path.abspath(path), int(m.group("line")),
                           int(m.group("col")) - 1,
                           _RUFF_SEVERITY.get(code[:1], "warning"),
                           code, m.group("msg").strip(), "ruff"))
    return out


def _have_module(mod):
    try:
        __import__(mod)
        return True
    except Exception:  # noqa: BLE001
        return False


def linter_backend():
    """Returns (kind, exe_or_none): kind is "pyflakes" | "ruff" | ""."""
    if _have_module("pyflakes"):
        return "pyflakes", None
    exe = shutil.which("ruff")
    if exe:
        return "ruff", exe
    return "", None


def analyze_file(path):
    """All real findings for one on-disk .py file. Never raises; returns
    (findings, backend_label)."""
    abs_path = os.path.abspath(path)
    findings = []
    backend = BackendInfo("python (built-in compile)", "")
    try:
        with open(abs_path, "r", encoding="utf-8",
                  errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ([Finding(abs_path, 1, 0, "error", "read",
                         "file could not be read", "python")], backend)
    findings += _syntax_findings(abs_path, text)
    kind, extra = linter_backend()
    if kind == "pyflakes":
        pf = _pyflakes_findings(abs_path)
        if pf is not None:
            findings += pf
            backend = BackendInfo("python (built-in compile)", "pyflakes")
    elif kind == "ruff":
        rf = _ruff_findings(abs_path, extra)
        if rf is not None:
            findings += rf
            backend = BackendInfo("python (built-in compile)", "ruff")
    return findings, backend


def analyze_text(path, text):
    """Syntax-layer findings for an in-memory buffer (what the editor
    shows *right now*, unsaved edits included). Same finding shape."""
    return _syntax_findings(os.path.abspath(path), text), BackendInfo(
        "python (built-in compile)", "")


def python_files(root, limit=400):
    """Workspace .py files to analyze, capped sanely for huge trees."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith((".", "__pycache__"))
                       and d not in ("node_modules", "venv", ".venv")]
        for name in filenames:
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
                if len(out) >= limit:
                    return out
    return out


def analyze_workspace(root, limit=400):
    """{file: [Finding, ...]} for every workspace .py file; files with no
    findings are omitted. Plus the label of whatever backend actually ran."""
    aggregate = {}
    label = "python (built-in compile)"
    for path in python_files(root, limit):
        findings, backend = analyze_file(path)
        if backend.linter:
            label = backend.label
        if findings:
            aggregate[path] = findings
    return aggregate, label

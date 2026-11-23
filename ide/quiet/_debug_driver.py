#!/usr/bin/env python3
"""Python debug-session driver for the Friday IDE (stdio/TCP stdlib only).

Launched as a subprocess by ide/quiet/debug_session.py:

    python _debug_driver.py <script.py>

Protocol (JSON lines, one object per line, over 127.0.0.1):

  startup:  prints ``PORT <n>`` on stdout *before* anything else, listens
            for exactly one controller connection.
  controller -> driver (commands):
      {"cmd": "break", "file": <abs path>, "line": <int>, "set": true|false}
      {"cmd": "run"}                              start executing the script
      {"cmd": "continue" | "next" | "step" | "out"}
      {"cmd": "eval", "id": <n>, "expr": <str>}    evaluate in paused frame
      {"cmd": "vars"}                              re-send frame locals
      {"cmd": "quit"}                              terminate the session
  driver -> controller (events, spontaneous):
      {"event": "ready"}
      {"event": "output", "stream": "stdout"|"stderr", "text": <str>}
      {"event": "stopped", "reason": "entry|breakpoint|step|exception",
         "file": <str>, "line": <int>, "func": <str>,
         "stack": [{"file":..., "line":..., "func":...}, ...],
         "vars": [{"name":..., "value": <repr, truncated>}, ...]}
      {"event": "terminated", "code": <int>}
    and replies: {"reply": <cmd>, "ok": true, "error": <str only on failure>}
                 {"reply": "eval", "id": <n>, "ok": true, "value": <str>,
                  "error": <str>}

Design notes:
- The traced program runs on the MAIN thread via bdb.Bdb.run() -- the
  socket reader lives on a daemon thread feeding a Queue. A pause
  (user_line/user_exception) drains that queue until a resume directive
  arrives, evaluating eval/vars/break commands right there in the live
  frame. This is the classic in-process debugger loop, no pydevd.
- The traced program's sys.stdout/sys.stderr are proxied to output
  events *while traced*; driver noise itself never enters the console.
- No third-party deps: runs under any stock Python that can run the
  target script (including the IDE's own interpreter).
"""

import bdb
import json
import os
import queue
import socket
import sys
import threading
import traceback


_REASON_PAUSE = {"continue", "next", "step", "out"}
_VALUE_LIMIT = 240


def _brief(value, limit=_VALUE_LIMIT):
    try:
        s = repr(value)
    except Exception:  # noqa: BLE001
        s = f"<{type(value).__name__} object>"
    return s if len(s) <= limit else s[: limit - 3] + "..."


class _StreamProxy:
    """Routes the traced program's stdout/stderr to output events."""

    def __init__(self, real, send, stream):
        self._real = real
        self._send = send
        self._stream = stream

    def write(self, text):
        if text:
            try:
                self._send({"event": "output", "stream": self._stream,
                            "text": text})
            except Exception:  # noqa: BLE001 -- reporting must not kill a run
                pass
        if self._real is not None:
            try:
                self._real.write(text)
            except Exception:  # noqa: BLE001
                pass

    def flush(self):
        if self._real is not None:
            try:
                self._real.flush()
            except Exception:  # noqa: BLE001
                pass


class _CommandReader(threading.Thread):
    """Reads JSON-line commands from the controller into a queue."""

    def __init__(self, conn):
        super().__init__(daemon=True, name="friday-debug-reader")
        self.conn = conn
        self.cmds = queue.Queue()
        self.dead = threading.Event()
        self.error = None

    def run(self):
        buf = b""
        try:
            while True:
                chunk = self.conn.recv(65536)
                if not chunk:
                    self.dead.set()
                    self.cmds.put(None)
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self.cmds.put(json.loads(line.decode("utf-8")))
                    except ValueError:
                        self.cmds.put({"cmd": "__malformed__"})
        except OSError as exc:
            self.error = exc
            self.dead.set()
            self.cmds.put(None)


class DebugDriver(bdb.Bdb):
    """bed-based debugger core; one object drives the whole session."""

    def __init__(self, target, cmds, send):
        super().__init__()
        self.target = os.path.abspath(target)
        self.cmds = cmds
        self.send = send
        self._idle = threading.Event()
        self._idle.set()
        self.code = None

    # -- driving the interpreter -------------------------------------------
    def run_target(self):
        # Handshakes/breakpoint uploads arrive before {"cmd": "run"}.
        while True:
            cmd = self.cmds.get()
            if cmd is None or cmd.get("cmd") == "run":
                break
            self._handle_cmd(cmd)

        with open(self.target, "rb") as fh:
            code_obj = compile(fh.read(), self.target, "exec")

        sys.path.insert(0, os.path.dirname(self.target))
        sys.argv = [self.target] + sys.argv[2:]
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout = _StreamProxy(real_out, self.send, "stdout")
        sys.stderr = _StreamProxy(real_err, self.send, "stderr")
        rc = 0
        try:
            # Run in a dedicated __main__-style namespace -- bdb.run() with
            # no globals would share the DRIVER's own __main__ dict instead
            # (wrong __file__/argv for the user's program).
            globs = {
                "__name__": "__main__",
                "__file__": self.target,
                "__builtins__": __builtins__,
                "__package__": None,
                "__cached__": None,
                "__spec__": None,
            }
            self.run(code_obj, globs, globs)
        except bdb.BdbQuit:
            pass
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
        except BaseException:  # noqa: BLE001 -- propagate the trace as events
            rc = 1
            self.send({"event": "output", "stream": "stderr",
                       "text": traceback.format_exc()})
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        self.send({"event": "terminated", "code": rc})

    # -- bdb hooks -----------------------------------------------------------
    def user_call(self, frame, args):
        pass  # call events are noise for a line-stepper; bdb only needs stop checks

    def user_line(self, frame):
        # bdb only calls us when something actually stopped (its own
        # stop_here/break_here checks); figure out which and suspend.
        reason = "breakpoint" if self._breaking(frame) else "step"
        self._suspend(frame, reason)

    def user_exception(self, frame, exc_info):
        self._suspend(frame, "exception", exc_info=exc_info)

    def user_return(self, frame, return_value):
        pass

    def _breaking(self, frame):
        # bdb's own predicate for "this line matched a breakpoint" --
        # the same one its line dispatch checks before calling user_line.
        try:
            return bool(self.break_here(frame))
        except Exception:  # noqa: BLE001
            return False

    def _suspend(self, frame, reason, exc_info=None):
        self._idle.clear()
        # Stack of the TRACED program only: everything above (older than)
        # a frame from this driver module or bdb itself is plumbing, not
        # user code -- cut it off the top.
        stack = []
        f = frame
        while f is not None:
            stack.append({"file": f.f_code.co_filename,
                          "line": f.f_lineno,
                          "func": f.f_code.co_name})
            f = f.f_back
        stack.reverse()
        cut = 0
        for ix, entry in enumerate(stack):
            if os.path.basename(entry["file"]) in (
                    "_debug_driver.py", "bdb.py", "pdb.py"):
                cut = ix + 1
        stack = stack[cut:]

        frame_locals = {k: v for k, v in frame.f_locals.items()}
        vars_payload = [
            {"name": name, "value": _brief(value)}
            for name, value in sorted(frame_locals.items())
            if not name.startswith("__")
        ][:64]

        msg = {"event": "stopped", "reason": reason,
               "file": frame.f_code.co_filename,
               "line": frame.f_lineno,
               "func": frame.f_code.co_name,
               "stack": stack, "vars": vars_payload}
        if exc_info is not None:
            exc_type, exc_val, _tb = exc_info
            msg["exception"] = f"{exc_type.__name__}: {exc_val}"
        self.send(msg)

        # Pause until the controller tells us to resume -- meanwhile answer
        # eval/vars/break requests right here, against the live frame.
        try:
            while True:
                cmd = self.cmds.get()
                resume = self._handle_cmd(cmd, frame=frame)
                if resume:
                    break
        finally:
            self._idle.set()

    # -- command handling -----------------------------------------------------
    def _handle_cmd(self, cmd, frame=None):
        """Process one controller command. Returns True when it tells the
        suspended frame to resume execution (or the session to end)."""
        if cmd is None:
            # controller disconnected mid-pause: keep the target running
            return frame is not None
        name = cmd.get("cmd")
        try:
            if name == "continue":
                self.set_continue()
                return True
            if name == "next" and frame is not None:
                self.set_next(frame)
                return True
            if name == "step":
                self.set_step()
                return True
            if name == "out" and frame is not None:
                self.set_return(frame)
                return True
            if name == "quit":
                self.send({"reply": "quit", "ok": True})
                self.set_quit()
                return True
            if name == "break":
                self._handle_break(cmd)
                return False
            if name == "eval" and frame is not None:
                self._handle_eval(cmd, frame)
                return False
            if name == "vars" and frame is not None:
                self._suspend_vars(frame)
                return False
            self.send({"reply": name or "unknown", "ok": False,
                       "error": "unknown or inapplicable command"})
            return False
        except Exception as exc:  # noqa: BLE001
            self.send({"reply": name or "unknown", "ok": False,
                       "error": f"{type(exc).__name__}: {exc}"})
            return False

    def _handle_break(self, cmd):
        fname = cmd.get("file") or ""
        line = int(cmd.get("line") or 0)
        try:
            if cmd.get("set"):
                err = self.set_break(fname, line)
            else:
                err = self.clear_break(fname, line)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
        if err:
            self.send({"reply": "break", "ok": False, "error": err})
        else:
            self.send({"reply": "break", "ok": True,
                       "file": fname, "line": line,
                       "set": bool(cmd.get("set"))})

    def _handle_eval(self, cmd, frame):
        expr = str(cmd.get("expr") or "")
        rid = cmd.get("id")
        captured = []
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout = _CollectingSink(captured)
        sys.stderr = _CollectingSink(captured)
        try:
            try:
                value = eval(expr, frame.f_globals, frame.f_locals)  # noqa: S307
                self.send({"reply": "eval", "id": rid, "ok": True,
                           "value": _brief(value),
                           "printed": "".join(captured)})
            except SyntaxError:
                # statement, not expression -- exec it in the frame
                exec(compile(expr, "<console>", "exec"),  # noqa: S102
                     frame.f_globals, frame.f_locals)
                self._suspend_vars(frame)
                self.send({"reply": "eval", "id": rid, "ok": True,
                           "value": None,
                           "printed": "".join(captured)})
        except BaseException as exc:  # noqa: BLE001
            self.send({"reply": "eval", "id": rid, "ok": False,
                       "error": f"{type(exc).__name__}: {exc}",
                       "printed": "".join(captured)})
        finally:
            sys.stdout, sys.stderr = real_out, real_err

    def _suspend_vars(self, frame):
        vars_payload = [
            {"name": name, "value": _brief(value)}
            for name, value in sorted(frame.f_locals.items())
            if not name.startswith("__")
        ][:64]
        self.send({"event": "vars", "vars": vars_payload})


class _CollectingSink:
    def __init__(self, buf):
        self._buf = buf

    def write(self, text):
        self._buf.append(str(text))

    def flush(self):
        pass


def serve(target):
    """Bind an ephemeral localhost port, announce it, take one controller
    connection, then drive the session until it ends."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    # Hand-rolled write: the stdout proxy below must not swallow this.
    sys.stdout.write(f"PORT {port}\n")
    sys.stdout.flush()
    conn, _addr = listener.accept()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.close()

    send_lock = threading.Lock()

    def send(obj):
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with send_lock:
                conn.sendall(line)
        except OSError:
            pass

    reader = _CommandReader(conn)
    reader.start()
    send({"event": "ready"})

    driver = DebugDriver(target, reader.cmds, send)
    try:
        driver.run_target()
    finally:
        try:
            conn.close()
        except OSError:
            pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stderr.write("usage: _debug_driver.py <script.py>\n")
        sys.exit(2)
    serve(sys.argv[1])

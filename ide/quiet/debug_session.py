"""Controller for one live debug session (quiet IDE).

Spawns ide/quiet/_debug_driver.py as a subprocess, connects as its single
controller, and presents a small facade the UI layer uses:

    sess = DebugSession(on_event=my_callback)
    sess.start("/abs/path/game.py")
    sess.set_breakpoint(path, line, True)
    sess.continue_(); sess.step_over(); sess.step_into(); sess.step_out()
    sess.evaluate("player.score")            # while paused
    sess.stop()

Every driver event/reply is delivered to `on_event(kind, payload)` from the
internal reader thread -- the UI must marshal those onto the Tk main thread
itself (Panel does exactly that with its existing queue+after pump).

Properties are readable from any thread: `state` is one of
"idle" (never started / terminated), "running" (target executing),
"paused" (stopped at a frame), "ended" (terminated normally/with error),
"error" (driver itself failed).
"""

import json
import os
import queue
import socket
import subprocess
import sys
import threading

_DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_debug_driver.py")

_STATES = ("idle", "running", "paused", "ended", "error")


class DebugSession:
    def __init__(self, on_event=None, python=None, driver_path=None,
                 spawn_fn=None, connect_timeout=10):
        self.on_event = on_event or (lambda kind, payload: None)
        self.python = python or sys.executable
        self.driver_path = driver_path or _DRIVER
        self.spawn_fn = spawn_fn          # test seam
        self.connect_timeout = connect_timeout
        self.state = "idle"
        self.target = None
        self.last_location = None          # (file, line) of last pause
        self.state_lock = threading.Lock()
        self._proc = None
        self._sock = None
        self._reader = None
        self._recvbuf = b""
        self._dead = threading.Event()
        self._eval_seq = 0
        self._eval_waiters = {}            # id -> queue.Queue
        self._break_cache = {}             # file -> set(lines)

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    @property
    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    @property
    def paused(self):
        return self.state == "paused"

    def start(self, target, cwd=None):
        """Spawn the driver, connect, deliver breakpoints, start tracing."""
        if self.alive:
            self.stop()
        self._dead.clear()
        self.target = os.path.abspath(target)
        spawn = self.spawn_fn or subprocess.Popen
        self._proc = spawn(
            [self.python, "-u", self.driver_path, self.target],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, cwd=cwd or os.path.dirname(self.target) or None)
        assert self._proc.stdout is not None
        line = self._proc.stdout.readline().strip()
        if not line.startswith("PORT "):
            raise RuntimeError(f"debug driver did not come up: {line!r}")
        port = int(line.split()[1])
        self._sock = socket.create_connection(("127.0.0.1", port),
                                              timeout=self.connect_timeout)
        self._sock.settimeout(None)
        self._recvbuf = b""
        self._reader = threading.Thread(target=self._recv_loop, daemon=True,
                                        name="friday-debug-session-reader")
        self._reader.start()
        for fname, lines in list(self._break_cache.items()):
            for lineno in sorted(lines):
                self._send({"cmd": "break", "file": fname, "line": lineno,
                            "set": True})
        self._send({"cmd": "run"})
        self._set_state("running")
        return True

    def stop(self):
        try:
            self._send({"cmd": "quit"})
        except OSError:
            pass
        self._teardown("ended")

    def _teardown(self, final_state):
        with self.state_lock:
            if self._sock is not None:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self._sock = None
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._proc = None
        self._dead.set()
        self._set_state(final_state if not self._dead.is_set() else "ended")

    def _set_state(self, state):
        with self.state_lock:
            self.state = state if state in _STATES else "error"

    # ------------------------------------------------------------------ #
    # driver events → owner callback
    # ------------------------------------------------------------------ #
    def _recv_loop(self):
        try:
            while not self._dead.is_set():
                chunk = self._sock.recv(65536)
                if not chunk:
                    break
                self._recvbuf += chunk
                while b"\n" in self._recvbuf:
                    line, self._recvbuf = self._recvbuf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8"))
                    except ValueError:
                        continue
                    self._dispatch(obj)
        except OSError:
            pass
        finally:
            if self.state in ("running", "paused"):
                # driver vanished without a terminated event
                self._set_state("error")
                self._emit("error", {
                    "message": "the debug driver closed the connection "
                               "unexpectedly"})
        self._dead.set()

    def _dispatch(self, obj):
        reply = obj.get("reply")
        if reply == "eval":
            waiter = self._eval_waiters.pop(obj.get("id"), None)
            if waiter is not None:
                waiter.put(obj)
            # Async/UI consumers receive eval results as events too.
            self._emit("eval", obj)
            return
        ev = obj.get("event")
        if ev == "stopped":
            self._set_state("paused")
            self.last_location = (obj.get("file"), obj.get("line"))
        elif ev == "terminated":
            self._set_state("ended")
        # everything (including eval replies) mirrors to the owner -- the
        # console displays eval results from here; the blocking waiter
        # above is only for programmatic callers/tests.
        self._emit(ev, obj)

    def _emit(self, kind, payload):
        try:
            self.on_event(kind, payload)
        except Exception:  # noqa: BLE001 -- an owner-side bug can't kill us
            pass

    # ------------------------------------------------------------------ #
    # commands (safe to call from any thread; only honored when they apply)
    # ------------------------------------------------------------------ #
    def _send(self, obj):
        sock = self._sock
        if sock is None:
            return
        try:
            sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n")
                         .encode("utf-8"))
        except OSError:
            pass

    def set_breakpoint(self, fname, line, enabled):
        """Record + (if a session is live) send one breakpoint change.
        Persisted across stop/start for this DebugSession's own lifetime."""
        fname = os.path.abspath(fname)
        bucket = self._break_cache.setdefault(fname, set())
        if enabled:
            bucket.add(int(line))
        else:
            bucket.discard(int(line))
        if not bucket:
            self._break_cache.pop(fname, None)
        if self.alive:
            self._send({"cmd": "break", "file": fname, "line": int(line),
                        "set": bool(enabled)})

    def breakpoints(self):
        return {f: sorted(lines) for f, lines in self._break_cache.items()}

    def continue_(self):
        if self.paused:
            self._send({"cmd": "continue"})
            self._set_state("running")

    def step_over(self):
        if self.paused:
            self._send({"cmd": "next"})
            self._set_state("running")

    def step_into(self):
        if self.paused:
            self._send({"cmd": "step"})
            self._set_state("running")

    def step_out(self):
        if self.paused:
            self._send({"cmd": "out"})
            self._set_state("running")

    def evaluate(self, expr, timeout=5):
        """Evaluate `expr` in the paused frame. Returns
        {"ok": bool, "value": ...} (same shape the driver sends), or a
        {"ok": False, "error": ...} dict if impossible/timeout. The
        result is ALSO delivered to on_event as event "eval" for the
        console window; blocking here is only for callers that want the
        value directly."""
        if not self.paused:
            return {"ok": False, "error": "no paused frame to evaluate in"}
        self._eval_seq += 1
        rid = self._eval_seq
        waiter = queue.Queue(maxsize=1)
        self._eval_waiters[rid] = waiter
        self._send({"cmd": "eval", "id": rid, "expr": expr})
        try:
            return waiter.get(timeout=timeout)
        except queue.Empty:
            self._eval_waiters.pop(rid, None)
            return {"ok": False, "error": "evaluation timed out"}

    def evaluate_async(self, expr):
        """Non-blocking evaluate for the UI console: the result arrives
        as an on_event(kind="eval", ...) payload, same shape as
        evaluate()'s return value. Returns an eval id or None."""
        if not self.paused:
            return None
        self._eval_seq += 1
        self._send({"cmd": "eval", "id": self._eval_seq, "expr": expr})
        return self._eval_seq

    def request_vars(self):
        if self.paused:
            self._send({"cmd": "vars"})

    def kill(self):
        self._teardown("ended")

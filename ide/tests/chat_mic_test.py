"""IDE chat mic: click to record -> click to stop -> transcript lands
editable in the composer; Enter (existing flow) sends it. Backends are
faked through chat_voice's sealed injection points -- no mic needed.
"""
import os
import shutil
import sys
import tempfile
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)
os.environ.setdefault("DISPLAY", ":99")
import tkinter.messagebox as MB
MB.showerror = lambda *a, **k: None
MB.showinfo = lambda *a, **k: None
MB.askyesnocancel = lambda *a, **k: False
MB.askokcancel = lambda *a, **k: True
import ide as V
V.messagebox = MB
V.IDEApp.open_url = lambda self, u: None
from _fixtures import make_code_workspace

W = make_code_workspace(tempfile.mkdtemp(prefix="idew"))
P = []
F = []


def chk(n, c, d=""):
    (P if c else F).append(f"{n} {d}")
    print(("  OK   " if c else "  FAIL ") + n + "  " + str(d), flush=True)


def wait_for(cond, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.update()
        if cond():
            return True
        time.sleep(0.04)
    return cond()


app = V.IDEApp(W)


def main():
    # ---- mic button exists when the chat panel exists -------------------
    # (standalone runs without Friday's agent host don't build
    # ChatSidePanel at all; spin up a fake host-free panel directly)
    panel = app.chat_panel
    if panel is None:
        # construct a bare ChatSidePanel the same way the app does --
        # tests the widget itself without needing the agent host
        panel = V.ChatSidePanel(app)
        panel.__dict__["_standalone_test"] = True
    chk("mic button built", hasattr(panel, "mic_button"),
        type(panel).__name__)

    # ---- composer cosmetics: blue focus glow, real blinking cursor ------
    C = V.C
    tb = panel.entry._textbox
    chk("blinking text cursor configured",
        int(tb.cget("insertwidth")) >= 2,
        tb.cget("insertwidth"))
    chk("cursor is accent blue", tb.cget("insertbackground") == "#3B82F6")
    # Tk won't deliver events to unmapped widgets under Xvfb -- a fresh
    # bare panel in a small mapped toplevel mirrors clicking the docked
    # chat panel in production (widgets can't be re-parented into a
    # different toplevel, so the already-built `panel` stays where it
    # is for the remaining checks).
    import tkinter as _tk
    _demowin = _tk.Toplevel(app)
    _demowin.geometry("420x460+80+80")
    _demowin._agent_host = None
    cpanel = V.ChatSidePanel(_demowin)
    cpanel.pack(fill="both", expand=True)
    _demowin.update_idletasks()
    _demowin.deiconify()
    _demowin.update()
    ct_b = cpanel.entry._textbox
    ct_b.event_generate("<Button-1>", when="tail")
    _demowin.update()
    chk("click border glows blue",
        cpanel.composer.cget("border_color") == "#3B82F6",
        cpanel.composer.cget("border_color"))
    ct_b.event_generate("<FocusIn>", when="tail")
    _demowin.update()
    chk("focus keeps glow",
        cpanel.composer.cget("border_color") == "#3B82F6")
    _demowin.destroy()
    panel.focus_set(); app.update()
    chk("unfocused border returns",
        panel.composer.cget("border_color") == C["border"])
    chk("speaker button removed", not hasattr(panel, "speaker_button"))

    from quiet import chat_voice

    # ---- happy path with a fake recorder -------------------------------
    class _FakeRec:
        def __init__(self, wav_path):
            self.error = None
            self.wav_path = wav_path
        def start(self):
            return True
        def stop(self, join_timeout=1.0):
            with open(self.wav_path, "wb") as fh:
                fh.write(b"RIFFfake")

    fake = chat_voice.ChatVoiceRecorder(
        audio_source_factory=lambda path: _FakeRec(path),
        transcriber=lambda p: ("make a hello world script", None))
    panel._mic_rec = fake

    avail_orig = chat_voice.availability
    chat_voice.availability = lambda: (True, "")
    try:
        panel._toggle_mic()
        app.update()
        chk("recording starts on first click", fake.recording)
        chk("recording notice shown",
            "Recording" in getattr(panel, "_last_notice", ""),
            getattr(panel, "_last_notice", ""))
        chk("mic button turns blue while recording",
            panel.mic_button.cget("fg_color") == "#3B82F6")
        panel._toggle_mic()   # second click: stop + transcribe
        got = wait_for(lambda: "hello world" in panel.entry.get(), 5.0)
        chk("transcript lands editable in composer", got, panel.entry.get())
        chk("send enabled once text present",
            panel.send_button.cget("state") == "normal")
    finally:
        chat_voice.availability = avail_orig

    # ---- unavailable stack reports honestly -----------------------------
    avail_orig = chat_voice.availability
    chat_voice.availability = lambda: (False, "no microphone here sir")
    gone = [None]
    def boom(**kw):
        gone[0] = True
        raise AssertionError("recorder must not be built when unavailable")
    panel._mic_rec = None
    chat_voice.ChatVoiceRecorder = boom
    try:
        panel._toggle_mic()
        app.update(); app.update()
        chk("no recorder built when stack unavailable", gone[0] is None)
        chk("honest unavailable message posted",
            "no microphone here sir" in getattr(panel, "_last_notice", ""))
    finally:
        chat_voice.availability = avail_orig
        import importlib
        importlib.reload(chat_voice)


try:
    main()
except Exception:
    traceback.print_exc()
    F.append("EXCEPTION")
print(f"\n===== PASS {len(P)}   FAIL {len(F)} =====")
for f in F:
    print("   XX", f)
app.destroy()
shutil.rmtree(W, ignore_errors=True)

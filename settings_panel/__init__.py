"""Friday's Settings panel (models & API keys, and the Profile page), in QML.

The UI is Qt Quick: drawn on the GPU in step with the display, with fades,
slides and spinners running on Qt's render thread -- so motion stays smooth
while checks and model fetches run. All settings logic is the shared code in
settings_store, settings_checks, provider_autodetect and user_profile;
backend.py only adapts it to Qt.

Friday opens it through app_panels.AppPanelManager like every other panel.
That manager drives panels from its own Tk thread, so `SettingsPanel` below
is a small adapter: it speaks the manager's panel protocol (withdraw,
deiconify, destroy, maximize_to, move_to_slot, restore_default,
set_active_panel, set_friday_status, show_page) and forwards each call to
the QML window on the Qt GUI thread. It never waits on Qt, so the two
threads can't deadlock each other.

Standalone: python -m settings_panel [profile]
"""
import os
import threading

_QML_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml")
_style_set = False
_dispatcher = None
_dispatcher_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Building a window (Qt GUI thread only)
# ---------------------------------------------------------------------------
def _ensure_style():
    """The Controls style must be chosen before the first QML engine loads."""
    global _style_set
    if not _style_set:
        from PySide6.QtQuickControls2 import QQuickStyle
        QQuickStyle.setStyle("Basic")
        _style_set = True


def create_window(initial_page="keys", host=None, geometry=None):
    """Load the Settings window. Returns (engine, backend, window).

    host:      optional QObject with requestMinimize()/requestClose()/
               pageShown(str) slots -- set when a panel manager owns the window.
    geometry:  optional (x, y, w, h) in Qt logical pixels for the first show.

    When the window really closes (not when it is hidden) it emits
    `finished`; the backend is shut down and the engine released then.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from .backend import SettingsBackend, theme_colors

    _ensure_style()
    engine = QQmlApplicationEngine()
    backend = SettingsBackend(engine)
    ctx = engine.rootContext()
    ctx.setContextProperty("backend", backend)
    ctx.setContextProperty("theme", theme_colors())
    ctx.setContextProperty("initialPage", initial_page or "keys")
    ctx.setContextProperty("host", host)
    ctx.setContextProperty("startGeometry", list(geometry) if geometry else [])
    engine.load(QUrl.fromLocalFile(os.path.join(_QML_DIR, "Main.qml")))
    roots = engine.rootObjects()
    if not roots:
        engine.deleteLater()
        raise RuntimeError("The Settings window failed to load "
                           "(see the console for QML errors).")
    window = roots[0]

    def _finished():
        try:
            backend.shutdown()
            engine.deleteLater()
        except RuntimeError:     # Qt already tore it down (app quitting)
            pass

    window.finished.connect(_finished)
    return engine, backend, window


def to_logical(x, y, w, h):
    """Physical screen pixels (what Friday's panel placement and Tk use) ->
    Qt logical pixels on whichever screen holds the point. Qt keeps each
    screen's top-left in native coordinates and scales from there."""
    from PySide6.QtGui import QGuiApplication
    screens = QGuiApplication.screens()
    for screen in screens:
        g = screen.geometry()
        r = screen.devicePixelRatio() or 1.0
        if g.x() <= x < g.x() + g.width() * r and g.y() <= y < g.y() + g.height() * r:
            return (round(g.x() + (x - g.x()) / r), round(g.y() + (y - g.y()) / r),
                    round(w / r), round(h / r))
    primary = QGuiApplication.primaryScreen()
    r = (primary.devicePixelRatio() if primary else 1.0) or 1.0
    return round(x / r), round(y / r), round(w / r), round(h / r)


def _centered_default(w=820, h=640):
    from PySide6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return (100, 100, w, h)
    a = screen.availableGeometry()
    w, h = min(w, a.width()), min(h, a.height())
    return (a.x() + (a.width() - w) // 2, a.y() + (a.height() - h) // 2, w, h)


# ---------------------------------------------------------------------------
# Running code on the Qt GUI thread from any thread
# ---------------------------------------------------------------------------
def _get_dispatcher():
    global _dispatcher
    with _dispatcher_lock:
        if _dispatcher is not None:
            return _dispatcher
        from PySide6.QtCore import QObject, Signal, Slot
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            raise RuntimeError("Settings needs Friday's Qt app, and none is running.")

        class _Dispatcher(QObject):
            run = Signal(object)

            def __init__(self):
                super().__init__()
                self.run.connect(self._run)

            @Slot(object)
            def _run(self, fn):
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001
                    print(f"[Friday] Settings panel: {exc}")

        d = _Dispatcher()
        d.moveToThread(app.thread())     # queued delivery onto the GUI thread
        _dispatcher = d
        return d


def _post(fn):
    """Run fn on the Qt GUI thread, in order, without waiting for it."""
    _get_dispatcher().run.emit(fn)


# ---------------------------------------------------------------------------
# The panel-manager adapter
# ---------------------------------------------------------------------------
class SettingsPanel:
    """app_panels' Settings panel: the QML window behind the manager's
    panel protocol. Constructed (and called) on the manager's Tk thread;
    every effect is posted to the Qt GUI thread."""

    DEFAULT_W = 820
    DEFAULT_H = 640

    def __init__(self, master=None, on_minimize=None, on_close=None,
                 corner_index=0, initial_content=None, bounds=None,
                 initial_page=None):
        _get_dispatcher()                     # fail fast without a Qt app
        self._on_minimize = on_minimize
        self._on_close = on_close
        self._page = initial_page if initial_page in ("keys", "profile") else "keys"
        self._start_bounds = tuple(bounds) if bounds else None
        self.is_maximized = False
        self._is_active_panel = False
        self._friday_glow_state = "idle"
        self._friday_glow_is_error = False
        self._visible = True
        self._destroyed = False
        self._engine = self._backend = self._window = self._host = None
        self._default_logical = None
        _post(self._build)

    # -- Qt-thread internals -------------------------------------------------
    def _build(self):
        from PySide6.QtCore import QObject, Slot

        panel = self

        class _Host(QObject):
            @Slot()
            def requestMinimize(self):
                panel._call_manager(panel._on_minimize)

            @Slot()
            def requestClose(self):
                panel._call_manager(panel._on_close)

            @Slot(str)
            def pageShown(self, name):
                panel._page = name

        if self._destroyed:
            return
        self._host = _Host()
        self._default_logical = (to_logical(*self._start_bounds)
                                 if self._start_bounds else _centered_default())
        self._engine, self._backend, self._window = create_window(
            self._page, host=self._host, geometry=self._default_logical)
        self._window.finished.connect(self._released)
        self._apply_glow()

    def _released(self):
        self._window = self._engine = self._backend = None

    def _call_manager(self, callback):
        # The manager's minimize/close block on its Tk thread; run them off
        # the GUI thread so neither side ever waits on the other.
        if callback is not None:
            threading.Thread(target=callback, name="FridaySettingsPanelAction",
                             daemon=True).start()

    def _invoke(self, method):
        from PySide6.QtCore import QMetaObject
        if self._window is not None:
            QMetaObject.invokeMethod(self._window, method)

    def _set_logical_geometry(self, rect):
        if self._window is None:
            return
        from PySide6.QtCore import QRect
        if self._window.visibility() in (self._window.Visibility.Maximized,
                                         self._window.Visibility.FullScreen):
            self._window.showNormal()
        self._window.setGeometry(QRect(*rect))

    def _apply_glow(self):
        if self._window is None:
            return
        self._window.setProperty("activePanel", self._is_active_panel)
        self._window.setProperty("fridayState", self._friday_glow_state)
        self._window.setProperty("fridayError", self._friday_glow_is_error)

    # -- panel protocol (called on the manager's thread) ---------------------
    def withdraw(self):
        self._visible = False
        _post(lambda: self._invoke("animatedHide"))

    def deiconify(self):
        self._visible = True
        _post(lambda: self._invoke("reveal"))

    def destroy(self):
        if self._destroyed:
            return
        self._destroyed = True
        _post(lambda: self._invoke("animatedClose"))

    def winfo_exists(self):
        return not self._destroyed

    def _move(self, x, y, w, h):
        _post(lambda: (self._set_logical_geometry(to_logical(x, y, w, h)),
                       self._invoke("reveal") if self._visible else None))

    def maximize_to(self, x, y, w, h):
        self.is_maximized = True
        self._move(x, y, w, h)

    def move_to_slot(self, x, y, w, h):
        self.is_maximized = False
        self._move(x, y, w, h)

    move_to = move_to_slot

    def restore_default(self):
        def apply():
            if self._default_logical is not None:
                self._set_logical_geometry(self._default_logical)
            if self._window is not None:
                self._window.lower()
        self.is_maximized = False
        _post(apply)
        self.set_active_panel(False)

    def set_active_panel(self, active):
        self._is_active_panel = bool(active)
        _post(self._apply_glow)

    def set_friday_status(self, state, is_error=False):
        self._friday_glow_state = state
        self._friday_glow_is_error = bool(is_error)
        _post(self._apply_glow)

    def show_page(self, name):
        if name not in ("keys", "profile"):
            return
        self._page = name
        _post(lambda: self._window is not None
              and self._window.setProperty("page", name))


__all__ = ["SettingsPanel", "create_window", "to_logical"]

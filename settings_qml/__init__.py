"""Friday's Settings panel rebuilt in QML (Qt Quick) -- a side-by-side preview.

Same data and logic as settings_panel.py (settings_store, settings_checks,
provider_autodetect, user_profile); only the UI layer is new. Qt Quick
draws on the GPU and syncs to the display's refresh rate, and opacity /
scale / rotation animators run on the render thread, so animations stay
smooth even while Python is busy.

    open_settings_window()          # from inside Friday (Qt main thread)
    python -m settings_qml          # standalone
"""
import os

_QML_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml")
_open = {}          # "window" -> (engine, backend, window) while open
_style_set = False


def _ensure_style():
    """Controls style must be chosen before the first QML engine loads."""
    global _style_set
    if not _style_set:
        from PySide6.QtQuickControls2 import QQuickStyle
        QQuickStyle.setStyle("Basic")
        _style_set = True


def open_settings_window(initial_page="keys", embedded=True):
    """Open (or bring back) the QML Settings window. Qt GUI thread only.

    The window has no taskbar button. `embedded` (inside Friday) keeps the
    minimize button, which fades the panel out; calling this again brings
    it back. Standalone (embedded=False) there is no minimize button.
    """
    from PySide6.QtCore import QMetaObject, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from .backend import SettingsBackend, theme_colors

    if "window" in _open:
        _engine, _backend, window = _open["window"]
        QMetaObject.invokeMethod(window, "reveal")
        return window

    _ensure_style()
    engine = QQmlApplicationEngine()
    backend = SettingsBackend(engine)
    ctx = engine.rootContext()
    ctx.setContextProperty("backend", backend)
    ctx.setContextProperty("theme", theme_colors())
    ctx.setContextProperty("initialPage", initial_page)
    ctx.setContextProperty("embedded", bool(embedded))
    engine.load(QUrl.fromLocalFile(os.path.join(_QML_DIR, "Main.qml")))
    roots = engine.rootObjects()
    if not roots:
        engine.deleteLater()
        raise RuntimeError("The QML Settings window failed to load "
                           "(see the console for QML errors).")
    window = roots[0]

    def _finished():
        # Emitted by Main.qml on a real close -- minimizing only hides it.
        if _open.get("window", (None,))[0] is not engine:
            return
        _open.pop("window", None)
        try:
            backend.shutdown()
            engine.deleteLater()
        except RuntimeError:     # Qt already tore it down (app quitting)
            pass

    window.finished.connect(_finished)
    _open["window"] = (engine, backend, window)
    return window

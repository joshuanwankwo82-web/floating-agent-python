"""Standalone launch: python -m settings_qml [profile]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from PySide6.QtWidgets import QApplication
    from settings_qml import open_settings_window

    app = QApplication.instance() or QApplication(sys.argv)
    page = "profile" if "profile" in sys.argv[1:] else "keys"
    window = open_settings_window(page, embedded=False)
    window.finished.connect(app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

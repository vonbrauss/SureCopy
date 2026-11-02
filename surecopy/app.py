"""Start the SureCopy app:  python -m surecopy.app"""

from __future__ import annotations

import os
import sys


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":
        from .selftest import run
        return run(sys.argv[2])
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from .gui.theme import STYLE
    from .gui.window import MainWindow
    from .store import Store

    app = QApplication(sys.argv)
    app.setOrganizationName("SureCopy")
    app.setApplicationName("SureCopy")
    if sys.platform == "darwin":
        app.setFont(QFont(".AppleSystemUIFont", 13))
    app.setStyleSheet(STYLE)
    store = Store(os.environ.get("SURECOPY_DB"))   # default: ~/Library/Application Support/SureCopy
    window = MainWindow(store)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

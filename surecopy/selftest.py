"""`SureCopy --self-test REPORT.json`: proves a built app works on its own.

Uses only a new temporary folder, which it deletes. Never touches real drives or SureCopy's job
history. Copies a small mixed tree to two destinations (Fast and Full verify), checks every byte with
sha256, re-runs to confirm everything is skipped, loads the Qt GUI offscreen with its icons, and
writes the results as JSON. Exit code 0 means every check passed.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
import traceback


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(report_path: str) -> int:
    checks: dict[str, object] = {}
    tmp = tempfile.mkdtemp(prefix="surecopy-selftest-")
    try:
        from . import __version__
        from .engine import Engine
        from .jobs import plan_job
        from .models import Destination, JobSpec, JobStatus, VerifyLevel
        from .store import Store

        checks["version"] = __version__
        src = os.path.join(tmp, "src", "Show")
        files = {"clip.mov": os.urandom(9 * 1024 * 1024), "docs/notes.txt": b"hello\n", "empty.txt": b"",
                 "Café NFC/résumé.pdf": os.urandom(5000), "a" * 200 + ".txt": b"long name"}
        for rel, data in files.items():
            p = os.path.join(src, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(data)
        a, b = os.path.join(tmp, "A"), os.path.join(tmp, "B")
        os.makedirs(a)
        os.makedirs(b)
        store = Store(os.path.join(tmp, "selftest.db"))
        spec = JobSpec("self-test", [src], [Destination(a), Destination(b, VerifyLevel.FULL)])
        job, _ = plan_job(store, spec)
        status = Engine(store).run(job)
        checks["copy_status"] = status.value
        same = all(_sha(os.path.join(src, r)) == _sha(os.path.join(d, "Show", r)) for r in files for d in (a, b))
        checks["copies_match_sha256"] = same
        job2, _ = plan_job(store, spec)
        checks["rerun_status"] = Engine(store).run(job2).value
        checks["rerun_all_skipped"] = store.run_summary(job2).get("skipped") == len(files)
        checks["xxhash"] = __import__("xxhash").VERSION
        store.close()

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("SureCopySelfTest")
        from .gui.theme import ASSETS, STYLE
        from .gui.window import MainWindow
        app.setStyleSheet(STYLE)
        icons = [f for f in ("chevron.svg", "check.svg") if not QIcon(os.path.join(ASSETS, f)).pixmap(16, 16).isNull()]
        checks["gui_icons_load"] = len(icons) == 2
        gui_store = Store(os.path.join(tmp, "gui.db"))
        w = MainWindow(gui_store)
        w._force_close = True
        w.show()
        app.processEvents()
        checks["gui_window_opens"] = w.isVisible()
        w.close()
        gui_store.close()
        ok = (status == JobStatus.DONE and same and checks["rerun_status"] == "done"
              and checks["rerun_all_skipped"] and checks["gui_icons_load"] and checks["gui_window_opens"])
    except Exception:
        checks["error"] = traceback.format_exc()
        ok = False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    checks.update(ok=ok, python=sys.version.split()[0], platform=platform.platform(),
                  frozen=bool(getattr(sys, "frozen", False)), when=time.strftime("%Y-%m-%dT%H:%M:%S"))
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(checks, fh, indent=2)
    print(json.dumps(checks, indent=2))
    return 0 if ok else 1

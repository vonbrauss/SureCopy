import json
import os
import tempfile
import unittest

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication as PySide6  # noqa: F401  (Qt must actually load)
except ImportError:   # pragma: no cover
    PySide6 = None


@unittest.skipIf(PySide6 is None, "PySide6 not installed")
class SelfTestTests(unittest.TestCase):
    def test_self_test_passes_and_cleans_up(self):
        from surecopy.selftest import run
        before = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("surecopy-selftest-")}
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fh:
            path = fh.name
        try:
            self.assertEqual(run(path), 0)
            with open(path) as fh2:
                report = json.load(fh2)
            self.assertTrue(report["ok"])
            self.assertTrue(report["copies_match_sha256"])
        finally:
            os.unlink(path)
        after = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("surecopy-selftest-")}
        self.assertEqual(after, before)

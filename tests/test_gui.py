"""GUI smoke tests. Offscreen, temporary files only. Skipped if PySide6 isn't installed."""

import os
import unittest

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEventLoop, QSettings, QTimer
    from PySide6.QtWidgets import QApplication
except ImportError:   # pragma: no cover
    QApplication = None

from surecopy.models import Destination, JobSpec, JobStatus

from .helpers import Sandbox, tree


def wait_until(cond, timeout_ms=20000):
    """Spin a real event loop (not QTest.qWait, which starves the copy threads)."""
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: cond() and loop.quit())
    timer.start(50)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    timer.stop()
    return cond()


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class GuiTests(Sandbox):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("SureCopyTests")   # never touch the real settings
        QCoreApplication.setApplicationName("SureCopyTests")
        QSettings().clear()

    def window(self):
        from surecopy.gui.theme import STYLE
        from surecopy.gui.window import MainWindow
        self.app.setStyleSheet(STYLE)
        w = MainWindow(self.store)
        w._force_close = True
        w.show()
        self._windows = getattr(self, "_windows", []) + [w]
        return w

    def tearDown(self):
        for w in getattr(self, "_windows", []):   # close windows before the database closes
            w.close()
        super().tearDown()

    def test_new_copy_runs_and_shows_done(self):
        from surecopy.jobs import plan_job
        src = self.make({"a.mov": 300_000, "docs/b.pdf": 2000})
        a, b = self.dest("A"), self.dest("B")
        w = self.window()
        w.open_new([src])
        w.new_panel._add_dest_row(a, w.new_panel._default_level())
        w.new_panel._add_dest_row(b, w.new_panel._default_level())
        spec = w.new_panel.spec()
        self.assertEqual(spec.sources, [src])
        self.assertEqual(spec.name, "Show")
        job, _ = plan_job(self.store, spec)
        w.runner.start()
        w.select_job(job)
        self.assertTrue(wait_until(lambda: self.store.job_status(job) == JobStatus.DONE))
        w.tick()
        self.assertEqual(w.job_view.primary.text(), "Run Again")
        self.assertEqual(tree(os.path.join(a, "Show")), tree(src))
        self.assertEqual(w.done_list.count(), 1)

    def test_pause_button_and_resume(self):
        from surecopy.jobs import plan_job
        src = self.make({"big.mov": 40 * 1024 * 1024})
        a = self.dest("A")
        job, _ = plan_job(self.store, JobSpec("t", [src], [Destination(a)], chunk_mib=1))
        w = self.window()
        w.select_job(job)
        w.runner.start()
        self.assertTrue(wait_until(lambda: w.runner.current == job, 5000))
        w.tick()
        self.assertEqual(w.job_view.primary.text(), "Pause")
        w.on_action("pause", job)
        self.assertTrue(wait_until(lambda: self.store.job_status(job) in (JobStatus.PAUSED, JobStatus.DONE)))
        w.on_action("resume", job)
        self.assertTrue(wait_until(lambda: self.store.job_status(job) == JobStatus.DONE))

    def test_mirror_dialog_locks_removal_until_typed(self):
        from surecopy.gui.dialogs import MirrorDialog
        from surecopy.jobs import mirror_preview, plan_job
        from surecopy.engine import Engine
        src = self.make({"keep.mov": 100})
        a = self.dest("A")
        os.makedirs(os.path.join(a, "Show"))
        with open(os.path.join(a, "Show", "stale.mov"), "wb") as fh:
            fh.write(b"old")
        spec = JobSpec("m", [src], [Destination(a)], mirror=True)
        job, _ = plan_job(self.store, spec)
        self.assertEqual(Engine(self.store).run(job), JobStatus.CONFIRM_MIRROR)
        w = self.window()
        d = MirrorDialog(w, spec, mirror_preview(self.store, job))
        self.assertFalse(d.remove.isEnabled())
        d.typed.setText("mirror")
        self.assertFalse(d.remove.isEnabled())
        d.typed.setText("MIRROR")
        self.assertTrue(d.remove.isEnabled())
        self.assertTrue(os.path.exists(os.path.join(a, "Show", "stale.mov")))

    def test_pause_and_resume_ignore_the_wrong_job(self):
        from surecopy.jobs import plan_job
        from surecopy.engine import Engine
        src = self.make({"a.mov": 1000})
        done_job, _ = plan_job(self.store, JobSpec("d", [src], [Destination(self.dest("A"))], mirror=True))
        Engine(self.store).run(done_job)
        os.makedirs(os.path.join(self.tmp, "A", "Show", "x"), exist_ok=True)
        waiting, _ = plan_job(self.store, JobSpec("w", [src], [Destination(self.dest("B"))]))
        self.store.set_job_status(waiting, JobStatus.CONFIRM_MIRROR)
        w = self.window()
        w.on_action("pause", done_job)                  # nothing running: no effect
        w.on_action("resume", waiting)                  # waiting for mirror OK: must not re-queue
        self.assertEqual(self.store.job_status(waiting), JobStatus.CONFIRM_MIRROR)
        self.assertEqual(self.store.job_status(done_job), JobStatus.DONE)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class EstimateGuiTests(GuiTests):
    # reuses the window() helper; skip GuiTests' own tests here
    test_new_copy_runs_and_shows_done = test_pause_button_and_resume = None
    test_mirror_dialog_locks_removal_until_typed = test_pause_and_resume_ignore_the_wrong_job = None

    def _job_with_history(self, past_runs):
        from surecopy.drives import _cache, sources_key, volume_for
        from surecopy.jobs import plan_job
        _cache.clear()
        src = self.make({"a.mov": 1000})
        a = self.dest("A")
        spec = JobSpec("t", [src], [Destination(a)])
        src_key, dst_key = sources_key(spec.sources), volume_for(a).key
        MB = 1024 * 1024
        for _ in range(past_runs):        # earlier copies between the same drives: 100 MB/s, 1 ms per file
            old, _r = plan_job(self.store, spec)
            self.store.add_perf(old, self.store.dests(old)[0].id, 1000 * MB, 200, 10.2, src_key, dst_key, False)
        job, _r = plan_job(self.store, spec)
        return job, spec

    def test_first_copy_has_no_estimate(self):
        job, _ = self._job_with_history(0)
        w = self.window()
        w.select_job(job)
        d = self.store.dests(job)[0]
        self.assertEqual(w.job_view._usual_text(job, d, (100 * 1024 * 1024, 5, 1.0), True)[0],
                         "First copy between these drives")
        self.assertEqual(w.job_view._usual_text(job, d, None, True)[0], "")          # nothing copied yet
        d.state = "disconnected"
        self.assertEqual(w.job_view._usual_text(job, d, (100 * 1024 * 1024, 5, 1.0), True)[0], "")

    def test_cards_compare_with_usual_speed(self):
        from surecopy.gui import theme
        MB = 1024 * 1024
        job, _ = self._job_with_history(3)
        w = self.window()
        w.select_job(job)
        d = self.store.dests(job)[0]
        text, color, tip = w.job_view._usual_text(job, d, (1000 * MB, 200, 10.7), True)
        self.assertEqual((text, color), ("95% of usual speed", theme.GREEN))
        self.assertIn("based on 3 past copies", tip)
        self.assertEqual(w.job_view._usual_text(job, d, (1000 * MB, 200, 40.0), True)[1], theme.RED)
        self.assertEqual(w.job_view._usual_text(job, d, (1000 * MB, 200, 5.0), True)[0], "Faster than usual")
        self.assertEqual(w.job_view._usual_text(job, d, (5 * MB, 2, 1.0), True)[0], "Comparing with usual speed…")

    def test_check_dialog_shows_the_estimate(self):
        from surecopy.estimate import estimate_job
        from surecopy.gui.dialogs import CheckDialog
        from surecopy.jobs import check_job
        from PySide6.QtWidgets import QLabel
        job, spec = self._job_with_history(2)
        result = check_job(spec)
        est = estimate_job(self.store, spec, result.files)
        self.assertEqual((est[0].nfiles, est[0].runs), (1, 2))
        texts = " | ".join(l.text() for l in CheckDialog(self.window(), spec, result, True, est).findChildren(QLabel))
        self.assertIn("Estimated time: under a minute", texts)
        self.assertIn("based on 2 past copies", texts)
        self.assertIn("1 new or changed file", texts)

    def test_history_shows_speed_and_usual(self):
        MB = 1024 * 1024
        job, spec = self._job_with_history(2)
        d = self.store.dests(job)[0]
        old = self.store.perf_detail(job - 1)[self.store.dests(job - 1)[0].id]
        self.store.add_perf(job, d.id, 1000 * MB, 200, 20.4, old[3], old[4], False)   # half the usual speed
        self.store.set_job_status(job, JobStatus.DONE)
        w = self.window()
        w.select_job(job)
        w.tick()
        from PySide6.QtWidgets import QLabel
        rows = " | ".join(l.text() for l in w.job_view.history.findChildren(QLabel))
        self.assertIn("50% of usual speed", rows)
        self.assertIn("MB/s", rows)
